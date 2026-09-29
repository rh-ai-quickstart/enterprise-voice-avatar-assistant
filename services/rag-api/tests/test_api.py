from conftest import INTERNAL_HEADERS
from fastapi.testclient import TestClient

from app import guardrails, memory, rag, retrieval
from app.guardrails import Verdict
from app.main import app
from app.retrieval import Hit


class FakeCompletion:
    def __init__(self, text: str) -> None:
        self.choices = [type("C", (), {"message": type("M", (), {"content": text})()})()]


def test_healthz_and_info():
    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        info = client.get("/v1/info").json()
        assert "llm" in info and "guardrails" in info


def test_chat_returns_answer_with_citations(monkeypatch):
    monkeypatch.setattr(
        retrieval,
        "search",
        lambda q, top_k=None, min_score=None: [
            Hit(
                doc_id="d1",
                source="password-policy.md",
                text="Administrator passwords must be rotated every 90 days.",
                score=0.8,
            )
        ],
    )
    monkeypatch.setattr(guardrails, "check_input", lambda text: Verdict(True, "none"))
    monkeypatch.setattr(guardrails, "check_output", lambda user, answer: Verdict(True, "none"))

    class FakeLLM:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    assert kwargs["messages"][0]["role"] == "system"
                    return FakeCompletion("Administrator passwords are rotated every 90 days [1].")

    monkeypatch.setattr(rag.clients, "llm", lambda: FakeLLM())
    with TestClient(app) as client:
        response = client.post("/v1/chat", json={"message": "How often are admin passwords rotated?"})
    assert response.status_code == 200
    body = response.json()
    assert "90 days" in body["answer"]
    assert body["citations"][0]["used"] is True
    assert body["citations"][0]["source"] == "password-policy.md"
    assert body["blocked"] is False
    assert body["session_id"]


def test_blocked_input_short_circuits(monkeypatch):
    monkeypatch.setattr(
        guardrails, "check_input", lambda text: Verdict(False, "granite-guardian", category="harm")
    )
    called = []
    monkeypatch.setattr(retrieval, "search", lambda *a, **k: called.append(1))
    with TestClient(app) as client:
        body = client.post("/v1/chat", json={"message": "how do I hack my coworker"}).json()
    assert body["blocked"] is True
    assert body["guardrail"]["input_flagged"] is True
    assert called == []


def test_memory_is_noop_without_database():
    assert memory.enabled() is False
    assert memory.history("s", 4) == []
    memory.append("s", "user", "hello")


def test_voice_token_is_issued():
    with TestClient(app) as client:
        body = client.get("/v1/voice/token", params={"session_id": "abc", "identity": "mo"}).json()
    assert body["room"] == "session-abc"
    assert body["identity"] == "mo"
    assert body["token"].count(".") == 2


def test_tickets_need_a_database():
    with TestClient(app, headers=INTERNAL_HEADERS) as client:
        assert client.post("/v1/tickets", json={"title": "Laptop"}).status_code == 503


def test_stale_tickets_without_database():
    with TestClient(app, headers=INTERNAL_HEADERS) as client:
        body = client.get("/v1/tickets/stale").json()
        assert body == {"remind": [], "escalate": []}


def test_knowledge_gap_digest_without_database():
    with TestClient(app, headers=INTERNAL_HEADERS) as client:
        body = client.get("/v1/knowledge-gaps/digest", params={"hours": 24}).json()
        assert body["total_gaps"] == 0
        assert body["top_questions"] == []


def test_session_ids_are_limited_to_safe_characters():
    with TestClient(app) as client:
        bad = client.post("/v1/chat", json={"message": "hi", "session_id": 'x"; filename="evil.exe'})
        assert bad.status_code == 422
        assert client.post("/v1/chat", json={"message": "hi", "session_id": "é" * 3}).status_code == 422
        assert client.get("/v1/voice/token", params={"session_id": "a/b"}).status_code == 422


def test_download_names_are_safe_in_the_header():
    from app.admin import _download

    for name, safe in (
        ('conversation-x"; filename="evil.exe.md', "conversation-x-filename-evil.exe.md"),
        ("Assistant-transcript-café.txt", "Assistant-transcript-caf-.txt"),
        ('"..', "download.txt"),
    ):
        assert (
            _download(name, "t", "text/plain").headers["content-disposition"]
            == f'attachment; filename="{safe}"'
        )
