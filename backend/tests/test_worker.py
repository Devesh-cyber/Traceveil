import asyncio
import time
import uuid

import pytest
from sqlalchemy import select

from app.crawler.domains import DomainEntry
from app.crawler.runner import ScanLimits
from app.db.session import SessionLocal
from app.models import AuditLog, DomainResult, Match, Scan
from app.worker.main import claim_next_scan, process_scan, recover_stuck_scans
from tests.test_scan import FakeWeb, basic_site, make_fetcher
from tests.test_scans_api import PNG, make_case, start_scan, upload  # PNG is make_photo(1)

LIMITS = ScanLimits(parallel_domains=1)


def queued_scan(client, user):
    case = make_case(client, user)
    evidence = upload(client, user, case["id"]).json()
    scan = start_scan(client, user, case["id"], evidence["id"]).json()
    return case, uuid.UUID(scan["id"])


def run_job(scan_id, web, hosts, limits=LIMITS, poll=0.05):
    assert claim_next_scan(scan_id) == scan_id

    async def go():
        async with make_fetcher(web) as fetcher:
            await process_scan(
                scan_id, fetcher=fetcher, domains=[DomainEntry(domain=h) for h in hosts],
                limits=limits, poll_seconds=poll,
            )

    asyncio.run(go())


def get(client, user, path):
    response = client.get(f"/api/scans/{path}", headers=user["headers"])
    assert response.status_code == 200
    return response.json()


def test_a_finished_scan_leaves_real_results_in_the_database(client, make_user):
    user = make_user()
    case, scan_id = queued_scan(client, user)
    web = FakeWeb()
    basic_site(web, "good.test")
    web.add("denied.test", "/", "no", status=403)

    run_job(scan_id, web, ["good.test", "denied.test"])

    scan = get(client, user, scan_id)
    assert scan["status"] == "completed" and scan["error"] is None
    assert scan["started_at"] and scan["finished_at"]
    assert scan["stats"]["sites_total"] == 2 and scan["stats"]["sites_done"] == 2
    assert scan["stats"]["matches_found"] == 1 and scan["stats"]["images_compared"] == 2
    assert scan["stats"]["coverage"] == {"checked": 1, "no_images": 0, "blocked": 1, "unreachable": 0, "skipped": 0}

    domains = {d["domain"]: d for d in get(client, user, f"{scan_id}/domains")}
    assert domains["good.test"]["status"] == "checked" and domains["good.test"]["pages_scanned"] == 4
    assert domains["denied.test"]["status"] == "blocked" and domains["denied.test"]["detail"] == "HTTP 403"

    matches = get(client, user, f"{scan_id}/matches")
    assert len(matches) == 1
    match = matches[0]
    assert match["kind"] == "exact" and match["review_status"] == "potential"  # a score is not proof
    assert match["image_url"] == "https://good.test/img/copy.jpg"
    assert match["source_page_url"] == "https://good.test/video/1"
    assert match["signals"]["method"] == "fingerprint" and match["signals"]["variant"] == "original"

    with SessionLocal() as db:
        actions = list(db.scalars(select(AuditLog.action).where(AuditLog.case_id == uuid.UUID(case["id"]))))
    assert {"scan.queued", "scan.started", "scan.completed"} <= set(actions)


def test_a_scan_where_nothing_could_be_compared_is_failed_not_completed(client, make_user):
    user = make_user()
    _, scan_id = queued_scan(client, user)
    web = FakeWeb()
    web.add("denied.test", "/", "no", status=403)
    web.add("gone.test", "/", "x", status=404)

    run_job(scan_id, web, ["denied.test", "gone.test"])

    scan = get(client, user, scan_id)
    assert scan["status"] == "failed"
    assert "No pictures could be compared" in scan["error"]
    assert scan["stats"]["coverage"]["blocked"] == 1 and scan["stats"]["coverage"]["unreachable"] == 1
    assert len(get(client, user, f"{scan_id}/domains")) == 2  # the reasons are still recorded


def test_a_scan_with_no_websites_configured_fails_with_a_clear_message(client, make_user):
    user = make_user()
    _, scan_id = queued_scan(client, user)
    run_job(scan_id, FakeWeb(), [])
    scan = get(client, user, scan_id)
    assert scan["status"] == "failed" and "domains.csv" in scan["error"]


def test_a_scan_that_hits_its_limit_is_marked_truncated(client, make_user):
    user = make_user()
    _, scan_id = queued_scan(client, user)
    web = FakeWeb()
    basic_site(web, "good.test")
    run_job(scan_id, web, ["good.test"], limits=ScanLimits(parallel_domains=1, max_total_images=1))
    scan = get(client, user, scan_id)
    assert scan["status"] == "completed"
    assert scan["truncated"] is True and scan["truncation_reason"] == "image limit reached"


class SlowWeb(FakeWeb):
    """Answers slowly, and runs `on_first_request` once, so a test can cancel mid-scan."""

    def __init__(self, on_first_request):
        super().__init__()
        self._on_first = on_first_request
        self._fired = False

    async def handler(self, request):
        if not self._fired:
            self._fired = True
            self._on_first()
        await asyncio.sleep(0.05)
        return super().handler(request)


def test_cancelling_a_running_scan_stops_it_and_it_stays_cancelled(client, make_user):
    user = make_user()
    _, scan_id = queued_scan(client, user)
    web = SlowWeb(lambda: client.post(f"/api/scans/{scan_id}/cancel", headers=user["headers"]))
    web.add("big.test", "/", "".join(f'<a href="/video/{i}">x</a>' for i in range(40)))
    for i in range(40):
        web.add("big.test", f"/video/{i}", f'<img src="/img/{i}.jpg">')
        web.add("big.test", f"/img/{i}.jpg", b"not really an image", "image/jpeg")

    started = time.monotonic()
    run_job(scan_id, web, ["big.test"])
    assert time.monotonic() - started < 15  # it stopped; it did not crawl all 40 pages slowly

    assert get(client, user, scan_id)["status"] == "cancelled"  # never overwritten by 'completed'


def test_claiming_takes_a_scan_only_once(client, make_user):
    user = make_user()
    _, scan_id = queued_scan(client, user)
    assert claim_next_scan(scan_id) == scan_id
    assert claim_next_scan(scan_id) is None
    assert get(client, user, scan_id)["status"] == "running"


def test_scans_left_running_by_a_dead_worker_are_failed_on_restart(client, make_user):
    user = make_user()
    _, scan_id = queued_scan(client, user)
    claim_next_scan(scan_id)  # 'running', and then the worker dies
    assert recover_stuck_scans() >= 1
    scan = get(client, user, scan_id)
    assert scan["status"] == "failed" and "worker stopped" in scan["error"]


def test_development_targets_are_refused_outside_development(monkeypatch):
    from app.core.config import Settings

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SECRET_KEY", "a-long-random-secret-for-this-test-only-123456")
    monkeypatch.setenv("SCAN_DEV_TARGETS", '["http://localhost:8001"]')
    with pytest.raises(ValueError):
        Settings()