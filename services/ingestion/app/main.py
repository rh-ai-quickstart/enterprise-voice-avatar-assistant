"""Ingestion service API.

Endpoints
  GET  /healthz                 liveness
  GET  /readyz                  readiness (Qdrant and the documents bucket reachable)
  POST /v1/ingest               ingest one object from a bucket (202, returns a job)
  POST /v1/ingest/upload        upload a file; it is stored in the bucket and ingested (202)
  POST /v1/events/s3            S3 bucket notification (created -> ingest, removed -> delete); the chart
                                routes notifications through n8n (WF2) instead
  GET  /v1/jobs, /v1/jobs/{id}  job status
  POST /v1/extract              convert one object and return its text as Markdown (for classification)
  GET  /v1/documents            documents known to the database (501 without a database)
  DELETE /v1/documents/{doc_id} remove a document's vectors and record; ?purge_object=true also the object

Every POST and DELETE needs Authorization: Bearer $INTERNAL_API_TOKEN (the RAG API and n8n send it).
"""

import asyncio
import hmac
import logging
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from . import db, storage, vectorstore
from .config import settings
from .events import CREATED, REMOVED, parse_s3_event
from .jobs import Job, JobManager
from .pipeline import extract_text, make_doc_id
from .schemas import (
    DocumentInfo,
    EventResponse,
    ExtractRequest,
    ExtractResponse,
    IngestAccepted,
    IngestRequest,
    JobStatus,
)

log = logging.getLogger("ingestion")
jobs = JobManager(max_concurrent=settings.max_concurrent_jobs)


@asynccontextmanager
async def lifespan(_: FastAPI):
    logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log.info("ingestion service starting; qdrant=%s embeddings=%s buckets=%s",
             settings.qdrant_url, settings.embeddings_base_url, sorted(settings.ingest_bucket_set))
    if not settings.internal_api_token:
        log.warning("INTERNAL_API_TOKEN is empty: the write routes are open to anyone who can reach this service")
    await asyncio.to_thread(db.init_schema)
    buckets = asyncio.create_task(_ensure_buckets())
    yield
    buckets.cancel()


app = FastAPI(title="Enterprise voice avatar assistant - ingestion", version="0.1.0", lifespan=lifespan)


async def _ensure_buckets() -> None:
    """Create the missing buckets, retrying until the object store answers (it may start later)."""
    delay = 2.0
    while True:
        try:
            created = await asyncio.to_thread(storage.ensure_buckets, settings.bucket_list)
            log.info("buckets ready: %s%s", ", ".join(settings.bucket_list), f" (created {', '.join(created)})" if created else "")
            return
        except Exception as exc:  # noqa: BLE001 - the object store is not up yet
            log.info("object store not ready (%s); retrying in %.0fs", type(exc).__name__, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)


def require_internal_token(request: Request) -> None:
    token = settings.internal_api_token
    if not token:
        return
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(value.strip().encode(), token.encode()):
        raise HTTPException(status_code=401, detail="this route needs the internal API token",
                            headers={"WWW-Authenticate": "Bearer"})


WRITE = [Depends(require_internal_token)]


def _status(job: Job) -> JobStatus:
    return JobStatus(**job.__dict__)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
async def readyz():
    problems: dict[str, str] = {}
    for name, check in (("qdrant", vectorstore.ping), ("s3", lambda: storage.head_bucket(settings.s3_bucket))):
        try:
            await asyncio.to_thread(check)
        except Exception as exc:  # noqa: BLE001
            problems[name] = str(exc)[:200]
    if problems:
        return JSONResponse(status_code=503, content={"status": "not ready", "problems": problems})
    return {"status": "ready"}


@app.post("/v1/ingest", status_code=202, response_model=IngestAccepted, dependencies=WRITE)
async def ingest(request: IngestRequest):
    bucket = request.bucket or settings.s3_bucket
    doc_id = request.doc_id or make_doc_id(bucket, request.key)
    job = await jobs.submit(bucket, request.key, doc_id, request.metadata)
    return IngestAccepted(job_id=job.job_id, doc_id=doc_id, status=job.status)


@app.post("/v1/ingest/upload", status_code=202, response_model=IngestAccepted, dependencies=WRITE)
async def ingest_upload(file: Annotated[UploadFile, File()], bucket: Annotated[str | None, Form()] = None):
    bucket = bucket or settings.s3_bucket
    key = file.filename or "upload"
    data = await file.read()
    await asyncio.to_thread(storage.put_bytes, bucket, key, data, file.content_type)
    doc_id = make_doc_id(bucket, key)
    job = await jobs.submit(bucket, key, doc_id, {})
    return IngestAccepted(job_id=job.job_id, doc_id=doc_id, status=job.status)


@app.post("/v1/events/s3", response_model=EventResponse, dependencies=WRITE)
async def s3_event(event: dict):
    response = EventResponse()
    for kind, bucket, key in parse_s3_event(event):
        if bucket not in settings.ingest_bucket_set:
            response.ignored.append(f"{bucket}/{key}")
            continue
        doc_id = make_doc_id(bucket, key)
        if kind == CREATED:
            job = await jobs.submit(bucket, key, doc_id, {})
            response.accepted.append(_status(job))
        elif kind == REMOVED:
            await asyncio.to_thread(vectorstore.delete_document, doc_id)
            await asyncio.to_thread(db.delete_document, doc_id)
            response.deleted.append(doc_id)
    return response


@app.post("/v1/extract", response_model=ExtractResponse, dependencies=WRITE)
async def extract(request: ExtractRequest):
    bucket = request.bucket or settings.s3_bucket
    try:
        result = await asyncio.to_thread(extract_text, bucket, request.key, request.max_chars)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"could not extract text: {type(exc).__name__}: {exc}"[:300]) from exc
    return ExtractResponse(doc_id=make_doc_id(bucket, request.key), source=request.key, **result)


@app.get("/v1/jobs", response_model=list[JobStatus])
def list_jobs(limit: int = 50):
    return [_status(j) for j in jobs.recent(limit)]


@app.get("/v1/jobs/{job_id}", response_model=JobStatus)
def get_job(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return _status(job)


@app.get("/v1/documents", response_model=list[DocumentInfo])
async def list_documents():
    rows = await asyncio.to_thread(db.list_documents)
    if rows is None:
        raise HTTPException(status_code=501, detail="document listing needs DATABASE_URL")
    return rows


@app.delete("/v1/documents/{doc_id}", dependencies=WRITE)
async def delete_document(doc_id: str, purge_object: bool = False):
    """Vectors and record; with purge_object the object in its bucket too (found from the record)."""
    removed = None
    if purge_object:
        uri = await asyncio.to_thread(db.source_uri, doc_id)
        if uri and uri.startswith("s3://"):
            bucket, _, key = uri.removeprefix("s3://").partition("/")
            await asyncio.to_thread(storage.delete_object, bucket, key)
            removed = uri
    await asyncio.to_thread(vectorstore.delete_document, doc_id)
    await asyncio.to_thread(db.delete_document, doc_id)
    return {"deleted": doc_id, "object": removed}
