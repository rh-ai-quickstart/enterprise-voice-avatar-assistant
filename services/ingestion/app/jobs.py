"""In-process job queue. Jobs run in worker threads with a concurrency limit; status is mirrored to PostgreSQL when configured."""

import asyncio
import logging
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from . import db, pipeline

log = logging.getLogger("ingestion.jobs")

MAX_KEPT = 500
# Jobs waiting for a worker; beyond this, submissions are refused rather than queued without limit
MAX_WAITING = 200


class QueueFull(Exception):
    pass


@dataclass
class Job:
    job_id: str
    doc_id: str
    bucket: str
    key: str
    metadata: dict[str, Any] = field(default_factory=dict)
    status: str = "queued"
    error: str | None = None
    chunks: int | None = None
    pages: int | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None


class JobManager:
    def __init__(self, max_concurrent: int = 2) -> None:
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._semaphore = asyncio.Semaphore(max(1, max_concurrent))

    async def submit(self, bucket: str, key: str, doc_id: str, metadata: dict[str, Any]) -> Job:
        waiting = sum(1 for j in self._jobs.values() if j.status == "queued")
        if waiting >= MAX_WAITING:
            raise QueueFull(f"{waiting} ingestion jobs are waiting; try again shortly")
        job = Job(job_id=uuid.uuid4().hex[:12], doc_id=doc_id, bucket=bucket, key=key, metadata=metadata)
        self._jobs[job.job_id] = job
        while len(self._jobs) > MAX_KEPT:
            self._jobs.popitem(last=False)
        db.record_job(job)
        asyncio.create_task(self._run(job))
        return job

    async def _run(self, job: Job) -> None:
        async with self._semaphore:
            job.status = "running"
            db.record_job(job)
            try:
                result = await asyncio.to_thread(
                    pipeline.ingest_document, job.bucket, job.key, job.doc_id, job.metadata
                )
                job.chunks = result.get("chunks")
                job.pages = result.get("pages")
                job.status = "done"
            except Exception as exc:
                log.exception("job %s failed for s3://%s/%s", job.job_id, job.bucket, job.key)
                job.status = "failed"
                job.error = f"{type(exc).__name__}: {exc}"[:500]
            job.finished_at = datetime.now(UTC)
            db.record_job(job)

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def recent(self, limit: int = 50) -> list[Job]:
        return list(reversed(list(self._jobs.values())))[:limit]
