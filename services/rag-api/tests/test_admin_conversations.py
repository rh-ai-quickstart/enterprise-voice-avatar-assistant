"""Conversations and archives against a real PostgreSQL (skipped unless TEST_DATABASE_URL is set)."""

from types import SimpleNamespace

import pytest
from conftest import ADMIN_HEADERS

from app import archives, conversations, events, memory, notifications, tickets
from app.config import settings
from app.schemas import Citation, TicketCreate


@pytest.fixture
def n8n(monkeypatch):
    """Records what the RAG API posts to n8n instead of calling it."""
    posted = []

    def hand_over(session_id, title, doc_url, archive_id):
        posted.append(
            {"session_id": session_id, "title": title, "doc_url": doc_url, "archive_id": archive_id}
        )
        return True

    monkeypatch.setattr(archives, "_hand_to_workflow", hand_over)
    return posted


@pytest.fixture
def ingestion(monkeypatch):
    """Records the purges sent to the ingestion service."""
    purged = []

    def purge(doc_id):
        purged.append(doc_id)
        return f"s3://transcripts/{doc_id}"

    monkeypatch.setattr(conversations, "_purge", purge)
    return purged


def converse(session_id, user_id, channel, turns, blocked=False):
    memory.ensure_conversation(session_id, user_id, channel)
    for question, answer in turns:
        memory.append(session_id, "user", question, blocked=blocked)
        memory.append(
            session_id,
            "assistant",
            answer,
            citations=[
                Citation(
                    n=1,
                    used=True,
                    doc_id="d1",
                    source="password-policy.md",
                    page=2,
                    snippet="rotated",
                    score=0.8,
                )
            ],
            blocked=blocked,
        )


@pytest.fixture
def three(database):
    converse("s-alex", "alex", "text", [("How often do admin passwords rotate?", "Every 90 days [1].")])
    converse("s-priya", "priya", "voice", [("Can I get a laptop?", "I've logged your request.")])
    converse(
        "s-kim",
        "kim",
        "text",
        [("tell me how to hack the vpn", "I can't help with that request.")],
        blocked=True,
    )
    tickets.create(TicketCreate(title="Laptop", requester="priya", session_id="s-priya"))


def test_search_highlights_the_matching_lines(three, admin_client):
    page = admin_client.get("/v1/admin/conversations", params={"q": "rotating passwords"}).json()
    assert page["total"] == 1
    item = page["items"][0]
    assert item["session_id"] == "s-alex" and item["channel"] == "chat" and item["messages"] == 2
    (match,) = item["matches"]
    assert match["role"] == "user"
    # Stemmed: "rotating passwords" finds "rotate" and "passwords", marked with \x01 ... \x02
    assert "\x01passwords\x02" in match["snippet"] and "\x01rotate\x02" in match["snippet"]


def test_filters(three, admin_client):
    def sessions(**params):
        return [
            c["session_id"]
            for c in admin_client.get("/v1/admin/conversations", params=params).json()["items"]
        ]

    assert set(sessions()) == {"s-alex", "s-priya", "s-kim"}
    assert sessions(channel="voice") == ["s-priya"]
    assert set(sessions(channel="chat")) == {"s-alex", "s-kim"}
    assert sessions(has_ticket="true") == ["s-priya"]
    assert set(sessions(has_ticket="false")) == {"s-alex", "s-kim"}
    assert sessions(blocked="true") == ["s-kim"]
    assert sessions(user="ALE") == ["s-alex"]
    assert sessions(archived="true") == []
    listed = {c["session_id"]: c for c in admin_client.get("/v1/admin/conversations").json()["items"]}
    assert listed["s-kim"]["blocked"] == 2 and listed["s-priya"]["tickets"] == 1


