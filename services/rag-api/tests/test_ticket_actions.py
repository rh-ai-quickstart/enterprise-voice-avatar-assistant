"""Ticket rules and notices without a database."""

from datetime import UTC, datetime
from types import SimpleNamespace

from app import notifications, tickets
from app.config import settings
from app.schemas import Ticket


def ticket(status: str, session_id: str | None = "s1") -> Ticket:
    now = datetime.now(UTC)
    return Ticket(
        id=1,
        ticket_ref="REQ-000001",
        title="Laptop",
        priority="normal",
        status=status,
        session_id=session_id,
        created_at=now,
        updated_at=now,
    )


def test_allowed_actions_follow_the_state_machine():
    assert tickets.allowed_actions(ticket("pending_approval")) == [
        "approve",
        "reject",
        "cancel",
        "edit",
        "message",
    ]
    assert tickets.allowed_actions(ticket("classified")) == ["approve", "cancel", "edit", "message"]
    assert tickets.allowed_actions(ticket("approved")) == ["cancel", "fulfil", "edit", "message"]
    assert tickets.allowed_actions(ticket("intake", None)) == ["cancel", "edit"]
    for done in ("rejected", "fulfilled", "cancelled"):
        assert tickets.allowed_actions(ticket(done, None)) == []


def test_notices_are_kept_per_ticket_and_kind(monkeypatch):
    now = datetime.now(UTC)
    rows = [
        {
            "id": 1,
            "session_id": "s",
            "ticket_ref": "REQ-1",
            "kind": "ticket_update",
            "text": "approved",
            "created_at": now,
        },
        {
            "id": 2,
            "session_id": "s",
            "ticket_ref": "REQ-1",
            "kind": "admin_message",
            "text": "hello",
            "created_at": now,
        },
        {
            "id": 3,
            "session_id": "s",
            "ticket_ref": "REQ-1",
            "kind": "ticket_update",
            "text": "fulfilled",
            "created_at": now,
        },
        {
            "id": 4,
            "session_id": "s",
            "ticket_ref": None,
            "kind": "ticket_update",
            "text": "other",
            "created_at": now,
        },
    ]
    superseded = []

    def run(query, params=(), fetch=False):
        if fetch:
            return rows
        superseded.extend(params[1])

    monkeypatch.setattr(notifications.memory, "run", run)
    assert [n.text for n in notifications.pending("s")] == ["hello", "fulfilled", "other"]
    assert superseded == [1]


def test_workflow_actors_are_not_named_as_approvers():
    t = ticket("approved").model_copy(update={"approver": "sla-escalation"})
    assert "by" not in notifications.ticket_text(t)
    assert "approved by Dana" in notifications.ticket_text(t.model_copy(update={"approver": "Dana"}))


def test_decision_is_posted_to_wf4_with_the_token(monkeypatch):
    monkeypatch.setattr(settings, "internal_api_token", "tok")
    posted = []

    class Client:
        def __init__(self, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json, headers):
            posted.append((url, json, headers))
            return SimpleNamespace(status_code=200)

    monkeypatch.setattr(tickets.httpx, "Client", Client)
    assert tickets.notify_decision(ticket("approved"), "approved", "Dana", "ok") is True
    url, body, headers = posted[0]
    assert url == "http://n8n:5678/webhook/ticket-decided"
    assert headers == {"Authorization": "Bearer tok"}
    assert body["via"] == "portal" and body["decision"] == "approved" and body["actor"] == "Dana"
    assert body["ticket"]["ticket_ref"] == "REQ-000001"


def test_unreachable_workflow_is_reported_not_raised(monkeypatch):
    import httpx

    class Client:
        def __init__(self, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **k):
            raise httpx.ConnectError("n8n is down")

    monkeypatch.setattr(tickets.httpx, "Client", Client)
    assert tickets.notify_decision(ticket("approved"), "approved", "Dana", None) is False
