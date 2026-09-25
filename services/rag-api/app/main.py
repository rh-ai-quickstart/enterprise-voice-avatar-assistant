"""RAG API.

Public (the chat UI and the voice agent; the frontend's nginx forwards only these and /v1/admin):
GET  /healthz, /readyz
POST /v1/chat                       grounded answer with citations, memory, guardrails (text or voice mode)
POST /v1/chat/stream                the same answer as newline-delimited JSON while it is generated
GET  /v1/sessions/{id}/messages     conversation history
GET  /v1/sessions/{id}/notifications, POST .../notifications/ack   outcome notices for the conversation
POST /v1/sessions/{id}/archive       trigger the transcript archival workflow (WF5)
DELETE /v1/sessions/{id}            forget a conversation
GET  /v1/voice/token                LiveKit token for the browser (face_id picks the avatar face)
GET  /v1/voice/faces                avatar faces to choose from, with the voice each one speaks with
GET  /v1/voice/faces/{id}/poster    still image of a face for the picker (cut from the Tavus thumbnail video)
GET  /v1/info                       active models and providers (for the diagnostics panel)

Admin portal, with the session cookie: /v1/admin/* (admin.py)

Internal, with Authorization: Bearer $INTERNAL_API_TOKEN (n8n, the ingestion service, scripts):
POST /v1/search                     retrieval only
GET  /v1/sessions/{id}/transcript   plain-text transcript (for archival workflows)
GET/PUT/DELETE /v1/users/{id}/memory   long-lived facts injected into prompts
POST /v1/classify                   document type and fields (text, or bucket/key via the ingestion service)
POST /v1/tickets, GET /v1/tickets, GET /v1/tickets/{ref}, PATCH /v1/tickets/{ref}
GET  /v1/tickets/stale              tickets past SLA thresholds (for escalation workflows)
POST /v1/tickets/stale/escalate     bump priority of a stale ticket
POST /v1/requests                   service request intake: classify, create ticket, notify n8n
GET  /v1/knowledge-gaps/digest      aggregated unanswered questions over a time window
POST /v1/internal/events            record an activity event the RAG API cannot see itself
PATCH /v1/internal/archives/{id}    what re-ingestion of an archived transcript came to (WF5)
"""

import asyncio
import json
import logging
import threading
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse

from . import (
    admin,
    archives,
    auth,
    classify,
    clients,
    conversations,
    events,
    faces,
    gdocs,
    knowledge_gaps,
    memory,
    notifications,
    rag,
    retrieval,
    stream,
    tickets,
    voice,
)
from .config import settings
from .schemas import (
    ActivityEventIn,
    ArchiveResult,
    ChatRequest,
    ChatResponse,
    ClassifyRequest,
    ClassifyResponse,
    Message,
    Notification,
    NotificationAck,
    RequestIntake,
    RequestIntakeResponse,
    SearchRequest,
    SearchResponse,
    Ticket,
    TicketCreate,
    TicketUpdate,
    UserMemoryItem,
    VoiceFace,
    VoiceFacesResponse,
    VoiceTokenResponse,
)

log = logging.getLogger("rag")


