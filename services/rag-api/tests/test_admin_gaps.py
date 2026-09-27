"""Knowledge gaps in the portal against a real PostgreSQL (skipped unless TEST_DATABASE_URL is set)."""

from types import SimpleNamespace

import pytest
from conftest import ADMIN_HEADERS

from app import events, knowledge_gaps, memory
from app.retrieval import Hit

VECTORS = {
    "how do i reset my password": [1.0, 0.0, 0.0],
    "password reset procedure": [0.95, 0.2, 0.0],
    "where can i park": [0.0, 1.0, 0.0],
}


def vector(question):
    return VECTORS[" ".join(question.lower().split()).rstrip(" ?.!")]


@pytest.fixture
def embeddings(monkeypatch):
    """Embeddings from the table above, synchronously; counts the batch calls for older gaps."""
    batches = []

    class Now:
        def submit(self, fn, *args):
            fn(*args)

    class Client:
        def with_options(self, **_):
            return self

        class embeddings:
            @staticmethod
            def create(model, input):
                batches.append(list(input))
                return SimpleNamespace(data=[SimpleNamespace(embedding=vector(q)) for q in input])

    monkeypatch.setattr(knowledge_gaps, "_embedder", Now())
    monkeypatch.setattr(knowledge_gaps.retrieval, "embed", vector)
    monkeypatch.setattr(knowledge_gaps.clients, "embeddings", lambda: Client())
    return batches


def test_recorded_gaps_get_their_embedding(database, embeddings):
    knowledge_gaps.record("s1", "How do I reset my password?", 0.1, 1)
    knowledge_gaps.record("s1", "Is the policy clear?", 0.9, 3)  # answered: no gap
    rows = memory.run("SELECT id, question, embedding FROM knowledge_gaps", fetch=True)
    assert len(rows) == 1 and rows[0]["embedding"] == pytest.approx([1.0, 0.0, 0.0])
    (event,) = events.recent(kind="gap.recorded")
    assert event["title"] == "Not answered from the documents: How do I reset my password?"
    assert event["ref_type"] == "gap" and event["ref_id"] == str(rows[0]["id"])
    assert event["data"]["reason"] == "low_score" and event["data"]["session_id"] == "s1"


def test_grouping_fills_older_gaps_and_the_page_lists_groups(database, embeddings, admin_client):
    for q in (
        "How do I reset my password?",
        "how do I reset my password",
        "Password reset procedure",
        "Where can I park?",
    ):
        memory.run(
            "INSERT INTO knowledge_gaps (session_id, question, top_score, hit_count, reason) VALUES ('s1', %s, 0.2, 1, 'low_score')",
            (q,),
        )
    page = admin_client.get("/v1/admin/knowledge-gaps").json()
    assert [(g["count"], g["wordings"]) for g in page["groups"]] == [(3, 2), (1, 1)]
    assert page["total"] == 4 and page["threshold"] == 0.85
    assert len(embeddings) == 1 and len(embeddings[0]) == 4  # one batch for the four older gaps
    admin_client.get("/v1/admin/knowledge-gaps")
    assert len(embeddings) == 1  # stored: not embedded again
    flat = admin_client.get("/v1/admin/knowledge-gaps", params={"group": "false"}).json()
    assert flat["total"] == 4 and "embedding" not in flat["items"][0]


