"""Conversation history, long-term user memory, and document records in PostgreSQL.

Every function degrades gracefully: without DATABASE_URL, or when the database is
unreachable, reads return empty results and writes are skipped with a warning, so
the chat path keeps working (without memory).
"""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import clients
from .config import settings

log = logging.getLogger("rag.memory")
SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def enabled() -> bool:
    return bool(settings.database_url)


def init_schema() -> None:
    if not enabled():
        log.info("DATABASE_URL not set; running without memory, tickets, and document records")
        return
    try:
        with clients.db() as conn:
            # One replica at a time: the files recreate a trigger, which concurrent starts would race on
            conn.execute("SELECT pg_advisory_xact_lock(4715)")
            for path in sorted(SQL_DIR.glob("*.sql")):
                conn.execute(path.read_text())
            conn.commit()
        log.info("database schema ready")
    except Exception as exc:  # noqa: BLE001
        log.warning("database unavailable, continuing without persistence: %s", exc)


def _run(query: str, params: tuple = (), fetch: bool = False):
    if not enabled():
        return [] if fetch else None
    try:
        with clients.db() as conn:
            cur = conn.execute(query, params)
            rows = cur.fetchall() if fetch else None
            conn.commit()
            return rows
    except Exception as exc:  # noqa: BLE001
        log.warning("database operation failed: %s", exc)
        return [] if fetch else None


def run(query: str, params: tuple = (), fetch: bool = False):
    """Run one statement with the same graceful degradation as every other helper here."""
    return _run(query, params, fetch)


def select_page(
    table: str,
    columns: str,
    equals: dict[str, Any],
    since: datetime | None = None,
    until: datetime | None = None,
    before_id: int | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """One page of an append-only table, newest first, filtered by exact values and a time range.
    `table`, `columns` and the keys of `equals` are constants in the code, never request input."""
    where, params = [], []
    for column, value in equals.items():
        if value is not None:
            where.append(f"{column} = %s")
            params.append(value)
    for clause, value in (("created_at >= %s", since), ("created_at < %s", until), ("id < %s", before_id)):
        if value is not None:
            where.append(clause)
            params.append(value)
    query = f"SELECT {columns} FROM {table}" + (" WHERE " + " AND ".join(where) if where else "")
    rows = _run(query + " ORDER BY id DESC LIMIT %s", (*params, limit), fetch=True)
    return [dict(r) for r in rows or []]


def ensure_conversation(session_id: str, user_id: str | None, channel: str) -> None:
    _run(
        """INSERT INTO conversations (session_id, user_id, channel) VALUES (%s, %s, %s)
           ON CONFLICT (session_id) DO UPDATE SET user_id = COALESCE(EXCLUDED.user_id, conversations.user_id),
           updated_at = now()""",
        (session_id, user_id, channel),
    )


def history(session_id: str, limit_messages: int) -> list[dict[str, str]]:
    rows = _run(
        "SELECT role, content FROM messages WHERE session_id = %s AND blocked = false ORDER BY id DESC LIMIT %s",
        (session_id, limit_messages),
        fetch=True,
    )
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows or [])]


def append(
    session_id: str, role: str, content: str, citations: list[Any] | None = None, blocked: bool = False
) -> None:
    payload = json.dumps([c.model_dump() if hasattr(c, "model_dump") else c for c in (citations or [])])
    _run(
        "INSERT INTO messages (session_id, role, content, citations, blocked) VALUES (%s, %s, %s, %s::jsonb, %s)",
        (session_id, role, content, payload, blocked),
    )


def messages(session_id: str) -> list[dict[str, Any]]:
    rows = _run(
        "SELECT role, content, citations, blocked, created_at FROM messages WHERE session_id = %s ORDER BY id",
        (session_id,),
        fetch=True,
    )
    return [dict(r) for r in rows or []]


def clear(session_id: str) -> None:
    _run("DELETE FROM conversations WHERE session_id = %s", (session_id,))


def transcript(session_id: str) -> str:
    lines = []
    for m in messages(session_id):
        stamp = m["created_at"].strftime("%Y-%m-%d %H:%M") if m.get("created_at") else ""
        lines.append(f"[{stamp}] {m['role']}: {m['content']}")
    return "\n".join(lines)


def get_user_memory(user_id: str) -> dict[str, str]:
    rows = _run("SELECT key, value FROM user_memory WHERE user_id = %s ORDER BY key", (user_id,), fetch=True)
    return {r["key"]: r["value"] for r in rows or []}


def set_user_memory(user_id: str, key: str, value: str) -> None:
    _run(
        """INSERT INTO user_memory (user_id, key, value) VALUES (%s, %s, %s)
           ON CONFLICT (user_id, key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
        (user_id, key, value),
    )


def delete_user_memory(user_id: str, key: str) -> None:
    _run("DELETE FROM user_memory WHERE user_id = %s AND key = %s", (user_id, key))


def record_extraction(
    doc_id: str, source: str, source_uri: str, doc_type: str, extracted: dict[str, Any]
) -> None:
    _run(
        """INSERT INTO documents (doc_id, source, source_uri, doc_type, extracted)
           VALUES (%s, %s, %s, %s, %s::jsonb)
           ON CONFLICT (doc_id) DO UPDATE SET doc_type = EXCLUDED.doc_type, extracted = EXCLUDED.extracted,
           updated_at = now()""",
        (doc_id, source, source_uri, doc_type, json.dumps(extracted)),
    )


def request_archive(session_id: str) -> dict:
    """Archive the session: create the Google Doc here (service account, when configured), then
    ask n8n (WF5) to re-ingest the transcript and post the Slack notice with the link."""
    import httpx

    from . import gdocs

    text = transcript(session_id)
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")
    title = f"Assistant transcript {session_id[:8]} {stamp}"
    doc_url = gdocs.create_document(title, f"Assistant transcript {session_id}\n\n{text}") if text else None
    url = settings.n8n_url.rstrip("/") + settings.n8n_archive_webhook_path
    requested = False
    try:
        with httpx.Client(timeout=10) as http:
            response = http.post(
                url, json={"session_id": session_id, "title": title, "doc_url": doc_url or ""}
            )
        if response.status_code >= 400:
            log.warning("n8n archive webhook returned %s", response.status_code)
        else:
            requested = True
    except httpx.HTTPError as exc:
        log.warning("n8n archive webhook unreachable: %s", exc)
    return {"requested": requested, "doc_url": doc_url}
