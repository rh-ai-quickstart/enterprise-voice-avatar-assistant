"""The Integrations routes against a real PostgreSQL (skipped unless TEST_DATABASE_URL is set)."""

from types import SimpleNamespace

import httpx
from conftest import ADMIN_HEADERS

from app import integrations, tickets
from app.schemas import Ticket


def test_page_test_audit_and_an_n8n_failure(database, admin_client, monkeypatch):
    monkeypatch.setenv("N8N_API_KEY", "k")
    listed = admin_client.get("/v1/admin/integrations").json()["items"]
    assert [i["name"] for i in listed] == list(integrations.LABELS)
    assert next(i for i in listed if i["name"] == "n8n")["state"] == "on"

    class Http:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None, headers=None):
            return SimpleNamespace(status_code=200, json=lambda: {"data": [{"id": "llama-3.1-8b-instruct"}]})

    monkeypatch.setattr(integrations, "_http", lambda: Http())
    result = admin_client.post("/v1/admin/integrations/llm/test", headers=ADMIN_HEADERS).json()
    assert result["ok"] is True and result["status"]["last_test"]["severity"] == "success"
    assert admin_client.post("/v1/admin/integrations/nope/test", headers=ADMIN_HEADERS).status_code == 422
    audit = admin_client.get("/v1/admin/audit", params={"action": "integration.test"}).json()["items"][0]
    assert audit["target_id"] == "llm" and audit["after"]["ok"] is True

    # The RAG API cannot reach n8n: the n8n card turns failing, the overview says so
    class Down:
        def __init__(self, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **k):
            raise httpx.ConnectError("n8n is down")

    monkeypatch.setattr(tickets.httpx, "Client", Down)
    ticket = Ticket(
        id=1,
        ticket_ref="REQ-000001",
        title="t",
        priority="normal",
        status="pending_approval",
        created_at="2026-09-26T10:00:00Z",
        updated_at="2026-09-26T10:00:00Z",
    )
    assert tickets.notify_n8n(ticket, {}, "chat") is False
    n8n = next(i for i in admin_client.get("/v1/admin/integrations").json()["items"] if i["name"] == "n8n")
    assert n8n["state"] == "failing" and "unreachable" in n8n["last_error"]["title"]
    overview = admin_client.get("/v1/admin/overview").json()["integrations"]
    assert {"name": "n8n", "label": "n8n workflows", "state": "failing"} in overview
