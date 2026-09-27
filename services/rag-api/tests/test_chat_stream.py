import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app import guardrails, intent, rag, retrieval
from app.guardrails import Verdict
from app.main import app
from app.retrieval import Hit

PIECES = ["Administrator passwords ", "are rotated every 90 days", " [1]."]


def _question(monkeypatch):
    monkeypatch.setattr(
        retrieval,
        "search",
        lambda q, top_k=None, min_score=None: [
            Hit(doc_id="d1", source="password-policy.md", text="Rotated every 90 days.", score=0.8)
        ],
    )
    monkeypatch.setattr(guardrails, "check_input", lambda text: Verdict(True, "none"))
    monkeypatch.setattr(guardrails, "check_output", lambda user, answer: Verdict(True, "none"))
    monkeypatch.setattr(intent, "detect", lambda text, previous=None: "question")

    class FakeLLM:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    assert kwargs.get("stream") is True and kwargs["max_tokens"] == 120
                    return iter(
                        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=p))])
                        for p in PIECES
                    )

    monkeypatch.setattr(rag.clients, "llm", lambda: FakeLLM())


def test_stream_yields_deltas_then_the_final_reply(monkeypatch):
    _question(monkeypatch)
    with (
        TestClient(app) as client,
        client.stream("POST", "/v1/chat/stream", json={"message": "How often?", "mode": "voice"}) as response,
    ):
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/x-ndjson")
        events = [json.loads(line) for line in response.iter_lines() if line]
    deltas = [e["text"] for e in events if e["type"] == "delta"]
    assert "".join(deltas) == "".join(PIECES)
    final = events[-1]
    assert final["type"] == "final" and final["answer"] == "".join(PIECES).strip()
    assert final["citations"][0]["used"] is True and final["blocked"] is False


def test_stream_of_a_blocked_message_is_only_the_final(monkeypatch):
    monkeypatch.setattr(guardrails, "check_input", lambda text: Verdict(False, "harm"))
    with (
        TestClient(app) as client,
        client.stream("POST", "/v1/chat/stream", json={"message": "bad"}) as response,
    ):
        events = [json.loads(line) for line in response.iter_lines() if line]
    assert len(events) == 1 and events[0]["type"] == "final" and events[0]["blocked"] is True


def test_stream_of_a_request_carries_the_ticket(monkeypatch):
    """The final line carries the ticket with its timestamps; it must serialise (the voice agent
    saw the connection drop mid-stream when it did not)."""
    from datetime import UTC, datetime

    from app import memory, tickets
    from app.schemas import Ticket

    monkeypatch.setattr(guardrails, "check_input", lambda text: Verdict(True, "none"))
    monkeypatch.setattr(retrieval, "search", lambda *a, **k: [])
    monkeypatch.setattr(intent, "detect", lambda text, previous=None: "request")
    monkeypatch.setattr(memory, "ensure_conversation", lambda *a, **k: None)
    monkeypatch.setattr(memory, "history", lambda *a, **k: [])
    monkeypatch.setattr(memory, "append", lambda *a, **k: None)
    now = datetime.now(UTC)
    ticket = Ticket(
        id=2,
        ticket_ref="REQ-000002",
        title="Repair the laptop",
        description="my laptop is broken",
        category="hardware",
        priority="normal",
        status="pending_approval",
        requester="Joe",
        session_id="s1",
        payload={},
        needs_approval=True,
        approver=None,
        decision_note=None,
        events=[],
        created_at=now,
        updated_at=now,
    )
    monkeypatch.setattr(tickets, "intake", lambda request: (ticket, {"summary": "laptop"}, True))
    with (
        TestClient(app) as client,
        client.stream(
            "POST", "/v1/chat/stream", json={"message": "my laptop is broken", "mode": "voice"}
        ) as response,
    ):
        assert response.status_code == 200
        events = [json.loads(line) for line in response.iter_lines() if line]
    assert len(events) == 1 and events[0]["type"] == "final"
    assert events[0]["ticket"]["ticket_ref"] == "REQ-000002"
    assert "REQ-000002" in events[0]["answer"]
