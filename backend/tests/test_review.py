import hashlib
import uuid

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models import AuditLog
from tests.test_scan import COPY_JPEG, FakeWeb, basic_site
from tests.test_scans_api import make_case
from tests.test_worker import queued_scan, run_job

NOTE = "Compared the pages myself: it is my photo, cropped."


def scanned_case(client, user, with_snapshot=True, monkeypatch=None):
    """A case whose scan found one match (a thumbnail copy of the reference)."""
    case, scan_id = queued_scan(client, user)
    web = FakeWeb()
    basic_site(web, "good.test")
    web.add("denied.test", "/", "no", status=403)
    run_job(scan_id, web, ["good.test", "denied.test"])
    matches = client.get(f"/api/scans/{scan_id}/matches", headers=user["headers"]).json()
    assert len(matches) == 1
    return case, scan_id, matches[0]


def review(client, user, match_id, status, note=None):
    body = {"status": status}
    if note is not None:
        body["note"] = note
    return client.post(f"/api/matches/{match_id}/review", json=body, headers=user["headers"])


def audit_actions(case_id):
    with SessionLocal() as db:
        return list(db.scalars(select(AuditLog.action).where(AuditLog.case_id == uuid.UUID(case_id))))


# ===================================================== the preserved copy

def test_a_private_copy_of_the_matched_image_is_kept_as_evidence(client, make_user):
    user = make_user()
    case, _, match = scanned_case(client, user)
    assert match["has_snapshot"] is True and match["review_status"] == "potential"

    response = client.get(f"/api/matches/{match['id']}/snapshot", headers=user["headers"])
    assert response.status_code == 200
    assert response.content == COPY_JPEG  # exactly what was downloaded from the page
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "match.snapshot_viewed" in audit_actions(case["id"])  # looking at it leaves a trace


def test_the_copy_is_listed_with_its_fingerprint_and_capture_time(client, make_user):
    user = make_user()
    case, _, _ = scanned_case(client, user)
    files = client.get(f"/api/cases/{case['id']}/evidence", headers=user["headers"]).json()
    copies = [f for f in files if f["kind"] == "supporting"]
    assert len(copies) == 1
    assert copies[0]["sha256"] == hashlib.sha256(COPY_JPEG).hexdigest()
    assert copies[0]["created_at"] and copies[0]["delete_after"]


def test_keeping_copies_can_be_switched_off(client, make_user, monkeypatch):
    monkeypatch.setattr(get_settings(), "preserve_match_images", False)
    user = make_user()
    _, _, match = scanned_case(client, user)
    assert match["has_snapshot"] is False
    assert client.get(f"/api/matches/{match['id']}/snapshot", headers=user["headers"]).status_code == 404


def test_the_copy_is_removed_when_the_case_is_deleted(client, make_user, private_evidence_folder):
    user = make_user()
    case, _, _ = scanned_case(client, user)
    assert any((private_evidence_folder / case["id"]).iterdir())
    client.delete(f"/api/cases/{case['id']}", headers=user["headers"])
    assert not (private_evidence_folder / case["id"]).exists()


# ===================================================== reviewing

def test_a_person_can_review_a_finding(client, make_user):
    user = make_user()
    case, _, match = scanned_case(client, user)
    response = review(client, user, match["id"], "reviewed", "Looks like my photo.")
    assert response.status_code == 200
    body = response.json()
    assert body["review_status"] == "reviewed" and body["review_note"] == "Looks like my photo."
    assert body["reviewed_at"] is not None
    assert "match.reviewed" in audit_actions(case["id"])


def test_a_finding_cannot_jump_straight_to_verified(client, make_user):
    user = make_user()
    _, _, match = scanned_case(client, user)
    response = review(client, user, match["id"], "verified", NOTE)
    assert response.status_code == 409 and "reviewed before" in response.json()["detail"]


def test_verifying_needs_a_written_note(client, make_user):
    user = make_user()
    _, _, match = scanned_case(client, user)
    review(client, user, match["id"], "reviewed")
    assert review(client, user, match["id"], "verified").status_code == 422
    assert review(client, user, match["id"], "verified", "too short").status_code == 422
    assert review(client, user, match["id"], "verified", "   " + "x" * 5 + "   ").status_code == 422


def test_verifying_needs_a_preserved_copy(client, make_user, monkeypatch):
    monkeypatch.setattr(get_settings(), "preserve_match_images", False)
    user = make_user()
    _, _, match = scanned_case(client, user)
    review(client, user, match["id"], "reviewed")
    response = review(client, user, match["id"], "verified", NOTE)
    assert response.status_code == 409 and "preserved copy" in response.json()["detail"]


def test_the_full_path_to_verified(client, make_user):
    user = make_user()
    case, _, match = scanned_case(client, user)
    assert review(client, user, match["id"], "reviewed").status_code == 200
    verified = review(client, user, match["id"], "verified", NOTE)
    assert verified.status_code == 200
    assert verified.json()["review_status"] == "verified" and verified.json()["review_note"] == NOTE
    assert audit_actions(case["id"]).count("match.reviewed") == 2


def test_a_false_alarm_can_be_dismissed_and_a_review_can_be_undone(client, make_user):
    user = make_user()
    _, _, match = scanned_case(client, user)
    assert review(client, user, match["id"], "dismissed", "Different person.").json()["review_status"] == "dismissed"

    undone = review(client, user, match["id"], "potential").json()
    assert undone["review_status"] == "potential"
    assert undone["reviewed_at"] is None and undone["review_note"] is None


def test_bad_review_requests_are_rejected(client, make_user):
    user = make_user()
    _, _, match = scanned_case(client, user)
    assert review(client, user, match["id"], "approved").status_code == 422          # not a real status
    assert review(client, user, match["id"], "reviewed", "x" * 2001).status_code == 422  # note too long


def test_matches_are_private_to_their_owner(client, make_user):
    alice, bob = make_user(), make_user()
    _, _, match = scanned_case(client, alice)
    for path in ("", "/snapshot"):
        assert client.get(f"/api/matches/{match['id']}{path}", headers=bob["headers"]).status_code == 404
    assert review(client, bob, match["id"], "reviewed").status_code == 404
    assert client.get(f"/api/matches/{match['id']}", headers=alice["headers"]).status_code == 200


def test_match_endpoints_need_login(client):
    fake = uuid.uuid4()
    assert client.get(f"/api/matches/{fake}").status_code == 401
    assert client.get(f"/api/matches/{fake}/snapshot").status_code == 401
    assert client.post(f"/api/matches/{fake}/review", json={"status": "reviewed"}).status_code == 401