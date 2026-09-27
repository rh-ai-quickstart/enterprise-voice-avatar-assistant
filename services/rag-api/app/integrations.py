"""The admin portal's Integrations page: what each integration is set to, and a live test.

States: off (disabled), misconfigured (enabled with a key missing), failing (the last test failed,
or an integration.error came after the last passing test), on. Test results and errors are
activity events (integration.test, integration.error) with ref_type integration, so the page, the
overview and the feed read the same history.
"""

import logging
import os
from collections.abc import Callable
from datetime import datetime
from typing import Any

import httpx

from . import events, faces, gdocs, memory
from .config import settings
from .tls import tls_context

log = logging.getLogger("rag.integrations")

SLACK_API = "https://slack.com/api"
DRIVE_API = "https://www.googleapis.com/drive/v3/files"
SLACK_CHANNELS = [
    "assistant-ingestion",
    "assistant-documents",
    "assistant-approvals",
    "assistant-tickets",
    "assistant-knowledge-gaps",
]
WORKFLOWS = 7
LABELS = {
    "slack": "Slack",
    "google_docs": "Google Docs",
    "avatar": "Avatar",
    "n8n": "n8n workflows",
    "llm": "Language model",
    "embeddings": "Embeddings",
    "stt": "Speech to text",
    "tts": "Text to speech",
    "guardrails": "Guardrails",
}
AVATAR_KEYS = faces.AVATAR_KEYS


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _models() -> dict[str, dict[str, str]]:
    """Base URL, served model name and key per model (the config map and the models secret)."""
    return {
        "llm": {"url": settings.llm_base_url, "model": settings.llm_model, "key": settings.llm_api_key},
        "embeddings": {
            "url": settings.embeddings_base_url,
            "model": settings.embeddings_model,
            "key": settings.embeddings_api_key,
        },
        "stt": {"url": _env("STT_BASE_URL"), "model": _env("STT_MODEL"), "key": _env("STT_API_KEY")},
        "tts": {"url": _env("TTS_BASE_URL"), "model": _env("TTS_MODEL"), "key": _env("TTS_API_KEY")},
        "guardrails": {
            "url": settings.guardrails_base_url,
            "model": settings.guardrails_model,
            "key": settings.guardrails_api_key,
        },
    }


def _configuration(name: str) -> tuple[bool, dict[str, Any], list[str]]:
    """(enabled, settings shown on the card without secrets, keys missing)."""
    if name == "slack":
        missing = [k for k in ("SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET") if not _env(k)]
        return settings.slack_enabled, {"channels": SLACK_CHANNELS}, missing
    if name == "google_docs":
        missing = [
            k
            for k, v in (
                ("GOOGLE_SERVICE_ACCOUNT_JSON", settings.google_service_account_json),
                ("GOOGLE_DOCS_FOLDER_ID", settings.google_docs_folder_id),
            )
            if not v
        ]
        config = {
            "folder_id": settings.google_docs_folder_id,
            "service_account": gdocs.service_account_email(),
        }
        return settings.google_docs_enabled, config, missing
    if name == "avatar":
        provider = settings.avatar_provider
        key = AVATAR_KEYS.get(provider)
        missing = [key] if key and not _env(key) else []
        return provider != "none", {"provider": provider, "faces": [f.id for f in faces.catalog()]}, missing
    if name == "n8n":
        return True, {"url": settings.n8n_url}, [] if _env("N8N_API_KEY") else ["N8N_API_KEY"]
    model = _models()[name]
    if name == "guardrails":
        enabled = settings.guardrails_provider != "none"
        config = {"provider": settings.guardrails_provider, "base_url": model["url"], "model": model["model"]}
        return (
            enabled,
            config,
            [] if model["url"] or settings.guardrails_provider == "trustyai" else ["GUARDRAILS_BASE_URL"],
        )
    missing = [] if model["url"] else [f"{name.upper()}_BASE_URL"]
    return True, {"base_url": model["url"], "model": model["model"]}, missing


