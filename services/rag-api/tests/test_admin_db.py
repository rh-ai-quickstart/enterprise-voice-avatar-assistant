"""Admin foundations against a real PostgreSQL (skipped unless TEST_DATABASE_URL is set)."""

import asyncio
import time

import pytest
from conftest import ADMIN_HEADERS, ADMIN_PASSWORD
from fastapi.testclient import TestClient

from app import auth, clients, events, memory, stream
from app.config import settings
from app.main import app


def test_schema_files_are_idempotent(database):
    memory.init_schema()
    memory.init_schema()
    with clients.db() as conn:
        tables = {
            r["table_name"]
            for r in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()"
            ).fetchall()
        }
        assert {
            "activity_events",
            "admin_audit",
            "transcript_archives",
            "knowledge_gaps",
            "messages",
        } <= tables
        triggers = conn.execute(
            "SELECT count(*) AS n FROM information_schema.triggers WHERE event_object_table = 'activity_events' "
            "AND trigger_schema = current_schema()"
        ).fetchone()
        assert triggers["n"] == 1


def test_full_text_search_column(database):
    memory.ensure_conversation("s1", "u1", "text")
    memory.append("s1", "user", "My laptop screen is flickering")
    memory.append("s1", "assistant", "Please contact the service desk")
    rows = memory.run(
        "SELECT content FROM messages WHERE tsv @@ plainto_tsquery('english', %s)", ("flicker",), fetch=True
    )
    assert [r["content"] for r in rows] == ["My laptop screen is flickering"]


def test_events_record_list_and_page(database):
    first = events.record("document.ingested", "policy.pdf indexed", ref_type="document", ref_id="d1")
    second = events.record(
        "integration.error", "Slack refused the card", severity="error", source="n8n", data={"error": "x"}
    )
    third = events.record("ticket.approved", "REQ-000001 approved", severity="success", actor="Dana")
    assert first < second < third
    assert events.latest_id() == third
    assert [e["id"] for e in events.recent()] == [third, second, first]
    assert [e["id"] for e in events.recent(severity="error")] == [second]
    assert events.recent(kind="integration.error")[0]["data"] == {"error": "x"}
    assert [e["id"] for e in events.recent(before_id=third, limit=1)] == [second]
    assert [e["id"] for e in events.after(first)] == [second, third]
    assert set(events.after(first)[0]) == {"id", "kind", "ref_type", "ref_id"}


def test_internal_events_reach_the_activity_page(database, admin_client, monkeypatch):
    monkeypatch.setattr(settings, "internal_api_token", "tok")
    response = admin_client.post(
        "/v1/internal/events",
        json={
            "kind": "ticket.sla_reminder",
            "title": "REQ-000002 waits",
            "severity": "warning",
            "ref_type": "ticket",
        },
        headers={"Authorization": "Bearer tok"},
    )
    assert response.status_code == 201 and response.json()["id"] > 0
    page = admin_client.get("/v1/admin/activity", params={"kind": "ticket.sla_reminder"}).json()
    assert page["items"][0]["title"] == "REQ-000002 waits" and page["items"][0]["source"] == "n8n"
    assert admin_client.get("/v1/admin/activity", params={"limit": 1}).json()["next_before_id"] is not None


def test_sign_in_attempts_are_audited(database, admin_settings):
    with TestClient(app, base_url="https://testserver") as client:
        client.post("/v1/admin/login", json={"password": "guess", "name": "Mallory"}, headers=ADMIN_HEADERS)
        client.post(
            "/v1/admin/login",
            json={"password": ADMIN_PASSWORD, "name": "Dana"},
            headers={**ADMIN_HEADERS, "X-Client-Address": "198.51.100.7", "User-Agent": "pytest"},
        )
        client.post("/v1/admin/logout", headers=ADMIN_HEADERS)
        items = (
            client.post(
                "/v1/admin/login", json={"password": ADMIN_PASSWORD, "name": "Dana"}, headers=ADMIN_HEADERS
            )
            and client.get("/v1/admin/audit").json()["items"]
        )
    assert [(e["actor"], e["action"]) for e in items] == [
        ("Dana", "login"),
        ("Dana", "logout"),
        ("Dana", "login"),
        ("Mallory", "login_failed"),
    ]
    assert items[3]["after"] == {"reason": "wrong password"}
    assert items[2]["client_ip"] == "198.51.100.7" and items[2]["user_agent"] == "pytest"


def test_notify_trigger_reaches_the_hub(database):
    async def scenario():
        hub = stream.Hub()
        hub.start(asyncio.get_running_loop())
        queue = hub.subscribe()
        try:
            await asyncio.sleep(0.5)  # the listener connects
            event_id = await asyncio.to_thread(
                events.record, "ticket.approved", "REQ-1 approved", ref_type="ticket", ref_id="REQ-000001"
            )
            event = await asyncio.wait_for(queue.get(), timeout=5)
        finally:
            await asyncio.to_thread(hub.stop)
        return event_id, event

    event_id, event = asyncio.run(scenario())
    assert event == {"id": event_id, "kind": "ticket.approved", "ref_type": "ticket", "ref_id": "REQ-000001"}


def test_stream_replays_after_last_event_id_and_ends_with_the_session(database, admin_settings, monkeypatch):
    first = events.record("document.ingested", "a.pdf indexed")
    second = events.record("document.ingested", "b.pdf indexed", ref_type="document", ref_id="d2")
    monkeypatch.setattr(stream, "KEEPALIVE_SECONDS", 0.2)
    # A session with one second left, so the stream (and the test client) finishes
    cookie, _ = auth.sign_session("Dana", now=time.time() - settings.admin_session_hours * 3600 + 1)
    with TestClient(app, base_url="https://testserver") as client:
        client.cookies.set(auth.COOKIE, cookie)
        response = client.get("/v1/admin/stream", headers={"Last-Event-ID": str(first)})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-accel-buffering"] == "no"
    body = response.text
    assert body.startswith("retry: 5000\n\n")
    assert f"id: {second}\nevent: activity\n" in body and f"id: {first}\n" not in body
    assert '"ref_id": "d2"' in body and ": keepalive" in body


@pytest.mark.parametrize("last_id", [None])
def test_stream_without_last_event_id_starts_at_the_newest_event(database, last_id):
    newest = events.record("document.ingested", "c.pdf indexed")

    async def first_chunk():
        generator = stream.stream(last_id, time.time() + 60)
        chunk = await generator.__anext__()
        await generator.aclose()
        return chunk

    assert asyncio.run(first_chunk()) == f"retry: 5000\nid: {newest}\n\n"
