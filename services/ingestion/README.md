# Ingestion service

Turns documents in object storage into searchable chunks: download from MinIO,
convert with [Docling](https://docling-project.github.io/docling/), split into
token-bounded chunks that keep page numbers and headings, embed with the
embeddings model, and upsert into Qdrant. Re-ingesting the same object replaces
its previous chunks.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/healthz` | liveness |
| GET | `/readyz` | readiness: Qdrant and the documents bucket reachable |
| POST | `/v1/ingest` | `{"key": "policies/leave.pdf", "bucket": "documents", "metadata": {}}` → 202 with a job id |
| POST | `/v1/ingest/upload` | multipart `file` (and optional `bucket`); stored in the bucket, then ingested |
| POST | `/v1/events/minio` | MinIO bucket notification webhook; created objects are ingested, removed ones deleted |
| GET | `/v1/jobs`, `/v1/jobs/{id}` | job status: `queued`, `running`, `done`, `failed` |
| POST | `/v1/extract` | `{"key": "inbox/invoice.pdf", "bucket": "inbox"}` → the document's text as Markdown, for classification |
| GET | `/v1/documents` | documents recorded in PostgreSQL (501 when no database is configured) |
| DELETE | `/v1/documents/{doc_id}` | remove a document's vectors and record; `?purge_object=true` also removes the object from its bucket (found from the record) |

Every POST and DELETE needs `Authorization: Bearer $INTERNAL_API_TOKEN`; the RAG API and the n8n
workflows send it. The service has no Route, so the reads stay open inside the cluster.

Interactive docs are served at `/docs`.

Document ids are stable: `uuid5(bucket/key)`. Every Qdrant point carries
`doc_id`, `source` (the object key), `source_uri`, `page`, `chunk_index`,
`headings`, `text`, and `metadata`, which is what the RAG API turns into
citations.

## Configuration

All settings are environment variables (see `app/config.py`). The Helm chart
provides them through the `assistant-config` config map and the secrets.

| Variable | Default | Notes |
|---|---|---|
| `S3_ENDPOINT_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET` | MinIO defaults | object storage |
| `INGEST_BUCKETS` | `documents,transcripts` | buckets whose events are ingested; others are ignored |
| `QDRANT_URL`, `QDRANT_API_KEY`, `QDRANT_COLLECTION` | `http://localhost:6333`, none, `documents` | vector store |
| `EMBEDDINGS_BASE_URL`, `EMBEDDINGS_MODEL`, `EMBEDDINGS_API_KEY` | vLLM defaults | OpenAI-compatible embeddings |
| `SERVICE_CA_FILE` | unset | extra CA for in-cluster TLS endpoints |
| `DATABASE_URL` | unset | enables the `documents` and `ingestion_jobs` tables |
| `INTERNAL_API_TOKEN` | unset | bearer token the write routes require; unset disables the check (local development only) |
| `CHUNK_MAX_TOKENS`, `CHUNK_TOKENIZER` | `512`, `BAAI/bge-m3` | chunk size measured with the embedding model's tokenizer |
| `OCR_ENABLED` | `false` | OCR for scanned PDFs and images (slower, needs the OCR models) |
| `MAX_CONCURRENT_JOBS` | `2` | parallel conversions |

## Run locally

```bash
uv sync
uv run uvicorn app.main:app --reload --port 8081
uv run pytest
```

The container image bakes in the Docling models and the tokenizer:

```bash
podman build -t assistant-ingestion -f Containerfile .
```
