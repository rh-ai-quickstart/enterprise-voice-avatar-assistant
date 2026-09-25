"""Transcript archival: one record per archive of a conversation, whether or not Google Docs is on.

The RAG API keeps the transcript as archived (the portal offers it as a download), creates the
Google Doc when that integration is on, and hands the archive to WF5, which re-ingests the
transcript into the transcripts bucket and reports back with PATCH /v1/internal/archives/{id}.
"""

import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from . import auth, events, gdocs, memory
from .config import settings

log = logging.getLogger("rag.archives")

COLUMNS = "id, session_id, title, doc_url, object_key, doc_id, job_id, status, error, requested_by, created_at, updated_at"


def request(session_id: str, requested_by: str | None = None) -> dict[str, Any]:
    """Archive a conversation; {requested, doc_url, archive_id}, and a reason when nothing was done."""
    text = memory.transcript(session_id)
    if not text:
        return {
            "requested": False,
            "doc_url": None,
            "archive_id": None,
            "reason": "the conversation has no messages",
        }
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")
    title = f"Assistant transcript {session_id[:8]} {stamp}"
    rows = memory.run(
        """INSERT INTO transcript_archives (session_id, title, requested_by, transcript)
           VALUES (%s, %s, %s, %s) RETURNING id""",
        (session_id, title, requested_by, text),
        fetch=True,
    )
    archive_id = int(rows[0]["id"]) if rows else None
    doc_url = gdocs.create_document(title, f"Assistant transcript {session_id}\n\n{text}")
    if doc_url and archive_id:
        memory.run(
            "UPDATE transcript_archives SET doc_url = %s, updated_at = now() WHERE id = %s",
            (doc_url, archive_id),
        )
    requested = _hand_to_workflow(session_id, title, doc_url, archive_id)
    if not requested and archive_id:
        record_result(archive_id, "failed", error="the archival workflow (n8n) could not be reached")
    return {"requested": requested, "doc_url": doc_url, "archive_id": archive_id}


def _hand_to_workflow(session_id: str, title: str, doc_url: str | None, archive_id: int | None) -> bool:
    url = settings.n8n_url.rstrip("/") + settings.n8n_archive_webhook_path
    body = {"session_id": session_id, "title": title, "doc_url": doc_url or "", "archive_id": archive_id}
    try:
        with httpx.Client(timeout=10) as http:
            response = http.post(url, json=body, headers=auth.internal_headers())
        if response.status_code >= 400:
            log.warning("n8n archive webhook returned %s", response.status_code)
            return False
        return True
    except httpx.HTTPError as exc:
        log.warning("n8n archive webhook unreachable: %s", exc)
        return False


def record_result(
    archive_id: int,
    status: str,
    object_key: str | None = None,
    doc_id: str | None = None,
    job_id: str | None = None,
    error: str | None = None,
) -> dict[str, Any] | None:
    """What re-ingestion made of an archive (indexed or failed); None for an unknown archive."""
    rows = memory.run(
        f"""UPDATE transcript_archives SET status = %s, object_key = COALESCE(%s, object_key),
                  doc_id = COALESCE(%s, doc_id), job_id = COALESCE(%s, job_id), error = %s, updated_at = now()
           WHERE id = %s RETURNING {COLUMNS}""",
        (status, object_key, doc_id, job_id, error, archive_id),
        fetch=True,
    )
    if not rows:
        return None
    archive = dict(rows[0])
    short = archive["session_id"][:8]
    if status == "indexed":
        events.record(
            "transcript.archived",
            f"Transcript {short} archived"
            + (" to Google Docs and" if archive["doc_url"] else "")
            + " indexed",
            severity="success",
            ref_type="conversation",
            ref_id=archive["session_id"],
            actor=archive["requested_by"],
            data={"archive_id": archive_id, "object_key": object_key, "doc_url": archive["doc_url"]},
        )
    else:
        events.record(
            "transcript.archive_failed",
            f"Transcript {short} was not archived",
            severity="error",
            detail=error,
            ref_type="conversation",
            ref_id=archive["session_id"],
            actor=archive["requested_by"],
            data={"archive_id": archive_id},
        )
    return archive


def for_session(session_id: str) -> list[dict[str, Any]]:
    rows = memory.run(
        f"SELECT {COLUMNS} FROM transcript_archives WHERE session_id = %s ORDER BY id DESC",
        (session_id,),
        fetch=True,
    )
    return [dict(r) for r in rows or []]


def transcript(session_id: str, archive_id: int) -> dict[str, Any] | None:
    """The transcript as it was archived, for the portal's download."""
    rows = memory.run(
        "SELECT title, transcript, created_at FROM transcript_archives WHERE session_id = %s AND id = %s",
        (session_id, archive_id),
        fetch=True,
    )
    return dict(rows[0]) if rows else None