def _last(name: str, kinds: list[str]) -> dict[str, Any] | None:
    rows = memory.run(
        """SELECT kind, severity, title, detail, data, created_at FROM activity_events
           WHERE ref_type = 'integration' AND ref_id = %s AND kind = ANY(%s) ORDER BY id DESC LIMIT 1""",
        (name, kinds),
        fetch=True,
    )
    return dict(rows[0]) if rows else None


def status(name: str) -> dict[str, Any]:
    enabled, config, missing = _configuration(name)
    last_test = _last(name, ["integration.test"])
    last_error = _last(name, ["integration.error"])
    passed_at: datetime | None = (
        last_test["created_at"] if last_test and last_test["severity"] == "success" else None
    )
    if not enabled:
        state = "off"
    elif missing:
        state = "misconfigured"
    elif (last_test and last_test["severity"] != "success") or (
        last_error and (passed_at is None or last_error["created_at"] > passed_at)
    ):
        state = "failing"
    else:
        state = "on"
    return {
        "name": name,
        "label": LABELS[name],
        "state": state,
        "enabled": enabled,
        "config": config,
        "missing": missing,
        "last_test": last_test,
        "last_error": last_error,
    }


def status_all() -> list[dict[str, Any]]:
    return [status(name) for name in LABELS]


# ---------------------------------------------------------------- tests -----------------------


def _http() -> httpx.Client:
    return httpx.Client(verify=tls_context(), timeout=httpx.Timeout(15.0, connect=5.0))


def _check(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "ok": ok, "detail": detail}


def _test_slack() -> list[dict[str, Any]]:
    token = _env("SLACK_BOT_TOKEN")
    if not token:
        return [_check("bot token", False, "SLACK_BOT_TOKEN is empty")]
    checks = [
        _check(
            "signing secret",
            bool(_env("SLACK_SIGNING_SECRET")),
            "SLACK_SIGNING_SECRET is set"
            if _env("SLACK_SIGNING_SECRET")
            else "SLACK_SIGNING_SECRET is empty: clicks on cards are refused",
        )
    ]
    headers = {"Authorization": f"Bearer {token}"}
    with _http() as http:
        me = http.get(f"{SLACK_API}/auth.test", headers=headers).json()
        if not me.get("ok"):
            return [*checks, _check("auth.test", False, f"Slack refuses the token: {me.get('error')}")]
        checks.append(_check("auth.test", True, f"app {me.get('user')} in workspace {me.get('team')}"))
        listing = http.get(
            f"{SLACK_API}/conversations.list",
            params={"types": "public_channel", "exclude_archived": "true", "limit": 999},
            headers=headers,
        ).json()
    if not listing.get("ok"):
        return [*checks, _check("channels", False, f"conversations.list: {listing.get('error')}")]
    channels = {c["name"]: c for c in listing.get("channels", [])}
    for name in SLACK_CHANNELS:
        channel = channels.get(name)
        if channel is None:
            checks.append(_check(f"#{name}", False, "missing"))
        else:
            member = bool(channel.get("is_member"))
            checks.append(
                _check(f"#{name}", member, "the app is a member" if member else "the app is not a member")
            )
    return checks


def _test_google_docs() -> list[dict[str, Any]]:
    if not gdocs.keys_present():
        return [
            _check(
                "service account and folder",
                False,
                "GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_DOCS_FOLDER_ID is empty",
            )
        ]
    response = gdocs._session().get(
        f"{DRIVE_API}/{settings.google_docs_folder_id}",
        params={"supportsAllDrives": "true", "fields": "id,name,mimeType,capabilities(canAddChildren)"},
        timeout=20,
    )
    if response.status_code >= 400:
        return [_check("folder", False, f"Drive answers {response.status_code}: {response.text[:200]}")]
    folder = response.json()
    writable = bool((folder.get("capabilities") or {}).get("canAddChildren"))
    return [
        _check(
            "folder", folder.get("mimeType") == "application/vnd.google-apps.folder", f"{folder.get('name')}"
        ),
        _check(
            "can add documents",
            writable,
            "shared as Editor" if writable else f"share it with {gdocs.service_account_email()} as Editor",
        ),
    ]


