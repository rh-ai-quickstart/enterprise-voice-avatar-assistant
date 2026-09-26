"""Documents for the admin portal: what is indexed and classified, and the actions on it.

The listing reads the documents and ingestion_jobs tables the ingestion service keeps in the same
database. Uploads, re-ingestion and deletion go through the ingestion service, which owns the
object store and Qdrant: an upload only writes the object, and the object store's notification
drives the usual workflows (WF2 indexes documents, WF3 classifies the inbox).
"""

import logging
from typing import Any

import httpx

from . import auth, events, memory
from .config import settings

log = logging.getLogger("rag.documents")

COLUMNS = """d.doc_id, d.source, d.source_uri, split_part(substr(d.source_uri, 6), '/', 1) AS bucket,
             d.doc_type, d.pages, d.chunks, d.metadata, d.extracted, d.ingested_at, d.updated_at"""


class DocumentError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _require_db() -> None:
    if not memory.enabled():
        raise DocumentError(503, "documents need DATABASE_URL")


def search(
    kind: str = "all",
    q: str | None = None,
    bucket: str | None = None,
    page: int = 1,
    limit: int = 50,
) -> tuple[list[dict[str, Any]], int]:
    """indexed: documents with chunks in Qdrant; classified: inbox files with extracted fields."""
    _require_db()
    where, params = [], []
    if kind == "indexed":
        where.append("COALESCE(d.chunks, 0) > 0")
    elif kind == "classified":
        where.append("d.extracted IS NOT NULL")
    if q:
        where.append("d.source ILIKE %s")
        params.append(f"%{q}%")
    if bucket:
        where.append("d.source_uri LIKE %s")
        params.append(f"s3://{bucket}/%")
    rows = memory.run(
        f"""SELECT {COLUMNS}, count(*) OVER () AS total FROM documents d
            {"WHERE " + " AND ".join(where) if where else ""}
            ORDER BY d.updated_at DESC, d.doc_id LIMIT %s OFFSET %s""",
        (*params, limit, (page - 1) * limit),
        fetch=True,
    )
    items = [dict(r) for r in rows or []]
    total = int(items[0]["total"]) if items else 0
    for item in items:
        item.pop("total")
    return items, total


def detail(doc_id: str) -> dict[str, Any]:
    _require_db()
    rows = memory.run(f"SELECT {COLUMNS} FROM documents d WHERE d.doc_id = %s", (doc_id,), fetch=True)
    if not rows:
        raise DocumentError(404, "document not found")
    document = dict(rows[0])
    document["jobs"] = jobs(doc_id=doc_id, limit=10)
    return document


def jobs(doc_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """Recent ingestion jobs, newest first (the table exists once the ingestion service started)."""
    rows = memory.run(
        """SELECT job_id, doc_id, source_uri, status, error, chunks, pages, created_at, finished_at,
                  EXTRACT(EPOCH FROM (finished_at - created_at)) AS seconds
           FROM ingestion_jobs"""
        + (" WHERE doc_id = %s" if doc_id else "")
        + " ORDER BY created_at DESC LIMIT %s",
        ((doc_id, limit) if doc_id else (limit,)),
        fetch=True,
    )
    return [dict(r) for r in rows or []]


def _ingestion(method: str, path: str, **kwargs) -> dict[str, Any]:
    url = settings.ingestion_url.rstrip("/") + path
    try:
        with httpx.Client(timeout=120) as http:
            response = http.request(method, url, headers=auth.internal_headers(), **kwargs)
    except httpx.HTTPError as exc:
        raise DocumentError(502, f"the ingestion service could not be reached: {exc}") from exc
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise DocumentError(
            response.status_code if response.status_code < 500 else 502, f"ingestion: {detail}"
        )
    return response.json()


def upload(bucket: str, filename: str, data: bytes, content_type: str | None, actor: str) -> dict[str, Any]:
    """Write the file to the bucket; the object store's notification starts the workflow."""
    result = _ingestion(
        "POST",
        "/v1/objects",
        files={"file": (filename, data, content_type or "application/octet-stream")},
        data={"bucket": bucket},
    )
    events.record(
        "document.uploaded",
        f"{actor} uploaded {result['key']} to {bucket}",
        detail="indexed next" if bucket == "documents" else "classified next" if bucket == "inbox" else None,
        ref_type="document",
        ref_id=result["doc_id"],
        actor=actor,
        source="portal",
        data={"bucket": bucket, "key": result["key"], "size": result["size"]},
    )
    return result


def _location(doc_id: str) -> tuple[str, str]:
    uri = detail(doc_id)["source_uri"]
    bucket, _, key = uri.removeprefix("s3://").partition("/")
    return bucket, key


def reingest(doc_id: str, actor: str) -> dict[str, Any]:
    bucket, key = _location(doc_id)
    result = _ingestion("POST", "/v1/ingest", json={"bucket": bucket, "key": key, "doc_id": doc_id})
    events.record(
        "document.reingest_requested",
        f"{actor} re-ingests {key}",
        ref_type="document",
        ref_id=doc_id,
        actor=actor,
        source="portal",
        data={"job_id": result.get("job_id")},
    )
    return result


def delete(doc_id: str, actor: str) -> dict[str, Any]:
    """Its vectors in Qdrant, its record and the object in the bucket."""
    document = detail(doc_id)
    result = _ingestion("DELETE", f"/v1/documents/{doc_id}", params={"purge_object": "true"})
    events.record(
        "document.deleted",
        f"{actor} deleted {document['source']}",
        severity="warning",
        ref_type="document",
        ref_id=doc_id,
        actor=actor,
        source="portal",
        data={"source_uri": document["source_uri"], "object": result.get("object")},
    )
    return {"doc_id": doc_id, "source": document["source"], "object": result.get("object")}
