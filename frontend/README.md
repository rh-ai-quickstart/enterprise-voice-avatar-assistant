# Frontend

React chat interface for the assistant: a chat panel with clickable citation
markers, a sources panel showing the retrieved passages (cited ones first), a
persistent AI disclaimer, and a status strip that reports the active models
from the RAG API. Voice mode connects to the LiveKit room and shows the avatar video when a
provider is configured; the header has New conversation and Archive transcript.

Calls go to `/api/...`, which nginx proxies to the RAG API inside the cluster
(`RAG_API_UPSTREAM`, default `rag-api:8080`). The proxy forwards only the routes the chat UI and
the admin portal call ([nginx/api-allowlist.conf](nginx/api-allowlist.conf)) and answers 404 for
every other RAG API route, which needs the internal token; `ADMIN_ENABLED=false` refuses
`/api/v1/admin/*` as well. The session id is kept in
`sessionStorage`, the optional user name in `localStorage`; both are sent with
every question so memory works across turns.

## Admin portal

`/admin` is a second entry of the same build ([admin/index.html](admin/index.html),
[src/admin](src/admin)): PatternFly 6, React Router and TanStack Query, loaded by the portal only,
so none of it reaches the chat bundle. Sign-in, approvals and tickets talk to the RAG API's
`/v1/admin` routes; live updates come from `/api/v1/admin/stream` (server-sent events), with a
refresh every 30 seconds when the stream cannot be opened. nginx serves `/admin/*` from
`admin/index.html` ([nginx/admin.conf](nginx/admin.conf)).

## Develop

```bash
npm install
VITE_API_PROXY=http://localhost:8080 npm run dev     # RAG API on localhost:8080, or a port-forward
# chat: http://localhost:3000/   admin portal: http://localhost:3000/admin/
npm test                                            # unit tests (Vitest, Testing Library)
npm run build
nginx/test-proxy.sh                                 # the /api allowlist and /admin in the nginx image (docker)
```

To point the dev server at the cluster:

```bash
oc port-forward -n voice-avatar-assistant svc/rag-api 8080:8080
```

## Container

```bash
podman build -t assistant-frontend -f Containerfile .
podman run -p 8080:8080 -e RAG_API_UPSTREAM=host.containers.internal:8080 assistant-frontend
```
