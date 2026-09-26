import pytest
from conftest import ADMIN_HEADERS, ADMIN_PASSWORD
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app import auth
from app.config import settings
from app.main import app

# What the chat UI and the voice agent call; the frontend's nginx forwards only these (and
# /v1/admin/*) from the public /api proxy (frontend/nginx/api-allowlist.conf).
PUBLIC = {
    ("GET", "/healthz"),
    ("GET", "/readyz"),
    ("GET", "/v1/info"),
    ("POST", "/v1/chat"),
    ("POST", "/v1/chat/stream"),
    ("GET", "/v1/voice/token"),
    ("GET", "/v1/voice/faces"),
    ("GET", "/v1/voice/faces/{face_id}/poster"),
    ("GET", "/v1/sessions/{session_id}/messages"),
    ("GET", "/v1/sessions/{session_id}/notifications"),
    ("POST", "/v1/sessions/{session_id}/notifications/ack"),
    ("POST", "/v1/sessions/{session_id}/archive"),
    ("DELETE", "/v1/sessions/{session_id}"),
}


def _dependencies(route: APIRoute) -> set:
    return {d.call for d in route.dependant.dependencies}


def test_every_route_is_public_admin_or_internal():
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods:
            if (method, route.path) in PUBLIC:
                assert auth.require_internal_token not in _dependencies(route), route.path
            elif route.path.startswith("/v1/admin/"):
                assert auth.require_admin_enabled in _dependencies(route), route.path
            else:
                assert auth.require_internal_token in _dependencies(route), (
                    f"{method} {route.path} is unprotected"
                )


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setattr(settings, "internal_api_token", "s3cret-token")
    return "s3cret-token"


def test_internal_routes_need_the_token(token):
    with TestClient(app) as client:
        assert client.get("/v1/knowledge-gaps/digest").status_code == 401
        assert (
            client.get("/v1/knowledge-gaps/digest", headers={"Authorization": "Bearer wrong"}).status_code
            == 401
        )
        assert (
            client.get("/v1/knowledge-gaps/digest", headers={"Authorization": f"Basic {token}"}).status_code
            == 401
        )
        ok = client.get("/v1/knowledge-gaps/digest", headers={"Authorization": f"Bearer {token}"})
        assert ok.status_code == 200 and ok.json()["total_gaps"] == 0
        assert client.post("/v1/internal/events", json={"kind": "a.b", "title": "x"}).status_code == 401


def test_public_routes_need_no_token(token):
    with TestClient(app) as client:
        assert client.get("/v1/info").status_code == 200
        assert client.get("/v1/sessions/abc/notifications").status_code == 200


def test_empty_token_disables_the_check(monkeypatch):
    monkeypatch.setattr(settings, "internal_api_token", "")
    assert auth.internal_headers() == {}
    with TestClient(app) as client:
        assert client.get("/v1/tickets/stale").status_code == 200


def test_internal_headers_carry_the_token(token):
    assert auth.internal_headers() == {"Authorization": f"Bearer {token}"}


def test_internal_event_validation(token):
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(app) as client:
        assert (
            client.post(
                "/v1/internal/events", json={"kind": "Bad Kind", "title": "x"}, headers=headers
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/v1/internal/events",
                json={"kind": "a.b", "title": "x", "severity": "fatal"},
                headers=headers,
            ).status_code
            == 422
        )
        # Without a database the event is accepted and not stored
        response = client.post(
            "/v1/internal/events",
            json={"kind": "document.ingested", "title": "policy.pdf indexed", "ref_type": "document"},
            headers=headers,
        )
        assert response.status_code == 201 and response.json() == {"id": None}


# ---------------------------------------------------------------- session cookie -------------


def test_session_round_trip(admin_settings):
    cookie, session = auth.sign_session("Dana", now=1000)
    assert auth.read_session(cookie, now=1001) == session
    assert session.expires_at == 1000 + 8 * 3600


def test_session_expires(admin_settings):
    cookie, session = auth.sign_session("Dana", now=1000)
    assert auth.read_session(cookie, now=session.expires_at) is None


def test_tampered_session_is_refused(admin_settings):
    cookie, _ = auth.sign_session("Dana", now=1000)
    body, mac = cookie.split(".")
    forged, _ = auth.sign_session("Mallory", now=1000)
    assert auth.read_session(f"{forged.split('.')[0]}.{mac}", now=1001) is None
    assert auth.read_session(f"{body}.{mac[:-2]}xx", now=1001) is None
    assert auth.read_session("not-a-cookie", now=1001) is None
    assert auth.read_session(f"{body}.é", now=1001) is None


def test_rotating_the_secret_or_the_password_signs_everyone_out(admin_settings, monkeypatch):
    cookie, _ = auth.sign_session("Dana", now=1000)
    monkeypatch.setattr(settings, "admin_session_secret", "rotated")
    assert auth.read_session(cookie, now=1001) is None
    monkeypatch.setattr(settings, "admin_session_secret", "test-session-secret")
    assert auth.read_session(cookie, now=1001) is not None
    monkeypatch.setattr(settings, "admin_password", "new password")
    assert auth.read_session(cookie, now=1001) is None