def test_resolve_dismiss_reopen_and_the_digest(database, embeddings, admin_client):
    for q in ("How do I reset my password?", "Where can I park?"):
        memory.run(
            "INSERT INTO knowledge_gaps (session_id, question, top_score, hit_count, reason) VALUES ('s1', %s, 0.2, 1, 'low_score')",
            (q,),
        )
    ids = [r["id"] for r in memory.run("SELECT id FROM knowledge_gaps ORDER BY id", fetch=True)]
    response = admin_client.post(
        "/v1/admin/knowledge-gaps/resolve",
        json={"ids": [ids[0]], "status": "resolved", "note": "added the password policy"},
        headers=ADMIN_HEADERS,
    )
    assert response.json() == {"changed": 1}
    row = memory.run(
        "SELECT status, resolved_by, resolution_note FROM knowledge_gaps WHERE id = %s", (ids[0],), fetch=True
    )[0]
    assert row == {
        "status": "resolved",
        "resolved_by": "Dana",
        "resolution_note": "added the password policy",
    }
    assert events.recent(kind="gap.resolved")[0]["actor"] == "Dana"
    # Resolved gaps leave the open list and the digest
    open_groups = admin_client.get("/v1/admin/knowledge-gaps").json()["groups"]
    assert [g["question"] for g in open_groups] == ["Where can I park?"]
    digest = knowledge_gaps.digest(24)
    assert digest["total_gaps"] == 1 and [g["question"] for g in digest["groups"]] == ["Where can I park?"]
    resolved = admin_client.get("/v1/admin/knowledge-gaps", params={"status": "resolved"}).json()["groups"]
    assert resolved[0]["gaps"][0]["resolution_note"] == "added the password policy"
    # Reopen clears the resolution
    admin_client.post(
        "/v1/admin/knowledge-gaps/resolve", json={"ids": [ids[0]], "status": "open"}, headers=ADMIN_HEADERS
    )
    row = memory.run("SELECT status, resolved_by FROM knowledge_gaps WHERE id = %s", (ids[0],), fetch=True)[0]
    assert row == {"status": "open", "resolved_by": None}
    assert (
        admin_client.post(
            "/v1/admin/knowledge-gaps/resolve",
            json={"ids": [ids[1]], "status": "later"},
            headers=ADMIN_HEADERS,
        ).status_code
        == 422
    )
    audit = admin_client.get("/v1/admin/audit", params={"action": "gap.resolve"}).json()["items"]
    assert audit[0]["after"]["note"] == "added the password policy"


def test_retest_retrieves_without_the_model(database, admin_client, monkeypatch):
    memory.run(
        "INSERT INTO knowledge_gaps (session_id, question, top_score, hit_count, reason) VALUES ('s1', 'How do I reset my password?', 0.1, 1, 'low_score')"
    )
    gap_id = memory.run("SELECT id FROM knowledge_gaps", fetch=True)[0]["id"]
    calls = []

    def search(query, top_k=None, min_score=None):
        calls.append((query, top_k, min_score))
        return [
            Hit(
                doc_id="d1",
                source="password-policy.md",
                text="Reset it in the self-service portal.",
                score=0.71,
                page=1,
            )
        ]

    monkeypatch.setattr(knowledge_gaps.retrieval, "search", search)
    result = admin_client.post(f"/v1/admin/knowledge-gaps/{gap_id}/retest", headers=ADMIN_HEADERS).json()
    assert calls == [("How do I reset my password?", 5, 0.0)]
    assert (
        result["answered"] is True
        and result["best_score"] == 0.71
        and result["recorded_score"] == pytest.approx(0.1)
    )
    assert result["hits"][0]["source"] == "password-policy.md"
    assert admin_client.post("/v1/admin/knowledge-gaps/999/retest", headers=ADMIN_HEADERS).status_code == 404

    def unreachable(query, top_k=None, min_score=None):
        raise ConnectionError("Connection refused")

    monkeypatch.setattr(knowledge_gaps.retrieval, "search", unreachable)
    failed = admin_client.post(f"/v1/admin/knowledge-gaps/{gap_id}/retest", headers=ADMIN_HEADERS)
    assert failed.status_code == 502 and "Qdrant did not answer" in failed.json()["detail"]


def test_overview_counts_open_gaps_without_embedding_calls(database, embeddings, admin_client):
    memory.run(
        "INSERT INTO knowledge_gaps (session_id, question, top_score, hit_count, reason) VALUES ('s1', 'Where can I park?', 0.2, 1, 'no_hits')"
    )
    gaps = admin_client.get("/v1/admin/overview").json()["knowledge_gaps"]
    assert gaps == {"open_week": 1, "top_groups": [{"question": "Where can I park?", "count": 1}]}
    assert embeddings == []
