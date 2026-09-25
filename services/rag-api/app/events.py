"""The activity feed: what happened, recorded in PostgreSQL whether or not Slack is on.

The RAG API records what it sees itself; n8n posts the rest to /v1/internal/events (ingestion
results, SLA reminders, Slack failures, the digest). Every insert notifies the portal's event
streams through a trigger (sql/004_admin.sql, stream.py).
"""

import json
import logging
from datetime import datetime
from typing import Any

from . import memory

log = logging.getLogger("rag.events")

SEVERITIES = ("info", "success", "warning", "error")
REF_TYPES = ("ticket", "conversation", "document", "gap", "integration")
KIND_PATTERN = r"^[a-z][a-z_]*(\.[a-z][a-z_]*)+$"
COLUMNS = "id, kind, severity, title, detail, ref_type, ref_id, actor, source, data, created_at"


def record(
    kind: str,
    title: str,
    *,
    severity: str = "info",
    detail: str | None = None,
    ref_type: str | None = None,
    ref_id: str | None = None,
    actor: str | None = None,
    source: str = "rag-api",
    data: dict[str, Any] | None = None,
) -> int | None:
    """Add an event to the feed; its id, or None without a database."""
    rows = memory.run(
        """INSERT INTO activity_events (kind, severity, title, detail, ref_type, ref_id, actor, source, data)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb) RETURNING id""",
        (kind, severity, title, detail, ref_type, ref_id, actor, source, json.dumps(data or {})),
        fetch=True,
    )
    if not rows:
        return None
    log.info("activity %s: %s", kind, title)
    return int(rows[0]["id"])


def recent(
    kind: str | None = None,
    severity: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    before_id: int | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    return memory.select_page(
        "activity_events", COLUMNS, {"kind": kind, "severity": severity}, since, until, before_id, limit
    )


def latest_id() -> int:
    rows = memory.run("SELECT COALESCE(MAX(id), 0) AS id FROM activity_events", fetch=True)
    return int(rows[0]["id"]) if rows else 0


def after(last_id: int, limit: int = 500) -> list[dict[str, Any]]:
    """What the event stream sends for events newer than last_id, oldest first."""
    rows = memory.run(
        "SELECT id, kind, ref_type, ref_id FROM activity_events WHERE id > %s ORDER BY id LIMIT %s",
        (last_id, limit),
        fetch=True,
    )
    return [dict(r) for r in rows or []]
