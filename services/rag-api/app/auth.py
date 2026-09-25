"""Who may call what.

Public routes serve the chat UI and the voice agent. Every other route needs the internal bearer
token (INTERNAL_API_TOKEN) that n8n, the ingestion service and the scripts send. The admin portal
(/v1/admin/*) signs in with the shared admin password and a display name and then carries a signed
session cookie; the name is not verified, the password is the trust boundary.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import time
from collections import deque
from dataclasses import dataclass

from fastapi import HTTPException, Request

from .config import settings

COOKIE = "admin_session"
CSRF_HEADER = "X-Admin-Request"
# Actors the workflows write into tickets; a person signing in as one would read as a machine
RESERVED_NAMES = {"n8n", "assistant", "system", "slack", "sla-escalation"}


# ---------------------------------------------------------------- internal token -------------


def require_internal_token(request: Request) -> None:
    token = settings.internal_api_token
    if not token:
        return
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(value.strip().encode(), token.encode()):
        raise HTTPException(
            status_code=401,
            detail="this route needs the internal API token (Authorization: Bearer $INTERNAL_API_TOKEN)",
            headers={"WWW-Authenticate": "Bearer"},
        )


def internal_headers() -> dict[str, str]:
    """Headers for calls to the ingestion service and n8n, which check the same token."""
    return {"Authorization": f"Bearer {settings.internal_api_token}"} if settings.internal_api_token else {}


# ---------------------------------------------------------------- admin session --------------


@dataclass(frozen=True)
class AdminSession:
    name: str
    issued_at: int
    expires_at: int


def admin_configured() -> bool:
    return bool(settings.admin_password and settings.admin_session_secret)


def _key() -> bytes:
    # The password is part of the key: changing it signs everyone out, as rotating the secret does
    return hashlib.sha256(f"{settings.admin_session_secret}\0{settings.admin_password}".encode()).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _mac(body: str) -> str:
    return _b64(hmac.new(_key(), body.encode(), hashlib.sha256).digest())


def sign_session(name: str, now: float | None = None) -> tuple[str, AdminSession]:
    issued = int(now if now is not None else time.time())
    session = AdminSession(
        name=name, issued_at=issued, expires_at=issued + settings.admin_session_hours * 3600
    )
    body = _b64(json.dumps(session.__dict__, separators=(",", ":")).encode())
    return f"{body}.{_mac(body)}", session


def read_session(cookie: str | None, now: float | None = None) -> AdminSession | None:
    """The session in a cookie value, or None when it is missing, tampered with or expired."""
    if not cookie or not admin_configured():
        return None
    body, _, mac = cookie.partition(".")
    if not hmac.compare_digest(mac.encode(), _mac(body).encode()):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        session = AdminSession(str(data["name"]), int(data["issued_at"]), int(data["expires_at"]))
    except (ValueError, KeyError, TypeError):
        return None
    return session if session.expires_at > (now if now is not None else time.time()) else None


def password_matches(password: str) -> bool:
    return hmac.compare_digest(password.encode(), settings.admin_password.encode())


def display_name(raw: str) -> str:
    """The name to attribute decisions to: trimmed, 2 to 40 characters, not a workflow actor."""
    name = " ".join(raw.split())
    if not 2 <= len(name) <= 40 or not name.isprintable():
        raise HTTPException(status_code=422, detail="the display name needs 2 to 40 printable characters")
    if name.lower() in RESERVED_NAMES:
        raise HTTPException(
            status_code=422, detail=f"{name!r} is the name of a workflow actor; sign in with your own name"
        )
    return name


def client_address(request: Request) -> str:
    """The address the OpenShift router saw, which the frontend's nginx passes as X-Client-Address
    (the last X-Forwarded-For entry; the entries before it are whatever the client sent)."""
    return request.headers.get("x-client-address") or (request.client.host if request.client else "unknown")


class LoginLimiter:
    """Failed sign-ins per client address in a sliding window, in memory per replica."""

    def __init__(self, limit: int = 5, window_seconds: float = 60.0) -> None:
        self.limit = limit
        self.window = window_seconds
        self._failures: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, address: str, now: float) -> deque[float]:
        failures = self._failures.setdefault(address, deque())
        while failures and failures[0] <= now - self.window:
            failures.popleft()
        return failures

    def retry_after(self, address: str, now: float | None = None) -> int:
        """Seconds until the address may try again; 0 when it is not blocked."""
        now = now if now is not None else time.monotonic()
        with self._lock:
            failures = self._recent(address, now)
            if len(failures) < self.limit:
                return 0
            return max(1, int(failures[0] + self.window - now) + 1)

    def failed(self, address: str, now: float | None = None) -> None:
        now = now if now is not None else time.monotonic()
        with self._lock:
            self._recent(address, now).append(now)
            # Addresses that stopped trying are forgotten, so the map cannot grow without bound
            if len(self._failures) > 10000:
                for key in [k for k, v in self._failures.items() if not v or v[-1] <= now - self.window]:
                    del self._failures[key]

    def succeeded(self, address: str) -> None:
        with self._lock:
            self._failures.pop(address, None)


login_limiter = LoginLimiter()


# ---------------------------------------------------------------- dependencies ---------------


def require_admin_enabled() -> None:
    if not settings.admin_enabled:
        raise HTTPException(status_code=404, detail="Not Found")


def require_csrf_header(request: Request) -> None:
    """A cross-site form cannot set a custom header; together with SameSite=Strict this stops CSRF."""
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get(CSRF_HEADER) != "1":
        raise HTTPException(status_code=403, detail=f"admin requests need the header {CSRF_HEADER}: 1")


def require_admin(request: Request) -> AdminSession:
    session = read_session(request.cookies.get(COOKIE))
    if session is None:
        raise HTTPException(status_code=401, detail="sign in to the admin portal")
    return session
