"""The grounded chat: guardrails, retrieval, prompt assembly, generation, citations, memory."""

import logging
import re
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from . import clients, guardrails, intent, knowledge_gaps, memory, retrieval, tickets
from .config import NO_ACTIONS, VOICE_STYLE, settings
from .retrieval import Hit
from .schemas import ChatRequest, ChatResponse, Citation, GuardrailInfo, RequestIntake, Ticket

log = logging.getLogger("rag.chat")
MARKER_RE = re.compile(r"\[(\d{1,2})\]")
# Where a sentence ends in generated text (the voice agent splits the same way)
SENTENCE_END_RE = re.compile(r"[.!?][*_`)\]]*\s+")
# The answer itself saying it logged, filed or ordered something, or will ("I've logged…", "I'll go
# ahead and order…", "let me log…"). Only the ticket path files requests; NO_ACTIONS forbids this,
# but a small model still copies the ticket path's confirmations from the history now and then.
CLAIM_RE = re.compile(
    r"\b(?:I(?:'ve|'ll|'m| have| will| am)|let me)\s+(?:just\s+|now\s+|already\s+|also\s+)?"
    r"(?:(?:go|gone|going) ahead and\s+|going to\s+)?"
    r"(?:log|logged|logging|file|filed|filing|submit|submitted|submitting|create|created|creating|place|placed|"
    r"placing|order|ordered|ordering|raise|raised|raising|open|opened|opening|book|booked|booking)\b",
    re.IGNORECASE,
)
NO_CLAIM = (
    "I haven't logged a request for that. "
    'If you want one, say "Please log a request for" followed by what you need.'
)


def build_context(hits: list[Hit]) -> str:
    parts: list[str] = []
    total = 0
    for n, hit in enumerate(hits, start=1):
        where = f"source: {hit.source}" + (f", page {hit.page}" if hit.page else "")
        block = f"[{n}] ({where})\n{hit.text.strip()}"
        if total + len(block) > settings.max_context_chars:
            break
        parts.append(block)
        total += len(block)
    return "\n\n".join(parts)


def first_name(user_name: str | None) -> str | None:
    """The first word of a display name, for greetings; None for empty or placeholder names."""
    if not user_name:
        return None
    first = user_name.strip().split()[0] if user_name.strip() else ""
    return first or None


def build_messages(
    question: str,
    hits: list[Hit],
    history: list[dict[str, str]],
    mode: str,
    user_memory: dict[str, str] | None = None,
    user_name: str | None = None,
) -> list[dict[str, str]]:
    system = settings.system_prompt.format(assistant_name=settings.assistant_name) + NO_ACTIONS
    if mode == "voice":
        system += VOICE_STYLE
    name = first_name(user_name)
    if name:
        system += (
            f"\n\nYou are talking to {user_name.strip()}. Address them as {name} where it reads naturally, "
            "for example in a greeting or when confirming something, not in every sentence."
        )
    if user_memory:
        facts = "\n".join(f"- {k}: {v}" for k, v in user_memory.items())
        system += f"\n\nWhat you remember about this user:\n{facts}"
    context = build_context(hits) if hits else "(no relevant company documents were found)"
    system += f"\n\nContext:\n{context}"
    return [{"role": "system", "content": system}, *history, {"role": "user", "content": question}]


def cited_numbers(answer: str, max_n: int) -> set[int]:
    return {int(m) for m in MARKER_RE.findall(answer or "") if 1 <= int(m) <= max_n}


def retrieval_query(message: str, history: list[dict[str, str]]) -> str:
    """Short follow-ups carry little meaning alone; prepend the previous user question for retrieval only."""
    if len(message.split()) <= settings.followup_max_words:
        previous = [m["content"] for m in history if m.get("role") == "user"]
        if previous:
            return f"{previous[-1]} {message}"
    return message


def sentences(text: str) -> list[str]:
    """The text cut after each sentence end, whitespace kept, so the pieces join back into the text."""
    pieces, start = [], 0
    for match in SENTENCE_END_RE.finditer(text):
        pieces.append(text[start : match.end()])
        start = match.end()
    if start < len(text):
        pieces.append(text[start:])
    return pieces


