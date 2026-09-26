"""Documents in the portal against a real PostgreSQL (skipped unless TEST_DATABASE_URL is set)."""

import json

import httpx
import pytest
from conftest import ADMIN_HEADERS

from app import admin, classify, documents, events, memory
from app.schemas import ClassifyRequest

INGESTION_JOBS = """CREATE TABLE IF NOT EXISTS ingestion_jobs (
  job_id TEXT PRIMARY KEY, doc_id TEXT NOT NULL, source_uri TEXT NOT NULL, status TEXT NOT NULL, error TEXT,
  chunks INTEGER, pages INTEGER, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), finished_at TIMESTAMPTZ)"""


@pytest.fixture
def stored(database):
    memory.run(INGESTION_JOBS)
    for doc_id, uri, chunks, extracted in (
        ("d-leave", "s3://documents/leave-policy.docx", 12, None),
        ("d-pw", "s3://documents/policies/password-policy.md", 4, None),
        (
            "d-inv",
            "s3://inbox/invoice-INV-2026-0042.pdf",
            0,
            {"doc_type": "invoice", "fields": {"total_amount": 1250}},
        ),
    ):
        memory.run(
            "INSERT INTO documents (doc_id, source, source_uri, chunks, extracted) VALUES (%s, %s, %s, %s, %s::jsonb)",
            (doc_id, uri.rsplit("/", 1)[-1], uri, chunks, json.dumps(extracted) if extracted else None),
        )
    memory.run(
        "INSERT INTO ingestion_jobs (job_id, doc_id, source_uri, status, chunks, finished_at) VALUES ('j1', 'd-leave', 's3://documents/leave-policy.docx', 'done', 12, now())"
    )
    memory.run(
        "INSERT INTO ingestion_jobs (job_id, doc_id, source_uri, status, error) VALUES ('j2', 'd-pw', 's3://documents/policies/password-policy.md', 'failed', 'ConversionError: empty')"
    )