def _test_avatar() -> list[dict[str, Any]]:
    provider = settings.avatar_provider
    key = AVATAR_KEYS.get(provider)
    if not key:
        return [_check("provider", provider == "none", f"unknown provider {provider}")]
    if not _env(key):
        return [_check("key", False, f"{key} is empty")]
    if provider != "tavus":
        return [_check("key", True, f"{key} is set (no live test for {provider})")]
    ids = [f.id for f in faces.catalog()] or ([settings.tavus_face_id] if settings.tavus_face_id else [])
    if not ids:
        return [_check("faces", False, "no face configured (voiceAgent.faces or TAVUS_FACE_ID)")]
    found = faces._tavus_faces(ids, _env(key))
    return [
        _check(
            f"face {i}",
            i in found,
            (found[i].get("face_name") or "found") if i in found else "not found with this key",
        )
        for i in ids
    ]


def _test_n8n() -> list[dict[str, Any]]:
    key = _env("N8N_API_KEY")
    if not key:
        return [_check("API key", False, "N8N_API_KEY is empty (the n8n-setup job writes it)")]
    with _http() as http:
        response = http.get(
            f"{settings.n8n_url.rstrip('/')}/api/v1/workflows",
            params={"limit": 100},
            headers={"X-N8N-API-KEY": key},
        )
    if response.status_code >= 400:
        return [_check("API", False, f"n8n answers {response.status_code}")]
    workflows = [w for w in response.json().get("data", []) if str(w.get("name", "")).startswith("WF")]
    inactive = [w["name"] for w in workflows if not w.get("active")]
    return (
        [
            _check("workflows", len(workflows) >= WORKFLOWS, f"{len(workflows)} of {WORKFLOWS} found"),
            _check(
                "active",
                not inactive,
                "all active" if not inactive else "inactive: " + ", ".join(sorted(inactive)),
            ),
        ]
        if workflows
        else [_check("workflows", False, "none found")]
    )


def _test_model(name: str) -> list[dict[str, Any]]:
    model = _models()[name]
    if not model["url"]:
        return [_check("endpoint", False, "no base URL")]
    headers = {"Authorization": f"Bearer {model['key']}"} if model["key"] and model["key"] != "none" else {}
    with _http() as http:
        response = http.get(f"{model['url'].rstrip('/')}/models", headers=headers)
    if response.status_code >= 400:
        return [_check("endpoint", False, f"{model['url']}/models answers {response.status_code}")]
    served = [m.get("id") for m in response.json().get("data", [])]
    return [
        _check("endpoint", True, model["url"]),
        _check(
            "model",
            model["model"] in served,
            f"{model['model']} served"
            if model["model"] in served
            else f"{model['model']} not among {', '.join(map(str, served)) or 'nothing'}",
        ),
    ]


TESTS: dict[str, Callable[[], list[dict[str, Any]]]] = {
    "slack": _test_slack,
    "google_docs": _test_google_docs,
    "avatar": _test_avatar,
    "n8n": _test_n8n,
    **{name: (lambda n=name: _test_model(n)) for name in ("llm", "embeddings", "stt", "tts", "guardrails")},
}


def test(name: str, actor: str) -> dict[str, Any]:
    """Run an integration's test and record it (integration.test, success or error)."""
    try:
        checks = TESTS[name]()
    except Exception as exc:  # noqa: BLE001 - a network or auth failure is a failed test
        checks = [_check("reachable", False, f"{type(exc).__name__}: {str(exc)[:200]}")]
    ok = all(c["ok"] for c in checks)
    failed = [c for c in checks if not c["ok"]]
    events.record(
        "integration.test",
        f"{LABELS[name]} test {'passed' if ok else 'failed'}"
        + (f": {failed[0]['name']} {failed[0]['detail']}" if failed else ""),
        severity="success" if ok else "error",
        ref_type="integration",
        ref_id=name,
        actor=actor,
        source="portal",
        data={"checks": checks},
    )
    return {"name": name, "ok": ok, "checks": checks, "status": status(name)}