def false_claim(sentence: str, known_refs: set[str]) -> bool:
    """The sentence says the answer itself logged or ordered something, and names no request this
    conversation really filed ("I've logged your request REQ-000002" about a real one is true)."""
    if not CLAIM_RE.search(sentence):
        return False
    refs = set(intent.TICKET_REF_RE.findall(sentence))
    return not refs or not refs <= known_refs


def drop_false_claims(text: str, known_refs: set[str]) -> str:
    """The answer up to its first false claim, which becomes NO_CLAIM (what follows such a claim
    usually builds on it); unchanged when there is none."""
    kept: list[str] = []
    for sentence in sentences(text):
        if false_claim(sentence, known_refs):
            kept.append(NO_CLAIM)
            break
        kept.append(sentence)
    return "".join(kept).strip()


def request_reply(ticket: Ticket, user_name: str | None = None) -> str:
    """What the assistant says right after filing a request from the conversation."""
    name = first_name(user_name)
    head = f"{name + ', ' if name else ''}I've logged your request {ticket.ticket_ref}: {ticket.title.rstrip('.')}. "
    detail = f"It's a {ticket.category or 'general'} request with {ticket.priority} priority"
    if ticket.status == "pending_approval":
        return head + detail + " and needs approval. I'll let you know here as soon as it's decided."
    return (
        head
        + detail
        + ". No approval is needed, so it's being fulfilled now, and I'll confirm when it's done."
    )


def file_request(request: ChatRequest, session_id: str, info: GuardrailInfo) -> ChatResponse:
    """The message was a service request: classify it, open a ticket, start the approval workflow."""
    try:
        ticket, _classification, notified = tickets.intake(
            RequestIntake(
                text=request.message,
                session_id=session_id,
                user_id=request.user_id,
                requester=request.user_name or None,  # the display name shows on the approval card
                channel="voice" if request.mode == "voice" else "chat",
            )
        )
    except tickets.TicketError as exc:
        log.warning("could not file a request for session %s: %s", session_id, exc.detail)
        text = "I understood that as a service request, but I can't file tickets right now. Please try again later."
        memory.append(session_id, "user", request.message)
        memory.append(session_id, "assistant", text)
        return ChatResponse(
            session_id=session_id, answer=text, citations=[], guardrail=info, model=settings.llm_model
        )
    text = request_reply(ticket, request.user_name)
    memory.append(session_id, "user", request.message)
    memory.append(session_id, "assistant", text)
    log.info(
        "session=%s filed %s (%s, notified n8n=%s)", session_id, ticket.ticket_ref, ticket.status, notified
    )
    return ChatResponse(
        session_id=session_id,
        answer=text,
        citations=[],
        guardrail=info,
        model=settings.llm_model,
        ticket=ticket,
    )


@dataclass
class Prepared:
    """A question that passed the input guardrail, with its context ready for the model."""

    session_id: str
    info: GuardrailInfo
    hits: list[Hit]
    messages: list[dict[str, str]]
    max_tokens: int
    known_refs: set[str]  # requests this conversation filed (REQ- references in its history)


def _prepare(request: ChatRequest) -> ChatResponse | Prepared:
    """Guardrail, intent and retrieval; returns a finished reply for blocked messages and requests."""
    session_id = request.session_id or uuid.uuid4().hex
    memory.ensure_conversation(session_id, request.user_id, request.mode)
    info = GuardrailInfo(provider=settings.guardrails_provider)

    verdict = guardrails.check_input(request.message)
    if not verdict.allowed:
        info.input_flagged, info.category = True, verdict.category
        memory.append(session_id, "user", request.message, blocked=True)
        memory.append(session_id, "assistant", settings.blocked_message, blocked=True)
        return ChatResponse(
            session_id=session_id,
            answer=settings.blocked_message,
            citations=[],
            blocked=True,
            guardrail=info,
            model=settings.llm_model,
        )

    history = memory.history(session_id, settings.history_turns * 2)
    previous_assistant = next((m["content"] for m in reversed(history) if m.get("role") == "assistant"), None)
    with ThreadPoolExecutor(max_workers=2) as pool:
        intent_future = pool.submit(intent.detect, request.message, previous_assistant)
        hits_future = pool.submit(
            retrieval.search, retrieval_query(request.message, history), top_k=request.top_k
        )
        kind = intent_future.result()
        hits = hits_future.result()
    log.info("session=%s intent=%s", session_id, kind)
    if kind == "request":
        return file_request(request, session_id, info)

    top_score = max((h.score for h in hits), default=0.0)
    knowledge_gaps.record(session_id, request.message, top_score, len(hits))

    user_memory = memory.get_user_memory(request.user_id) if request.user_id else {}
    messages = build_messages(request.message, hits, history, request.mode, user_memory, request.user_name)
    max_tokens = settings.voice_max_tokens if request.mode == "voice" else settings.answer_max_tokens
    known_refs = {
        ref
        for m in history
        if m.get("role") == "assistant"
        for ref in intent.TICKET_REF_RE.findall(m["content"])
    }
    return Prepared(session_id, info, hits, messages, max_tokens, known_refs)


