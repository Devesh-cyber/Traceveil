import hashlib
import uuid

import pytest

from app.core.config import get_settings
from tests.factories import make_photo, to_bytes

PNG = to_bytes(make_photo(1), "PNG")


def make_case(client, user, title="a case"):
    response = client.post("/api/cases", json={"title": title}, headers=user["headers"])
    assert response.status_code == 201
    return response.json()


def upload(client, user, case_id, data=PNG, name="photo.png", content_type="image/png"):
    return client.post(
        f"/api/cases/{case_id}/evidence",
        files={"file": (name, data, content_type)},
        headers=user["headers"],
    )


def start_scan(client, user, case_id, evidence_id):
    return client.post(
        f"/api/cases/{case_id}/scans", json={"evidence_id": evidence_id}, headers=user["headers"]
    )


def ready_case(client, user):
    case = make_case(client, user)
    evidence = upload(client, user, case["id"]).json()
    return case, evidence


# ===================================================== uploading the reference file

def test_upload_stores_the_file_privately_and_records_its_fingerprint(client, make_user, private_evidence_folder):
    user = make_user()
    case = make_case(client, user)
    response = upload(client, user, case["id"], name="../../evil name.png")
    assert response.status_code == 201
    body = response.json()
    assert body["sha256"] == hashlib.sha256(PNG).hexdigest()
    assert body["content_type"] == "image/png" and body["size_bytes"] == len(PNG)
    assert body["original_filename"] == "evil name.png"  # path tricks removed
    assert body["delete_after"] is not None

    stored = list((private_evidence_folder / case["id"]).iterdir())
    assert len(stored) == 1 and stored[0].read_bytes() == PNG
    assert stored[0].name != "evil name.png"  # the on-disk name is random
    assert oct(stored[0].stat().st_mode & 0o777) == "0o600"  # only the server account may read it


def test_upload_rejects_things_that_are_not_images(client, make_user):
    user = make_user()
    case = make_case(client, user)
    assert upload(client, user, case["id"], data=b"just some text", name="a.png").status_code == 422
    bmp = to_bytes(make_photo(2), "BMP")
    assert upload(client, user, case["id"], data=bmp, name="a.bmp").status_code == 422


