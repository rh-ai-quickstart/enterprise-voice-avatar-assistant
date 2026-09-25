"""Service requests as tickets with a small state machine, plus LLM-based request classification.

Every change goes through update(), wherever it comes from (the chat, n8n, Slack, the admin
portal): it validates the transition, writes the ticket event, records the activity event and
queues the notice for the requester.
"""

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

import httpx

from . import auth, clients, events, memory, notifications
from .config import settings
from .schemas import (
    AdminTicket,
    AdminTicketDetail,
    RequestIntake,
    Ticket,
    TicketConversation,
    TicketCreate,
    TicketEvent,
    TicketSla,
    TicketUpdate,
)

log = logging.getLogger("rag.tickets")

STATES = ["intake", "classified", "pending_approval", "approved", "rejected", "fulfilled", "cancelled"]
TRANSITIONS: dict[str, set[str]] = {
    "intake": {"classified", "pending_approval", "cancelled"},
    "classified": {"pending_approval", "approved", "cancelled"},
    "pending_approval": {"approved", "rejected", "cancelled"},
    "approved": {"fulfilled", "cancelled"},
    "rejected": set(),
    "fulfilled": set(),
    "cancelled": set(),
}
CATEGORIES = ["access_request", "hardware", "software", "hr", "facilities", "finance", "other"]
PRIORITIES = ["low", "normal", "high", "urgent"]
OPEN_STATES = {"intake", "classified", "pending_approval", "approved"}
DECISION_STATES = {"approved", "rejected", "cancelled"}
# Activity event kind and severity per state a ticket moves to
TRANSITION_EVENTS = {
    "classified": ("ticket.classified", "info"),
    "pending_approval": ("ticket.approval_requested", "info"),
    "approved": ("ticket.approved", "success"),
    "rejected": ("ticket.rejected", "warning"),
    "fulfilled": ("ticket.fulfilled", "success"),
    "cancelled": ("ticket.cancelled", "warning"),
}


class TicketError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def can_transition(current: str, new: str) -> bool:
    return new in TRANSITIONS.get(current, set())


def _require_db() -> None:
    if not settings.database_url:
        raise TicketError(503, "tickets need DATABASE_URL")


def _row_to_ticket(row: dict[str, Any], events: list[dict[str, Any]]) -> Ticket:
    return Ticket(**{**row, "payload": row.get("payload") or {}}, events=[TicketEvent(**e) for e in events])


def _load(conn, ticket_id: int, lock: bool = False) -> Ticket:
    # FOR UPDATE: a portal decision and a Slack click on the same ticket are taken one at a time
    row = conn.execute(
        "SELECT * FROM tickets WHERE id = %s" + (" FOR UPDATE" if lock else ""), (ticket_id,)
    ).fetchone()
    if row is None:
        raise TicketError(404, "ticket not found")
    events = conn.execute(
        "SELECT from_status, to_status, actor, note, created_at FROM ticket_events WHERE ticket_id = %s ORDER BY id",
        (ticket_id,),
    ).fetchall()
    return _row_to_ticket(dict(row), [dict(e) for e in events])


def resolve_id(ref: str) -> int:
    if ref.isdigit():
        return int(ref)
    match = re.fullmatch(r"REQ-0*(\d+)", ref.upper())
    if match:
        return int(match.group(1))
    raise TicketError(404, "ticket not found")


def create(data: TicketCreate, actor: str | None = None) -> Ticket:
    _require_db()
    with clients.db() as conn:
        row = conn.execute(
            """INSERT INTO tickets (title, description, category, priority, requester, session_id, payload)
               VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb) RETURNING id""",
            (
                data.title,
                data.description,
                data.category,
                data.priority,
                data.requester,
                data.session_id,
                json.dumps(data.payload),
            ),
        ).fetchone()
        ticket_id = row["id"]
        conn.execute("UPDATE tickets SET ticket_ref = %s WHERE id = %s", (f"REQ-{ticket_id:06d}", ticket_id))
        conn.execute(
            "INSERT INTO ticket_events (ticket_id, from_status, to_status, actor, note) VALUES (%s, NULL, 'intake', %s, %s)",
            (ticket_id, actor or data.requester, "created"),
        )
        conn.commit()
        return _load(conn, ticket_id)


def get(ref: str) -> Ticket:
    _require_db()
    with clients.db() as conn:
        return _load(conn, resolve_id(ref))