def _finish(request: ChatRequest, prep: Prepared, text: str) -> ChatResponse:
    """Output guardrail, citations and memory for a generated answer."""
    blocked = False
    out = guardrails.check_output(request.message, text)
    if not out.allowed:
        prep.info.output_flagged, prep.info.category = True, out.category
        text, blocked = settings.blocked_message, True

    used = cited_numbers(text, len(prep.hits))
    citations: list[Citation] = [
        hit.to_citation(n, used=n in used) for n, hit in enumerate(prep.hits, start=1)
    ]

    memory.append(prep.session_id, "user", request.message)
    memory.append(prep.session_id, "assistant", text, citations=citations, blocked=blocked)
    log.info("session=%s hits=%d cited=%s blocked=%s", prep.session_id, len(prep.hits), sorted(used), blocked)
    return ChatResponse(
        session_id=prep.session_id,
        answer=text,
        citations=citations,
        blocked=blocked,
        guardrail=prep.info,
        model=settings.llm_model,
    )


def answer(request: ChatRequest) -> ChatResponse:
    prep = _prepare(request)
    if isinstance(prep, ChatResponse):
        return prep
    completion = clients.llm().chat.completions.create(
        model=settings.llm_model,
        messages=prep.messages,
        temperature=settings.llm_temperature,
        max_tokens=prep.max_tokens,
    )
    text = (completion.choices[0].message.content or "").strip()
    checked = drop_false_claims(text, prep.known_refs)
    if checked != text:
        log.warning("session=%s the answer claimed an action; replaced from: %r", prep.session_id, text[:200])
    return _finish(request, prep, checked)


def answer_stream(request: ChatRequest) -> Iterator[tuple[str, str | ChatResponse]]:
    """The same answer as `answer`, handed out while it is generated: ("delta", text) pieces,
    then ("final", ChatResponse) once the output guardrail, citations and memory are settled.
    Blocked messages and service requests produce only the final."""
    prep = _prepare(request)
    if isinstance(prep, ChatResponse):
        yield "final", prep
        return
    parts: list[str] = []
    stream = clients.llm().chat.completions.create(
        model=settings.llm_model,
        messages=prep.messages,
        temperature=settings.llm_temperature,
        max_tokens=prep.max_tokens,
        stream=True,
    )
    # handed out a sentence at a time, each checked before it can be spoken (the voice agent speaks
    # whole sentences anyway); a false claim ends the answer with NO_CLAIM
    buffer, claimed = "", None
    for chunk in stream:
        delta = chunk.choices[0].delta.content if getattr(chunk, "choices", None) else None
        if not delta:
            continue
        buffer += delta
        *done, buffer = sentences(buffer) if SENTENCE_END_RE.search(buffer) else [buffer]
        for sentence in done:
            if false_claim(sentence, prep.known_refs):
                claimed = sentence
                break
            parts.append(sentence)
            yield "delta", sentence
        if claimed is not None:
            break
    if claimed is None and buffer.strip() and false_claim(buffer, prep.known_refs):
        claimed = buffer
    if claimed is not None:
        getattr(stream, "close", lambda: None)()
        log.warning("session=%s the answer claimed an action; replaced: %r", prep.session_id, claimed[:200])
        parts.append(NO_CLAIM)
        yield "delta", NO_CLAIM
    elif buffer:
        parts.append(buffer)
        yield "delta", buffer
    yield "final", _finish(request, prep, "".join(parts).strip())
