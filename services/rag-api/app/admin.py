"""Admin portal API, under /v1/admin (docs/admin-portal.md).

POST /login, /logout; GET /me        sign-in with the shared password and a display name
GET  /overview                        counts for the dashboard tiles
GET  /tickets, /tickets/{ref}         tickets with filters, one ticket with its events, SLA and actions
POST /tickets/{ref}/decision          approve or reject; the workflow fulfils and updates the Slack card
POST /tickets/{ref}/cancel, /fulfil   cancel an open ticket; mark an approved one fulfilled
PATCH /tickets/{ref}                  change the priority or the category
POST /tickets/{ref}/message           a notice to the requester, spoken by the avatar
GET  /conversations, /conversations/{id}   full-text search and filters; one with messages, notices, tickets
POST /conversations/{id}/archive      the chat's archive path, attributed to the admin
GET  /conversations/{id}/export       the transcript as Markdown or plain text
GET  /conversations/{id}/archives/{archive_id}/download   the transcript as it was archived
DELETE /conversations/{id}            messages, notices, archive records and the archived copy
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
from fastapi.responses import PlainTextResponse, StreamingResponse

from . import archives, audit, auth, conversations, events, gdocs, stream, tickets
from .config import settings
from .schemas import (
    ActivityPage,
    AdminLogin,
    AdminMe,
    AdminTicketDetail,
    AuditPage,
    ConversationDetail,
    ConversationPage,
    TicketActionResult,
    TicketDecision,
    TicketEdit,
    TicketMessage,
    TicketNote,
    TicketPage,
)

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


@router.get("/overview")
async def overview(_: Admin):
    counts = await asyncio.to_thread(tickets.dashboard)
    recent = await asyncio.to_thread(events.recent, limit=10)
    today = await asyncio.to_thread(conversations.today)
    return {
        **counts,
        "conversations_today": today,
        "recent_activity": recent,
        "integrations": {"slack": settings.slack_enabled, "google_docs": gdocs.configured()},
    }


# ---------------------------------------------------------------- tickets --------------------


def _snapshot(ticket) -> dict:
    return {k: getattr(ticket, k) for k in ("status", "priority", "category", "approver", "decision_note")}


@router.get("/tickets", response_model=TicketPage)
async def list_tickets(
    _: Admin,
    status: str | None = None,
    category: str | None = None,
    priority: str | None = None,
    requester: str | None = None,
    channel: str | None = None,
    q: str | None = None,
    since: Annotated[datetime | None, Query(alias="from")] = None,
    until: Annotated[datetime | None, Query(alias="to")] = None,
    order: Annotated[str, Query(pattern="^(newest|oldest)$")] = "newest",
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Limit = 50,
):
    items, total = await asyncio.to_thread(
        tickets.search, status, category, priority, requester, channel, q, since, until, order, page, limit
    )
    return TicketPage(items=items, total=total, page=page, limit=limit)


@router.get("/tickets/{ref}", response_model=AdminTicketDetail)
async def ticket_detail(ref: str, _: Admin):
    return await asyncio.to_thread(tickets.detail, ref)


async def _acted(ref: str, before, session: auth.AdminSession, request: Request, action: str, extra=None):
    after = await asyncio.to_thread(tickets.detail, ref)
    await asyncio.to_thread(
        audit.record,
        session.name,
        action,
        request,
        "ticket",
        after.ticket_ref,
        _snapshot(before),
        {**_snapshot(after), **(extra or {})},
    )
    return after


@router.post("/tickets/{ref}/decision", response_model=TicketActionResult)
async def ticket_decision(ref: str, data: TicketDecision, session: Admin, request: Request):
    before = await asyncio.to_thread(tickets.get, ref)
    _, notified = await asyncio.to_thread(tickets.decide, ref, data.decision, session.name, data.note)
    action = "ticket.approve" if data.decision == "approved" else "ticket.reject"
    after = await _acted(ref, before, session, request, action, {"note": data.note})
    return TicketActionResult(ticket=after, workflow_notified=notified)


@router.post("/tickets/{ref}/cancel", response_model=TicketActionResult)
async def ticket_cancel(ref: str, data: TicketNote, session: Admin, request: Request):
    before = await asyncio.to_thread(tickets.get, ref)
    _, notified = await asyncio.to_thread(tickets.cancel, ref, session.name, data.note)
    after = await _acted(ref, before, session, request, "ticket.cancel", {"note": data.note})
    return TicketActionResult(ticket=after, workflow_notified=notified)


@router.post("/tickets/{ref}/fulfil", response_model=TicketActionResult)
async def ticket_fulfil(ref: str, data: TicketNote, session: Admin, request: Request):
    before = await asyncio.to_thread(tickets.get, ref)
    await asyncio.to_thread(tickets.mark_fulfilled, ref, session.name, data.note)
    after = await _acted(ref, before, session, request, "ticket.fulfil", {"note": data.note})
    return TicketActionResult(ticket=after)


@router.patch("/tickets/{ref}", response_model=TicketActionResult)
async def ticket_edit(ref: str, data: TicketEdit, session: Admin, request: Request):
    before = await asyncio.to_thread(tickets.get, ref)
    await asyncio.to_thread(tickets.edit, ref, session.name, data.priority, data.category, data.note)
    after = await _acted(ref, before, session, request, "ticket.edit", {"note": data.note})
    return TicketActionResult(ticket=after)


@router.post("/tickets/{ref}/message", response_model=TicketActionResult)
async def ticket_message(ref: str, data: TicketMessage, session: Admin, request: Request):
    before = await asyncio.to_thread(tickets.get, ref)
    await asyncio.to_thread(tickets.message, ref, session.name, data.text)
    after = await _acted(ref, before, session, request, "ticket.message", {"text": data.text})
    return TicketActionResult(ticket=after)


# ---------------------------------------------------------------- conversations --------------


@router.get("/conversations", response_model=ConversationPage)
async def list_conversations(
    _: Admin,
    q: str | None = None,
    user: str | None = None,
    channel: Annotated[str | None, Query(pattern="^(chat|voice|system)$")] = None,
    since: Annotated[datetime | None, Query(alias="from")] = None,
    until: Annotated[datetime | None, Query(alias="to")] = None,
    has_ticket: bool | None = None,
    archived: bool | None = None,
    blocked: bool | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Limit = 50,
):
    items, total = await asyncio.to_thread(
        conversations.search, q, user, channel, since, until, has_ticket, archived, blocked, page, limit
    )
    return ConversationPage(items=items, total=total, page=page, limit=limit)


@router.get("/conversations/{session_id}", response_model=ConversationDetail)
async def conversation_detail(session_id: str, _: Admin):
    return await asyncio.to_thread(conversations.detail, session_id)


@router.post("/conversations/{session_id}/archive")
async def conversation_archive(session_id: str, session: Admin, request: Request):
    await asyncio.to_thread(conversations.detail, session_id)  # 404 for an unknown conversation
    result = await asyncio.to_thread(archives.request, session_id, session.name)
    await asyncio.to_thread(
        audit.record, session.name, "conversation.archive", request, "conversation", session_id, None, result
    )
    return {"session_id": session_id, **result}


def _download(filename: str, text: str, media_type: str) -> PlainTextResponse:
    return PlainTextResponse(
        text, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@router.get("/conversations/{session_id}/export")
async def conversation_export(
    session_id: str,
    session: Admin,
    request: Request,
    format: Annotated[str, Query(pattern="^(md|txt)$")] = "md",
):
    filename, text = await asyncio.to_thread(conversations.export, session_id, format)
    await asyncio.to_thread(
        audit.record,
        session.name,
        "conversation.export",
        request,
        "conversation",
        session_id,
        None,
        {"format": format},
    )
    return _download(filename, text, "text/markdown" if format == "md" else "text/plain")


@router.get("/conversations/{session_id}/archives/{archive_id}/download")
async def archive_download(session_id: str, archive_id: int, session: Admin, request: Request):
    archive = await asyncio.to_thread(archives.transcript, session_id, archive_id)
    if archive is None or not archive["transcript"]:
        raise HTTPException(status_code=404, detail="archive not found")
    await asyncio.to_thread(
        audit.record,
        session.name,
        "conversation.export",
        request,
        "conversation",
        session_id,
        None,
        {"archive_id": archive_id},
    )
    return _download(
        f"{archive['title'].replace(' ', '-').replace(':', '')}.txt", archive["transcript"], "text/plain"
    )


@router.delete("/conversations/{session_id}")
async def conversation_delete(session_id: str, session: Admin, request: Request):
    result = await asyncio.to_thread(conversations.delete, session_id, session.name)
    await asyncio.to_thread(
        audit.record,
        session.name,
        "conversation.delete",
        request,
        "conversation",
        session_id,
        {"messages": result["messages"], "archives": result["archives"]},
        result,
    )
    return result


# ---------------------------------------------------------------- activity and audit ---------


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