@pytest.mark.parametrize(
    "name", ["n8n", "System", " assistant ", "SLA-escalation", "slack", "D", "x" * 41, "Da\x00na"]
)
def test_reserved_and_malformed_names_are_refused(name):
    with pytest.raises(auth.HTTPException) as info:
        auth.display_name(name)
    assert info.value.status_code == 422


def test_display_name_is_trimmed():
    assert auth.display_name("  Dana \t Scully ") == "Dana Scully"


def test_limiter_blocks_after_five_failures_per_address():
    limiter = auth.LoginLimiter(limit=5, window_seconds=60)
    for i in range(5):
        assert limiter.retry_after("10.0.0.1", now=100 + i) == 0
        limiter.failed("10.0.0.1", now=100 + i)
    assert limiter.retry_after("10.0.0.1", now=105) > 0
    assert limiter.retry_after("10.0.0.2", now=105) == 0
    # The oldest failure leaves the window after a minute
    assert limiter.retry_after("10.0.0.1", now=160.5) == 0
    limiter.failed("10.0.0.3", now=100)
    limiter.succeeded("10.0.0.3")
    assert limiter.retry_after("10.0.0.3", now=101) == 0


# ---------------------------------------------------------------- sign-in API ----------------


def test_login_sets_a_strict_secure_cookie(admin_settings):
    with TestClient(app, base_url="https://testserver") as client:
        response = client.post(
            "/v1/admin/login", json={"password": ADMIN_PASSWORD, "name": " Dana "}, headers=ADMIN_HEADERS
        )
        assert response.status_code == 200
        assert response.json()["name"] == "Dana"
        cookie = response.headers["set-cookie"]
        for flag in ("admin_session=", "HttpOnly", "Secure", "SameSite=strict", "Path=/", "Max-Age=28800"):
            assert flag in cookie
        assert client.get("/v1/admin/me").json()["name"] == "Dana"


def test_me_needs_a_session(admin_settings):
    with TestClient(app, base_url="https://testserver") as client:
        assert client.get("/v1/admin/me").status_code == 401
        client.cookies.set(auth.COOKIE, "forged.cookie")
        assert client.get("/v1/admin/me").status_code == 401


def test_wrong_password_then_rate_limit(admin_settings):
    with TestClient(app, base_url="https://testserver") as client:
        for _ in range(5):
            response = client.post(
                "/v1/admin/login", json={"password": "guess", "name": "Mallory"}, headers=ADMIN_HEADERS
            )
            assert response.status_code == 401
        response = client.post(
            "/v1/admin/login", json={"password": ADMIN_PASSWORD, "name": "Mallory"}, headers=ADMIN_HEADERS
        )
        assert response.status_code == 429 and int(response.headers["retry-after"]) > 0
        # Another client address is not blocked
        other = client.post(
            "/v1/admin/login",
            json={"password": ADMIN_PASSWORD, "name": "Dana"},
            headers={**ADMIN_HEADERS, "X-Client-Address": "203.0.113.9"},
        )
        assert other.status_code == 200


def test_non_get_admin_calls_need_the_csrf_header(admin_client):
    assert admin_client.post("/v1/admin/logout").status_code == 403
    assert (
        admin_client.post("/v1/admin/login", json={"password": ADMIN_PASSWORD, "name": "Dana"}).status_code
        == 403
    )
    # GET needs no header
    assert admin_client.get("/v1/admin/me").status_code == 200


def test_logout_clears_the_cookie(admin_client):
    response = admin_client.post("/v1/admin/logout", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    assert (
        'admin_session=""' in response.headers["set-cookie"] or "Max-Age=0" in response.headers["set-cookie"]
    )
    assert admin_client.get("/v1/admin/me").status_code == 401


def test_login_refused_when_not_configured(admin_settings, monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "")
    with TestClient(app, base_url="https://testserver") as client:
        response = client.post(
            "/v1/admin/login", json={"password": "", "name": "Dana"}, headers=ADMIN_HEADERS
        )
    assert response.status_code == 503


def test_admin_routes_vanish_when_disabled(admin_client, monkeypatch):
    monkeypatch.setattr(settings, "admin_enabled", False)
    assert admin_client.get("/v1/admin/me").status_code == 404
    assert admin_client.get("/v1/admin/stream").status_code == 404
    assert (
        admin_client.post(
            "/v1/admin/login", json={"password": ADMIN_PASSWORD, "name": "Dana"}, headers=ADMIN_HEADERS
        ).status_code
        == 404
    )


def test_lists_without_a_database_are_empty(admin_client):
    assert admin_client.get("/v1/admin/activity").json() == {"items": [], "next_before_id": None}
    assert admin_client.get("/v1/admin/audit", params={"actor": "Dana"}).json() == {
        "items": [],
        "next_before_id": None,
    }
    assert admin_client.get("/v1/admin/audit", params={"limit": 500}).status_code == 422
