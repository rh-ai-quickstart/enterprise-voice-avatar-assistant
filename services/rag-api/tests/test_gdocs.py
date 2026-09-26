import json
from types import SimpleNamespace

from app import gdocs, memory
from app.config import settings

SA = json.dumps({"client_email": "assistant@project.iam.gserviceaccount.com", "private_key": "x"})


def test_not_configured_means_no_document(monkeypatch):
    monkeypatch.setattr(settings, "google_service_account_json", None)
    monkeypatch.setattr(settings, "google_docs_folder_id", "folder")
    assert gdocs.configured() is False and gdocs.create_document("t", "x") is None


def test_multipart_body_and_document_link(monkeypatch):
    monkeypatch.setattr(settings, "google_docs_enabled", True)
    monkeypatch.setattr(settings, "google_service_account_json", SA)
    monkeypatch.setattr(settings, "google_docs_folder_id", "folder123")
    assert gdocs.service_account_email() == "assistant@project.iam.gserviceaccount.com"
    calls = []

    class Session:
        def post(self, url, params, data, headers, timeout):
            calls.append((url, params, data, headers))
            return SimpleNamespace(
                status_code=200,
                json=lambda: {"id": "doc1", "webViewLink": "https://docs.google.com/document/d/doc1/edit"},
                text="",
            )

    monkeypatch.setattr(gdocs, "_session", lambda: Session())
    link = gdocs.create_document("Assistant transcript abc", "hello world")
    assert link == "https://docs.google.com/document/d/doc1/edit"
    _url, params, data, headers = calls[0]
    assert params["uploadType"] == "multipart" and headers["Content-Type"].startswith("multipart/related")
    assert (
        b'"parents": ["folder123"]' in data and b'"mimeType": "application/vnd.google-apps.document"' in data
    )
    assert data.endswith(b"hello world\r\n--" + gdocs.BOUNDARY.encode() + b"--")


def test_keys_without_the_flag_mean_no_document(monkeypatch):
    monkeypatch.setattr(settings, "google_docs_enabled", False)
    monkeypatch.setattr(settings, "google_service_account_json", SA)
    monkeypatch.setattr(settings, "google_docs_folder_id", "folder123")
    monkeypatch.setattr(gdocs, "_session", lambda: (_ for _ in ()).throw(AssertionError("Drive called")))
    assert gdocs.keys_present() is True and gdocs.configured() is False
    assert gdocs.create_document("t", "x") is None


def test_drive_refusal_is_logged_not_raised(monkeypatch):
    monkeypatch.setattr(settings, "google_docs_enabled", True)
    monkeypatch.setattr(settings, "google_service_account_json", SA)
    monkeypatch.setattr(settings, "google_docs_folder_id", "folder123")

    class Session:
        def post(self, *a, **k):
            return SimpleNamespace(status_code=403, json=dict, text="forbidden")

    monkeypatch.setattr(gdocs, "_session", lambda: Session())
    assert gdocs.create_document("t", "x") is None


def test_archive_creates_the_doc_then_calls_n8n(monkeypatch):
    from app import archives

    monkeypatch.setattr(settings, "internal_api_token", "tok")
    monkeypatch.setattr(memory, "transcript", lambda sid: "[2026-09-10 10:00] user: hi")
    monkeypatch.setattr(
        gdocs, "create_document", lambda title, text: "https://docs.google.com/document/d/d1/edit"
    )
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

    import httpx

    monkeypatch.setattr(httpx, "Client", Client)
    # Without a database there is no archive record, but the document and the workflow still happen
    result = archives.request("abcdef1234")
    assert result == {
        "requested": True,
        "doc_url": "https://docs.google.com/document/d/d1/edit",
        "archive_id": None,
    }
    url, body, headers = posted[0]
    assert url.endswith("/webhook/archive-transcript") and body["doc_url"].endswith("/d1/edit")
    assert body["archive_id"] is None and headers == {"Authorization": "Bearer tok"}


def test_nothing_to_archive(monkeypatch):
    from app import archives

    monkeypatch.setattr(memory, "transcript", lambda sid: "")
    assert archives.request("empty")["requested"] is False
