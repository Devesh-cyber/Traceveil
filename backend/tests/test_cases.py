import uuid


def test_register_login_and_me(client, make_user):
    user = make_user()
    response = client.get("/api/auth/me", headers=user["headers"])
    assert response.status_code == 200
    assert response.json()["email"] == user["email"]


def test_wrong_password_is_rejected(client, make_user):
    user = make_user()
    response = client.post(
        "/api/auth/login", data={"username": user["email"], "password": "wrong-password-123"}
    )
    assert response.status_code == 401


def test_unknown_email_is_rejected_the_same_way(client):
    response = client.post(
        "/api/auth/login", data={"username": "nobody@example.org", "password": "whatever-123456"}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect email or password."


def test_duplicate_email_is_rejected(client, make_user):
    user = make_user()
    response = client.post(
        "/api/auth/register", json={"email": user["email"], "password": "another-long-password"}
    )
    assert response.status_code == 409


def test_short_password_is_rejected(client):
    response = client.post(
        "/api/auth/register", json={"email": "short@example.org", "password": "short"}
    )
    assert response.status_code == 422


def test_cases_require_login(client):
    assert client.get("/api/cases").status_code == 401
    assert client.post("/api/cases", json={"title": "x"}).status_code == 401


def test_bad_token_is_rejected(client):
    response = client.get("/api/cases", headers={"Authorization": "Bearer not-a-real-token"})
    assert response.status_code == 401


def test_create_list_get_and_delete_case(client, make_user):
    user = make_user()
    created = client.post(
        "/api/cases", json={"title": "  My case  ", "description": "notes"}, headers=user["headers"]
    )
    assert created.status_code == 201
    case = created.json()
    assert case["title"] == "My case"  # spaces trimmed

    listed = client.get("/api/cases", headers=user["headers"]).json()
    assert [c["id"] for c in listed] == [case["id"]]

    assert client.get(f"/api/cases/{case['id']}", headers=user["headers"]).status_code == 200
    assert client.delete(f"/api/cases/{case['id']}", headers=user["headers"]).status_code == 204
    assert client.get(f"/api/cases/{case['id']}", headers=user["headers"]).status_code == 404


def test_empty_title_is_rejected(client, make_user):
    user = make_user()
    response = client.post("/api/cases", json={"title": "   "}, headers=user["headers"])
    assert response.status_code == 422


def test_other_users_cannot_see_or_delete_my_case(client, make_user):
    alice, bob = make_user(), make_user()
    case = client.post("/api/cases", json={"title": "private"}, headers=alice["headers"]).json()

    assert client.get(f"/api/cases/{case['id']}", headers=bob["headers"]).status_code == 404
    assert client.delete(f"/api/cases/{case['id']}", headers=bob["headers"]).status_code == 404
    assert client.get("/api/cases", headers=bob["headers"]).json() == []

    # Alice still has it
    assert client.get(f"/api/cases/{case['id']}", headers=alice["headers"]).status_code == 200


def test_unknown_case_id_is_404(client, make_user):
    user = make_user()
    response = client.get(f"/api/cases/{uuid.uuid4()}", headers=user["headers"])
    assert response.status_code == 404