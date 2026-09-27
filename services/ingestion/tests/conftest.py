import pytest

from app.config import settings

INTERNAL_TOKEN = "test-internal-token"
INTERNAL_HEADERS = {"Authorization": f"Bearer {INTERNAL_TOKEN}"}


@pytest.fixture(autouse=True)
def internal_token(monkeypatch):
    """The token every deployment has (the write routes are closed without one)."""
    monkeypatch.setattr(settings, "internal_api_token", INTERNAL_TOKEN)
    return INTERNAL_TOKEN
