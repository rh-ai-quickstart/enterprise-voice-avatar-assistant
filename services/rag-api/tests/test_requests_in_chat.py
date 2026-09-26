from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app import guardrails, intent, memory, notifications, rag, retrieval, tickets
from app.guardrails import Verdict
from app.main import app
from app.schemas import RequestIntake, Ticket


class FakeCompletion:
    def __init__(self, text: str) -> None:
        self.choices = [type("C", (), {"message": type("M", (), {"content": text})()})()]


def fake_llm(text: str):
    class FakeLLM:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    return FakeCompletion(text)

    return lambda: FakeLLM()


def make_ticket(status="pending_approval", **overrides) -> Ticket:
    data = {
        "id": 7,
        "ticket_ref": "REQ-000007",
        "title": "Replace non-functional laptop",
        "description": "x",
        "category": "hardware",
        "priority": "normal",
        "status": status,
        "requester": "mohamed",
        "session_id": "s1",
        "payload": {},
        "approver": None,
        "decision_note": None,
        "events": [],
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    data.update(overrides)
    return Ticket(**data)


def test_intent_detection_words(monkeypatch):
    monkeypatch.setattr(intent.clients, "llm", fake_llm("REQUEST"))
    assert intent.detect("I need a new laptop, mine no longer boots") == "request"
    monkeypatch.setattr(intent.clients, "llm", fake_llm("question"))
    assert intent.detect("How often must passwords be rotated?") == "question"

    def broken():
        raise RuntimeError("llm down")

    monkeypatch.setattr(intent.clients, "llm", broken)
    assert intent.detect("anything") == "question"


def test_intent_detection_sees_the_previous_turn(monkeypatch):
    seen = {}

    def llm():
        class Completions:
            def create(self, **kwargs):
                seen["content"] = kwargs["messages"][1]["content"]
                return fake_llm("QUESTION")().chat.completions.create(**kwargs)

        class Chat:
            completions = Completions()

        class Client:
            chat = Chat()

        return Client()

    monkeypatch.setattr(intent.clients, "llm", llm)
    assert intent.detect("place the request", "I've logged your request REQ-000002: Repair the laptop.") == "question"
    assert "REQ-000002" in seen["content"] and "place the request" in seen["content"]


def test_intake_requires_approval_by_default(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(
        tickets,
        "classify_request",
        lambda text: {"title": "Repair laptop", "category": "hardware", "priority": "normal", "summary": "", "needs_approval": False, "details": {}},
    )
    created = {}

    def fake_create(data, actor=None):
        created["needs_approval"] = data.needs_approval
        created["payload"] = data.payload
        return make_ticket(status="intake")

    def fake_update(ticket_id, data):
        created.setdefault("statuses", []).append(data.status)
        return make_ticket(status=data.status)

    monkeypatch.setattr(tickets, "create", fake_create)
    monkeypatch.setattr(tickets, "update", fake_update)
    monkeypatch.setattr(tickets, "notify_n8n", lambda *a, **k: True)
    monkeypatch.setattr(settings, "requests_require_approval", True)
    ticket, _classification, _ = tickets.intake(RequestIntake(text="my laptop is broken", session_id="s1", user_id="u1", channel="voice"))
    assert created["needs_approval"] is True
    assert created["payload"]["model_needs_approval"] is False
    assert "pending_approval" in created["statuses"]
    assert ticket.status == "pending_approval"

    created.clear()
    monkeypatch.setattr(settings, "requests_require_approval", False)
    tickets.intake(RequestIntake(text="my laptop is broken", session_id="s1", user_id="u1", channel="voice"))
    assert created["needs_approval"] is False
    assert "pending_approval" not in created["statuses"]


def test_chat_files_a_request(monkeypatch):
    monkeypatch.setattr(guardrails, "check_input", lambda text: Verdict(True, "none"))
    monkeypatch.setattr(retrieval, "search", lambda *a, **k: [])
    monkeypatch.setattr(intent, "detect", lambda message, previous=None: "request")
    filed = []

    def fake_intake(request):
        filed.append(request)
        return make_ticket(session_id=request.session_id), {"summary": "laptop"}, True

    monkeypatch.setattr(tickets, "intake", fake_intake)
    with TestClient(app) as client:
        body = client.post(
            "/v1/chat",
            json={
                "message": "I need a new laptop",
                "session_id": "s1",
                "user_id": "mohamed",
                "mode": "voice",
                "user_name": "Joe Bloggs",
            },
        ).json()
    assert body["ticket"]["ticket_ref"] == "REQ-000007"
    assert "REQ-000007" in body["answer"] and "needs approval" in body["answer"]
    assert body["citations"] == []
    assert filed[0].channel == "voice" and filed[0].session_id == "s1"
    assert filed[0].requester == "Joe Bloggs"


def test_chat_without_ticket_backend(monkeypatch):
    monkeypatch.setattr(guardrails, "check_input", lambda text: Verdict(True, "none"))
    monkeypatch.setattr(retrieval, "search", lambda *a, **k: [])
    monkeypatch.setattr(intent, "detect", lambda message, previous=None: "request")

    def no_db(request):
        raise tickets.TicketError(503, "no database")

    monkeypatch.setattr(tickets, "intake", no_db)
    with TestClient(app) as client:
        body = client.post("/v1/chat", json={"message": "I need a new laptop"}).json()
    assert body["ticket"] is None and "can't file tickets" in body["answer"]


def test_text_mode_maps_to_chat_channel(monkeypatch):
    monkeypatch.setattr(guardrails, "check_input", lambda text: Verdict(True, "none"))
    monkeypatch.setattr(retrieval, "search", lambda *a, **k: [])
    monkeypatch.setattr(intent, "detect", lambda message, previous=None: "request")
    seen = []

    def fake_intake(request):
        seen.append(request.channel)
        return make_ticket(), {}, True

    monkeypatch.setattr(tickets, "intake", fake_intake)
    with TestClient(app) as client:
        client.post("/v1/chat", json={"message": "I need a new laptop", "mode": "text"})
    assert seen == ["chat"]


def test_ticket_notice_text():
    fulfilled = make_ticket("fulfilled", approver="mohamed.adel.heiba")
    assert "approved by mohamed.adel.heiba and has been fulfilled" in notifications.ticket_text(fulfilled)
    rejected = make_ticket("rejected", approver="n8n", decision_note="decided in Slack")
    assert (
        notifications.ticket_text(rejected)
        == "Your request REQ-000007, Replace non-functional laptop, was rejected."
    )
    assert notifications.ticket_text(make_ticket("classified")) is None


def test_pending_keeps_newest_per_ticket(monkeypatch):
    now = datetime.now(UTC)
    rows = [
        {
            "id": 1,
            "session_id": "s1",
            "ticket_ref": "REQ-000007",
            "kind": "ticket_update",
            "text": "approved",
            "created_at": now,
        },
        {
            "id": 2,
            "session_id": "s1",
            "ticket_ref": "REQ-000007",
            "kind": "ticket_update",
            "text": "fulfilled",
            "created_at": now,
        },
        {
            "id": 3,
            "session_id": "s1",
            "ticket_ref": "REQ-000008",
            "kind": "ticket_update",
            "text": "rejected",
            "created_at": now,
        },
    ]
    acked = []

    def fake_run(query, params=(), fetch=False):
        if fetch:
            return rows
        acked.append(params)
        return None

    monkeypatch.setattr(memory, "run", fake_run)
    recorded = []
    monkeypatch.setattr(memory, "append", lambda sid, role, text, **kw: recorded.append((sid, role, text)))
    pending = notifications.pending("s1")
    assert [n.text for n in pending] == ["fulfilled", "rejected"]
    assert acked == [("s1", [1])]  # the superseded notice is marked delivered without a transcript line
    assert recorded == []


def test_ack_writes_transcript_once(monkeypatch):
    now = datetime.now(UTC)
    calls = []

    def fake_run(query, params=(), fetch=False):
        calls.append(query.split()[0])
        if fetch:
            return [{"id": 2, "text": "fulfilled", "created_at": now}]
        return None

    recorded = []
    monkeypatch.setattr(memory, "run", fake_run)
    monkeypatch.setattr(memory, "append", lambda sid, role, text, **kw: recorded.append(text))
    notifications.ack("s1", [2])
    assert recorded == ["fulfilled"] and calls == ["SELECT", "UPDATE"]


def test_notify_ticket_is_noop_without_session():
    notifications.notify_ticket(make_ticket("fulfilled", session_id=None))


def test_request_reply_wording():
    assert "needs approval" in rag.request_reply(make_ticket("pending_approval"))
    assert "No approval is needed" in rag.request_reply(make_ticket("approved"))
    assert rag.request_reply(make_ticket("approved"), "Joe Bloggs").startswith(
        "Joe, I've logged your request"
    )


def test_archive_session_calls_n8n(monkeypatch):
    from app import archives

    calls = []
    monkeypatch.setattr(
        archives,
        "request",
        lambda sid: calls.append(sid) or {"requested": True, "doc_url": "https://docs/x", "archive_id": 3},
    )
    with TestClient(app) as client:
        r = client.post("/v1/sessions/s1/archive")
    assert r.status_code == 202
    assert r.json() == {"session_id": "s1", "requested": True, "doc_url": "https://docs/x", "archive_id": 3}
    assert calls == ["s1"]
