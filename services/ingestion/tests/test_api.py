from fastapi.testclient import TestClient

from app.main import app
from app.pipeline import make_doc_id


def test_healthz():
    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}


def test_doc_id_is_stable():
    assert make_doc_id("documents", "a.pdf") == make_doc_id("documents", "a.pdf")
    assert make_doc_id("documents", "a.pdf") != make_doc_id("inbox", "a.pdf")


def test_events_for_other_buckets_are_ignored():
    with TestClient(app) as client:
        response = client.post(
            "/v1/events/s3",
            json={"EventName": "s3:ObjectCreated:Put", "Key": "inbox/invoice.pdf"},
        )
    assert response.status_code == 200
    assert response.json() == {"accepted": [], "deleted": [], "ignored": ["inbox/invoice.pdf"]}


def test_unknown_job_is_404():
    with TestClient(app) as client:
        assert client.get("/v1/jobs/nope").status_code == 404


def test_missing_buckets_are_created(monkeypatch):
    from botocore.exceptions import ClientError

    from app import storage

    existing, created = {"documents"}, []

    class Client:
        def head_bucket(self, Bucket):
            if Bucket not in existing:
                raise ClientError({"Error": {"Code": "404"}}, "HeadBucket")

        def create_bucket(self, Bucket):
            created.append(Bucket)
            existing.add(Bucket)

    monkeypatch.setattr(storage, "client", lambda: Client())
    assert storage.ensure_buckets(["documents", "inbox", "transcripts"]) == ["inbox", "transcripts"]
    assert storage.ensure_buckets(["documents", "inbox", "transcripts"]) == []
    assert created == ["inbox", "transcripts"]
