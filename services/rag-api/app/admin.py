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
GET  /knowledge-gaps                  open (or resolved, dismissed) gaps, grouped by meaning or listed
POST /knowledge-gaps/resolve          resolve, dismiss or reopen gaps; /knowledge-gaps/{id}/retest retrieves again
GET  /documents, /documents/{doc_id}  indexed and classified documents; one with its ingestion jobs
POST /documents/upload                multipart file and bucket (documents or inbox)
POST /documents/{doc_id}/reingest, DELETE /documents/{doc_id}   through the ingestion service
GET  /ingestion/jobs                  recent ingestion jobs
GET  /integrations                    each integration's state; POST /integrations/{name}/test runs a live test
GET  /activity                        the activity feed, newest first
GET  /audit                           the audit log, newest first
GET  /stream                          server-sent events for live updates (stream.py)

Every route returns 404 when ADMIN_ENABLED is false, needs the session cookie (except login), and
every non-GET route needs the X-Admin-Request: 1 header and writes an audit entry.
"""

import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import PlainTextResponse, StreamingResponse

from . import (
    archives,
    audit,
    auth,
    conversations,
    documents,
    events,
    integrations,
    knowledge_gaps,
    stream,
    tickets,
)
from .config import settings
from .schemas import (
    ActivityPage,
    AdminLogin,
    AdminMe,
    AdminTicketDetail,
    AuditPage,
    ConversationDetail,
    ConversationPage,
    GapResolve,
    TicketActionResult,
    TicketDecision,
    TicketEdit,
    TicketMessage,
    TicketNote,
    TicketPage,
)

log = logging.getLogger("rag.admin")
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
    # Grouped with the embeddings already stored: the overview never waits for the embeddings service
    week = await asyncio.to_thread(
        knowledge_gaps.grouped, "open", datetime.now(UTC) - timedelta(days=7), None, False
    )
    return {
        **counts,
        "conversations_today": today,
        "knowledge_gaps": {
            "open_week": sum(g["count"] for g in week),
            "top_groups": [{"question": g["question"], "count": g["count"]} for g in week[:3]],
        },
        "recent_activity": recent,
        "integrations": [
            {"name": i["name"], "label": i["label"], "state": i["state"]}
            for i in await asyncio.to_thread(integrations.status_all)
        ],
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
    # Only characters that are safe in the header: a quote or a non-Latin-1 character in a title or
    # an old conversation id would otherwise rename the file or fail the download
    filename = re.sub(r"[^A-Za-z0-9._-]+", "-", filename).strip("-.") or "download.txt"
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


# ---------------------------------------------------------------- knowledge gaps -------------


@router.get("/knowledge-gaps")
async def knowledge_gaps_list(
    _: Admin,
    status: Annotated[str, Query(pattern="^(open|resolved|dismissed|all)$")] = "open",
    since: Annotated[datetime | None, Query(alias="from")] = None,
    until: Annotated[datetime | None, Query(alias="to")] = None,
    group: bool = True,
):
    """Gaps in a window (the last 7 days by default), grouped by meaning unless group=false."""
    since = since or datetime.now(UTC) - timedelta(days=7)
    wanted = None if status == "all" else status
    if not group:
        items = await asyncio.to_thread(knowledge_gaps.gaps, wanted, since, until)
        return {"items": items, "total": len(items)}
    groups = await asyncio.to_thread(knowledge_gaps.grouped, wanted, since, until)
    return {
        "groups": groups,
        "total": sum(g["count"] for g in groups),
        "threshold": settings.gap_group_threshold,
        "from": since,
    }


@router.post("/knowledge-gaps/resolve")
async def knowledge_gaps_resolve(data: GapResolve, session: Admin, request: Request):
    changed = await asyncio.to_thread(knowledge_gaps.resolve, data.ids, data.status, session.name, data.note)
    action = {"resolved": "gap.resolve", "dismissed": "gap.dismiss", "open": "gap.reopen"}[data.status]
    await asyncio.to_thread(
        audit.record,
        session.name,
        action,
        request,
        "gap",
        ",".join(map(str, data.ids[:20])),
        None,
        {"ids": data.ids, "note": data.note, "changed": changed},
    )
    return {"changed": changed}


@router.post("/knowledge-gaps/{gap_id}/retest")
async def knowledge_gap_retest(gap_id: int, _: Admin):
    try:
        result = await asyncio.to_thread(knowledge_gaps.retest, gap_id)
    except Exception as exc:  # the embeddings service or Qdrant did not answer
        log.warning("re-test of knowledge gap %s failed: %s: %s", gap_id, type(exc).__name__, exc)
        raise HTTPException(
            status_code=502,
            detail=f"retrieval failed, the embeddings service or Qdrant did not answer: {type(exc).__name__}",
        ) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="knowledge gap not found")
    return result


# ---------------------------------------------------------------- documents ------------------

UPLOAD_LIMIT = 25 * 1024 * 1024  # the frontend proxy's client_max_body_size


@router.get("/documents")
async def documents_list(
    _: Admin,
    kind: Annotated[str, Query(pattern="^(all|indexed|classified)$")] = "all",
    q: str | None = None,
    bucket: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Limit = 50,
):
    items, total = await asyncio.to_thread(documents.search, kind, q, bucket, page, limit)
    return {"items": items, "total": total, "page": page, "limit": limit}


@router.get("/documents/{doc_id}")
async def document_detail(doc_id: str, _: Admin):
    return await asyncio.to_thread(documents.detail, doc_id)


@router.post("/documents/upload", status_code=201)
async def document_upload(
    session: Admin,
    request: Request,
    file: Annotated[UploadFile, File()],
    bucket: Annotated[str, Form(pattern="^(documents|inbox)$")],
):
    data = await file.read(UPLOAD_LIMIT + 1)
    if len(data) > UPLOAD_LIMIT:
        raise HTTPException(status_code=413, detail="files up to 25 MiB")
    result = await asyncio.to_thread(
        documents.upload, bucket, file.filename or "upload", data, file.content_type, session.name
    )
    await asyncio.to_thread(
        audit.record, session.name, "document.upload", request, "document", result["doc_id"], None, result
    )
    return result


@router.post("/documents/{doc_id}/reingest")
async def document_reingest(doc_id: str, session: Admin, request: Request):
    result = await asyncio.to_thread(documents.reingest, doc_id, session.name)
    await asyncio.to_thread(
        audit.record, session.name, "document.reingest", request, "document", doc_id, None, result
    )
    return result


@router.delete("/documents/{doc_id}")
async def document_delete(doc_id: str, session: Admin, request: Request):
    before = await asyncio.to_thread(documents.detail, doc_id)
    result = await asyncio.to_thread(documents.delete, doc_id, session.name)
    await asyncio.to_thread(
        audit.record,
        session.name,
        "document.delete",
        request,
        "document",
        doc_id,
        {"source_uri": before["source_uri"], "chunks": before["chunks"]},
        result,
    )
    return result


@router.get("/ingestion/jobs")
async def ingestion_jobs(_: Admin, limit: Limit = 50):
    return {"items": await asyncio.to_thread(documents.jobs, None, limit)}


# ---------------------------------------------------------------- integrations ---------------


@router.get("/integrations")
async def integrations_list(_: Admin):
    return {"items": await asyncio.to_thread(integrations.status_all)}


@router.post("/integrations/{name}/test")
async def integration_test(
    name: Annotated[str, Path(pattern="^(" + "|".join(integrations.LABELS) + ")$")],
    session: Admin,
    request: Request,
):
    result = await asyncio.to_thread(integrations.test, name, session.name)
    await asyncio.to_thread(
        audit.record,
        session.name,
        "integration.test",
        request,
        "integration",
        name,
        None,
        {"ok": result["ok"], "checks": result["checks"]},
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
