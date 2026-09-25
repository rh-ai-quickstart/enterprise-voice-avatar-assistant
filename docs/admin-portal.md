# Admin portal: specification

> **Status: proposed, for review.** Nothing here is built yet. The decisions in the first table were
> agreed; [Open questions](#open-questions) lists the defaults chosen where no decision was made.
> Implementation starts after this document is approved, in the [phases](#phases) at the end.

The assistant files tickets, waits for approvals, archives transcripts and reports what it does, but
every one of those surfaces is Slack or Google Docs. With Slack off, approval cards are never posted
(the Slack node continues on failure), tickets stay in `pending_approval` for good, WF6 keeps
escalating them, and the ingestion, classification, SLA and knowledge-gap notices go nowhere. With
Google Docs off, an archived transcript leaves no trace except its re-ingested copy.

The admin portal is the surface that always exists: tickets and approvals, transcripts, knowledge
gaps, documents, an activity feed, integration status and an audit log, served at `/admin` on the
frontend host. Slack and Google Docs become optional sinks that the portal reflects when they are on
and does without when they are off.

## Table of contents

- [Decisions](#decisions)
- [Behaviour with Slack and Google Docs on or off](#behaviour-with-slack-and-google-docs-on-or-off)
- [Sign-in and attribution](#sign-in-and-attribution)
- [Sections](#sections)
- [Admin API](#admin-api)
- [Data model](#data-model)
- [Workflow changes](#workflow-changes)
- [Locking down the existing API](#locking-down-the-existing-api)
- [Live updates](#live-updates)
- [Chart, secrets and setup](#chart-secrets-and-setup)
- [Frontend](#frontend)
- [Tests](#tests)
- [Documentation](#documentation)
- [Out of scope](#out-of-scope)
- [Open questions](#open-questions)
- [Phases](#phases)

## Decisions

| Topic | Decision |
|---|---|
| Target | Demo-grade, done right: real sign-in, real approvals, no multi-tenancy or HA |
| Sign-in | One shared admin password, generated into a Secret |
| Roles | A single admin role |
| Attribution | The login form asks for a display name; it is the actor on every ticket event, the approver the requester hears, and the audit log entry |
| Sections | Overview, Approvals and Tickets, Conversations, Knowledge gaps, Documents, Activity, Integrations, Audit |
| Slack card after a portal decision | Updated: buttons replaced with "Approved in portal by Dana"; a late Slack click is told the ticket was already decided |
| Integration on/off | Explicit Helm values `integrations.slack.enabled` and `integrations.googleDocs.enabled`, passed to the RAG API and n8n |
| Fulfilment after a portal approval | The same n8n path as a Slack click: the RAG API calls a new WF4 webhook |
| Ticket actions | Approve or reject with a reason, cancel, mark fulfilled, edit priority and category, message the requester |
| Archive with Google Docs off | In-cluster: an archive record in PostgreSQL, re-ingestion into the `transcripts` bucket, a download in the portal; the Google Doc link is added only when Docs is on |
| Transcripts | Full-text search, filters, tickets and citations inline, archive, export and delete |
| Activity feed | A PostgreSQL events table written by the RAG API and the workflows; Slack stays an extra sink |
| Knowledge gaps | Resolve or dismiss, group similar questions, re-test retrieval, open the conversation |
| Documents | Upload, re-ingest and delete, extracted fields, ingestion jobs |
| Integrations page | Status from the Helm values plus a Test button per integration |
| Home | Overview dashboard |
| Placement | `/admin` in the existing frontend image and Route, as a separate Vite entry |
| UI toolkit | PatternFly 6 (supports React 19), loaded only by the admin entry |
| Admin API | In the RAG API under `/v1/admin/*` |
| Freshness | Server-sent events from the RAG API, fed by PostgreSQL `LISTEN/NOTIFY` |
| Existing API | nginx allowlist on the public `/api` proxy, plus a shared internal token for the endpoints n8n calls |
| Audit | An audit table and a read-only page |
| Delivery | This specification first, then phased commits on one branch and one pull request |
| Done means | Backend tests, frontend unit tests, browser end-to-end tests, documentation and setup |

## Behaviour with Slack and Google Docs on or off

The portal is on by default (`admin.enabled: true`). Everything in this table works in all four
combinations; the columns say what Slack and Google Docs add.

| Event | Always (portal and database) | Slack on adds | Google Docs on adds |
|---|---|---|---|
| Request filed from chat or voice | Ticket in `pending_approval`, event `ticket.approval_requested`, Approvals badge updates live | Approval card with buttons in `#assistant-approvals`; its channel and timestamp stored on the ticket | |
| Decision in the portal | Ticket approved or rejected with the admin's name, WF4 fulfils, requester hears the outcome, audit entry | Card updated to "Approved in portal by Dana", notice in `#assistant-tickets` | |
| Decision in Slack | Same ticket events and notice to the requester; the portal updates live | (the existing flow) | |
| Late Slack click on a decided ticket | Nothing changes on the ticket | Ephemeral reply "already approved in the portal by Dana"; card updated | |
| Pending past the SLA | Events `ticket.sla_reminder` and `ticket.escalated`, age badge on the ticket | Reminder in `#assistant-approvals`, escalation in `#assistant-tickets` | |
| Document ingested or failed | Event `document.ingested` or `document.ingest_failed` | Notice in `#assistant-ingestion` | |
| Inbox file classified | Event `document.classified`, fields in Documents | Notice in `#assistant-documents` | |
| Transcript archived | Archive record, re-ingestion, download in the portal, event `transcript.archived` | Notice in `#assistant-ingestion` | Google Doc created; its link on the archive record and in the chat |
| Daily knowledge-gap digest | Event `gaps.digest` with the grouped counts; the Knowledge gaps page is live regardless | Digest in `#assistant-knowledge-gaps` | |
| Slack or Drive call fails | Event `integration.error` with the reason; shown on Integrations | | |

When Slack is off and the portal is disabled, nothing can approve a request. The chart refuses to
render that combination while `ragApi.requestsRequireApproval` is true (see
[Chart, secrets and setup](#chart-secrets-and-setup)).

## Sign-in and attribution

- **Login.** `POST /v1/admin/login` with `{password, name}`. The password is compared in constant
  time with `ADMIN_PASSWORD`. The name is required, 2 to 40 characters, trimmed, and may not be one
  of the workflow actors (`n8n`, `assistant`, `system`, `slack`, `sla-escalation`), so a notice never
  reads "approved by system".
- **Session.** A cookie `admin_session` holding `{name, issued_at, expires_at}` signed with HMAC-SHA256
  under a key derived from `ADMIN_SESSION_SECRET` and `ADMIN_PASSWORD`. `HttpOnly`, `Secure`,
  `SameSite=Strict`, `Path=/`, lifetime `admin.sessionHours` (default 8). Changing the password or
  rotating the secret signs everyone out.
- **CSRF.** `SameSite=Strict` plus a required `X-Admin-Request: 1` header on every non-GET admin
  call; a cross-site form cannot set it.
- **Brute force.** Five failed logins per client address per minute return 429 (in memory per
  replica, which is enough for a demo). The address is the one the OpenShift router saw: nginx
  passes the last `X-Forwarded-For` entry as `X-Client-Address`, since the entries before it are
  whatever the client sent. Every attempt, failed or not, is an audit entry.
- **Not configured.** An empty `ADMIN_PASSWORD` makes login return 503 "admin portal not configured";
  `admin.enabled: false` removes the routes and the nginx locations altogether.
- **Attribution.** The session name is the `actor` on `ticket_events`, the `approver` on the ticket
  (so the voice notice says "approved by Dana"), and the `actor` on audit entries. It is not
  verified; the shared password is the trust boundary.

## Sections

Every list is paginated (50 per page), filterable from a PatternFly toolbar, and refreshed by
[live updates](#live-updates). Every destructive action asks for confirmation and states what it
removes.

### Overview (home)

Tiles that each link to their section:

- Pending approvals, with the oldest one's age against the SLA reminder and escalation thresholds
- Approved but not fulfilled for more than five minutes (fulfilment did not run, for example n8n was down)
- Tickets by status over the last 7 days
- Conversations today, split into chat and voice, and how many were blocked by guardrails
- Open knowledge gaps this week, and the top three groups
- Integration health: one line per integration, with its state from the Integrations page
- The last ten activity events

### Approvals and Tickets

- **Approvals** is the tickets list filtered to `pending_approval`, oldest first, with an SLA age
  badge (grey, amber past the reminder threshold, red past escalation) and Approve and Reject buttons
  on each row. Reject asks for a reason, which the requester hears.
- **Tickets** lists every ticket with filters for status, category, priority, requester, channel,
  created date and free text (title, description, reference).
- **Ticket detail** (a drawer from either list) shows the fields, the classification details from
  `payload`, the event timeline with actors, the linked conversation (open it in Conversations), and
  a link to the Slack card when one exists. Actions, each available only where the state machine
  allows it:

| Action | Allowed from | Effect |
|---|---|---|
| Approve | `pending_approval`, `classified` | Status change with the admin's name and note; WF4 fulfils the ticket and updates the Slack card; the requester hears the outcome |
| Reject | `pending_approval` | Status change with the admin's name and the reason; WF4 updates the Slack card; the requester hears the reason |
| Cancel | `intake`, `classified`, `pending_approval`, `approved` | Status `cancelled`; Slack card updated; the requester hears "was cancelled" |
| Mark fulfilled | `approved` | Status `fulfilled` without the workflow, for when fulfilment never ran |
| Edit priority / category | any open state | Ticket event recording the old and new values; no notice to the requester |
| Message the requester | any state, when the ticket has a conversation | A notice in the requester's conversation, which the chat shows and the avatar speaks, and a ticket event |

### Conversations (transcripts)

- **List.** User, channel (chat or voice), started, last activity, message count, tickets filed,
  archived, blocked messages. Filters: user, date range, channel, has ticket, archived, blocked.
  Search is PostgreSQL full-text search over every message, with the matching lines highlighted.
- **Detail.** The messages in order, each answer with its citations in a Sources panel like the chat's,
  the notices the requester was given, and the tickets filed from the conversation (links to their
  detail). Archive records with the Google Doc link when there is one.
- **Actions.** Archive (the same path as the chat's Archive button), export as Markdown or plain
  text, delete. Delete removes the conversation, its messages, notices and archive records, and the
  archived copy in the `transcripts` bucket and Qdrant. Tickets filed from it are kept and show
  "conversation deleted". A Google Doc is not deleted (see [Open questions](#open-questions)).

### Knowledge gaps

- **Grouped list** of open gaps over a chosen window (default 7 days): the most frequent wording,
  how many times the group was asked, the best retrieval score, the reasons (`no_hits`,
  `low_score`), and the dates. Expanding a group shows each question with a link to its conversation.
- **Resolve or dismiss** a group or a single gap, with an optional note (for example the document
  added). Resolved and dismissed gaps leave the open list and the WF7 digest.
- **Re-test** runs retrieval for the question now and shows the top passages and whether the best
  score clears the gap threshold, so an admin can check that a newly uploaded document answers it.
  Retrieval only; no language model call.

### Documents

Four tabs:

- **Documents.** Everything indexed: source, bucket, pages, chunks, ingested date. Actions:
  re-ingest, delete (Qdrant points, the database row and the object).
- **Classified.** Inbox files with document type, confidence, summary, and the extracted fields as a
  key and value table.
- **Ingestion jobs.** Recent jobs with status, duration and the error for failed ones.
- **Upload.** Drop files into `documents` (to index) or `inbox` (to classify). The object is written to
  the bucket and the bucket notification drives the usual workflows, the same path as the MinIO
  console and `scripts/load-sample-docs.sh`. Up to 25 MiB per file, the proxy's existing limit.

### Activity

A reverse-chronological feed of the `activity_events` table with filters for kind, severity and
date, each entry linking to its ticket, conversation or document. New entries appear at the top
as they happen.

### Integrations

One card per integration: Slack, Google Docs, avatar provider, n8n workflows, and the models (LLM,
embeddings, speech-to-text, text-to-speech, guardrails). Each shows its state, configuration without
secrets, the last `integration.error` event, and a **Test** button:

| Integration | State from | Test |
|---|---|---|
| Slack | `integrations.slack.enabled`, `SLACK_BOT_TOKEN` present | `auth.test`, then `conversations.list`: the five channels exist and the bot is a member |
| Google Docs | `integrations.googleDocs.enabled`, the service account and folder present | Drive `files.get` on the folder: it is reachable and `capabilities.canAddChildren` is true |
| Avatar | `voiceAgent.avatarProvider`, the provider's key present | Tavus: list the configured faces with the key; other providers: key present |
| n8n | always on | n8n API with the key the chart stores: all seven workflows exist and are active |
| Models | the endpoints in the config map | `GET /models` on each endpoint; the served model name is listed |

States: **on** (enabled and the last test passed or none failed since), **off** (disabled),
**misconfigured** (enabled but a key is missing), **failing** (the last test or call failed).

### Audit

A read-only table of `admin_audit`: time, actor, action, target (linked), and a diff of the before
and after values. Filters: actor, action, date. Recorded actions: login, failed login, logout,
ticket decision, cancel, mark fulfilled, edit, message, conversation archive, export, delete, gap
resolve and dismiss, document upload, re-ingest and delete, integration test.

## Admin API

Every route is under `/v1/admin`, requires the session cookie (except `login`), and returns 404 when
`ADMIN_ENABLED` is false. Non-GET routes also require `X-Admin-Request: 1`, write an audit entry,
and record an activity event where the table above says so.

| Method and path | Purpose |
|---|---|
| `POST /login`, `POST /logout`, `GET /me` | Session |
| `GET /overview` | Counts for the dashboard tiles |
| `GET /tickets` | List with filters `status, category, priority, requester, channel, q, from, to`, `page`, `limit` |
| `GET /tickets/{ref}` | Ticket, events, SLA age, conversation summary, Slack card link |
| `POST /tickets/{ref}/decision` | `{decision: approved \| rejected, note}` |
| `POST /tickets/{ref}/cancel`, `POST /tickets/{ref}/fulfil` | `{note}` |
| `PATCH /tickets/{ref}` | `{priority?, category?, note?}` |
| `POST /tickets/{ref}/message` | `{text}`; 409 when the ticket has no conversation |
| `GET /conversations` | List with filters `q, user, channel, from, to, has_ticket, archived, blocked` |
| `GET /conversations/{id}` | Messages with citations, notices, tickets, archives |
| `POST /conversations/{id}/archive` | The chat's archive path, attributed to the admin |
| `GET /conversations/{id}/export?format=md\|txt` | Download |
| `DELETE /conversations/{id}` | Delete as described in [Conversations](#conversations-transcripts) |
| `GET /knowledge-gaps` | `status, from, to, group=true` |
| `POST /knowledge-gaps/resolve` | `{ids, status: resolved \| dismissed, note}` |
| `POST /knowledge-gaps/{id}/retest` | Retrieval for the question, with the scores |
| `GET /documents`, `GET /documents/{doc_id}` | Indexed and classified documents |
| `POST /documents/upload` | Multipart, `bucket=documents \| inbox` |
| `POST /documents/{doc_id}/reingest`, `DELETE /documents/{doc_id}` | Through the ingestion service |
| `GET /ingestion/jobs` | The ingestion service's recent jobs |
| `GET /activity` | `kind, severity, from, to, before_id` |
| `GET /stream` | Server-sent events, see [Live updates](#live-updates) |
| `GET /integrations`, `POST /integrations/{name}/test` | Status and tests |
| `GET /audit` | `actor, action, from, to, before_id` |

The RAG API reaches the ingestion service in the cluster for document operations, and gains one
internal route for the workflows:

| Method and path | Caller | Purpose |
|---|---|---|
| `POST /v1/internal/events` | n8n | Record an activity event the RAG API cannot see itself (ingestion results, SLA reminders, Slack failures, the digest) |
| `PATCH /v1/internal/archives/{id}` | n8n (WF5) | Record the transcript's object key and whether re-ingestion succeeded |

The ingestion service gains two small changes: `POST /v1/objects` writes an object to a bucket
without starting a job (so the bucket notification drives ingestion or classification, as for a
console upload), and `DELETE /v1/documents/{doc_id}?purge_object=true` also removes the object.

Ticket changes stay in `tickets.update`, which gains `priority` and `category` fields (with an
event recording the change, replacing the raw SQL in `knowledge_gaps.escalate_ticket`) and records
the activity event for every transition, so Slack, portal and workflow changes all reach the feed
through one place.

## Data model

A new idempotent file, `services/rag-api/sql/004_admin.sql`:

```sql
-- What happened, for the portal's activity feed; Slack is an optional copy of some of these.
CREATE TABLE IF NOT EXISTS activity_events (
  id         BIGSERIAL PRIMARY KEY,
  kind       TEXT NOT NULL,                  -- ticket.approved, document.ingested, integration.error, ...
  severity   TEXT NOT NULL DEFAULT 'info',   -- info | success | warning | error
  title      TEXT NOT NULL,
  detail     TEXT,
  ref_type   TEXT,                           -- ticket | conversation | document | gap | integration
  ref_id     TEXT,
  actor      TEXT,
  source     TEXT NOT NULL,                  -- rag-api | n8n | portal
  data       JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS activity_events_created_idx ON activity_events (id DESC);
CREATE INDEX IF NOT EXISTS activity_events_ref_idx ON activity_events (ref_type, ref_id);

-- Every insert wakes the portal's event streams (LISTEN admin_events in each RAG API replica).
CREATE OR REPLACE FUNCTION activity_events_notify() RETURNS trigger AS $$
BEGIN
  PERFORM pg_notify('admin_events', json_build_object(
    'id', NEW.id, 'kind', NEW.kind, 'ref_type', NEW.ref_type, 'ref_id', NEW.ref_id)::text);
  RETURN NEW;
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS activity_events_notify ON activity_events;
CREATE TRIGGER activity_events_notify AFTER INSERT ON activity_events
  FOR EACH ROW EXECUTE FUNCTION activity_events_notify();

-- Who did what in the portal.
CREATE TABLE IF NOT EXISTS admin_audit (
  id          BIGSERIAL PRIMARY KEY,
  actor       TEXT NOT NULL,
  action      TEXT NOT NULL,
  target_type TEXT,
  target_id   TEXT,
  before      JSONB,
  after       JSONB,
  client_ip   TEXT,
  user_agent  TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS admin_audit_created_idx ON admin_audit (id DESC);

-- One row per archive of a conversation, whether or not Google Docs is on.
CREATE TABLE IF NOT EXISTS transcript_archives (
  id           BIGSERIAL PRIMARY KEY,
  session_id   TEXT NOT NULL,
  title        TEXT NOT NULL,
  doc_url      TEXT,                          -- Google Doc, when Docs is on and Drive accepted it
  object_key   TEXT,                          -- in the transcripts bucket, set by WF5
  status       TEXT NOT NULL DEFAULT 'requested',  -- requested | indexed | failed
  requested_by TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS transcript_archives_session_idx ON transcript_archives (session_id, id);

-- Knowledge gaps can be resolved or dismissed, and grouped by meaning.
ALTER TABLE knowledge_gaps ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'open';
ALTER TABLE knowledge_gaps ADD COLUMN IF NOT EXISTS resolved_by TEXT;
ALTER TABLE knowledge_gaps ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ;
ALTER TABLE knowledge_gaps ADD COLUMN IF NOT EXISTS resolution_note TEXT;
ALTER TABLE knowledge_gaps ADD COLUMN IF NOT EXISTS embedding REAL[];

-- Full-text search over transcripts.
ALTER TABLE messages ADD COLUMN IF NOT EXISTS tsv tsvector
  GENERATED ALWAYS AS (to_tsvector('english', content)) STORED;
CREATE INDEX IF NOT EXISTS messages_tsv_idx ON messages USING GIN (tsv);

CREATE INDEX IF NOT EXISTS tickets_status_idx ON tickets (status, id DESC);
CREATE INDEX IF NOT EXISTS tickets_session_idx ON tickets (session_id);
```

Other changes to existing data:

- **Slack card reference.** WF4 stores `payload.slack = {channel, ts, permalink}` on the ticket after
  posting the card, so a portal decision can update it later. `chat.update` with the bot token is
  used rather than the click's `response_url`, which expires after 30 minutes.
- **Gap grouping.** The question's embedding is stored when a gap is recorded, with one embeddings
  call made after the answer has been returned, so the answer is not delayed. The retrieval vector
  cannot be reused because a follow-up is retrieved together with the previous question. Older rows
  are filled on the first grouping request. Grouping is greedy by cosine similarity, threshold `GAP_GROUP_THRESHOLD` (default 0.85),
  over at most 2,000 open gaps in the window, in the RAG API. The PostgreSQL image has no pgvector,
  and the volumes do not need it.
- **Notices.** `notifications.pending` keeps only the newest undelivered notice per ticket; it will
  key on `(ticket_ref, kind)` so an admin's message (kind `admin_message`) does not supersede a
  decision notice, or the reverse. The voice agent and the chat already speak and show any kind.

## Workflow changes

Every Slack node goes behind an IF node `Slack on? (<node>)` reading `$env.SLACK_ENABLED`, instead of
relying on `continueOnFail` to swallow the failure; its false output carries on to whatever followed
the Slack node, unless that talks to Slack too. Slack nodes keep `continueOnFail`, and a failure is
posted as an `integration.error` event. Every HTTP node that calls the RAG API or the ingestion
service sends `Authorization: Bearer {{ $env.INTERNAL_API_TOKEN }}`. Without `SLACK_BOT_TOKEN` the
import creates a placeholder Slack credential, so the workflows are published and run with Slack off.

| Workflow | Change |
|---|---|
| WF2 ingestion | After the job finishes or fails, post `document.ingested` or `document.ingest_failed` (with the error) to `/v1/internal/events` |
| WF3 classification | None beyond the guard: the RAG API records `document.classified` in `/v1/classify` |
| WF4 approval | Store the card's `channel`, `ts` and permalink on the ticket after posting. New webhook `POST /webhook/ticket-decided` (called by the RAG API after a portal decision or cancel, with the internal token checked in the first node) joins the existing branch at "Approved?", so fulfilment and notices are shared; it then updates the card with `chat.update`. A Slack click on a ticket that is no longer pending (409 from "Record decision") fetches the ticket, updates the card with the real outcome, and replies ephemerally "already approved in the portal by Dana" |
| WF5 archival | Report the object key and job to `PATCH /v1/internal/archives/{id}` (status `indexed` or `failed`); the RAG API records `transcript.archived` |
| WF6 SLA | Post `ticket.sla_reminder` for each reminder; escalations are recorded by the RAG API's escalate endpoint |
| WF7 digest | The digest excludes resolved and dismissed gaps and returns groups; post `gaps.digest` with the counts |

The archive webhook body gains the archive record's `id`, so WF5 can report back.

## Locking down the existing API

Today nginx forwards every path under `/api/` to the RAG API, so anyone with the frontend URL can
`PATCH /api/v1/tickets/REQ-000001` to approve a ticket, read any user's memory, or fetch transcripts.
Two changes close this:

1. **nginx allowlist.** The frontend proxy forwards only what the chat UI and the portal call, and
   returns 404 for the rest:

   | Public path | Used by |
   |---|---|
   | `POST /api/v1/chat`, `POST /api/v1/chat/stream` | chat |
   | `GET /api/v1/info` | chat status bar |
   | `GET /api/v1/voice/token`, `GET /api/v1/voice/faces`, `GET /api/v1/voice/faces/{id}/poster` | voice |
   | `GET /api/v1/sessions/{id}/messages`, `.../notifications`, `POST .../notifications/ack`, `POST .../archive`, `DELETE /api/v1/sessions/{id}` | chat, keyed by the unguessable session id |
   | `/api/v1/admin/*` | portal, with the session cookie |

2. **Internal token.** `INTERNAL_API_TOKEN` is required as a bearer token on every other RAG API
   route (tickets, requests, classify, search, stale tickets and escalation, digest, transcript text,
   user memory, `/v1/internal/*`) and on the ingestion service's write routes. n8n and the RAG API
   send it; the voice agent calls only public routes. An empty token disables the check and logs a
   warning at startup, for local development only; the chart always sets one.

The n8n Route stays public because Slack must reach `/webhook/slack-interactions`. The new
`ticket-decided` webhook checks the internal token. Signature verification of Slack clicks is an
[open question](#open-questions).

## Live updates

- `GET /v1/admin/stream` is a `text/event-stream` response. Each RAG API replica holds one
  PostgreSQL connection with `LISTEN admin_events` in a background thread and fans the
  notifications out to its connected streams through asyncio queues.
- Messages are `event: activity` with `{id, kind, ref_type, ref_id}`; the portal refetches what the
  kind affects (a ticket event refreshes the Approvals badge, the tickets list and that ticket's
  drawer). A comment line every 15 seconds keeps proxies from closing an idle stream.
- On reconnect the browser sends `Last-Event-ID`; the server replays newer `activity_events` rows,
  so nothing is missed across a restart.
- nginx serves the stream location with `proxy_buffering off` and a one-hour read timeout; the
  frontend Route gets `haproxy.router.openshift.io/timeout: 1h`, as the LiveKit Route already has.
- If the stream cannot connect, the portal falls back to refetching every 30 seconds and says so in
  the header.

## Chart, secrets and setup

New values:

```yaml
admin:
  enabled: true
  sessionHours: 8

integrations:
  slack:
    enabled: false        # setup.sh and deploy.sh set true when a Slack bot token is provided
  googleDocs:
    enabled: false        # setup.sh and deploy.sh set true when a service account and folder are provided

secrets:
  admin: assistant-admin  # ADMIN_PASSWORD, ADMIN_SESSION_SECRET, INTERNAL_API_TOKEN
```

- **Config map.** `SLACK_ENABLED`, `GOOGLE_DOCS_ENABLED`, `ADMIN_ENABLED`, `ADMIN_SESSION_HOURS`, read
  by the RAG API and n8n. Google Docs archival requires both the flag and the keys; the flag on with a
  key missing shows as misconfigured.
- **Secret `assistant-admin`.** Mounted by the RAG API (all three keys), n8n and the ingestion
  service (`INTERNAL_API_TOKEN` only). `scripts/create-secrets.sh` generates the values and keeps
  existing ones on a re-run, as it does for the n8n owner password.
- **Validation.** `chart/templates/validate.yaml` fails the render when
  `ragApi.requestsRequireApproval` is true and both `integrations.slack.enabled` and `admin.enabled`
  are false: "requests need an approval surface: enable Slack or the admin portal".
- **Frontend.** The nginx allowlist and the `/admin/` and stream locations are part of
  `frontend/nginx/app.conf`; with `admin.enabled: false` the entrypoint leaves the admin locations out.
- **Setup and deploy scripts.** `deploy.sh` and `deploy-argocd.sh` (which setup step 6 runs) set
  `integrations.slack.enabled` and `integrations.googleDocs.enabled` from the keys in the integrations
  secret; setup step 5 says which will be on. Step 9 prints the admin URL and the command that reads
  the password: `oc extract secret/assistant-admin -n <project> --keys=ADMIN_PASSWORD --to=-`.
- **Upgrades.** An existing install with a Slack token keeps Slack only if
  `integrations.slack.enabled: true` is set; the release notes and `docs/SETUP.md` say so.

## Frontend

- **A second Vite entry.** `frontend/admin/index.html` loads `src/admin/main.tsx`; `vite.config.ts`
  builds both entries. The chat bundle gets no router and no PatternFly, and PatternFly's global CSS
  never reaches the chat page. nginx serves `/admin/*` from `admin/index.html`.
- **Dependencies** (admin entry only): `@patternfly/react-core`, `@patternfly/react-table`,
  `@patternfly/react-icons` (6.6, React 19 compatible), `react-router` for the sections, and
  `@tanstack/react-query` for fetching and cache invalidation driven by the event stream.
- **Layout.** PatternFly `Page` with a sidebar: Overview, Approvals (badge), Tickets, Conversations,
  Knowledge gaps, Documents, Activity, Integrations, Audit. The header shows the admin's name, the
  live-update state and Sign out. A PatternFly `LoginPage` with password and display name.
- **Routes.** `/admin`, `/admin/approvals`, `/admin/tickets[/:ref]`, `/admin/conversations[/:id]`,
  `/admin/gaps`, `/admin/documents/:tab`, `/admin/activity`, `/admin/integrations`, `/admin/audit`,
  `/admin/login`. Filters live in the query string, so a filtered view can be bookmarked.
- **Development.** `npm run dev` serves both entries; the existing `/api` proxy works for the portal
  too. The chat header gains no admin link; admins go to `/admin` directly.

## Tests

| Layer | What | Where |
|---|---|---|
| RAG API unit | Cookie signing and expiry, login rate limit, reserved names, the CSRF header, the internal token dependency, admin ticket actions against the state machine, notice keying by kind, gap grouping, integration state from flags and keys | `services/rag-api/tests/`, no database, as today |
| RAG API with PostgreSQL | Schema file idempotency (run twice), full-text search, the notify trigger reaching an event stream, admin endpoints end to end with `TestClient`, delete removing everything it claims | Same directory, run when `TEST_DATABASE_URL` is set; CI adds a PostgreSQL service to the rag-api job |
| Ingestion | `POST /v1/objects`, `purge_object`, the token on write routes | `services/ingestion/tests/` |
| Workflows | A static check of the JSON: every Slack node is behind a Slack guard, every RAG API and ingestion HTTP node sends the token, the webhook paths the RAG API calls exist | `services/rag-api/tests/test_workflows.py` |
| Chart | `helm template` fails for the no-approval-surface combination and renders with each flag combination | the CI Helm job |
| Frontend unit | Vitest and Testing Library: login form, tickets table filters, decision dialog with a reason, the stream hook's reconnect and fallback | `frontend/src/admin/**/*.test.tsx`; CI runs `npm test` |
| End to end | Playwright in Chromium against the frontend image, the RAG API, PostgreSQL and Qdrant, with a fake OpenAI-compatible server and a fake n8n that records webhooks and fulfils like WF4. Scenarios: login and failed login; file a request in the chat, approve it in the portal, the chat shows the notice with the admin's name; the same with Slack on (the fake n8n receives the card update); archive with Docs off and download; the public proxy refuses `PATCH /api/v1/tickets/...` | `frontend/e2e/`; a new CI job |

## Documentation

- `README.md`: the portal in the overview, the architecture diagram and the components table
- `docs/SETUP.md`: the integration flags in step 5, the admin URL and password in step 9
- `chart/README.md`: the new values (regenerated with `scripts/gen-values-reference.py`)
- `docs/demo-script.md`: a portal step after the Slack approval, and the Slack-off variant
- `docs/troubleshooting.md`: login refused, the stream falling back to polling behind a proxy, 401 from
  the RAG API in n8n executions (the token)
- `docs/development.md`: running the portal locally and the new tests
- `n8n/workflows/README.md`: the new webhook, `SLACK_ENABLED`, `INTERNAL_API_TOKEN`, the event posts
- A screenshot of the Approvals page in `docs/images/`

## Out of scope

Individual accounts, SSO or OpenShift login, roles and per-category approvers, retention policies,
email notifications, managing users' long-term memory, editing workflows or Helm values from the
portal, localisation.

## Open questions

Defaults chosen where no decision was made. Each can change before implementation starts.

1. **Slack click signatures.** The n8n Route is public and `/webhook/slack-interactions` does not verify
   `X-Slack-Signature`, so anyone can approve a pending ticket by posting a fake Slack payload,
   bypassing the portal's sign-in. Default: **in scope**. Verify the HMAC in the first Code node of
   WF4 with a new `SLACK_SIGNING_SECRET` in the integrations secret, collected by setup step 5.
2. **Deleting an archived conversation.** Default: remove the in-cluster copies and **leave the Google
   Doc**, naming it in the confirmation dialog, since the Doc may already be shared. The service
   account could move it to the Drive trash instead.
3. **Default for `integrations.*.enabled`.** Default: `false`, set to `true` by the setup and deploy
   scripts when the keys are provided. The alternative, `true`, keeps an upgraded Slack install
   working without a values change, but reports Slack as misconfigured on a fresh install without a
   token.
4. **Portal enabled by default.** Default: `admin.enabled: true`, since it is the only approval
   surface without Slack.
5. **Session length.** Default: 8 hours.
6. **Re-test scope.** Default: retrieval only. A "draft an answer" button would add a language model
   call per click.

## Phases

Each phase is one or more commits on the same branch, with its tests passing, and one pull request
at the end.

1. **Foundations.** `004_admin.sql`; integration flags; the internal token in the RAG API, the
   ingestion service and the workflow HTTP nodes; the nginx allowlist; admin sign-in, audit, activity
   events and `/v1/internal/events`; the event stream; chart values, secret, validation and setup
   script changes; the PostgreSQL test service in CI.
2. **Tickets and approvals.** Admin ticket endpoints and the ticket changes in `tickets.update`; WF4
   (card reference, `ticket-decided`, late clicks, Slack guard, signature check if agreed); notice
   keying; the portal shell (entry, login, layout, stream hook) with Overview, Approvals and Tickets;
   frontend unit test setup.
3. **Conversations and archival.** Full-text search, list and detail, archive records and WF5, export,
   delete.
4. **Everything else.** Activity, Knowledge gaps (grouping, resolve, re-test, WF7), Documents (the
   ingestion changes, upload, jobs), Integrations (tests), Audit; WF2, WF3 and WF6 events.
5. **End to end and documentation.** The Playwright suite and its CI job, the documentation listed
   above, the screenshot.
