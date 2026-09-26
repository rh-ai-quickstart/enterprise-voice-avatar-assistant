import os
import uuid

import pytest
from fastapi.testclient import TestClient

from app import auth, memory
from app.config import settings
from app.main import app

ADMIN_PASSWORD = "correct horse battery staple"
ADMIN_HEADERS = {"X-Admin-Request": "1"}


@pytest.fixture
def admin_settings(monkeypatch):
    """The admin portal configured, with a fresh sign-in rate limiter."""
    monkeypatch.setattr(settings, "admin_enabled", True)
    monkeypatch.setattr(settings, "admin_password", ADMIN_PASSWORD)
    monkeypatch.setattr(settings, "admin_session_secret", "test-session-secret")
    monkeypatch.setattr(auth, "login_limiter", auth.LoginLimiter())
    return settings


@pytest.fixture
def admin_client(admin_settings):
    """A client signed in to the admin portal as Dana. HTTPS, because the cookie is Secure."""
    with TestClient(app, base_url="https://testserver") as client:
        response = client.post(
            "/v1/admin/login", json={"password": ADMIN_PASSWORD, "name": "Dana"}, headers=ADMIN_HEADERS
        )
        assert response.status_code == 200, response.text
        yield client


@pytest.fixture
def database(monkeypatch):
    """A PostgreSQL schema of its own with the RAG API's tables, when TEST_DATABASE_URL is set
    (CI runs a PostgreSQL service; locally: TEST_DATABASE_URL=postgresql://user@host:5432/db)."""
    base = os.environ.get("TEST_DATABASE_URL")
    if not base:
        pytest.skip("TEST_DATABASE_URL is not set")
    import psycopg

    schema = f"test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute(f"CREATE SCHEMA {schema}")
    separator = "&" if "?" in base else "?"
    monkeypatch.setattr(settings, "database_url", f"{base}{separator}options=-csearch_path%3D{schema}")
    memory.init_schema()
    try:
        yield settings.database_url
    finally:
        with psycopg.connect(base, autocommit=True) as conn:
            conn.execute(f"DROP SCHEMA {schema} CASCADE")
