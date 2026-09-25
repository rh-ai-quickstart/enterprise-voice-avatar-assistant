"""Outcome notices pushed back into a conversation, for example the decision on a service request.

A notice is written when a ticket changes state and read by whichever client is live for the
session: the voice agent speaks it, the frontend shows it. Only the newest undelivered notice
per ticket and kind is handed out (a decision does not supersede a message from an admin, or the
reverse); older ones are marked delivered as superseded.
"""

import logging

from . import memory
from .schemas import Notification, Ticket

log = logging.getLogger("rag.notifications")

NOTIFY_STATES = ("approved", "rejected", "fulfilled", "cancelled")
# Actors that are not people: a notice never says "approved by n8n"
WORKFLOW_ACTORS = {"n8n", "assistant", "system", "slack", "sla-escalation"}
_GENERIC_NOTES = {"decided in Slack", "no approval required"}


def ticket_text(ticket: Ticket) -> str | None:
    """Spoken-style sentence for a ticket outcome, or None when the state is not worth announcing."""
    title = ticket.title.rstrip(".")
    who = f" by {ticket.approver}" if ticket.approver and ticket.approver not in WORKFLOW_ACTORS else ""
    if ticket.status == "fulfilled":
        return f"Good news: your request {ticket.ticket_ref}, {title}, was approved{who} and has been fulfilled. You're all set."
    if ticket.status == "approved":
        return f"Your request {ticket.ticket_ref}, {title}, was approved{who}. It is being fulfilled now."
    if ticket.status == "rejected":
        reason = (
            ticket.decision_note
            if ticket.decision_note and ticket.decision_note not in _GENERIC_NOTES
            else None
        )
        return f"Your request {ticket.ticket_ref}, {title}, was rejected{who}." + (
            f" Reason: {reason}." if reason else ""
        )
    if ticket.status == "cancelled":
        return f"Your request {ticket.ticket_ref}, {title}, was cancelled."
    return None


def notify_ticket(ticket: Ticket) -> None:
    """Queue a notice for the conversation that filed the ticket."""
    if not ticket.session_id:
        return
    text = ticket_text(ticket)
    if not text:
        return
    memory.ensure_conversation(ticket.session_id, ticket.requester, "system")
    memory.run(
        "INSERT INTO session_notifications (session_id, ticket_ref, kind, text) VALUES (%s, %s, 'ticket_update', %s)",
        (ticket.session_id, ticket.ticket_ref, text),
    )
    log.info("notice queued for session %s: %s -> %s", ticket.session_id, ticket.ticket_ref, ticket.status)


def notify_message(ticket: Ticket, actor: str, text: str) -> None:
    """A message from an admin to the person who filed the ticket."""
    memory.ensure_conversation(ticket.session_id, ticket.requester, "system")
    memory.run(
        "INSERT INTO session_notifications (session_id, ticket_ref, kind, text) VALUES (%s, %s, 'admin_message', %s)",
        (
            ticket.session_id,
            ticket.ticket_ref,
            f"A message from {actor} about your request {ticket.ticket_ref}: {text}",
        ),
    )


def pending(session_id: str) -> list[Notification]:
    rows = (
        memory.run(
            "SELECT id, session_id, ticket_ref, kind, text, created_at FROM session_notifications "
            "WHERE session_id = %s AND delivered_at IS NULL ORDER BY id",
            (session_id,),
            fetch=True,
        )
        or []
    )
    latest: dict[str, dict] = {}
    superseded: list[int] = []
    for row in rows:
        key = (row["ticket_ref"], row["kind"]) if row["ticket_ref"] else f"id-{row['id']}"
        if key in latest:
            superseded.append(int(latest[key]["id"]))
        latest[key] = row
    if superseded:
        _mark_delivered(session_id, superseded)
    return [Notification(**row) for row in sorted(latest.values(), key=lambda r: r["id"])]


def ack(session_id: str, ids: list[int]) -> None:
    """Mark notices delivered. The transcript records what the person actually saw or heard, so the
    line is written here, once, and not for superseded notices."""
    if not ids:
        return
    rows = (
        memory.run(
            "SELECT id, text FROM session_notifications WHERE session_id = %s AND id = ANY(%s) AND delivered_at IS NULL ORDER BY id",
            (session_id, [int(i) for i in ids]),
            fetch=True,
        )
        or []
    )
    for row in rows:
        memory.append(session_id, "assistant", row["text"])
    _mark_delivered(session_id, [int(row["id"]) for row in rows])


def _mark_delivered(session_id: str, ids: list[int]) -> None:
    if not ids:
        return
    memory.run(
        "UPDATE session_notifications SET delivered_at = now() WHERE session_id = %s AND id = ANY(%s) AND delivered_at IS NULL",
        (session_id, ids),
    )
