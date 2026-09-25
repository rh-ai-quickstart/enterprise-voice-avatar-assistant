"""Optional PostgreSQL persistence for documents and jobs. Every function is a no-op without DATABASE_URL."""

import json
import logging
from typing import Any

from .config import settings

log = logging.getLogger("ingestion.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
  doc_id      TEXT PRIMARY KEY,
  source      TEXT NOT NULL,
  source_uri  TEXT NOT NULL,
  doc_type    TEXT,
  pages       INTEGER,
  chunks      INTEGER,
  metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
  extracted   JSONB,
  ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS ingestion_jobs (
  job_id      TEXT PRIMARY KEY,
  doc_id      TEXT NOT NULL,
  source_uri  TEXT NOT NULL,
  status      TEXT NOT NULL,
  error       TEXT,
  chunks      INTEGER,
  pages       INTEGER,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ
);
"""


def enabled() -> bool:
    return bool(settings.database_url)


def _connect():
    import psycopg

    return psycopg.connect(settings.database_url, connect_timeout=5)


def init_schema() -> None:
    if not enabled():
        log.info("DATABASE_URL not set; documents and jobs are kept in memory only")
        return
    try:
        with _connect() as conn:
            conn.execute(SCHEMA)
            conn.commit()
        log.info("database schema ready")
    except Exception as exc:  # noqa: BLE001
        log.warning("database unavailable, continuing without persistence: %s", exc)


def record_job(job: Any) -> None:
    if not enabled():
        return
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO ingestion_jobs (job_id, doc_id, source_uri, status, error, chunks, pages, created_at, finished_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (job_id) DO UPDATE SET
                  status = EXCLUDED.status, error = EXCLUDED.error, chunks = EXCLUDED.chunks,
                  pages = EXCLUDED.pages, finished_at = EXCLUDED.finished_at
                """,
                (job.job_id, job.doc_id, f"s3://{job.bucket}/{job.key}", job.status, job.error, job.chunks, job.pages,
                 job.created_at, job.finished_at),
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("could not record job %s: %s", job.job_id, exc)


def upsert_document(doc_id: str, source: str, source_uri: str, pages: int | None, chunks: int,
                    metadata: dict[str, Any]) -> None:
    if not enabled():
        return
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO documents (doc_id, source, source_uri, pages, chunks, metadata)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (doc_id) DO UPDATE SET
                  source = EXCLUDED.source, source_uri = EXCLUDED.source_uri, pages = EXCLUDED.pages,
                  chunks = EXCLUDED.chunks, metadata = EXCLUDED.metadata, updated_at = now()
                """,
                (doc_id, source, source_uri, pages, chunks, json.dumps(metadata)),
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("could not upsert document %s: %s", doc_id, exc)


def delete_document(doc_id: str) -> None:
    if not enabled():
        return
    try:
        with _connect() as conn:
            conn.execute("DELETE FROM documents WHERE doc_id = %s", (doc_id,))
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("could not delete document %s: %s", doc_id, exc)


def source_uri(doc_id: str) -> str | None:
    """Where a document came from (s3://bucket/key), or None when it is not recorded."""
    if not enabled():
        return None
    try:
        with _connect() as conn:
            row = conn.execute("SELECT source_uri FROM documents WHERE doc_id = %s", (doc_id,)).fetchone()
        return row[0] if row else None
    except Exception as exc:  # noqa: BLE001
        log.warning("could not look up document %s: %s", doc_id, exc)
        return None


def list_documents(limit: int = 200) -> list[dict[str, Any]] | None:
    if not enabled():
        return None
    from psycopg.rows import dict_row

    with _connect() as conn:
        conn.row_factory = dict_row
        rows = conn.execute(
            "SELECT doc_id, source, source_uri, pages, chunks, metadata, ingested_at FROM documents "
            "ORDER BY ingested_at DESC LIMIT %s",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]
