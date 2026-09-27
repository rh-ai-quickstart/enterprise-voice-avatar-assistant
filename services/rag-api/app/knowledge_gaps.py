"""Track and aggregate questions the RAG pipeline could not answer confidently.

Each gap keeps the embedding of its question, computed in the background so the answer is not
delayed (the retrieval vector cannot be reused: a follow-up is retrieved together with the
previous question). The portal groups gaps by meaning: identical wordings first, then wordings
whose embeddings are closer than GAP_GROUP_THRESHOLD (cosine), led by the most-asked wording.
Gaps without an embedding (the embeddings service was down) are grouped by their wording alone.
"""

import logging
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np

from . import clients, events, memory, retrieval
from .config import settings

log = logging.getLogger("rag.knowledge_gaps")

GAP_STATUSES = ("open", "resolved", "dismissed")
GROUP_LIMIT = 2000
EMBED_BATCH = 32
_embedder = ThreadPoolExecutor(max_workers=2, thread_name_prefix="gap-embedding")


def record(session_id: str | None, question: str, top_score: float, hit_count: int) -> None:
    if top_score >= settings.gap_score_threshold and hit_count > 0:
        return
    reason = "no_hits" if hit_count == 0 else "low_score"
    rows = memory.run(
        """INSERT INTO knowledge_gaps (session_id, question, top_score, hit_count, reason)
           VALUES (%s, %s, %s, %s, %s) RETURNING id""",
        (session_id, question[:4000], round(top_score, 4), hit_count, reason),
        fetch=True,
    )
    log.info("knowledge gap recorded: reason=%s top_score=%.4f hits=%d", reason, top_score, hit_count)
    if rows:
        gap_id = int(rows[0]["id"])
        # In the feed, and what refreshes the Knowledge gaps page and the overview while they are open
        events.record(
            "gap.recorded",
            f"Not answered from the documents: {question[:200]}",
            ref_type="gap",
            ref_id=str(gap_id),
            data={"reason": reason, "top_score": round(top_score, 4), "session_id": session_id},
        )
        _embedder.submit(_embed_one, gap_id, question[:4000])


def _embed_one(gap_id: int, question: str) -> None:
    try:
        vector = retrieval.embed(question)
    except Exception as exc:  # noqa: BLE001 - grouping falls back to the wording
        log.info("no embedding for gap %s: %s", gap_id, exc)
        return
    memory.run("UPDATE knowledge_gaps SET embedding = %s::real[] WHERE id = %s", (vector, gap_id))


def _fill_embeddings(rows: list[dict[str, Any]]) -> None:
    """Embed the questions of older gaps that have none yet, in batches; failures are left empty.
    The portal waits on this, so a slow embeddings service gets one short try, not the chat's retries."""
    missing = [r for r in rows if not r.get("embedding")]
    for start in range(0, len(missing), EMBED_BATCH):
        batch = missing[start : start + EMBED_BATCH]
        try:
            response = (
                clients.embeddings()
                .with_options(timeout=15.0, max_retries=0)
                .embeddings.create(model=settings.embeddings_model, input=[r["question"] for r in batch])
            )
        except Exception as exc:  # noqa: BLE001
            log.info("cannot embed %d older gaps: %s", len(batch), exc)
            return
        for row, item in zip(batch, response.data, strict=False):
            row["embedding"] = item.embedding
            memory.run(
                "UPDATE knowledge_gaps SET embedding = %s::real[] WHERE id = %s", (item.embedding, row["id"])
            )


def _wording(question: str) -> str:
    return " ".join(question.lower().split()).rstrip(" ?.!")


def gaps(
    status: str | None = "open",
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = GROUP_LIMIT,
    with_embeddings: bool = False,
) -> list[dict[str, Any]]:
    columns = "id, session_id, question, top_score, hit_count, reason, status, resolved_by, resolved_at, resolution_note, created_at"
    rows = memory.select_page(
        "knowledge_gaps",
        columns + (", embedding" if with_embeddings else ""),
        {"status": status},
        since,
        until,
        None,
        limit,
    )
    return rows


