"""A RAG API that starts before PostgreSQL creates its tables once the database answers."""

import asyncio
import contextlib

from app import clients, main, memory
from app.config import settings


def test_init_schema_reports_an_unreachable_database(monkeypatch):
    @contextlib.contextmanager
    def unreachable():
        raise OSError("connection refused")
        yield

    monkeypatch.setattr(settings, "database_url", "postgresql://nowhere/db")
    monkeypatch.setattr(clients, "db", unreachable)
    assert memory.init_schema() is False


def test_init_schema_without_a_database_has_nothing_to_wait_for(monkeypatch):
    monkeypatch.setattr(settings, "database_url", "")
    assert memory.init_schema() is True


def test_the_schema_is_retried_with_backoff_until_the_database_answers(monkeypatch):
    attempts = iter([False, False, True])
    calls, sleeps = [], []

    def init_schema() -> bool:
        calls.append(1)
        return next(attempts)

    async def no_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(memory, "init_schema", init_schema)
    monkeypatch.setattr(main.asyncio, "sleep", no_sleep)
    asyncio.run(main._retry_schema())
    assert len(calls) == 3
    assert sleeps == [2.0, 4.0, 8.0]
