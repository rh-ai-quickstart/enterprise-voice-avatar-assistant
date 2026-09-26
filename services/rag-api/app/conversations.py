"""Conversations for the admin portal: search, detail, export and delete.

Search is PostgreSQL full-text search over every message (messages.tsv, sql/004_admin.sql); the
matching lines come back with the matched words between the markers MARK_START and MARK_END,
which the portal turns into highlights (they are control characters, so they never occur in text).
"""

import logging
from datetime import datetime
from typing import Any

import httpx

from . import archives, auth, classify, clients, events, memory
from .config import settings

log = logging.getLogger("rag.conversations")

MARK_START, MARK_END = "\x01", "\x02"
_HEADLINE = f'StartSel="{MARK_START}", StopSel="{MARK_END}", MaxWords=24, MinWords=8, MaxFragments=2, FragmentDelimiter=" … "'
# The chat stores its mode (text or voice); the portal calls text conversations chat
_CHANNEL = "CASE WHEN c.channel IN ('text', 'chat') THEN 'chat' ELSE c.channel END"
TRANSCRIPTS_BUCKET = "transcripts"


class ConversationError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _require_db() -> None:
    if not memory.enabled():
        raise ConversationError(503, "conversations need DATABASE_URL")


def search(
    q: str | None = None,
    user: str | None = None,
    channel: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    has_ticket: bool | None = None,
    archived: bool | None = None,
    blocked: bool | None = None,
    page: int = 1,
    limit: int = 50,
) -> tuple[list[dict[str, Any]], int]:
    """One page of conversations, most recently active first, with the total that match. With q,
    each carries up to three matching messages with the matched words marked."""
    _require_db()
    where: list[str] = []
    params: dict[str, Any] = {"q": q, "headline": _HEADLINE, "limit": limit, "offset": (page - 1) * limit}
    if q:
        where.append(
            "EXISTS (SELECT 1 FROM messages m WHERE m.session_id = c.session_id "
            "AND m.tsv @@ websearch_to_tsquery('english', %(q)s))"
        )
    if user:
        where.append("c.user_id ILIKE %(user)s")
        params["user"] = f"%{user}%"
    if channel:
        where.append(f"{_CHANNEL} = %(channel)s")
        params["channel"] = channel
    if since:
        where.append("c.created_at >= %(since)s")
        params["since"] = since
    if until:
        where.append("c.created_at < %(until)s")
        params["until"] = until
    for flag, exists in (
        (has_ticket, "SELECT 1 FROM tickets t WHERE t.session_id = c.session_id"),
        (archived, "SELECT 1 FROM transcript_archives a WHERE a.session_id = c.session_id"),
        (blocked, "SELECT 1 FROM messages m WHERE m.session_id = c.session_id AND m.blocked"),
    ):
        if flag is not None:
            where.append(("EXISTS" if flag else "NOT EXISTS") + f" ({exists})")
    matches = (
        """(SELECT COALESCE(json_agg(json_build_object('id', x.id, 'role', x.role, 'snippet', x.snippet)
                                    ORDER BY x.id), '[]'::json)
            FROM (SELECT m.id, m.role, ts_headline('english', m.content, websearch_to_tsquery('english', %(q)s),
                                                   %(headline)s) AS snippet
                  FROM messages m WHERE m.session_id = c.session_id
                    AND m.tsv @@ websearch_to_tsquery('english', %(q)s)
                  ORDER BY m.id LIMIT 3) x)"""
        if q
        else "'[]'::json"
    )
    query = f"""
        SELECT c.session_id, c.user_id, {_CHANNEL} AS channel, c.created_at AS started,
               COALESCE((SELECT max(m.created_at) FROM messages m WHERE m.session_id = c.session_id),
                        c.updated_at) AS last_activity,
               (SELECT count(*) FROM messages m WHERE m.session_id = c.session_id) AS messages,
               (SELECT count(*) FROM messages m WHERE m.session_id = c.session_id AND m.blocked) AS blocked,
               (SELECT count(*) FROM tickets t WHERE t.session_id = c.session_id) AS tickets,
               (SELECT count(*) FROM transcript_archives a WHERE a.session_id = c.session_id) AS archives,
               {matches} AS matches,
               count(*) OVER () AS total
        FROM conversations c
        {"WHERE " + " AND ".join(where) if where else ""}
        ORDER BY last_activity DESC, c.session_id
        LIMIT %(limit)s OFFSET %(offset)s"""
    with clients.db() as conn:
        rows = [dict(r) for r in conn.execute(query, params).fetchall()]
    total = int(rows[0]["total"]) if rows else 0
    for row in rows:
        row.pop("total")
    return rows, total


def detail(session_id: str) -> dict[str, Any]:
    """The conversation with its messages and citations, notices, tickets and archives."""
    _require_db()
    with clients.db() as conn:
        conversation = conn.execute(
            f"""SELECT c.session_id, c.user_id, {_CHANNEL} AS channel, c.created_at AS started, c.updated_at
                FROM conversations c WHERE c.session_id = %s""",
            (session_id,),
        ).fetchone()
        if conversation is None:
            raise ConversationError(404, "conversation not found (never stored, or deleted)")
        messages = conn.execute(
            "SELECT id, role, content, citations, blocked, created_at FROM messages WHERE session_id = %s ORDER BY id",
            (session_id,),
        ).fetchall()
        notices = conn.execute(
            """SELECT id, ticket_ref, kind, text, created_at, delivered_at FROM session_notifications
               WHERE session_id = %s ORDER BY id""",
            (session_id,),
        ).fetchall()
        tickets = conn.execute(
            """SELECT ticket_ref, title, status, priority, category, created_at FROM tickets
               WHERE session_id = %s ORDER BY id""",
            (session_id,),
        ).fetchall()
    return {
        **dict(conversation),
        "messages": [dict(m) for m in messages],
        "notices": [dict(n) for n in notices],
        "tickets": [dict(t) for t in tickets],
        "archives": archives.for_session(session_id),
    }