def group(rows: list[dict[str, Any]], threshold: float | None = None) -> list[dict[str, Any]]:
    """Group gaps by meaning, most-asked first; see the module docstring."""
    threshold = settings.gap_group_threshold if threshold is None else threshold
    by_wording: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_wording[_wording(row["question"])].append(row)
    # Leaders are tried most-asked first, so a group is named after its most frequent wording
    wordings = sorted(by_wording, key=lambda w: (-len(by_wording[w]), -max(r["id"] for r in by_wording[w])))
    vectors: dict[str, np.ndarray] = {}
    for w in wordings:
        vector = next((r["embedding"] for r in by_wording[w] if r.get("embedding")), None)
        if vector:
            v = np.asarray(vector, dtype=np.float32)
            norm = float(np.linalg.norm(v))
            if norm > 0:
                vectors[w] = v / norm
    embedded = [w for w in wordings if w in vectors]
    similar: dict[str, list[str]] = {}
    if embedded:
        matrix = np.stack([vectors[w] for w in embedded])
        sims = matrix @ matrix.T
        index = {w: i for i, w in enumerate(embedded)}
        similar = {w: [embedded[j] for j in np.nonzero(sims[index[w]] >= threshold)[0]] for w in embedded}
    assigned: set[str] = set()
    groups = []
    for leader in wordings:
        if leader in assigned:
            continue
        members = [leader] + [w for w in similar.get(leader, []) if w != leader and w not in assigned]
        assigned.update(members)
        items = sorted((r for w in members for r in by_wording[w]), key=lambda r: r["id"], reverse=True)
        # The spelling asked most often (the newest on a tie: rows come newest first)
        spellings = Counter(r["question"] for r in by_wording[leader])
        groups.append(
            {
                "question": spellings.most_common(1)[0][0],
                "count": len(items),
                "wordings": len(members),
                "best_score": max(float(r["top_score"]) for r in items),
                "reasons": sorted({r["reason"] for r in items}),
                "first_seen": min(r["created_at"] for r in items),
                "last_seen": max(r["created_at"] for r in items),
                "ids": [r["id"] for r in items],
                "gaps": [{k: v for k, v in r.items() if k != "embedding"} for r in items],
            }
        )
    groups.sort(key=lambda g: (-g["count"], -g["last_seen"].timestamp()))
    return groups


def grouped(
    status: str | None = "open",
    since: datetime | None = None,
    until: datetime | None = None,
    fill: bool = True,
) -> list[dict[str, Any]]:
    rows = gaps(status, since, until, with_embeddings=True)
    if fill:
        _fill_embeddings(rows)
    return group(rows)


def resolve(ids: list[int], status: str, actor: str, note: str | None) -> int:
    """Resolve, dismiss or reopen gaps; how many changed."""
    rows = memory.run(
        """UPDATE knowledge_gaps SET status = %s,
                  resolved_by = CASE WHEN %s = 'open' THEN NULL ELSE %s END,
                  resolved_at = CASE WHEN %s = 'open' THEN NULL ELSE now() END,
                  resolution_note = CASE WHEN %s = 'open' THEN NULL ELSE %s END
           WHERE id = ANY(%s) AND status <> %s RETURNING id, question""",
        (status, status, actor, status, status, note, [int(i) for i in ids], status),
        fetch=True,
    )
    changed = rows or []
    if changed:
        verb = {"resolved": "resolved", "dismissed": "dismissed", "open": "reopened"}[status]
        events.record(
            f"gap.{verb}",
            f"{actor} {verb} {len(changed)} knowledge gap{'s' if len(changed) != 1 else ''}: {changed[0]['question'][:80]}",
            severity="success" if status == "resolved" else "info",
            detail=note,
            ref_type="gap",
            ref_id=str(changed[0]["id"]),
            actor=actor,
            data={"ids": [r["id"] for r in changed]},
        )
    return len(changed)


def retest(gap_id: int) -> dict[str, Any] | None:
    """Retrieval for the gap's question now: the top passages and whether it would still be a gap.
    Retrieval only, no language model call."""
    rows = memory.run(
        "SELECT id, question, top_score FROM knowledge_gaps WHERE id = %s", (gap_id,), fetch=True
    )
    if not rows:
        return None
    gap = rows[0]
    hits = retrieval.search(gap["question"], top_k=5, min_score=0.0)
    best = max((h.score for h in hits), default=0.0)
    return {
        "id": gap_id,
        "question": gap["question"],
        "recorded_score": float(gap["top_score"]),
        "best_score": round(best, 4),
        "threshold": settings.gap_score_threshold,
        "answered": bool(hits) and best >= settings.gap_score_threshold,
        "hits": [h.to_citation(n) for n, h in enumerate(hits, start=1)],
    }


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
    """Open gaps over the last hours, for WF7: counts, the most-asked questions and the groups."""
    total = memory.run(
        """SELECT COUNT(*) AS cnt FROM knowledge_gaps
           WHERE status = 'open' AND created_at > now() - make_interval(hours := %s)""",
        (hours,),
        fetch=True,
    )
    by_reason = memory.run(
        """SELECT reason, COUNT(*) AS cnt
           FROM knowledge_gaps WHERE status = 'open' AND created_at > now() - make_interval(hours := %s)
           GROUP BY reason ORDER BY cnt DESC""",
        (hours,),
        fetch=True,
    )
    top_questions = memory.run(
        """SELECT question, COUNT(*) AS times_asked, ROUND(AVG(top_score)::numeric, 4) AS avg_score
           FROM knowledge_gaps WHERE status = 'open' AND created_at > now() - make_interval(hours := %s)
           GROUP BY question ORDER BY times_asked DESC, avg_score ASC LIMIT 15""",
        (hours,),
        fetch=True,
    )
    groups = grouped("open", datetime.now(UTC) - timedelta(hours=hours)) if memory.enabled() else []
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
        "groups": [
            {
                "question": g["question"],
                "times_asked": g["count"],
                "wordings": g["wordings"],
                "best_score": g["best_score"],
            }
            for g in groups[:10]
        ],
    }
