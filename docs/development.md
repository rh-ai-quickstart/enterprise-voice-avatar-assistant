# Development guide

How to work on the services locally against a deployed cluster, run the tests, rebuild the sample documents, and change the workflows. The deployment itself is described in the [README](../README.md); every value is in [chart/README.md](../chart/README.md).

## Layout

| Path | What | Stack |
|---|---|---|
| `services/rag-api` | retrieval, memory, guardrails, classification, tickets, notices | Python 3.12, FastAPI, uv |
| `services/ingestion` | Docling parsing, chunking, embeddings, Qdrant | Python 3.12, FastAPI, uv |
| `services/voice-agent` | LiveKit Agents worker: Whisper, RAG API, Kokoro, avatar providers | Python 3.12, uv |
| `frontend` | React chat UI with citations, voice and avatar | Vite, React, TypeScript, nginx |
| `chart` | Helm chart; `files/n8n-workflows` holds the workflows n8n imports | Helm 3 |
| `scripts` | deploy, secrets, tests, sample documents, n8n helpers | bash, Python |

## Prerequisites

`oc` logged in to a cluster with the quickstart deployed, `uv`, Node 22, `helm`, and for the sample documents `pandoc` and LibreOffice. Podman or Docker only if you build images locally; CI builds them on every push to `main`.

## Running a service locally

Each Python service reads its configuration from environment variables with the same names the chart puts in the `assistant-config` config map and the secrets. The simplest way to work locally is to port-forward what the service needs and export the variables.

```bash
NS=voice-avatar-assistant
oc port-forward -n $NS svc/postgres 5432:5432 &
oc port-forward -n $NS svc/qdrant 6333:6333 &
oc port-forward -n $NS svc/llama-3-1-8b-instruct-predictor 8000:8080 &
oc port-forward -n $NS svc/bge-m3-predictor 8001:8080 &
oc port-forward -n $NS svc/object-store 7070:7070 &
```

```bash
# values the chart would set; read the full list with: oc get cm assistant-config -n $NS -o yaml
export LLM_BASE_URL=http://localhost:8000/v1 LLM_MODEL=llama-3.1-8b-instruct
export EMBEDDINGS_BASE_URL=http://localhost:8001/v1 EMBEDDINGS_MODEL=bge-m3
export QDRANT_URL=http://localhost:6333 QDRANT_API_KEY=$(oc extract secret/assistant-qdrant -n $NS --keys=QDRANT_API_KEY --to=- 2>/dev/null)
export DATABASE_URL=$(oc extract secret/assistant-postgres -n $NS --keys=DATABASE_URL --to=- 2>/dev/null | sed 's/@postgres:/@localhost:/')
export GUARDRAILS_PROVIDER=none
```

RAG API:

```bash
cd services/rag-api && uv sync && uv run uvicorn app.main:app --reload --port 8080
```

Ingestion (needs `S3_ENDPOINT_URL=http://localhost:7070` and `S3_ACCESS_KEY`, `S3_SECRET_KEY` from `assistant-object-store`; the Docling models download on first run):

```bash
cd services/ingestion && uv sync && uv run uvicorn app.main:app --reload --port 8081
```

Frontend, proxied to a local or port-forwarded RAG API:

```bash
cd frontend && npm install && VITE_API_PROXY=http://localhost:8080 npm run dev
```

Voice agent: it needs a LiveKit server it can register with and one the browser can reach, so develop it against the cluster's LiveKit with `LIVEKIT_URL` set to the public `wss://` Route and the key pair from `assistant-livekit`, or run `livekit-server --dev` locally with the frontend pointed at it. `uv run python agent.py dev` runs the worker in the foreground; `scripts/e2e_room_test.py` in the service joins a room, speaks a question through TTS, and waits for the answer.

## Tests and checks

```bash
(cd services/rag-api && uv run ruff check . && uv run pytest -q)
(cd services/ingestion && uv run ruff check . && uv run pytest -q)
(cd services/voice-agent && uv run ruff check . && uv run pytest -q)
(cd frontend && npm run build)
helm lint chart -f chart/values-demo-cluster.yaml
```

CI runs the same on every push and pull request (`.github/workflows/ci.yaml`). Against a running deployment, `NS=<project> scripts/demo-preflight.sh -f <values file>` checks models, the test pod and the n8n webhooks; `scripts/check-index.sh` lists what is indexed; `scripts/n8n-executions.sh` shows workflow runs with node errors.

## Images

`.github/workflows/build-images.yaml` builds the four images on every push to `main` that touches a service, pushes them tagged `sha-<7 chars>` and `latest`, and commits the new tags into `chart/values-demo-cluster.yaml`, which Argo CD rolls out on the demo cluster. To build one locally:

```bash
podman build -t enterprise-voice-avatar-assistant-rag-api -f services/rag-api/Containerfile services/rag-api
```

Note for the ingestion image: it swaps `opencv-python` for the headless build after `uv sync`, because Docling's OCR dependency needs a GL library the UBI image does not ship.

## Sample documents

Sources are Markdown in `data/sample-docs/src/`. Edit or add a file, register it in `scripts/build-sample-docs.sh` with the format to render (`md`, `docx`, `pdf`), run the script, and commit the sources with the rendered files. `NS=<project> scripts/load-sample-docs.sh [files]` uploads them: policies to `documents`, invoices and contracts to `inbox`. Keep the facts consistent across documents; the demo script quotes them.

## Workflows

The n8n workflows live in `chart/files/n8n-workflows/`, one JSON file each with a fixed `id`. Edit them in the n8n editor, export the workflow JSON, keep the `id` and the `credentials` references on the Slack nodes, and save the file back. Update a running instance with `N8N_URL=... N8N_API_KEY=... scripts/import-workflows.sh` (keeps credentials attached in the editor) or let a fresh install import them at first start (`n8n.workflows` values). Every workflow reads the service URLs from `$env.RAG_API_URL` and `$env.INGESTION_URL`.

## Database schema

`services/rag-api/sql/*.sql` are applied in order by the RAG API at start-up (idempotent `CREATE TABLE IF NOT EXISTS`). Add a new numbered file for a change; do not edit an applied one.

## Coding conventions

Python: ruff for lint and format (configured per service in `pyproject.toml`), type hints on public functions, no secrets in code or values. Frontend: TypeScript strict, components under `src/components`, API calls only through `src/lib/api.ts`. Chart: every value documented with a comment above it in `values.yaml`; run `scripts/gen-values-reference.py --write` afterwards.
