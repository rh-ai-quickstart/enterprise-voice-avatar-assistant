from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class IngestRequest(BaseModel):
    key: str = Field(description="Object key inside the bucket, for example policies/leave-policy.pdf")
    bucket: str | None = Field(default=None, description="Bucket name; defaults to the documents bucket")
    doc_id: str | None = Field(
        default=None, description="Stable document id; derived from the bucket and key when omitted"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Free-form metadata stored with every chunk"
    )


class JobStatus(BaseModel):
    job_id: str
    doc_id: str
    bucket: str
    key: str
    status: str = Field(description="queued | running | done | failed")
    chunks: int | None = None
    pages: int | None = None
    error: str | None = None
    created_at: datetime
    finished_at: datetime | None = None


class IngestAccepted(BaseModel):
    job_id: str
    doc_id: str
    status: str


class EventResponse(BaseModel):
    accepted: list[JobStatus] = Field(default_factory=list)
    deleted: list[str] = Field(default_factory=list)
    ignored: list[str] = Field(default_factory=list)


class ExtractRequest(BaseModel):
    key: str
    bucket: str | None = None
    max_chars: int = Field(default=60000, ge=1000, le=500000)


class ExtractResponse(BaseModel):
    doc_id: str
    source: str
    pages: int | None = None
    truncated: bool = False
    text: str = Field(description="Document content exported as Markdown")


class DocumentInfo(BaseModel):
    doc_id: str
    source: str
    source_uri: str
    pages: int | None = None
    chunks: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    ingested_at: datetime | None = None
