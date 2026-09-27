"""The Integrations page: states from flags, keys and history, and the live tests (no network)."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app import faces, gdocs, integrations
from app.config import settings

T0 = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)


@pytest.fixture
def history(monkeypatch):
    """The last integration.test / integration.error per integration, as the page reads them."""
    rows: dict[tuple[str, str], dict] = {}

    def last(name, kinds):
        found = [rows[(name, k)] for k in kinds if (name, k) in rows]
        return max(found, key=lambda r: r["created_at"]) if found else None

    monkeypatch.setattr(integrations, "_last", last)
    return rows


def test_states(history, monkeypatch):
    monkeypatch.setattr(settings, "slack_enabled", False)
    assert integrations.status("slack")["state"] == "off"
    monkeypatch.setattr(settings, "slack_enabled", True)
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "s")
    slack = integrations.status("slack")
    assert slack["state"] == "misconfigured" and slack["missing"] == ["SLACK_BOT_TOKEN"]
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-1")
    assert integrations.status("slack")["state"] == "on"
    history[("slack", "integration.test")] = {"severity": "success", "created_at": T0}
    history[("slack", "integration.error")] = {"severity": "error", "created_at": T0 + timedelta(minutes=5)}
    assert integrations.status("slack")["state"] == "failing"  # an error after the last passing test
    history[("slack", "integration.test")] = {"severity": "success", "created_at": T0 + timedelta(minutes=9)}
    assert integrations.status("slack")["state"] == "on"
    history[("slack", "integration.test")] = {"severity": "error", "created_at": T0 + timedelta(minutes=10)}
    assert integrations.status("slack")["state"] == "failing"


def test_every_integration_has_a_state_and_no_secrets(history, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "sk-secret-value")
    items = integrations.status_all()
    assert [i["name"] for i in items] == list(integrations.LABELS)
    assert "sk-secret-value" not in str(items)
    assert {i["state"] for i in items} <= {"on", "off", "misconfigured", "failing"}


class FakeHttp:
    """Answers GETs from a {url prefix: json} table."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, params=None, headers=None):
        self.calls.append((url, headers))
        for prefix, (status, body) in self.answers.items():
            if url.startswith(prefix):
                return SimpleNamespace(status_code=status, json=lambda body=body: body, text=str(body))
        raise AssertionError(f"unexpected call {url}")


def test_slack_test_checks_the_token_channels_and_membership(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-1")
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "s")
    channels = [
        {"name": c, "is_member": c != "assistant-tickets"}
        for c in integrations.SLACK_CHANNELS
        if c != "assistant-knowledge-gaps"
    ]
    http = FakeHttp(
        {
            "https://slack.com/api/auth.test": (
                200,
                {"ok": True, "user": "enterprise_assistant", "team": "Example Corp"},
            ),
            "https://slack.com/api/conversations.list": (200, {"ok": True, "channels": channels}),
        }
    )
    monkeypatch.setattr(integrations, "_http", lambda: http)
    checks = {c["name"]: c for c in integrations._test_slack()}
    assert checks["auth.test"]["ok"] and "Example Corp" in checks["auth.test"]["detail"]
    assert checks["#assistant-approvals"]["ok"]
    assert not checks["#assistant-tickets"]["ok"] and "not a member" in checks["#assistant-tickets"]["detail"]
    assert (
        not checks["#assistant-knowledge-gaps"]["ok"]
        and checks["#assistant-knowledge-gaps"]["detail"] == "missing"
    )
    assert http.calls[0][1] == {"Authorization": "Bearer xoxb-1"}


def test_slack_test_reports_a_refused_token(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-bad")
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "")
    monkeypatch.setattr(
        integrations,
        "_http",
        lambda: FakeHttp({"https://slack.com/api/auth.test": (200, {"ok": False, "error": "invalid_auth"})}),
    )
    checks = integrations._test_slack()
    assert [c["ok"] for c in checks] == [False, False] and "invalid_auth" in checks[1]["detail"]


def test_google_docs_test_needs_a_writable_folder(monkeypatch):
    monkeypatch.setattr(
        settings, "google_service_account_json", '{"client_email": "sa@p.iam.gserviceaccount.com"}'
    )
    monkeypatch.setattr(settings, "google_docs_folder_id", "folder1")
    answer = {
        "mimeType": "application/vnd.google-apps.folder",
        "name": "Transcripts",
        "capabilities": {"canAddChildren": False},
    }
    session = SimpleNamespace(
        get=lambda url, params, timeout: SimpleNamespace(status_code=200, json=lambda: answer, text="")
    )
    monkeypatch.setattr(gdocs, "_session", lambda: session)
    checks = integrations._test_google_docs()
    assert checks[0]["ok"] and not checks[1]["ok"] and "sa@p.iam.gserviceaccount.com" in checks[1]["detail"]


def test_avatar_test_looks_up_the_faces(monkeypatch):
    monkeypatch.setattr(settings, "avatar_provider", "tavus")
    monkeypatch.setenv("TAVUS_API_KEY", "k")
    monkeypatch.setattr(faces, "catalog", lambda: [SimpleNamespace(id="r1"), SimpleNamespace(id="r2")])
    monkeypatch.setattr(faces, "_tavus_faces", lambda ids, key: {"r1": {"face_name": "Jackie"}})
    checks = integrations._test_avatar()
    assert [(c["name"], c["ok"], c["detail"]) for c in checks] == [
        ("face r1", True, "Jackie"),
        ("face r2", False, "not found with this key"),
    ]


def test_n8n_test_counts_active_workflows(monkeypatch):
    monkeypatch.setenv("N8N_API_KEY", "n8n-key")
    data = [{"name": f"WF{i} x", "active": i != 6} for i in range(1, 8)] + [
        {"name": "My own", "active": False}
    ]
    http = FakeHttp({"http://n8n:5678/api/v1/workflows": (200, {"data": data})})
    monkeypatch.setattr(integrations, "_http", lambda: http)
    checks = integrations._test_n8n()
    assert checks[0]["ok"] and not checks[1]["ok"] and checks[1]["detail"] == "inactive: WF6 x"
    assert http.calls[0][1] == {"X-N8N-API-KEY": "n8n-key"}


def test_model_test_finds_the_served_model(monkeypatch):
    http = FakeHttp({"http://localhost:8080/v1/models": (200, {"data": [{"id": "llama-3.1-8b-instruct"}]})})
    monkeypatch.setattr(integrations, "_http", lambda: http)
    monkeypatch.setattr(settings, "llm_api_key", "none")
    checks = integrations._test_model("llm")
    assert [c["ok"] for c in checks] == [True, True] and http.calls[0][1] == {}
    monkeypatch.setattr(settings, "llm_model", "granite")
    assert not integrations._test_model("llm")[1]["ok"]


def test_a_test_is_recorded_and_errors_become_failed_checks(history, monkeypatch):
    recorded = []
    monkeypatch.setattr(
        integrations.events, "record", lambda kind, title, **k: recorded.append((kind, title, k["severity"]))
    )

    def boom():
        raise ConnectionError("no route to slack.com")

    monkeypatch.setitem(integrations.TESTS, "slack", boom)
    result = integrations.test("slack", "Dana")
    assert result["ok"] is False and "ConnectionError" in result["checks"][0]["detail"]
    assert recorded[0][0] == "integration.test" and recorded[0][2] == "error"
