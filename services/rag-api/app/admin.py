"""Admin portal API, under /v1/admin (docs/admin-portal.md).

POST /login, /logout; GET /me        sign-in with the shared password and a display name
GET  /activity                        the activity feed, newest first
GET  /audit                           the audit log, newest first
GET  /stream                          server-sent events for live updates (stream.py)

Every route returns 404 when ADMIN_ENABLED is false, needs the session cookie (except login), and
every non-GET route needs the X-Admin-Request: 1 header and writes an audit entry.
"""

import asyncio
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse

from . import audit, auth, events, stream
from .config import settings
from .schemas import ActivityPage, AdminLogin, AdminMe, AuditPage

router = APIRouter(
    prefix="/v1/admin",
    tags=["admin"],
    dependencies=[Depends(auth.require_admin_enabled), Depends(auth.require_csrf_header)],
)
Admin = Annotated[auth.AdminSession, Depends(auth.require_admin)]
Limit = Annotated[int, Query(ge=1, le=200)]


def _me(session: auth.AdminSession) -> AdminMe:
    return AdminMe(name=session.name, expires_at=datetime.fromtimestamp(session.expires_at, UTC))


def _next(items: list[dict], limit: int) -> int | None:
    return items[-1]["id"] if len(items) == limit else None


@router.post("/login", response_model=AdminMe)
def login(data: AdminLogin, request: Request, response: Response):
    if not auth.admin_configured():
        raise HTTPException(status_code=503, detail="admin portal not configured (ADMIN_PASSWORD is empty)")
    name = auth.display_name(data.name)
    address = auth.client_address(request)
    wait = auth.login_limiter.retry_after(address)
    if wait:
        audit.record(name, "login_failed", request, after={"reason": "too many attempts"})
        raise HTTPException(
            status_code=429,
            detail=f"too many failed sign-ins; try again in {wait} seconds",
            headers={"Retry-After": str(wait)},
        )
    if not auth.password_matches(data.password):
        auth.login_limiter.failed(address)
        audit.record(name, "login_failed", request, after={"reason": "wrong password"})
        raise HTTPException(status_code=401, detail="wrong password")
    auth.login_limiter.succeeded(address)
    cookie, session = auth.sign_session(name)
    response.set_cookie(
        auth.COOKIE,
        cookie,
        max_age=session.expires_at - session.issued_at,
        httponly=True,
        secure=settings.admin_cookie_secure,
        samesite="strict",
        path="/",
    )
    audit.record(name, "login", request)
    return _me(session)


@router.post("/logout")
def logout(request: Request, response: Response):
    session = auth.read_session(request.cookies.get(auth.COOKIE))
    response.delete_cookie(
        auth.COOKIE, path="/", httponly=True, secure=settings.admin_cookie_secure, samesite="strict"
    )
    if session:
        audit.record(session.name, "logout", request)
    return {"signed_out": True}


@router.get("/me", response_model=AdminMe)
def me(session: Admin):
    return _me(session)


@router.get("/activity", response_model=ActivityPage)
async def activity(
    _: Admin,
    kind: str | None = None,
    severity: str | None = None,
    since: Annotated[datetime | None, Query(alias="from")] = None,
    until: Annotated[datetime | None, Query(alias="to")] = None,
    before_id: int | None = None,
    limit: Limit = 50,
):
    items = await asyncio.to_thread(events.recent, kind, severity, since, until, before_id, limit)
    return ActivityPage(items=items, next_before_id=_next(items, limit))


@router.get("/audit", response_model=AuditPage)
async def audit_log(
    _: Admin,
    actor: str | None = None,
    action: str | None = None,
    since: Annotated[datetime | None, Query(alias="from")] = None,
    until: Annotated[datetime | None, Query(alias="to")] = None,
    before_id: int | None = None,
    limit: Limit = 50,
):
    items = await asyncio.to_thread(audit.entries, actor, action, since, until, before_id, limit)
    return AuditPage(items=items, next_before_id=_next(items, limit))


@router.get("/stream")
def event_stream(
    session: Admin,
    last_event_id: Annotated[str | None, Header()] = None,
    last_id: Annotated[int | None, Query(description="Last-Event-ID for clients that cannot set it")] = None,
):
    if last_event_id and last_event_id.isdigit():
        last_id = int(last_event_id)
    return StreamingResponse(
        stream.stream(last_id, session.expires_at),
        media_type="text/event-stream",
        # X-Accel-Buffering: nginx passes each event on at once instead of buffering the response
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
