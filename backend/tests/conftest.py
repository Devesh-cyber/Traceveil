import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.db.session import SessionLocal
from app.main import app
from app.models import User


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def make_user(client):
    """Creates real accounts through the API and deletes them (and their cases) afterwards."""
    created: list[str] = []

    def _make() -> dict:
        email = f"test-{uuid.uuid4().hex[:10]}@example.org"
        password = "correct-horse-battery"
        reg = client.post("/api/auth/register", json={"email": email, "password": password})
        assert reg.status_code == 201, reg.text
        login = client.post("/api/auth/login", data={"username": email, "password": password})
        assert login.status_code == 200, login.text
        created.append(email)
        token = login.json()["access_token"]
        return {"email": email, "password": password, "headers": {"Authorization": f"Bearer {token}"}}

    yield _make

    with SessionLocal() as db:
        db.execute(delete(User).where(User.email.in_(created)))
        db.commit()