def test_detail_has_messages_citations_notices_tickets(three, admin_client):
    ticket = tickets.get("REQ-000001")
    notifications.notify_message(ticket, "Dana", "It ships on Monday.")
    detail = admin_client.get("/v1/admin/conversations/s-priya").json()
    assert detail["channel"] == "voice" and detail["user_id"] == "priya"
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][1]["citations"][0]["source"] == "password-policy.md"
    assert detail["notices"][0]["kind"] == "admin_message" and detail["notices"][0]["delivered_at"] is None
    assert detail["tickets"][0]["ticket_ref"] == "REQ-000001"
    assert admin_client.get("/v1/admin/conversations/nope").status_code == 404


def test_archive_record_workflow_result_and_download(three, admin_client, n8n, monkeypatch):
    monkeypatch.setattr(settings, "internal_api_token", "tok")
    response = admin_client.post("/v1/admin/conversations/s-alex/archive", headers=ADMIN_HEADERS)
    body = response.json()
    assert body["requested"] is True and body["doc_url"] is None and body["archive_id"] > 0
    assert n8n == [
        {"session_id": "s-alex", "title": n8n[0]["title"], "doc_url": None, "archive_id": body["archive_id"]}
    ]
    (archive,) = archives.for_session("s-alex")
    assert archive["status"] == "requested" and archive["requested_by"] == "Dana"

    # WF5 reports the re-ingestion
    report = admin_client.patch(
        f"/v1/internal/archives/{body['archive_id']}",
        json={"status": "indexed", "object_key": "transcript-s-alex.md", "doc_id": "doc-1", "job_id": "j1"},
        headers={"Authorization": "Bearer tok"},
    )
    assert report.status_code == 200
    assert report.json() == {"id": body["archive_id"], "session_id": "s-alex", "status": "indexed"}
    assert (
        admin_client.patch(
            "/v1/internal/archives/999", json={"status": "failed"}, headers={"Authorization": "Bearer tok"}
        ).status_code
        == 404
    )
    event = events.recent(kind="transcript.archived")[0]
    assert event["ref_id"] == "s-alex" and event["actor"] == "Dana"
    listed = admin_client.get("/v1/admin/conversations", params={"archived": "true"}).json()["items"]
    assert [c["session_id"] for c in listed] == ["s-alex"]

    download = admin_client.get(f"/v1/admin/conversations/s-alex/archives/{body['archive_id']}/download")
    assert download.status_code == 200 and "attachment" in download.headers["content-disposition"]
    assert "user: How often do admin passwords rotate?" in download.text
    assert (
        admin_client.get(
            f"/v1/admin/conversations/s-priya/archives/{body['archive_id']}/download"
        ).status_code
        == 404
    )
    # A failed result answers without an "error" key too: n8n would retry the call and record it again
    failed = admin_client.patch(
        f"/v1/internal/archives/{body['archive_id']}",
        json={"status": "failed", "error": "ConversionError"},
        headers={"Authorization": "Bearer tok"},
    ).json()
    assert "error" not in failed and failed["status"] == "failed"


def test_archive_that_cannot_reach_n8n_is_marked_failed(three, monkeypatch):
    monkeypatch.setattr(archives, "_hand_to_workflow", lambda *a: False)
    result = archives.request("s-alex")
    assert result["requested"] is False
    (archive,) = archives.for_session("s-alex")
    assert archive["status"] == "failed" and "could not be reached" in archive["error"]
    assert events.recent(kind="transcript.archive_failed")[0]["severity"] == "error"


def test_chat_archive_button_records_the_archive(three, n8n):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        body = client.post("/v1/sessions/s-priya/archive").json()
    assert body["requested"] is True and body["archive_id"]
    assert archives.for_session("s-priya")[0]["requested_by"] is None