@asynccontextmanager
async def lifespan(_: FastAPI):
    logging.basicConfig(
        level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    log.info(
        "rag-api starting; llm=%s (%s) guardrails=%s qdrant=%s",
        settings.llm_model,
        settings.llm_base_url,
        settings.guardrails_provider,
        settings.qdrant_url,
    )
    if not settings.internal_api_token:
        log.warning(
            "INTERNAL_API_TOKEN is empty: internal routes are open to anyone who can reach this service"
        )
    if settings.admin_enabled and not auth.admin_configured():
        log.warning(
            "admin portal enabled but ADMIN_PASSWORD or ADMIN_SESSION_SECRET is empty: sign-in answers 503"
        )
    await asyncio.to_thread(memory.init_schema)
    if settings.admin_enabled and memory.enabled():
        stream.hub.start(asyncio.get_running_loop())
    if faces.catalog() and settings.tavus_api_key:
        threading.Thread(target=faces.warm_posters, name="face-posters", daemon=True).start()
    yield
    await asyncio.to_thread(stream.hub.stop)


app = FastAPI(title="Enterprise voice avatar assistant - RAG API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(admin.router)
# Routes that are not public: n8n, the ingestion service and the scripts send the internal token
INTERNAL = [Depends(auth.require_internal_token)]


@app.exception_handler(tickets.TicketError)
@app.exception_handler(conversations.ConversationError)
async def domain_error(_, exc: tickets.TicketError | conversations.ConversationError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
async def readyz():
    problems: dict[str, str] = {}
    checks = {"qdrant": lambda: clients.qdrant().get_collections()}
    if memory.enabled():
        checks["database"] = lambda: clients.db().close()
    for name, check in checks.items():
        try:
            await asyncio.to_thread(check)
        except Exception as exc:  # noqa: BLE001
            problems[name] = str(exc)[:200]
    if problems:
        return JSONResponse(status_code=503, content={"status": "not ready", "problems": problems})
    return {"status": "ready"}


@app.get("/v1/info")
def info():
    return {
        "llm": {"model": settings.llm_model, "base_url": settings.llm_base_url},
        "embeddings": {"model": settings.embeddings_model, "base_url": settings.embeddings_base_url},
        "guardrails": {"provider": settings.guardrails_provider, "model": settings.guardrails_model},
        "retrieval": {
            "collection": settings.qdrant_collection,
            "top_k": settings.rag_top_k,
            "min_score": settings.rag_min_score,
        },
        "memory": memory.enabled(),
        "archival": {"google_docs": gdocs.configured()},
        "voice": {
            "livekit_url": settings.livekit_public_url or settings.livekit_url,
            "avatar_provider": settings.avatar_provider,
            "faces": len(faces.catalog()),
        },
    }


@app.post("/v1/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    return await asyncio.to_thread(rag.answer, request)


@app.post("/v1/chat/stream")
def chat_stream(request: ChatRequest):
    """The answer as newline-delimited JSON while it is generated: {"type": "delta", "text": ...}
    lines, then one {"type": "final", ...ChatResponse}. The voice agent starts speaking on the
    first sentence instead of waiting for the whole answer."""

    def lines():
        for kind, payload in rag.answer_stream(request):
            if kind == "delta":
                yield json.dumps({"type": "delta", "text": payload}) + "\n"
            else:
                # mode="json": the final carries the ticket when the message was a request, and
                # its timestamps are datetimes that json.dumps cannot serialise otherwise
                yield json.dumps({"type": "final", **payload.model_dump(mode="json")}) + "\n"

    return StreamingResponse(lines(), media_type="application/x-ndjson")


@app.post("/v1/search", response_model=SearchResponse, dependencies=INTERNAL)
async def search(request: SearchRequest):
    hits = await asyncio.to_thread(retrieval.search, request.query, request.top_k, request.min_score)
    return SearchResponse(hits=[h.to_citation(n) for n, h in enumerate(hits, start=1)])


@app.get("/v1/sessions/{session_id}/messages", response_model=list[Message])
async def session_messages(session_id: str):
    return await asyncio.to_thread(memory.messages, session_id)


@app.get("/v1/sessions/{session_id}/notifications", response_model=list[Notification])
async def session_notifications(session_id: str):
    """Undelivered outcome notices (ticket decisions) for the session; newest per ticket."""
    return await asyncio.to_thread(notifications.pending, session_id)


@app.post("/v1/sessions/{session_id}/notifications/ack")
async def ack_notifications(session_id: str, data: NotificationAck):
    await asyncio.to_thread(notifications.ack, session_id, data.ids)
    return {"acknowledged": data.ids}


@app.post("/v1/sessions/{session_id}/archive", status_code=202)
async def archive_session(session_id: str):
    """Record the archive, create the Google Doc (when that integration is on) and hand the session
    to the archival workflow for re-ingestion and the Slack notice."""
    result = await asyncio.to_thread(archives.request, session_id)
    return {"session_id": session_id, **result}


@app.get("/v1/sessions/{session_id}/transcript", response_class=PlainTextResponse, dependencies=INTERNAL)
async def session_transcript(session_id: str):
    return await asyncio.to_thread(memory.transcript, session_id)


@app.delete("/v1/sessions/{session_id}")
async def delete_session(session_id: str):
    await asyncio.to_thread(memory.clear, session_id)
    return {"deleted": session_id}


@app.get("/v1/users/{user_id}/memory", dependencies=INTERNAL)
async def get_user_memory(user_id: str):
    return await asyncio.to_thread(memory.get_user_memory, user_id)


@app.put("/v1/users/{user_id}/memory", dependencies=INTERNAL)
async def put_user_memory(user_id: str, item: UserMemoryItem):
    await asyncio.to_thread(memory.set_user_memory, user_id, item.key, item.value)
    return {"user_id": user_id, item.key: item.value}


@app.delete("/v1/users/{user_id}/memory/{key}", dependencies=INTERNAL)
async def delete_user_memory(user_id: str, key: str):
    await asyncio.to_thread(memory.delete_user_memory, user_id, key)
    return {"deleted": key}


@app.post("/v1/classify", response_model=ClassifyResponse, dependencies=INTERNAL)
async def classify_document(request: ClassifyRequest):
    try:
        return await asyncio.to_thread(classify.classify_document, request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/v1/tickets", response_model=Ticket, status_code=201, dependencies=INTERNAL)
async def create_ticket(data: TicketCreate):
    return await asyncio.to_thread(tickets.create, data)


@app.get("/v1/tickets", response_model=list[Ticket], dependencies=INTERNAL)
async def list_tickets(status: str | None = None, limit: int = Query(default=50, ge=1, le=500)):
    return await asyncio.to_thread(tickets.list_tickets, status, limit)


@app.get("/v1/tickets/stale", dependencies=INTERNAL)
async def stale_tickets(
    reminder_minutes: int | None = None,
    escalation_minutes: int | None = None,
):
    return await asyncio.to_thread(knowledge_gaps.stale_tickets, reminder_minutes, escalation_minutes)


@app.post("/v1/tickets/stale/escalate", dependencies=INTERNAL)
async def escalate_stale_ticket(ticket_ref: str = Query(...), current_priority: str = Query(...)):
    result = await asyncio.to_thread(knowledge_gaps.escalate_ticket, ticket_ref, current_priority)
    if result is None:
        raise HTTPException(status_code=422, detail="already at maximum priority")
    return result


@app.get("/v1/tickets/{ref}", response_model=Ticket, dependencies=INTERNAL)
async def get_ticket(ref: str):
    return await asyncio.to_thread(tickets.get, ref)


@app.patch("/v1/tickets/{ref}", response_model=Ticket, dependencies=INTERNAL)
async def update_ticket(ref: str, data: TicketUpdate):
    return await asyncio.to_thread(tickets.update, ref, data)


@app.post("/v1/requests", response_model=RequestIntakeResponse, status_code=201, dependencies=INTERNAL)
async def request_intake(request: RequestIntake):
    ticket, classification, notified = await asyncio.to_thread(tickets.intake, request)
    return RequestIntakeResponse(ticket=ticket, classification=classification, notified=notified)


@app.get("/v1/knowledge-gaps/digest", dependencies=INTERNAL)
async def knowledge_gap_digest(hours: int = Query(default=24, ge=1, le=720)):
    return await asyncio.to_thread(knowledge_gaps.digest, hours)


@app.post("/v1/internal/events", status_code=201, dependencies=INTERNAL)
async def internal_event(data: ActivityEventIn):
    """An event the workflows saw (ingestion results, SLA reminders, Slack failures, the digest)."""
    event_id = await asyncio.to_thread(
        events.record,
        data.kind,
        data.title,
        severity=data.severity,
        detail=data.detail,
        ref_type=data.ref_type,
        ref_id=data.ref_id,
        actor=data.actor,
        source=data.source,
        data=data.data,
    )
    return {"id": event_id}


@app.patch("/v1/internal/archives/{archive_id}", dependencies=INTERNAL)
async def internal_archive_result(archive_id: int, data: ArchiveResult):
    archive = await asyncio.to_thread(
        archives.record_result, archive_id, data.status, data.object_key, data.doc_id, data.job_id, data.error
    )
    if archive is None:
        raise HTTPException(status_code=404, detail="archive not found")
    return archive


@app.get("/v1/voice/token", response_model=VoiceTokenResponse)
def voice_token(
    session_id: str | None = None,
    identity: str | None = None,
    name: str | None = None,
    face_id: str | None = None,
):
    session_id = session_id or uuid.uuid4().hex
    identity = identity or f"user-{uuid.uuid4().hex[:8]}"
    room = voice.room_for_session(session_id)
    attributes: dict[str, str] = {}
    if face_id:
        if faces.catalog() and faces.resolve(face_id) is None:
            raise HTTPException(
                status_code=400,
                detail=f"unknown avatar face {face_id!r}; GET /v1/voice/faces lists the choices",
            )
        attributes[faces.FACE_ATTRIBUTE] = face_id
    return VoiceTokenResponse(
        token=voice.mint_token(identity, room, name, attributes),
        url=settings.livekit_public_url or settings.livekit_url,
        room=room,
        identity=identity,
        session_id=session_id,
        face_id=face_id or None,
    )


@app.get("/v1/voice/faces", response_model=VoiceFacesResponse)
async def voice_faces():
    items = await asyncio.to_thread(faces.enriched)
    return VoiceFacesResponse(
        provider=settings.avatar_provider,
        default=faces.default_id(),
        faces=[
            VoiceFace(
                id=f.id,
                name=f.name,
                gender=f.gender,
                voice=faces.voice_for(f),
                thumbnail_url=f.thumbnail_url,
                poster_url=faces.poster_url(f),
            )
            for f in items
        ],
    )


@app.get("/v1/voice/faces/{face_id}/poster")
async def voice_face_poster(face_id: str):
    data = await asyncio.to_thread(faces.poster, face_id)
    if data is None:
        raise HTTPException(status_code=404, detail="no poster for this face")
    return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})
