from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app, require_internal_token
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


def test_every_write_route_needs_the_token():
    for route in app.routes:
        if isinstance(route, APIRoute) and route.methods & {"POST", "PUT", "PATCH", "DELETE"}:
            assert require_internal_token in {d.call for d in route.dependant.dependencies}, route.path


def test_write_routes_refuse_a_missing_or_wrong_token(monkeypatch):
    monkeypatch.setattr(settings, "internal_api_token", "tok")
    event = {"EventName": "s3:ObjectCreated:Put", "Key": "inbox/invoice.pdf"}
    with TestClient(app) as client:
        assert client.post("/v1/events/s3", json=event).status_code == 401
        assert client.post("/v1/events/s3", json=event, headers={"Authorization": "Bearer no"}).status_code == 401
        assert client.delete("/v1/documents/d1").status_code == 401
        assert client.post("/v1/events/s3", json=event, headers={"Authorization": "Bearer tok"}).status_code == 200
        # Reads stay open inside the cluster
        assert client.get("/v1/jobs").status_code == 200


def test_delete_can_purge_the_object(monkeypatch):
    from app import db, main, storage, vectorstore

    removed, calls = [], []
    monkeypatch.setattr(vectorstore, "delete_document", lambda d: calls.append(("vectors", d)))
    monkeypatch.setattr(db, "delete_document", lambda d: calls.append(("record", d)))
    monkeypatch.setattr(db, "source_uri", lambda d: "s3://transcripts/transcript-abc.md" if d == "d1" else None)
    monkeypatch.setattr(storage, "delete_object", lambda b, k: removed.append((b, k)))
    with TestClient(main.app) as client:
        kept = client.delete("/v1/documents/d1").json()
        assert kept == {"deleted": "d1", "object": None} and removed == []
        purged = client.delete("/v1/documents/d1", params={"purge_object": "true"}).json()
        assert purged == {"deleted": "d1", "object": "s3://transcripts/transcript-abc.md"}
        assert removed == [("transcripts", "transcript-abc.md")]
        # Not recorded: the vectors go, the object cannot be found
        assert client.delete("/v1/documents/d2", params={"purge_object": "true"}).json()["object"] is None
    assert ("vectors", "d2") in calls and ("record", "d2") in calls


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
