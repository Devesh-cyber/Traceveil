import csv
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models import AuditLog, TakedownRequest
from app.takedown import contacts as contacts_module
from app.takedown.contacts import Contact, find_contact, load_contacts
from tests.test_review import NOTE, audit_actions, review, scanned_case


def write_csv(path: Path, rows: list[dict]) -> Path:
    fields = ["domain", "abuse_email", "report_url", "source_url", "verified_on", "notes"]
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


# ============================================================ the contacts list

def test_the_shipped_contact_file_loads():
    contacts, problems = load_contacts()
    assert problems == [] and isinstance(contacts, dict)


def test_a_contact_is_verified_only_with_a_source_and_a_date(tmp_path):
    path = write_csv(tmp_path / "c.csv", [
        {"domain": "checked.test", "abuse_email": "abuse@checked.test", "source_url": "https://checked.test/abuse", "verified_on": "2026-10-01"},
        {"domain": "guess.test", "abuse_email": "abuse@guess.test"},
        {"domain": "nodate.test", "report_url": "https://nodate.test/form", "source_url": "https://nodate.test/help"},
    ])
    contacts, problems = load_contacts(path)
    assert problems == []
    assert contacts["checked.test"].verified is True
    assert contacts["guess.test"].verified is False
    assert contacts["nodate.test"].verified is False
    assert contacts["nodate.test"].address == "https://nodate.test/form"


def test_bad_rows_are_reported_and_never_stop_the_good_ones(tmp_path):
    path = write_csv(tmp_path / "c.csv", [
        {"domain": "ok.test", "abuse_email": "abuse@ok.test"},
        {"domain": "bademail.test", "abuse_email": "not an email"},
        {"domain": "http.test", "report_url": "http://http.test/form"},
        {"domain": "baddate.test", "abuse_email": "a@baddate.test", "source_url": "https://baddate.test/x", "verified_on": "yesterday"},
        {"domain": "ok.test", "abuse_email": "second@ok.test"},
        {"domain": "not a domain", "abuse_email": "a@b.test"},
    ])
    contacts, problems = load_contacts(path)
    assert set(contacts) == {"ok.test", "baddate.test"}
    assert contacts["baddate.test"].verified is False
    assert len(problems) == 7  # a row with no usable contact left is reported twice: why, and that it was dropped


def test_a_subdomain_finds_its_parent_and_an_unknown_site_gets_no_guess():
    book = {"example.test": Contact("example.test", abuse_email="abuse@example.test")}
    assert find_contact("video.cdn.example.test", book).abuse_email == "abuse@example.test"
    assert find_contact("other.test", book) is None


# ============================================================ drafting

def reviewed_match(client, user, status="reviewed"):
    case, scan_id, match = scanned_case(client, user)
    if status != "potential":
        assert review(client, user, match["id"], "reviewed").status_code == 200
    return case, match


def draft(client, user, match_id, **body):
    return client.post(f"/api/matches/{match_id}/takedown", json=body, headers=user["headers"])


def test_a_draft_needs_a_match_a_person_has_reviewed(client, make_user):
    user = make_user()
    _, match = reviewed_match(client, user, status="potential")
    assert draft(client, user, match["id"]).status_code == 409
    review(client, user, match["id"], "dismissed")
    assert draft(client, user, match["id"]).status_code == 409


def test_the_draft_states_facts_we_have_and_nothing_is_sent(client, make_user, monkeypatch):
    book = {"good.test": Contact("good.test", abuse_email="abuse@good.test",
                                 source_url="https://good.test/abuse", verified_on="2026-10-01")}
    monkeypatch.setattr("app.api.routes.takedowns.find_contact", lambda d: find_contact(d, book))
    user = make_user()
    case, match = reviewed_match(client, user)
    response = draft(client, user, match["id"], sender_name="A. Person", reply_to="a.person@example.org")
    assert response.status_code == 201
    t = response.json()
    assert t["status"] == "draft" and t["sent_at"] is None
    assert t["contact"] == "abuse@good.test" and t["contact_verified"] is True
    assert match["source_page_url"] in t["body"] and match["image_url"] in t["body"]
    assert "did not consent" in t["body"] and "A. Person" in t["body"] and "a.person@example.org" in t["body"]
    assert "good.test" in t["subject"]
    assert t["placeholders_left"] == [] and t["warnings"] == []
    assert len(t["history"]) == 1 and t["history"][0]["to"] == "draft"
    # the body carries the fingerprint of the preserved copy, never the picture
    files = client.get(f"/api/cases/{case['id']}/evidence", headers=user["headers"]).json()
    copy = next(f for f in files if f["kind"] == "supporting")
    assert copy["sha256"] in t["body"]
    assert "takedown.drafted" in audit_actions(case["id"])