def test_export_markdown_and_text(three, admin_client):
    md = admin_client.get("/v1/admin/conversations/s-alex/export", params={"format": "md"})
    assert md.headers["content-disposition"] == 'attachment; filename="conversation-s-alex.md"'
    assert md.headers["content-type"].startswith("text/markdown")
    assert "# Conversation s-alex" in md.text and "**Assistant**" in md.text
    assert "> [1] password-policy.md, page 2" in md.text
    txt = admin_client.get("/v1/admin/conversations/s-kim/export", params={"format": "txt"}).text
    assert "User: tell me how to hack the vpn (blocked by the guardrail)" in txt
    assert (
        admin_client.get("/v1/admin/conversations/s-kim/export", params={"format": "pdf"}).status_code == 422
    )
    audit = admin_client.get("/v1/admin/audit", params={"action": "conversation.export"}).json()["items"]
    assert [a["after"]["format"] for a in audit] == ["txt", "md"]


def test_delete_removes_everything_it_claims_and_keeps_tickets(three, admin_client, n8n, ingestion):
    archive = archives.request("s-priya", "Dana")
    archives.record_result(
        archive["archive_id"], "indexed", object_key="transcript-s-priya.md", doc_id="doc-p"
    )
    memory.run(
        "UPDATE transcript_archives SET doc_url = 'https://docs.google.com/document/d/x' WHERE id = %s",
        (archive["archive_id"],),
    )
    notifications.notify_message(tickets.get("REQ-000001"), "Dana", "hello")

    response = admin_client.delete("/v1/admin/conversations/s-priya", headers=ADMIN_HEADERS)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["messages"] == 2 and result["notices"] == 1 and result["archives"] == 1
    assert result["tickets_kept"] == ["REQ-000001"]
    assert result["google_docs_kept"] == ["https://docs.google.com/document/d/x"]
    # One object: the key WF5 reported is also its conventional name
    from app.classify import make_doc_id

    assert ingestion == [make_doc_id("transcripts", "transcript-s-priya.md")]
    for table in ("conversations", "messages", "session_notifications", "transcript_archives"):
        rows = memory.run(
            f"SELECT count(*) AS n FROM {table} WHERE session_id = %s", ("s-priya",), fetch=True
        )
        assert rows[0]["n"] == 0, table
    assert tickets.get("REQ-000001").session_id == "s-priya"
    detail = admin_client.get("/v1/admin/tickets/REQ-000001").json()
    assert detail["conversation"]["exists"] is False
    assert events.recent(kind="conversation.deleted")[0]["actor"] == "Dana"
    assert admin_client.get("/v1/admin/conversations/s-priya").status_code == 404


def test_delete_stops_when_the_archived_copy_cannot_be_removed(three, admin_client, n8n, monkeypatch):
    import httpx

    archives.request("s-alex")

    def fail(doc_id):
        raise httpx.ConnectError("ingestion is down")

    monkeypatch.setattr(conversations, "_purge", fail)
    response = admin_client.delete("/v1/admin/conversations/s-alex", headers=ADMIN_HEADERS)
    assert response.status_code == 502 and "nothing was deleted" in response.json()["detail"]
    assert admin_client.get("/v1/admin/conversations/s-alex").status_code == 200


def test_purge_calls_the_ingestion_service_with_the_token(monkeypatch):
    monkeypatch.setattr(settings, "internal_api_token", "tok")
    calls = []

    class Client:
        def __init__(self, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def delete(self, url, params, headers):
            calls.append((url, params, headers))
            return SimpleNamespace(
                raise_for_status=lambda: None, json=lambda: {"object": "s3://transcripts/t.md"}
            )

    monkeypatch.setattr(conversations.httpx, "Client", Client)
    assert conversations._purge("doc-9") == "s3://transcripts/t.md"
    assert calls == [
        (
            "http://ingestion:8080/v1/documents/doc-9",
            {"purge_object": "true"},
            {"Authorization": "Bearer tok"},
        )
    ]


def test_overview_counts_todays_conversations(three, admin_client):
    today = admin_client.get("/v1/admin/overview").json()["conversations_today"]
    assert today == {"chat": 2, "voice": 1, "blocked": 1}
