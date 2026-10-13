import hashlib
import json

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models import AuditLog
from app.reporting.evidence_report import NOTICE, verify_digest
from tests.test_review import NOTE, review, scanned_case
from tests.test_scan import COPY_JPEG
from tests.test_scans_api import PNG, make_case


def get_report(client, user, case_id, **params):
    response = client.get(f"/api/cases/{case_id}/report", params=params, headers=user["headers"])
    assert response.status_code == 200
    return response


def test_the_report_is_honest_about_what_it_does_and_does_not_show(client, make_user):
    user = make_user()
    case, scan_id, match = scanned_case(client, user)
    report = get_report(client, user, case["id"]).json()

    assert report["notice"] == NOTICE and "not proof" in report["notice"]
    assert report["case"]["id"] == case["id"]
    assert report["reference_files"][0]["sha256"] == hashlib.sha256(PNG).hexdigest()

    scan = report["scans"][0]
    assert scan["status"] == "completed"
    assert scan["stats"]["coverage"]["blocked"] == 1                        # the unsearched site is stated
    assert {s["domain"]: s["status"] for s in scan["sites"]} == {"good.test": "checked", "denied.test": "blocked"}

    assert report["finding_counts"]["potential"] == 1
    assert report["findings"] == []                                         # nobody has reviewed it yet
    assert report["includes_unreviewed_findings"] is False


def test_reviewed_and_verified_findings_appear_with_their_evidence(client, make_user):
    user = make_user()
    case, _, match = scanned_case(client, user)
    review(client, user, match["id"], "reviewed")
    review(client, user, match["id"], "verified", NOTE)

    report = get_report(client, user, case["id"]).json()
    assert len(report["findings"]) == 1
    finding = report["findings"][0]
    assert finding["review_status"] == "verified" and finding["review_note"] == NOTE
    assert finding["source_page_url"] == "https://good.test/video/1"
    assert finding["image_url"] == "https://good.test/img/copy.jpg"
    assert finding["match_kind"] == "exact" and finding["signals"]["method"] == "fingerprint"
    assert finding["preserved_copy"]["sha256"] == hashlib.sha256(COPY_JPEG).hexdigest()
    assert finding["discovered_at"] and finding["reviewed_at"]

    actions = [a["action"] for a in report["audit_trail"]]
    assert "match.reviewed" in actions and "scan.completed" in actions
    transitions = [(a["detail"]["from_status"], a["detail"]["to_status"])
                   for a in report["audit_trail"] if a["action"] == "match.reviewed"]
    assert transitions == [("potential", "reviewed"), ("reviewed", "verified")]


def test_unreviewed_findings_are_only_included_when_asked_for(client, make_user):
    user = make_user()
    case, _, match = scanned_case(client, user)
    report = get_report(client, user, case["id"], include_potential="true").json()
    assert report["includes_unreviewed_findings"] is True
    assert [f["review_status"] for f in report["findings"]] == ["potential"]


def test_dismissed_findings_are_never_listed_but_are_counted(client, make_user):
    user = make_user()
    case, _, match = scanned_case(client, user)
    review(client, user, match["id"], "dismissed", "Not me.")
    report = get_report(client, user, case["id"], include_potential="true").json()
    assert report["findings"] == [] and report["finding_counts"]["dismissed"] == 1


def test_any_change_to_the_report_is_detected_by_its_digest(client, make_user):
    user = make_user()
    case, _, match = scanned_case(client, user)
    review(client, user, match["id"], "reviewed")
    report = get_report(client, user, case["id"]).json()
    assert verify_digest(report)

    tampered = json.loads(json.dumps(report))
    tampered["findings"][0]["source_page_url"] = "https://somewhere-else.test/"
    assert not verify_digest(tampered)

    tampered = json.loads(json.dumps(report))
    tampered["scans"][0]["stats"]["coverage"]["blocked"] = 0   # hiding a site we could not search
    assert not verify_digest(tampered)


def test_the_markdown_version_states_the_same_things(client, make_user):
    user = make_user()
    case, _, match = scanned_case(client, user)
    review(client, user, match["id"], "reviewed", "Looks like my photo.")
    response = get_report(client, user, case["id"], format="markdown")
    text = response.text
    assert response.headers["content-type"].startswith("text/markdown")
    assert "not proof" in text and "NOT searched" in text
    assert "denied.test: blocked" in text and "good.test: checked" in text
    assert "REVIEWED" in text and "https://good.test/video/1" in text
    assert "Looks like my photo." in text and hashlib.sha256(COPY_JPEG).hexdigest() in text
    assert "## Audit trail" in text and "## Integrity" in text


def test_generating_a_report_is_logged_and_never_cached(client, make_user):
    user = make_user()
    case, *_ = scanned_case(client, user)
    response = get_report(client, user, case["id"])
    assert response.headers["cache-control"] == "no-store"
    with SessionLocal() as db:
        actions = list(db.scalars(select(AuditLog.action).where(AuditLog.case_id == __import__("uuid").UUID(case["id"]))))
    assert "report.generated" in actions


def test_reports_are_private_and_need_login(client, make_user):
    alice, bob = make_user(), make_user()
    case, *_ = scanned_case(client, alice)
    assert client.get(f"/api/cases/{case['id']}/report", headers=bob["headers"]).status_code == 404
    assert client.get(f"/api/cases/{case['id']}/report").status_code == 401


def test_a_case_with_no_scans_still_gives_a_valid_report(client, make_user):
    user = make_user()
    case = make_case(client, user)
    report = get_report(client, user, case["id"]).json()
    assert report["scans"] == [] and report["findings"] == [] and verify_digest(report)