def test_an_unknown_site_gets_no_contact_and_a_clear_warning(client, make_user):
    user = make_user()
    _, match = reviewed_match(client, user)
    t = draft(client, user, match["id"]).json()
    assert t["contact"] is None and t["contact_verified"] is False
    assert any("No contact is known" in w for w in t["warnings"])
    assert t["placeholders_left"] == ["[YOUR CONTACT DETAILS]", "[YOUR NAME]"]


def test_an_unverified_contact_is_flagged(client, make_user, monkeypatch):
    book = {"good.test": Contact("good.test", abuse_email="abuse@good.test")}
    monkeypatch.setattr("app.api.routes.takedowns.find_contact", lambda d: find_contact(d, book))
    user = make_user()
    _, match = reviewed_match(client, user)
    t = draft(client, user, match["id"]).json()
    assert t["contact"] == "abuse@good.test" and t["contact_verified"] is False
    assert any("not been verified" in w for w in t["warnings"])


def test_only_one_unsent_draft_per_match(client, make_user):
    user = make_user()
    _, match = reviewed_match(client, user)
    assert draft(client, user, match["id"]).status_code == 201
    assert draft(client, user, match["id"]).status_code == 409


def test_a_confirmed_video_is_mentioned_in_the_draft(client, make_user):
    from app.models import Match
    user = make_user()
    _, match = reviewed_match(client, user)
    with SessionLocal() as db:
        row = db.get(Match, uuid.UUID(match["id"]))
        row.signals = {**row.signals, "video": {"verification": "sequence_confirmed"}}
        db.commit()
    assert "also appears to contain the same footage" in draft(client, user, match["id"]).json()["body"]


# ============================================================ editing and sending (by the user)

def filled(client, user, match_id):
    t = draft(client, user, match_id).json()
    body = t["body"].replace("[YOUR NAME]", "A. Person").replace("[YOUR CONTACT DETAILS]", "a.person@example.org")
    r = client.patch(f"/api/takedowns/{t['id']}", json={"body": body, "contact": "abuse@good.test"}, headers=user["headers"])
    assert r.status_code == 200
    return r.json()


def test_editing_a_draft_and_a_typed_in_contact_is_never_called_verified(client, make_user):
    user = make_user()
    _, match = reviewed_match(client, user)
    t = filled(client, user, match["id"])
    assert t["contact"] == "abuse@good.test" and t["contact_verified"] is False and t["contact_source"] is None
    assert t["placeholders_left"] == []


def test_it_cannot_be_marked_sent_while_parts_are_unfilled(client, make_user):
    user = make_user()
    _, match = reviewed_match(client, user)
    t = draft(client, user, match["id"]).json()
    r = client.post(f"/api/takedowns/{t['id']}/sent", json={"via": "email"}, headers=user["headers"])
    assert r.status_code == 409 and "[YOUR NAME]" in r.json()["detail"]


def test_marking_sent_records_what_the_user_says_and_future_dates_are_refused(client, make_user):
    user = make_user()
    _, match = reviewed_match(client, user)
    t = filled(client, user, match["id"])
    future = client.post(f"/api/takedowns/{t['id']}/sent", json={"via": "email", "sent_at": "2999-01-01T00:00:00Z"}, headers=user["headers"])
    assert future.status_code == 422
    r = client.post(f"/api/takedowns/{t['id']}/sent", json={"via": "web_form", "sent_at": "2026-10-10T10:00:00Z"}, headers=user["headers"])
    body = r.json()
    assert r.status_code == 200 and body["status"] == "sent" and body["sent_via"] == "web_form"
    assert body["sent_at"].startswith("2026-10-10T10:00:00")
    assert [h["to"] for h in body["history"]] == ["draft", "sent"]
    # a sent request can no longer be edited, deleted or sent twice
    assert client.patch(f"/api/takedowns/{t['id']}", json={"subject": "something else"}, headers=user["headers"]).status_code == 409
    assert client.delete(f"/api/takedowns/{t['id']}", headers=user["headers"]).status_code == 409
    assert client.post(f"/api/takedowns/{t['id']}/sent", json={"via": "email"}, headers=user["headers"]).status_code == 409


def test_a_draft_can_be_deleted(client, make_user):
    user = make_user()
    case, match = reviewed_match(client, user)
    t = draft(client, user, match["id"]).json()
    assert client.delete(f"/api/takedowns/{t['id']}", headers=user["headers"]).status_code == 204
    assert client.get(f"/api/cases/{case['id']}/takedowns", headers=user["headers"]).json() == []