def list_tickets(status: str | None = None, limit: int = 50) -> list[Ticket]:
    _require_db()
    with clients.db() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM tickets WHERE status = %s ORDER BY id DESC LIMIT %s", (status, limit)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM tickets ORDER BY id DESC LIMIT %s", (limit,)).fetchall()
        return [_row_to_ticket(dict(r), []) for r in rows]


def update(ref: str, data: TicketUpdate, edit_kind: str = "ticket.updated") -> Ticket:
    """Apply a status change, field edits, a note and payload keys, in that order, in one transaction."""
    _require_db()
    ticket_id = resolve_id(ref)
    if data.category is not None and data.category not in CATEGORIES:
        raise TicketError(422, f"unknown category {data.category}; one of {', '.join(CATEGORIES)}")
    edits: list[str] = []
    with clients.db() as conn:
        current = _load(conn, ticket_id, lock=True)
        moved = bool(data.status and data.status != current.status)
        if moved:
            if data.status not in STATES:
                raise TicketError(422, f"unknown status {data.status}")
            if not can_transition(current.status, data.status):
                raise TicketError(409, f"cannot move a ticket from {current.status} to {data.status}")
            conn.execute(
                """UPDATE tickets SET status = %s, updated_at = now(),
                   approver = CASE WHEN %s IN ('approved', 'rejected') THEN COALESCE(%s, approver) ELSE approver END,
                   decision_note = CASE WHEN %s IN ('approved', 'rejected') THEN COALESCE(%s, decision_note) ELSE decision_note END
                   WHERE id = %s""",
                (data.status, data.status, data.actor, data.status, data.note, ticket_id),
            )
            conn.execute(
                "INSERT INTO ticket_events (ticket_id, from_status, to_status, actor, note) VALUES (%s, %s, %s, %s, %s)",
                (ticket_id, current.status, data.status, data.actor, data.note),
            )
        status = data.status if moved else current.status
        for field in ("priority", "category"):
            new, old = getattr(data, field), getattr(current, field)
            if new is not None and new != old:
                conn.execute(
                    f"UPDATE tickets SET {field} = %s, updated_at = now() WHERE id = %s", (new, ticket_id)
                )
                edits.append(f"{field} {old or 'none'} → {new}")
        if edits:
            note = "; ".join(edits) + (f". {data.note}" if data.note else "")
            conn.execute(
                "INSERT INTO ticket_events (ticket_id, from_status, to_status, actor, note) VALUES (%s, %s, %s, %s, %s)",
                (ticket_id, status, status, data.actor, note),
            )
        elif data.note and not moved:
            conn.execute(
                "INSERT INTO ticket_events (ticket_id, from_status, to_status, actor, note) VALUES (%s, %s, %s, %s, %s)",
                (ticket_id, current.status, current.status, data.actor, data.note),
            )
        payload = dict(data.payload or {})
        if moved and data.status in DECISION_STATES:
            payload["decision"] = {
                "status": data.status,
                "via": data.via or "api",
                "by": data.actor,
                "at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
        if payload:
            conn.execute(
                "UPDATE tickets SET payload = payload || %s::jsonb, updated_at = now() WHERE id = %s",
                (json.dumps(payload), ticket_id),
            )
        conn.commit()
        ticket = _load(conn, ticket_id)
    if moved:
        _record_transition(ticket, current.status, data)
    if edits:
        events.record(
            edit_kind,
            f"{ticket.ticket_ref} {'; '.join(edits)}" + (f" by {data.actor}" if data.actor else ""),
            severity="warning" if edit_kind == "ticket.escalated" else "info",
            detail=data.note,
            ref_type="ticket",
            ref_id=ticket.ticket_ref,
            actor=data.actor,
            data={"edits": edits},
        )
    if moved and data.status in notifications.NOTIFY_STATES:
        notifications.notify_ticket(ticket)
    return ticket


def _record_transition(ticket: Ticket, previous: str, data: TicketUpdate) -> None:
    kind, severity = TRANSITION_EVENTS.get(ticket.status, (f"ticket.{ticket.status}", "info"))
    title = ticket.title.rstrip(".")
    by = f" by {data.actor}" if data.actor and data.actor not in notifications.WORKFLOW_ACTORS else ""
    titles = {
        "classified": f"{ticket.ticket_ref} filed by {ticket.requester or 'someone'}: {title}",
        "pending_approval": f"{ticket.ticket_ref} waits for approval: {title}",
        "approved": f"{ticket.ticket_ref} approved{by}: {title}",
        "rejected": f"{ticket.ticket_ref} rejected{by}: {title}",
        "fulfilled": f"{ticket.ticket_ref} fulfilled: {title}",
        "cancelled": f"{ticket.ticket_ref} cancelled{by}: {title}",
    }
    events.record(
        kind,
        titles.get(ticket.status, f"{ticket.ticket_ref} {ticket.status}: {title}"),
        severity=severity,
        detail=data.note,
        ref_type="ticket",
        ref_id=ticket.ticket_ref,
        actor=data.actor,
        data={
            "from": previous,
            "to": ticket.status,
            "via": data.via,
            "category": ticket.category,
            "priority": ticket.priority,
            "channel": ticket.payload.get("channel"),
        },
    )


# ---------------------------------------------------------------- admin portal --------------


def allowed_actions(ticket: Ticket) -> list[str]:
    actions = []
    if ticket.status in ("pending_approval", "classified"):
        actions.append("approve")
    if ticket.status == "pending_approval":
        actions.append("reject")
    if can_transition(ticket.status, "cancelled"):
        actions.append("cancel")
    if ticket.status == "approved":
        actions.append("fulfil")
    if ticket.status in OPEN_STATES:
        actions.append("edit")
    if ticket.session_id:
        actions.append("message")
    return actions


_PENDING_SINCE = """CASE WHEN t.status = 'pending_approval' THEN (
    SELECT max(e.created_at) FROM ticket_events e WHERE e.ticket_id = t.id AND e.to_status = 'pending_approval'
) END AS pending_since"""


def _admin_ticket(row: dict[str, Any], events_rows: list[dict[str, Any]] | None = None) -> AdminTicket:
    row = dict(row)
    since = row.pop("pending_since", None)
    row.pop("total", None)
    minutes = (datetime.now(UTC) - since).total_seconds() / 60 if since else None
    ticket = _row_to_ticket(row, events_rows or [])
    return AdminTicket(
        **ticket.model_dump(),
        channel=ticket.payload.get("channel"),
        pending_since=since,
        pending_minutes=round(minutes, 1) if minutes is not None else None,
    )


def search(
    status: str | None = None,
    category: str | None = None,
    priority: str | None = None,
    requester: str | None = None,
    channel: str | None = None,
    q: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    order: str = "newest",
    page: int = 1,
    limit: int = 50,
) -> tuple[list[AdminTicket], int]:
    """One page of tickets for the portal, with the total that match."""
    _require_db()
    where, params = [], []
    for clause, value in (
        ("t.status = %s", status),
        ("t.category = %s", category),
        ("t.priority = %s", priority),
        ("t.payload->>'channel' = %s", channel),
        ("t.created_at >= %s", since),
        ("t.created_at < %s", until),
    ):
        if value is not None:
            where.append(clause)
            params.append(value)
    if requester:
        where.append("t.requester ILIKE %s")
        params.append(f"%{requester}%")
    if q:
        where.append("(t.title ILIKE %s OR t.description ILIKE %s OR t.ticket_ref ILIKE %s)")
        params.extend([f"%{q}%"] * 3)
    query = (
        f"SELECT t.*, {_PENDING_SINCE}, count(*) OVER () AS total FROM tickets t"
        + (" WHERE " + " AND ".join(where) if where else "")
        + f" ORDER BY t.id {'ASC' if order == 'oldest' else 'DESC'} LIMIT %s OFFSET %s"
    )
    with clients.db() as conn:
        rows = conn.execute(query, (*params, limit, (page - 1) * limit)).fetchall()
    total = int(rows[0]["total"]) if rows else 0
    return [_admin_ticket(r) for r in rows], total


def detail(ref: str) -> AdminTicketDetail:
    _require_db()
    ticket_id = resolve_id(ref)
    with clients.db() as conn:
        row = conn.execute(
            f"SELECT t.*, {_PENDING_SINCE} FROM tickets t WHERE t.id = %s", (ticket_id,)
        ).fetchone()
        if row is None:
            raise TicketError(404, "ticket not found")
        events_rows = conn.execute(
            "SELECT from_status, to_status, actor, note, created_at FROM ticket_events WHERE ticket_id = %s ORDER BY id",
            (ticket_id,),
        ).fetchall()
        conversation = None
        if row["session_id"]:
            c = conn.execute(
                """SELECT c.session_id, c.channel, c.user_id, c.created_at, c.updated_at,
                          (SELECT count(*) FROM messages m WHERE m.session_id = c.session_id) AS messages
                   FROM conversations c WHERE c.session_id = %s""",
                (row["session_id"],),
            ).fetchone()
            conversation = TicketConversation(
                session_id=row["session_id"],
                exists=c is not None,
                channel=c["channel"] if c else None,
                user_id=c["user_id"] if c else None,
                messages=c["messages"] if c else 0,
                started=c["created_at"] if c else None,
                last_activity=c["updated_at"] if c else None,
            )
    ticket = _admin_ticket(dict(row), [dict(e) for e in events_rows])
    minutes = ticket.pending_minutes
    level = None
    if minutes is not None:
        level = (
            "escalation"
            if minutes >= settings.sla_escalation_minutes
            else "reminder"
            if minutes >= settings.sla_reminder_minutes
            else "ok"
        )
    slack = ticket.payload.get("slack")
    return AdminTicketDetail(
        **ticket.model_dump(),
        sla=TicketSla(
            reminder_minutes=settings.sla_reminder_minutes,
            escalation_minutes=settings.sla_escalation_minutes,
            level=level,
        ),
        conversation=conversation,
        slack=slack if isinstance(slack, dict) else None,
        actions=allowed_actions(ticket),
    )


def _require_action(ref: str, action: str) -> Ticket:
    ticket = get(ref)
    if action not in allowed_actions(ticket):
        raise TicketError(
            409, f"{ticket.ticket_ref} is {ticket.status.replace('_', ' ')}; it cannot be {action}ed now"
        )
    return ticket


def decide(ref: str, decision: str, actor: str, note: str | None) -> tuple[Ticket, bool]:
    """Approve or reject in the portal; the workflow then fulfils and updates the Slack card."""
    note = (note or "").strip() or None
    if decision == "rejected" and not note:
        raise TicketError(422, "a rejection needs a reason; the requester hears it")
    _require_action(ref, "approve" if decision == "approved" else "reject")
    ticket = update(ref, TicketUpdate(status=decision, actor=actor, note=note, via="portal"))
    return ticket, notify_decision(ticket, decision, actor, note)


def cancel(ref: str, actor: str, note: str | None) -> tuple[Ticket, bool]:
    _require_action(ref, "cancel")
    ticket = update(ref, TicketUpdate(status="cancelled", actor=actor, note=note or None, via="portal"))
    return ticket, notify_decision(ticket, "cancelled", actor, note)


def mark_fulfilled(ref: str, actor: str, note: str | None) -> Ticket:
    """For when fulfilment never ran, for example because n8n was down after the approval."""
    _require_action(ref, "fulfil")
    return update(
        ref, TicketUpdate(status="fulfilled", actor=actor, note=note or "marked fulfilled in the portal")
    )


def edit(ref: str, actor: str, priority: str | None, category: str | None, note: str | None) -> Ticket:
    _require_action(ref, "edit")
    if priority is None and category is None:
        raise TicketError(422, "nothing to change: give a priority or a category")
    return update(ref, TicketUpdate(priority=priority, category=category, actor=actor, note=note or None))


def message(ref: str, actor: str, text: str) -> Ticket:
    """A notice in the requester's conversation, which the chat shows and the avatar speaks."""
    ticket = get(ref)
    if not ticket.session_id:
        raise TicketError(
            409, f"{ticket.ticket_ref} was not filed from a conversation; there is no one to message"
        )
    text = " ".join(text.split())
    notifications.notify_message(ticket, actor, text)
    ticket = update(ref, TicketUpdate(actor=actor, note=f"message to the requester: {text}"))
    events.record(
        "ticket.message",
        f"{actor} messaged the requester of {ticket.ticket_ref}",
        detail=text,
        ref_type="ticket",
        ref_id=ticket.ticket_ref,
        actor=actor,
    )
    return ticket


def notify_decision(ticket: Ticket, decision: str, actor: str, note: str | None) -> bool:
    """Tell WF4 about a decision taken in the portal: it fulfils an approval the same way as after a
    Slack click, posts the notices, and updates the Slack card when there is one."""
    url = settings.n8n_url.rstrip("/") + settings.n8n_decision_webhook_path
    body = {
        "via": "portal",
        "decision": decision,
        "actor": actor,
        "note": note or "",
        "ticket": ticket.model_dump(mode="json"),
    }
    try:
        with httpx.Client(timeout=10) as http:
            response = http.post(url, json=body, headers=auth.internal_headers())
        if response.status_code >= 400:
            log.warning("n8n decision webhook returned %s", response.status_code)
            return False
        return True
    except httpx.HTTPError as exc:
        log.warning("n8n decision webhook unreachable: %s", exc)
        return False


def dashboard() -> dict[str, Any]:
    """Ticket counts for the portal's overview."""
    pending = memory.run(
        f"""SELECT t.ticket_ref, {_PENDING_SINCE} FROM tickets t WHERE t.status = 'pending_approval'
            ORDER BY pending_since NULLS LAST""",
        fetch=True,
    )
    stuck = memory.run(
        """SELECT ticket_ref, updated_at FROM tickets
           WHERE status = 'approved' AND updated_at < now() - interval '5 minutes' ORDER BY updated_at""",
        fetch=True,
    )
    by_status = memory.run(
        "SELECT status, count(*) AS n FROM tickets WHERE created_at > now() - interval '7 days' GROUP BY status",
        fetch=True,
    )
    oldest = pending[0] if pending else None
    since = oldest["pending_since"] if oldest else None
    return {
        "sla": {
            "reminder_minutes": settings.sla_reminder_minutes,
            "escalation_minutes": settings.sla_escalation_minutes,
        },
        "pending": {
            "count": len(pending or []),
            "oldest_ref": oldest["ticket_ref"] if oldest else None,
            "oldest_minutes": round((datetime.now(UTC) - since).total_seconds() / 60, 1) if since else None,
        },
        "approved_not_fulfilled": {
            "count": len(stuck or []),
            "oldest_ref": stuck[0]["ticket_ref"] if stuck else None,
        },
        "tickets_last_7_days": {r["status"]: int(r["n"]) for r in by_status or []},
    }


def classify_request(text: str) -> dict[str, Any]:
    system = (
        "You triage IT and workplace service requests. Respond with a single JSON object and nothing else, with keys: "
        '"title" (short imperative, max 12 words), "category" (one of ' + ", ".join(CATEGORIES) + "), "
        '"priority" (low, normal, high, urgent), "summary" (one sentence), '
        '"needs_approval" (true when the request grants access, costs money, or changes permissions), '
        '"details" (object with any specific items mentioned, for example {"software": "Visual Studio Code"}).'
    )
    kwargs: dict[str, Any] = {
        "model": settings.llm_model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": text[:6000]}],
        "temperature": 0,
        "max_tokens": 400,
    }
    try:
        completion = clients.llm().chat.completions.create(response_format={"type": "json_object"}, **kwargs)
    except Exception:  # noqa: BLE001
        completion = clients.llm().chat.completions.create(**kwargs)
    content = completion.choices[0].message.content or "{}"
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, re.DOTALL)
        data = json.loads(match.group(0)) if match else {}
    category = str(data.get("category", "other")).lower()
    priority = str(data.get("priority", "normal")).lower()
    return {
        "title": str(data.get("title") or text[:80]),
        "category": category if category in CATEGORIES else "other",
        "priority": priority if priority in ("low", "normal", "high", "urgent") else "normal",
        "summary": str(data.get("summary", "")),
        "needs_approval": bool(data.get("needs_approval", True)),
        "details": data.get("details") if isinstance(data.get("details"), dict) else {},
    }