def _stamp(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else ""


def export(session_id: str, fmt: str) -> tuple[str, str]:
    """(file name, text) of the conversation as Markdown or plain text."""
    c = detail(session_id)
    who = {"user": "User", "assistant": "Assistant"}
    if fmt == "txt":
        head = [
            f"Conversation {session_id}",
            f"User: {c['user_id'] or '-'}  Channel: {c['channel'] or '-'}  Started: {_stamp(c['started'])}",
            "",
        ]
        body = [
            f"[{_stamp(m['created_at'])}] {who.get(m['role'], m['role'])}: {m['content']}"
            + (" (blocked by the guardrail)" if m["blocked"] else "")
            for m in c["messages"]
        ]
        return f"conversation-{session_id}.txt", "\n".join(head + body) + "\n"
    lines = [
        f"# Conversation {session_id}",
        "",
        f"- **User:** {c['user_id'] or '-'}",
        f"- **Channel:** {c['channel'] or '-'}",
        f"- **Started:** {_stamp(c['started'])}",
    ]
    if c["tickets"]:
        lines.append(
            "- **Tickets:** " + ", ".join(f"{t['ticket_ref']} ({t['status']})" for t in c["tickets"])
        )
    lines += ["", "## Transcript", ""]
    for m in c["messages"]:
        blocked = " *(blocked by the guardrail)*" if m["blocked"] else ""
        lines.append(f"**{who.get(m['role'], m['role'])}** ({_stamp(m['created_at'])}){blocked}")
        lines.append("")
        lines.append(m["content"])
        sources = [s for s in m["citations"] or [] if s.get("used")]
        if sources:
            lines.append("")
            for s in sources:
                page = f", page {s['page']}" if s.get("page") else ""
                lines.append(f"> [{s['n']}] {s['source']}{page}")
        lines.append("")
    return f"conversation-{session_id}.md", "\n".join(lines)


def _purge(doc_id: str) -> str | None:
    """Remove an archived transcript from Qdrant, the documents table and the bucket."""
    url = f"{settings.ingestion_url.rstrip('/')}/v1/documents/{doc_id}"
    with httpx.Client(timeout=30) as http:
        response = http.delete(url, params={"purge_object": "true"}, headers=auth.internal_headers())
    response.raise_for_status()
    return response.json().get("object")


def delete(session_id: str, actor: str) -> dict[str, Any]:
    """Delete the conversation, its messages, notices and archive records, and the archived copy
    in the transcripts bucket and Qdrant. Tickets filed from it are kept; Google Docs are not
    touched (they may already be shared), and are listed in the result."""
    c = detail(session_id)
    keys = {a["object_key"] for a in c["archives"] if a["object_key"]}
    if c["archives"]:
        keys.add(f"transcript-{session_id}.md")  # WF5's name for it, should an archive not have reported yet
    purged = []
    for key in sorted(keys):
        doc_id = classify.make_doc_id(TRANSCRIPTS_BUCKET, key)
        try:
            removed = _purge(doc_id)
        except httpx.HTTPError as exc:
            raise ConversationError(
                502, f"the archived transcript {key} could not be removed ({exc}); nothing was deleted"
            ) from exc
        purged.append({"key": key, "doc_id": doc_id, "object": removed})
    with clients.db() as conn:
        conn.execute("DELETE FROM session_notifications WHERE session_id = %s", (session_id,))
        conn.execute("DELETE FROM transcript_archives WHERE session_id = %s", (session_id,))
        conn.execute("DELETE FROM conversations WHERE session_id = %s", (session_id,))  # messages cascade
        conn.commit()
    result = {
        "session_id": session_id,
        "messages": len(c["messages"]),
        "notices": len(c["notices"]),
        "archives": len(c["archives"]),
        "purged": purged,
        "tickets_kept": [t["ticket_ref"] for t in c["tickets"]],
        "google_docs_kept": [a["doc_url"] for a in c["archives"] if a["doc_url"]],
    }
    events.record(
        "conversation.deleted",
        f"Conversation {session_id[:8]} deleted by {actor} ({result['messages']} messages, {result['archives']} archives)",
        severity="warning",
        ref_type="conversation",
        ref_id=session_id,
        actor=actor,
        data={k: v for k, v in result.items() if k != "session_id"},
    )
    return result


def today() -> dict[str, int]:
    """Conversations started today per channel, and how many had a message blocked by a guardrail."""
    rows = memory.run(
        f"""SELECT {_CHANNEL} AS channel, count(*) AS n,
                   count(*) FILTER (WHERE EXISTS (SELECT 1 FROM messages m
                                                  WHERE m.session_id = c.session_id AND m.blocked)) AS blocked
            FROM conversations c WHERE c.created_at >= date_trunc('day', now()) GROUP BY 1""",
        fetch=True,
    )
    counts = {"chat": 0, "voice": 0, "blocked": 0}
    for r in rows or []:
        if r["channel"] in ("chat", "voice"):
            counts[r["channel"]] += int(r["n"])
        counts["blocked"] += int(r["blocked"])
    return counts