def test_upload_rejects_files_over_the_size_limit(client, make_user, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_upload_bytes", 1000)
    user = make_user()
    case = make_case(client, user)
    assert upload(client, user, case["id"]).status_code == 413


def test_upload_needs_login_and_ownership(client, make_user):
    alice, bob = make_user(), make_user()
    case = make_case(client, alice)
    assert client.post(f"/api/cases/{case['id']}/evidence", files={"file": ("a.png", PNG)}).status_code == 401
    assert upload(client, bob, case["id"]).status_code == 404
    evidence = upload(client, alice, case["id"]).json()
    assert client.get(f"/api/cases/{case['id']}/evidence", headers=bob["headers"]).status_code == 404
    assert client.delete(f"/api/cases/{case['id']}/evidence/{evidence['id']}", headers=bob["headers"]).status_code == 404


def test_list_and_delete_evidence_removes_the_file(client, make_user, private_evidence_folder):
    user = make_user()
    case = make_case(client, user)
    evidence = upload(client, user, case["id"]).json()
    assert [e["id"] for e in client.get(f"/api/cases/{case['id']}/evidence", headers=user["headers"]).json()] == [evidence["id"]]

    assert client.delete(f"/api/cases/{case['id']}/evidence/{evidence['id']}", headers=user["headers"]).status_code == 204
    assert list((private_evidence_folder / case["id"]).iterdir()) == []
    assert client.delete(f"/api/cases/{case['id']}/evidence/not-a-uuid", headers=user["headers"]).status_code == 404


def test_evidence_that_was_used_in_a_scan_cannot_be_deleted_alone(client, make_user):
    user = make_user()
    case, evidence = ready_case(client, user)
    assert start_scan(client, user, case["id"], evidence["id"]).status_code == 201
    response = client.delete(f"/api/cases/{case['id']}/evidence/{evidence['id']}", headers=user["headers"])
    assert response.status_code == 409


def test_deleting_a_case_deletes_its_files_from_disk(client, make_user, private_evidence_folder):
    user = make_user()
    case, _ = ready_case(client, user)
    assert (private_evidence_folder / case["id"]).exists()
    assert client.delete(f"/api/cases/{case['id']}", headers=user["headers"]).status_code == 204
    assert not (private_evidence_folder / case["id"]).exists()


# ===================================================== starting and watching scans

def test_a_scan_starts_queued_and_appears_in_the_list(client, make_user):
    user = make_user()
    case, evidence = ready_case(client, user)
    response = start_scan(client, user, case["id"], evidence["id"])
    assert response.status_code == 201
    scan = response.json()
    assert scan["status"] == "queued" and scan["stats"] == {} and scan["started_at"] is None

    listed = client.get(f"/api/cases/{case['id']}/scans", headers=user["headers"]).json()
    assert [s["id"] for s in listed] == [scan["id"]]
    assert client.get(f"/api/scans/{scan['id']}", headers=user["headers"]).json()["status"] == "queued"
    assert client.get(f"/api/scans/{scan['id']}/domains", headers=user["headers"]).json() == []
    assert client.get(f"/api/scans/{scan['id']}/matches", headers=user["headers"]).json() == []


def test_a_scan_needs_a_reference_file_from_the_same_case(client, make_user):
    user = make_user()
    case_one, evidence_one = ready_case(client, user)
    case_two = make_case(client, user, "second")
    assert start_scan(client, user, case_two["id"], evidence_one["id"]).status_code == 404
    assert start_scan(client, user, case_two["id"], str(uuid.uuid4())).status_code == 404


def test_only_one_scan_can_be_active_per_case(client, make_user):
    user = make_user()
    case, evidence = ready_case(client, user)
    first = start_scan(client, user, case["id"], evidence["id"]).json()
    assert start_scan(client, user, case["id"], evidence["id"]).status_code == 409

    assert client.post(f"/api/scans/{first['id']}/cancel", headers=user["headers"]).status_code == 200
    assert start_scan(client, user, case["id"], evidence["id"]).status_code == 201  # allowed again


def test_cancelling_works_once_and_only_for_active_scans(client, make_user):
    user = make_user()
    case, evidence = ready_case(client, user)
    scan = start_scan(client, user, case["id"], evidence["id"]).json()
    cancelled = client.post(f"/api/scans/{scan['id']}/cancel", headers=user["headers"])
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled" and cancelled.json()["finished_at"]
    assert client.post(f"/api/scans/{scan['id']}/cancel", headers=user["headers"]).status_code == 409


def test_other_users_cannot_touch_my_scans(client, make_user):
    alice, bob = make_user(), make_user()
    case, evidence = ready_case(client, alice)
    scan = start_scan(client, alice, case["id"], evidence["id"]).json()

    assert start_scan(client, bob, case["id"], evidence["id"]).status_code == 404
    assert client.get(f"/api/cases/{case['id']}/scans", headers=bob["headers"]).status_code == 404
    for path in ("", "/domains", "/matches"):
        assert client.get(f"/api/scans/{scan['id']}{path}", headers=bob["headers"]).status_code == 404
    assert client.post(f"/api/scans/{scan['id']}/cancel", headers=bob["headers"]).status_code == 404
    assert client.get(f"/api/scans/{scan['id']}", headers=alice["headers"]).status_code == 200


def test_scan_endpoints_require_login(client):
    fake = uuid.uuid4()
    assert client.get(f"/api/scans/{fake}").status_code == 401
    assert client.post(f"/api/scans/{fake}/cancel").status_code == 401
    assert client.post(f"/api/cases/{fake}/scans", json={"evidence_id": str(fake)}).status_code == 401