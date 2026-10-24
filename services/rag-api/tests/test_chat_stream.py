import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app import guardrails, intent, rag, retrieval
from app.guardrails import Verdict
from app.main import app
from app.retrieval import Hit

PIECES = ["Administrator passwords ", "are rotated every 90 days", " [1]."]


def _question(monkeypatch, pieces=PIECES):
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
                        for p in pieces
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


def test_stream_stops_at_a_claimed_action_before_it_is_spoken(monkeypatch):
    pieces = [
        "Laptops are replaced every ",
        "36 months [1]. Joe, I've log",
        "ged your request REQ-000003: Monitor. ",
        "It needs approval.",
    ]
    _question(monkeypatch, pieces)
    with (
        TestClient(app) as client,
        client.stream(
            "POST", "/v1/chat/stream", json={"message": "Order me a monitor", "mode": "voice"}
        ) as response,
    ):
        events = [json.loads(line) for line in response.iter_lines() if line]
    deltas = [e["text"] for e in events if e["type"] == "delta"]
    assert deltas == ["Laptops are replaced every 36 months [1]. ", rag.NO_CLAIM]
    assert events[-1]["answer"] == "Laptops are replaced every 36 months [1]. " + rag.NO_CLAIM


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


class _RecordingStream:
    """A streamed completion that records whether it was read or closed unread."""

    def __init__(self, pieces):
        self.pieces, self.read, self.closed = pieces, False, False

    def __iter__(self):
        self.read = True
        return iter(
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=p))]) for p in self.pieces
        )

    def close(self):
        self.closed = True


def _slow_intent(monkeypatch, kind: str):
    """An intent check still running when retrieval is done, so the answer starts generating first."""
    import threading

    release = threading.Event()

    def detect(text, previous=None):
        release.wait(5)
        return kind

    monkeypatch.setattr(intent, "detect", detect)
    return release


def _recording_llm(monkeypatch, release, opened):
    class FakeLLM:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    assert kwargs.get("stream") is True
                    opened.append(_RecordingStream(PIECES))
                    release.set()  # the intent check finishes once the answer has started
                    return opened[-1]

    monkeypatch.setattr(rag.clients, "llm", lambda: FakeLLM())


def test_a_question_starts_generating_before_the_intent_check_returns(monkeypatch):
    _question(monkeypatch)
    release, opened = _slow_intent(monkeypatch, "question"), []
    _recording_llm(monkeypatch, release, opened)
    events = list(rag.answer_stream(rag.ChatRequest(message="How often?", mode="voice")))
    assert len(opened) == 1 and opened[0].read  # the early stream is the one spoken, not a second one
    assert "".join(p for kind, p in events if kind == "delta") == "".join(PIECES)


def test_a_request_drops_the_early_answer_unread(monkeypatch):
    from app import tickets

    _question(monkeypatch)
    release, opened = _slow_intent(monkeypatch, "request"), []
    _recording_llm(monkeypatch, release, opened)
    filed = rag.ChatResponse(
        session_id="s1",
        answer="I've logged your request REQ-000003.",
        citations=[],
        guardrail=rag.GuardrailInfo(provider="none"),
        model="test",
    )
    monkeypatch.setattr(rag, "file_request", lambda request, session_id, info: filed)
    monkeypatch.setattr(tickets, "intake", lambda request: (_ for _ in ()).throw(AssertionError("not used")))
    events = list(rag.answer_stream(rag.ChatRequest(message="I need a new laptop", mode="voice")))
    assert events == [("final", filed)]
    assert len(opened) == 1 and opened[0].closed and not opened[0].read


def test_an_early_start_that_fails_is_opened_again_for_a_question(monkeypatch):
    _question(monkeypatch)
    release, calls = _slow_intent(monkeypatch, "question"), []

    class FlakyLLM:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    calls.append(1)
                    release.set()
                    if len(calls) == 1:
                        raise ConnectionError("gateway not reachable yet")
                    return _RecordingStream(PIECES)

    monkeypatch.setattr(rag.clients, "llm", lambda: FlakyLLM())
    events = list(rag.answer_stream(rag.ChatRequest(message="How often?", mode="voice")))
    assert len(calls) == 2 and events[-1][0] == "final"
