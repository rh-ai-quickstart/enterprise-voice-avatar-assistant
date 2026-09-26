# Ingestion service

Turns documents in object storage into searchable chunks: download from the object store,
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
| POST | `/v1/events/s3` | S3 bucket notification (AWS-style Records); created objects are ingested, removed ones deleted. The chart routes the object store's notifications through n8n (WF2) instead |
| GET | `/v1/jobs`, `/v1/jobs/{id}` | job status: `queued`, `running`, `done`, `failed` |
| POST | `/v1/extract` | `{"key": "inbox/invoice.pdf", "bucket": "inbox"}` → the document's text as Markdown, for classification |
| GET | `/v1/documents` | documents recorded in PostgreSQL (501 when no database is configured) |
| DELETE | `/v1/documents/{doc_id}` | remove a document's vectors and record |

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
| `S3_ENDPOINT_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET` | `http://localhost:7070`, `assistant`, `assistant-secret`, `documents` | object storage (VersityGW in the chart) |
| `S3_BUCKETS` | `documents,inbox,transcripts` | buckets created at startup when missing |
| `INGEST_BUCKETS` | `documents,transcripts` | buckets whose events are ingested; others are ignored |
| `QDRANT_URL`, `QDRANT_API_KEY`, `QDRANT_COLLECTION` | `http://localhost:6333`, none, `documents` | vector store |
| `EMBEDDINGS_BASE_URL`, `EMBEDDINGS_MODEL`, `EMBEDDINGS_API_KEY` | vLLM defaults | OpenAI-compatible embeddings |
| `SERVICE_CA_FILE` | unset | extra CA for in-cluster TLS endpoints |
| `DATABASE_URL` | unset | enables the `documents` and `ingestion_jobs` tables |
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
