"""Track and aggregate questions the RAG pipeline could not answer confidently."""

import logging
from typing import Any

from . import memory
from .config import settings

log = logging.getLogger("rag.knowledge_gaps")


def record(session_id: str | None, question: str, top_score: float, hit_count: int) -> None:
    if top_score >= settings.gap_score_threshold and hit_count > 0:
        return
    reason = "no_hits" if hit_count == 0 else "low_score"
    memory.run(
        "INSERT INTO knowledge_gaps (session_id, question, top_score, hit_count, reason) VALUES (%s, %s, %s, %s, %s)",
        (session_id, question[:4000], round(top_score, 4), hit_count, reason),
    )
    log.info("knowledge gap recorded: reason=%s top_score=%.4f hits=%d", reason, top_score, hit_count)


def stale_tickets(
    reminder_minutes: int | None = None,
    escalation_minutes: int | None = None,
) -> dict[str, list[dict[str, Any]]]:
    rm = reminder_minutes or settings.sla_reminder_minutes
    em = escalation_minutes or settings.sla_escalation_minutes
    remind_rows = memory.run(
        """SELECT id, ticket_ref, title, category, priority, requester, status,
                  created_at, updated_at,
                  EXTRACT(EPOCH FROM (now() - updated_at)) / 60 AS pending_minutes
           FROM tickets
           WHERE status = 'pending_approval'
             AND updated_at < now() - make_interval(mins := %s)
             AND updated_at >= now() - make_interval(mins := %s)
           ORDER BY updated_at""",
        (rm, em),
        fetch=True,
    )
    escalate_rows = memory.run(
        """SELECT id, ticket_ref, title, category, priority, requester, status,
                  created_at, updated_at,
                  EXTRACT(EPOCH FROM (now() - updated_at)) / 60 AS pending_minutes
           FROM tickets
           WHERE status = 'pending_approval'
             AND priority != 'urgent'
             AND updated_at < now() - make_interval(mins := %s)
           ORDER BY updated_at""",
        (em,),
        fetch=True,
    )
    return {
        "remind": [dict(r) for r in remind_rows or []],
        "escalate": [dict(r) for r in escalate_rows or []],
    }


_PRIORITY_ESCALATION = {"low": "normal", "normal": "high", "high": "urgent"}


def escalate_ticket(ticket_ref: str, current_priority: str) -> dict[str, Any] | None:
    new_priority = _PRIORITY_ESCALATION.get(current_priority)
    if not new_priority:
        return None
    from . import tickets

    tickets.update(
        ticket_ref,
        tickets.TicketUpdate(
            actor="sla-escalation",
            priority=new_priority,
            note=f"SLA escalation: pending over {settings.sla_escalation_minutes} minutes",
        ),
        edit_kind="ticket.escalated",
    )
    return {"ticket_ref": ticket_ref, "old_priority": current_priority, "new_priority": new_priority}


def digest(hours: int = 24) -> dict[str, Any]:
    total = memory.run(
        "SELECT COUNT(*) AS cnt FROM knowledge_gaps WHERE created_at > now() - make_interval(hours := %s)",
        (hours,),
        fetch=True,
    )
    by_reason = memory.run(
        """SELECT reason, COUNT(*) AS cnt
           FROM knowledge_gaps WHERE created_at > now() - make_interval(hours := %s)
           GROUP BY reason ORDER BY cnt DESC""",
        (hours,),
        fetch=True,
    )
    top_questions = memory.run(
        """SELECT question, COUNT(*) AS times_asked, ROUND(AVG(top_score)::numeric, 4) AS avg_score
           FROM knowledge_gaps WHERE created_at > now() - make_interval(hours := %s)
           GROUP BY question ORDER BY times_asked DESC, avg_score ASC LIMIT 15""",
        (hours,),
        fetch=True,
    )
    return {
        "period_hours": hours,
        "total_gaps": total[0]["cnt"] if total else 0,
        "by_reason": {r["reason"]: r["cnt"] for r in by_reason or []},
        "top_questions": [
            {
                "question": r["question"],
                "times_asked": r["times_asked"],
                "avg_score": float(r["avg_score"] or 0),
            }
            for r in top_questions or []
        ],
    }