# ============================================================ following up

def move(client, user, tid, status, note=None):
    body = {"status": status, **({"note": note} if note else {})}
    return client.post(f"/api/takedowns/{tid}/status", json=body, headers=user["headers"])


def sent_request(client, user):
    case, match = reviewed_match(client, user)
    t = filled(client, user, match["id"])
    assert client.post(f"/api/takedowns/{t['id']}/sent", json={"via": "email"}, headers=user["headers"]).status_code == 200
    return case, t["id"]


def test_the_whole_follow_up_story_is_kept_in_order(client, make_user):
    user = make_user()
    case, tid = sent_request(client, user)
    assert move(client, user, tid, "no_response", "a week passed").status_code == 200
    assert move(client, user, tid, "sent", "second email").status_code == 200
    assert move(client, user, tid, "acknowledged", "they replied with a ticket number").status_code == 200
    assert move(client, user, tid, "removed", "page is gone").status_code == 200
    assert move(client, user, tid, "still_live", "it was re-uploaded").status_code == 200
    t = client.get(f"/api/takedowns/{tid}", headers=user["headers"]).json()
    assert [h["to"] for h in t["history"]] == ["draft", "sent", "no_response", "sent", "acknowledged", "removed", "still_live"]
    assert t["status"] == "still_live" and t["response_note"] == "it was re-uploaded"


def test_impossible_changes_are_refused(client, make_user):
    user = make_user()
    case, match = reviewed_match(client, user)
    t = draft(client, user, match["id"]).json()
    assert move(client, user, t["id"], "removed").status_code == 409       # never sent
    _, tid = sent_request(client, user)
    assert move(client, user, tid, "removed").status_code == 200
    assert move(client, user, tid, "rejected").status_code == 409          # removed cannot become rejected
    assert move(client, user, tid, "banana").status_code == 422


# ============================================================ privacy between users

def test_nobody_can_see_or_change_someone_elses_requests(client, make_user):
    owner, stranger = make_user(), make_user()
    case, tid = sent_request(client, owner)
    h = stranger["headers"]
    assert client.get(f"/api/takedowns/{tid}", headers=h).status_code == 404
    assert client.patch(f"/api/takedowns/{tid}", json={"subject": "hijack"}, headers=h).status_code == 404
    assert move(client, stranger, tid, "removed").status_code == 404
    assert client.get(f"/api/cases/{case['id']}/takedowns", headers=h).status_code == 404
    assert client.get(f"/api/takedowns/{tid}").status_code in (401, 403)
    match_id = client.get(f"/api/takedowns/{tid}", headers=owner["headers"]).json()["match_id"]
    assert draft(client, stranger, match_id).status_code == 404


# ============================================================ report and channels

def test_the_report_carries_the_takedown_history(client, make_user):
    user = make_user()
    case, tid = sent_request(client, user)
    move(client, user, tid, "no_response", "nothing back")
    report = client.get(f"/api/cases/{case['id']}/report", headers=user["headers"]).json()
    item = report["takedown_requests"][0]
    assert item["status"] == "no_response" and item["sent_via"] == "email"
    assert [h["to"] for h in item["history"]] == ["draft", "sent", "no_response"]
    md = client.get(f"/api/cases/{case['id']}/report?format=markdown", headers=user["headers"])
    assert "## Takedown requests" in md.text and "no_response" in md.text


def test_reporting_channels_are_information_only_and_marked_unchecked(client, make_user):
    user = make_user()
    data = client.get("/api/reporting-channels", headers=user["headers"]).json()
    ids = {c["id"] for c in data["channels"]}
    assert {"ncrp", "stopncii", "grievance_officer", "police_or_lawyer"} <= ids
    assert "NOT yet checked" in data["note"]
    assert all(c["last_checked"] is None for c in data["channels"])
    assert client.get("/api/reporting-channels").status_code in (401, 403)


# ============================================================ the promise

def test_no_code_can_send_anything_and_nothing_is_reported_by_us():
    """The takedown code imports nothing that could send an email or contact a website."""
    import ast

    root = Path(contacts_module.__file__).parent
    files = list(root.glob("*.py")) + [root.parent / "api" / "routes" / "takedowns.py"]
    forbidden = {"smtplib", "aiosmtplib", "email", "requests", "httpx", "urllib", "socket", "sendgrid", "http", "ssl"}
    for path in files:
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert name.split(".")[0] not in forbidden, f"{path.name} imports {name}"