@pytest.fixture
def ingestion(monkeypatch):
    """Records what the RAG API asks the ingestion service to do."""
    calls = []

    def fake(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if path == "/v1/objects":
            name = kwargs["files"]["file"][0]
            return {
                "bucket": kwargs["data"]["bucket"],
                "key": name,
                "doc_id": "d-new",
                "size": len(kwargs["files"]["file"][1]),
            }
        if path == "/v1/ingest":
            return {"job_id": "j3", "doc_id": kwargs["json"]["doc_id"], "status": "queued"}
        return {"deleted": path.rsplit("/", 1)[-1], "object": "s3://documents/leave-policy.docx"}

    monkeypatch.setattr(documents, "_ingestion", fake)
    return calls


def test_list_kinds_filters_and_jobs(stored, admin_client):
    def sources(**params):
        return [d["source"] for d in admin_client.get("/v1/admin/documents", params=params).json()["items"]]

    assert set(sources()) == {"leave-policy.docx", "password-policy.md", "invoice-INV-2026-0042.pdf"}
    assert set(sources(kind="indexed")) == {"leave-policy.docx", "password-policy.md"}
    assert sources(kind="classified") == ["invoice-INV-2026-0042.pdf"]
    assert sources(bucket="inbox") == ["invoice-INV-2026-0042.pdf"]
    assert sources(q="PASSWORD") == ["password-policy.md"]
    item = admin_client.get("/v1/admin/documents", params={"kind": "classified"}).json()["items"][0]
    assert item["bucket"] == "inbox" and item["extracted"]["fields"] == {"total_amount": 1250}
    detail = admin_client.get("/v1/admin/documents/d-leave").json()
    assert detail["bucket"] == "documents" and [j["job_id"] for j in detail["jobs"]] == ["j1"]
    assert admin_client.get("/v1/admin/documents/nope").status_code == 404
    jobs = admin_client.get("/v1/admin/ingestion/jobs").json()["items"]
    assert {j["job_id"]: j["status"] for j in jobs} == {"j1": "done", "j2": "failed"}
    assert next(j for j in jobs if j["job_id"] == "j2")["error"] == "ConversionError: empty"


def test_upload_goes_to_the_object_store_through_ingestion(stored, admin_client, ingestion):
    response = admin_client.post(
        "/v1/admin/documents/upload",
        files={"file": ("travel-policy.pdf", b"%PDF-1.4 travel", "application/pdf")},
        data={"bucket": "documents"},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 201, response.text
    method, path, kwargs = ingestion[0]
    assert (method, path, kwargs["data"]) == ("POST", "/v1/objects", {"bucket": "documents"})
    assert kwargs["files"]["file"] == ("travel-policy.pdf", b"%PDF-1.4 travel", "application/pdf")
    event = events.recent(kind="document.uploaded")[0]
    assert event["actor"] == "Dana" and event["detail"] == "indexed next"
    audit = admin_client.get("/v1/admin/audit", params={"action": "document.upload"}).json()["items"][0]
    assert audit["after"]["key"] == "travel-policy.pdf"
    wrong = admin_client.post(
        "/v1/admin/documents/upload",
        files={"file": ("x.pdf", b"x")},
        data={"bucket": "transcripts"},
        headers=ADMIN_HEADERS,
    )
    assert wrong.status_code == 422


def test_upload_size_limit(stored, admin_client, ingestion, monkeypatch):
    monkeypatch.setattr(admin, "UPLOAD_LIMIT", 10)
    response = admin_client.post(
        "/v1/admin/documents/upload",
        files={"file": ("big.pdf", b"x" * 11)},
        data={"bucket": "inbox"},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 413 and ingestion == []


def test_reingest_and_delete(stored, admin_client, ingestion):
    result = admin_client.post("/v1/admin/documents/d-pw/reingest", headers=ADMIN_HEADERS).json()
    assert result["job_id"] == "j3"
    assert ingestion[0][:2] == ("POST", "/v1/ingest")
    assert ingestion[0][2]["json"] == {
        "bucket": "documents",
        "key": "policies/password-policy.md",
        "doc_id": "d-pw",
    }
    deleted = admin_client.delete("/v1/admin/documents/d-leave", headers=ADMIN_HEADERS).json()
    assert deleted == {
        "doc_id": "d-leave",
        "source": "leave-policy.docx",
        "object": "s3://documents/leave-policy.docx",
    }
    assert ingestion[1][:2] == ("DELETE", "/v1/documents/d-leave") and ingestion[1][2]["params"] == {
        "purge_object": "true"
    }
    assert events.recent(kind="document.deleted")[0]["severity"] == "warning"
    audit = admin_client.get("/v1/admin/audit", params={"action": "document.delete"}).json()["items"][0]
    assert audit["before"] == {"source_uri": "s3://documents/leave-policy.docx", "chunks": 12}


def test_ingestion_errors_are_reported(stored, admin_client, monkeypatch):
    class Client:
        def __init__(self, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def request(self, *a, **k):
            raise httpx.ConnectError("ingestion is down")

    monkeypatch.setattr(documents.httpx, "Client", Client)
    response = admin_client.post("/v1/admin/documents/d-pw/reingest", headers=ADMIN_HEADERS)
    assert response.status_code == 502 and "could not be reached" in response.json()["detail"]


def test_classification_records_an_event(database, monkeypatch):
    monkeypatch.setattr(classify, "extract_text", lambda b, k: {"text": "Invoice 42", "doc_id": "d-42"})
    monkeypatch.setattr(
        classify,
        "classify_text",
        lambda text, filename: {
            "doc_type": "invoice",
            "confidence": 0.93,
            "summary": "An invoice",
            "fields": {"total": 1},
        },
    )
    classify.classify_document(ClassifyRequest(bucket="inbox", key="invoice-42.pdf"))
    event = events.recent(kind="document.classified")[0]
    assert event["title"] == "invoice-42.pdf classified as invoice (93%)" and event["ref_id"] == "d-42"