def notify_n8n(ticket: Ticket, classification: dict[str, Any], channel: str) -> bool:
    url = settings.n8n_url.rstrip("/") + settings.n8n_request_webhook_path
    body = {"ticket": ticket.model_dump(mode="json"), "classification": classification, "channel": channel}
    try:
        with httpx.Client(timeout=10) as http:
            response = http.post(url, json=body)
        if response.status_code >= 400:
            log.warning("n8n request webhook returned %s", response.status_code)
            return False
        return True
    except httpx.HTTPError as exc:
        log.warning("n8n request webhook unreachable: %s", exc)
        return False


def intake(request: RequestIntake) -> tuple[Ticket, dict[str, Any], bool]:
    classification = classify_request(request.text)
    if settings.requests_require_approval and not classification["needs_approval"]:
        classification["details"]["model_needs_approval"] = False
        classification["needs_approval"] = True
    ticket = create(
        TicketCreate(
            title=classification["title"],
            description=request.text,
            category=classification["category"],
            priority=classification["priority"],
            requester=request.requester or request.user_id,
            session_id=request.session_id,
            payload={
                "channel": request.channel,
                "summary": classification["summary"],
                **classification["details"],
            },
            needs_approval=classification["needs_approval"],
        ),
        actor=request.requester or request.user_id,
    )
    ticket = update(
        str(ticket.id), TicketUpdate(status="classified", actor="assistant", note=classification["summary"])
    )
    if classification["needs_approval"]:
        ticket = update(
            str(ticket.id),
            TicketUpdate(status="pending_approval", actor="assistant", note="awaiting approval"),
        )
    notified = notify_n8n(ticket, classification, request.channel)
    return ticket, classification, notified
