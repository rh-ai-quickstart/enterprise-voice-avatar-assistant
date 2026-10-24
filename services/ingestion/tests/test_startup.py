"""An ingestion service that starts before PostgreSQL creates its tables once the database answers."""

import asyncio

from app import db, main
from app.config import settings


def test_init_schema_reports_an_unreachable_database(monkeypatch):
    def unreachable():
        raise OSError("connection refused")

    monkeypatch.setattr(settings, "database_url", "postgresql://nowhere/db")
    monkeypatch.setattr(db, "_connect", unreachable)
    assert db.init_schema() is False


def test_the_schema_is_retried_with_backoff_until_the_database_answers(monkeypatch):
    attempts = iter([False, True])
    sleeps = []

    async def no_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(db, "init_schema", lambda: next(attempts))
    monkeypatch.setattr(main.asyncio, "sleep", no_sleep)
    asyncio.run(main._retry_schema())
    assert sleeps == [2.0, 4.0]
