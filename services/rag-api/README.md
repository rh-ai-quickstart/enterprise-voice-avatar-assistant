# RAG API

The brain of the assistant: one grounded answer path shared by the chat UI,
the voice agent, and the n8n workflows.

## Endpoints

Three kinds of route. **Public** routes serve the chat UI and the voice agent; the frontend's
nginx forwards only these (and `/v1/admin/*`) from its public `/api` proxy. **Admin** routes
(`/v1/admin/*`) need the admin portal's session cookie. Every other route is **internal** and
needs `Authorization: Bearer $INTERNAL_API_TOKEN`, which n8n, the ingestion service and the
scripts send. Public routes are marked below.

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/chat` (public) | `{"message", "session_id"?, "user_id"?, "user_name"?, "mode": "text"\|"voice"}` → answer, citations, guardrail info; `user_name` lets the assistant address the person by first name |
| POST | `/v1/chat/stream` (public) | the same answer as newline-delimited JSON while it is generated: `{"type":"delta","text":…}` lines, then `{"type":"final", …}` with citations; the voice agent speaks from the first sentence |
| POST | `/v1/search` | retrieval only: `{"query", "top_k"?}` → hits with source, page, snippet, score |
| GET | `/v1/sessions/{id}/messages` (public) | conversation history with citations |
| GET | `/v1/sessions/{id}/notifications` (public) | undelivered outcome notices for the session (ticket decisions), newest per ticket |
| POST | `/v1/sessions/{id}/notifications/ack` (public) | `{"ids": [...]}` marks notices delivered and records them in the transcript |
| POST | `/v1/sessions/{id}/archive` (public) | records an archive (kept with the transcript as archived), creates the Google Doc when that integration is on, and hands the session to the transcript archival workflow (WF5); `{requested, doc_url, archive_id}` |
| GET | `/v1/sessions/{id}/transcript` | plain-text transcript (transcript archival workflow) |
| DELETE | `/v1/sessions/{id}` (public) | forget a conversation |
| GET, PUT, DELETE | `/v1/users/{id}/memory` | long-lived facts about a user, injected into prompts |
| POST | `/v1/classify` | `{"text"}` or `{"bucket", "key"}` → `doc_type`, `fields`, `summary`, `confidence` |
| POST | `/v1/tickets` | create a ticket |
| GET | `/v1/tickets`, `/v1/tickets/{ref}` | list, or fetch by id or `REQ-000123` |
| PATCH | `/v1/tickets/{ref}` | `{"status", "actor", "note", "payload", "priority", "category", "via"}`; transitions are validated; a decision is kept as `payload.decision` (`via`: `portal`, `slack`, `workflow`) |
| GET | `/v1/tickets/stale` | tickets past the reminder and escalation thresholds (SLA workflow) |
| POST | `/v1/tickets/stale/escalate` | `?ticket_ref=&current_priority=` raises the priority one step |
| POST | `/v1/requests` | service request intake: classify, create the ticket, notify n8n |
| GET | `/v1/knowledge-gaps/digest` | `?hours=24` aggregated low-confidence questions (knowledge-gap workflow) |
| GET | `/v1/voice/token` (public) | LiveKit token; `session_id` maps to room `session-<id>`; `face_id` puts the chosen avatar face in the token |
| GET | `/v1/voice/faces` (public) | avatar faces to choose from (`AVATAR_FACES`), with the voice each one speaks with; names and thumbnail URLs from Tavus when `TAVUS_API_KEY` is set |
| GET | `/v1/voice/faces/{id}/poster` (public) | JPEG still of a face for the picker, cut from the Tavus thumbnail video and cached in the pod |
| GET | `/v1/info` (public) | active models and providers |
| PATCH | `/v1/internal/archives/{id}` | `{"status": "indexed"\|"failed", "object_key", "doc_id", "job_id", "error"}`: what re-ingestion made of an archive (WF5); records `transcript.archived` or `transcript.archive_failed` |
| POST | `/v1/internal/events` | `{"kind", "title", "severity"?, "detail"?, "ref_type"?, "ref_id"?, "actor"?, "data"?}` records an activity event the RAG API cannot see itself (n8n: ingestion results, SLA reminders, Slack failures) |
| POST | `/v1/admin/login`, `/v1/admin/logout` | `{"password", "name"}`: the shared admin password and the display name decisions are attributed to; sets the `admin_session` cookie |
| GET | `/v1/admin/me` | the signed-in name and when the session expires |
| GET | `/v1/admin/overview` | pending approvals and the oldest one's age, approved tickets not fulfilled after five minutes, tickets per status over seven days, the last ten events |
| GET | `/v1/admin/tickets` | `status`, `category`, `priority`, `requester`, `channel`, `q`, `from`, `to`, `order` (`newest`, `oldest`), `page`, `limit` → `{items, total, page, limit}`; pending tickets carry `pending_since` and `pending_minutes` |
| GET | `/v1/admin/tickets/{ref}` | the ticket with its events, SLA level, conversation summary, Slack card link and the `actions` the portal may take |
| POST | `/v1/admin/tickets/{ref}/decision` | `{"decision": "approved"\|"rejected", "note"}` (a rejection needs the note, which the requester hears); WF4 is told at `/webhook/ticket-decided` to fulfil and update the Slack card |
| POST | `/v1/admin/tickets/{ref}/cancel`, `/fulfil` | `{"note"}`: cancel an open ticket (WF4 updates the card); mark an approved one fulfilled when fulfilment never ran |
| PATCH | `/v1/admin/tickets/{ref}` | `{"priority", "category", "note"}` on an open ticket; not announced to the requester |
| GET | `/v1/admin/conversations` | `q` (full-text search over every message, matches returned with the matched words between `\x01` and `\x02`), `user`, `channel`, `from`, `to`, `has_ticket`, `archived`, `blocked`, `page`, `limit` |
| GET | `/v1/admin/conversations/{id}` | messages with citations, notices, tickets filed from it, archive records |
| POST | `/v1/admin/conversations/{id}/archive` | the chat's archive path, attributed to the admin |
| GET | `/v1/admin/conversations/{id}/export` | `?format=md\|txt`: the transcript as a download |
| GET | `/v1/admin/conversations/{id}/archives/{archive_id}/download` | the transcript as it was archived |
| DELETE | `/v1/admin/conversations/{id}` | the conversation, its messages, notices and archive records, and the archived copy in the transcripts bucket and Qdrant (through the ingestion service); tickets are kept, Google Docs are not touched |
| POST | `/v1/admin/tickets/{ref}/message` | `{"text"}`: a notice in the requester's conversation (409 when the ticket has none) |
| GET | `/v1/admin/activity` | the activity feed, newest first: `kind`, `severity`, `from`, `to`, `before_id`, `limit` |
| GET | `/v1/admin/audit` | who did what in the portal: `actor`, `action`, `from`, `to`, `before_id`, `limit` |
| GET | `/v1/admin/stream` | server-sent events (`event: activity`, `{id, kind, ref_type, ref_id}`) fed by PostgreSQL `LISTEN admin_events`; `Last-Event-ID` replays what was missed |

Admin routes answer 404 when `ADMIN_ENABLED` is false; every non-GET admin call needs the header
`X-Admin-Request: 1`. Five failed sign-ins from one address within a minute answer 429.

Interactive docs at `/docs`.

## How a chat request flows

Before retrieval, an intent check classifies the message as a question or a service request. Requests skip the LLM answer: the ticket is created, the n8n approval workflow is notified, and the reply carries the ticket. Decisions made in Slack come back as notices (`/v1/sessions/{id}/notifications`), which the voice agent speaks and the frontend shows.


1. Input guardrail (`GUARDRAILS_PROVIDER`): a flagged message gets the safe refusal and is stored as blocked.
2. Retrieval: the question is embedded and Qdrant returns the top passages above `RAG_MIN_SCORE`.
3. Prompt: system rules, numbered context, the user's long-term memory, the last `HISTORY_TURNS` turns, the question. Voice mode adds a short spoken style.
4. Generation with the LLM.
5. Output guardrail, then citations: every retrieved passage is returned, `used: true` for the ones the answer cites with `[n]`.
6. Both messages are stored in PostgreSQL for the session.

## Ticket states

`intake → classified → pending_approval → approved → fulfilled`, with `rejected`
from pending approval and `cancelled` from any open state. Every change, from the chat, n8n,
Slack or the admin portal, goes through `tickets.update`: it locks the ticket row, validates the
transition, writes the ticket event and an activity event, and queues the requester's notice. `/v1/requests` runs
intake and classification, moves the ticket to `pending_approval` when the
request needs it, and posts `{ticket, classification, channel}` to the n8n
webhook at `N8N_URL` + `N8N_REQUEST_WEBHOOK_PATH` (default `/webhook/request-intake`).

## Configuration

Environment variables, provided by the Helm chart's config map and secrets. See
`app/config.py` for every option and default.

| Variable | Purpose |
|---|---|
| `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` | chat model (OpenAI-compatible, base URL includes `/v1`) |
| `EMBEDDINGS_BASE_URL`, `EMBEDDINGS_MODEL`, `EMBEDDINGS_API_KEY` | must match the model used at ingestion |
| `GUARDRAILS_PROVIDER` (`none`, `granite-guardian`, `llama-guard`, `trustyai`), `GUARDRAILS_BASE_URL`, `GUARDRAILS_MODEL` | safety checks |
| `QDRANT_URL`, `QDRANT_API_KEY`, `QDRANT_COLLECTION`, `RAG_TOP_K`, `RAG_MIN_SCORE` | retrieval |
| `DATABASE_URL` | PostgreSQL; without it memory and tickets are disabled |
| `LIVEKIT_PUBLIC_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | voice tokens |
| `GOOGLE_SERVICE_ACCOUNT_JSON`, `GOOGLE_DOCS_FOLDER_ID` | transcript documents in Google Drive (both empty: no document, archival still re-ingests) |
| `AVATAR_PROVIDER`, `AVATAR_FACES`, `TAVUS_FACE_ID`, `TAVUS_API_KEY`, `TTS_VOICE`, `TTS_VOICE_FEMALE`, `TTS_VOICE_MALE` | face catalog served to the UI |
| `INGESTION_URL`, `N8N_URL` | neighbours used by classification and request intake |
| `INTERNAL_API_TOKEN` | bearer token for the internal routes, also sent to the ingestion service; empty disables the check (local development only) |
| `ADMIN_ENABLED`, `ADMIN_PASSWORD`, `ADMIN_SESSION_SECRET`, `ADMIN_SESSION_HOURS` | admin portal sign-in; an empty password makes sign-in answer 503; changing the password or the secret signs everyone out |
| `SLACK_ENABLED`, `GOOGLE_DOCS_ENABLED` | integrations the deployment turned on (`integrations.*.enabled` in the chart); Google Docs archival needs the flag and the keys |
| `SERVICE_CA_FILE` | extra CA for in-cluster TLS endpoints |
| `ASSISTANT_NAME`, `SYSTEM_PROMPT`, `BLOCKED_MESSAGE` | persona and wording |

## Run locally

```bash
uv sync
uv run uvicorn app.main:app --reload --port 8080
uv run pytest
# with the database tests: any PostgreSQL you can create schemas in
TEST_DATABASE_URL=postgresql://postgres@localhost:5432/postgres uv run pytest
```
