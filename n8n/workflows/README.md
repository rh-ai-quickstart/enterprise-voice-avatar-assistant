# n8n workflows

Seven workflows implement the orchestration layer. They call the in-cluster
services by their fixed names through environment variables the chart sets on
the n8n pod (`RAG_API_URL`, `INGESTION_URL`), so the exports contain no
cluster-specific values.

| File | Trigger | What it does |
|---|---|---|
| `wf1-chat-orchestration.json` | `POST /webhook/chat` | forwards `{message, session_id, user_id, mode}` to the RAG API and returns the grounded answer; entry point for forms and other channels |
| `wf2-document-ingestion.json` | `POST /webhook/object-created` (the object store's S3 notification; objects in `EVENT_BUCKETS` only) | `documents` and `transcripts` objects are sent to the ingestion service, the job is polled to completion, the result goes to the activity feed (`document.ingested` or `document.ingest_failed`) and to `#assistant-ingestion`; `inbox` objects are handed to WF3 |
| `wf3-classification-extraction.json` | `POST /webhook/classify` | calls the RAG API classifier, posts the type, summary and extracted fields to `#assistant-documents`, and forwards the JSON to `DOWNSTREAM_URL` when set |
| `wf4-request-intake-approval.json` | `POST /webhook/request-intake` (from the RAG API), `POST /webhook/slack-interactions` (Slack buttons) and `POST /webhook/ticket-decided` (decisions in the admin portal, from the RAG API with the internal token) | posts an approval card with Approve and Reject buttons to `#assistant-approvals` and stores where it was posted on the ticket (`payload.slack`); a click, once its Slack signature is verified, is recorded on the ticket; a click or a portal decision then fulfils approved requests (mock), notifies `#assistant-tickets`, and updates the card with the outcome (`chat.update`). A click on a ticket that was decided meanwhile changes nothing: the clicker gets an ephemeral reply ("already approved in the portal by Dana") and the card shows the real outcome |
| `wf5-transcript-archival.json` | `POST /webhook/archive-transcript` with `{session_id, title, doc_url, archive_id}` (from the RAG API, with the internal token) | fetches the session transcript, re-ingests it into the `transcripts` bucket as `transcript-<session>.md`, waits for the job, and reports the result to the RAG API (`PATCH /v1/internal/archives/{archive_id}`: indexed or failed); the Google Doc, when that integration is on, is created by the RAG API before |
| `wf6-sla-escalation.json` | Schedule (every 15 min) | asks the RAG API for tickets stuck in `pending_approval`, counting from when each started waiting: once past the reminder threshold (default 60 minutes) a ticket is reminded once, in `#assistant-approvals` and as a `ticket.sla_reminder` event; each time another escalation threshold passes (240 minutes by default, then 480, ...) its priority goes up one level through the RAG API, which records `ticket.escalated`, and `#assistant-tickets` is told. The thresholds are RAG API settings (`SLA_REMINDER_MINUTES`, `SLA_ESCALATION_MINUTES`) |
| `wf7-knowledge-gap-digest.json` | Schedule (weekdays 9 AM) | fetches the open knowledge gaps of the last 24 hours grouped by meaning (resolved and dismissed ones are left out), records a `gaps.digest` event, and posts the digest to `#assistant-knowledge-gaps` so content owners know what to add |

The workflow JSON files live in `chart/files/n8n-workflows/` so the Helm chart can ship them: an init container on the n8n Deployment imports and publishes them on first start (values `n8n.workflows.*`), and creates the Slack credential from `SLACK_BOT_TOKEN` in the integrations secret when it is set. Without a token it imports a placeholder credential of the same type, so the workflows are published and ingestion, approvals and archival run with Slack off.

Two rules every workflow follows (`services/rag-api/tests/test_workflows.py` checks them):

- **Slack is optional.** Every Slack node sits behind an IF node named `Slack on? (<node>)` that
  reads `$env.SLACK_ENABLED` (`integrations.slack.enabled` in the chart). With Slack off the
  workflow carries on without it; the admin portal is where approvals and notices appear.
- **Calls to the RAG API and the ingestion service send the internal token**:
  `Authorization: Bearer {{ $env.INTERNAL_API_TOKEN }}`, from the admin secret.
- **Results and failures reach the activity feed.** What only n8n sees is posted to
  `POST /v1/internal/events` (ingestion results, SLA reminders, the digest, refused Slack
  clicks), and every node that talks to Slack continues on failure into
  `Slack failed? (<node>)`, which posts an `integration.error` for Slack. The admin portal's
  Activity and Integrations pages show them; a failed post never stops a workflow.

## Tests

`services/rag-api/tests/test_workflows.py` checks the files statically (the rules above, the
webhook paths, the connections). `n8n/tests/` runs them: the chart's n8n image imports and
publishes the workflows as the chart does, a stub stands in for the RAG API and the ingestion
service and records every call, and `scenarios.mjs` plays the RAG API, Slack and the object store
against the webhooks, twice: with Slack off, and with Slack on but unreachable, where each
scenario also names the Slack nodes that must report their failure. The schedule triggers of WF6
and WF7 become webhooks (`run-wf6`, `run-wf7`) for the run. CI runs it; locally, with Docker:

```bash
n8n/tests/run.sh
```

## Import

Create an API key in n8n (Settings, then *n8n API*), then:

```bash
export N8N_URL=https://$(oc get route n8n -n voice-avatar-assistant -o jsonpath='{.spec.host}')
export N8N_API_KEY=<the key>
scripts/import-workflows.sh
```

The script creates or updates the workflows by name and activates them. Run it
again after editing the JSON. To capture changes made in the n8n editor, export
the workflow (menu, *Download*) over the file here and commit it.

## Credentials

Nothing to add by hand. The Slack credential (bot token) used by the Slack nodes of WF2 to WF7
is created by the chart from `SLACK_BOT_TOKEN`; the token comes from the Slack app created from
`n8n/slack-app-manifest.json`, whose interactivity request URL is
`https://<n8n host>/webhook/slack-interactions`. Google Docs needs no n8n credential: the RAG API
creates the transcript document with a service account before it calls WF5.

## Environment variables read by the workflows

The chart sets them on the n8n pod; `DOWNSTREAM_URL` comes from `n8n.extraEnv`.

| Variable | Purpose |
|---|---|
| `RAG_API_URL`, `INGESTION_URL` | in-cluster service URLs |
| `INTERNAL_API_TOKEN` | bearer token for the RAG API and the ingestion service (secret `assistant-admin`) |
| `SLACK_ENABLED` | `true` posts to Slack; anything else skips the Slack nodes (`integrations.slack.enabled`) |
| `SLACK_SIGNING_SECRET` | the Slack app's signing secret (integrations secret); WF4 refuses clicks that are not signed with it and records an `integration.error` event |
| `NODE_FUNCTION_ALLOW_BUILTIN` | `crypto`, set by the chart: WF4's signature check computes an HMAC in a Code node |
| `EVENT_BUCKETS` | the buckets whose object notifications WF2 acts on (`objectStore.eventBuckets`: `documents,inbox`) |
| `S3_EVENT_KEYS_ENCODED` | `true` for an object store that URL-encodes keys in its notifications (AWS S3); VersityGW does not |
| `DOWNSTREAM_URL` | optional HTTP endpoint that receives classified document JSON (WF3) |

## Manual tests

```bash
N8N=https://$(oc get route n8n -n voice-avatar-assistant -o jsonpath='{.spec.host}')
TOKEN=$(oc extract secret/assistant-admin -n voice-avatar-assistant --keys=INTERNAL_API_TOKEN --to=-)
# WF1: text question through n8n
curl -s -X POST $N8N/webhook/chat -H 'Content-Type: application/json' -d '{"message":"How often must administrator passwords be rotated?"}'
# WF2: simulate the object store's notification, which carries the token in its URL (or upload a file in its web UI)
curl -s -X POST "$N8N/webhook/object-created?token=$TOKEN" -H 'Content-Type: application/json' -d '{"Records":[{"eventName":"s3:ObjectCreated:Put","s3":{"bucket":{"name":"documents"},"object":{"key":"password-policy.md"}}}]}'
# WF3: classify an object from the inbox bucket (WF2 calls it with the token)
curl -s -X POST $N8N/webhook/classify -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"bucket":"documents","key":"password-policy.md"}'
```

WF4's request intake and WF5 only act on calls from the RAG API (with the internal token), so
start them through it: file a request in the chat, or archive a conversation with
`NS=voice-avatar-assistant scripts/archive-transcript.sh [session id]`.

```bash
# WF6 and WF7 are schedule-triggered; test the underlying API endpoints directly, from the
# RAG API pod, which has the internal token in its environment:
rag() { oc exec deploy/rag-api -n voice-avatar-assistant -- sh -c "curl -s -H \"Authorization: Bearer \$INTERNAL_API_TOKEN\" http://localhost:8080$1"; }
rag /v1/tickets/stale | python3 -m json.tool                     # stale tickets (SLA)
rag '/v1/knowledge-gaps/digest?hours=24' | python3 -m json.tool  # knowledge gap digest
```

Executions and their inputs and outputs are visible under *Executions* in n8n.

## Hardening notes

- **Slack request signatures are verified.** The n8n Route is public, so WF4's
  first step after `/webhook/slack-interactions` checks `X-Slack-Signature`: an
  HMAC-SHA256 of `v0:<timestamp>:<raw body>` with `SLACK_SIGNING_SECRET`, and a
  timestamp within five minutes. Unsigned, forged or replayed clicks, and every
  click while the secret is empty, are refused before the ticket is touched.
- **Calls from the RAG API need the internal token.** `/webhook/request-intake`,
  `/webhook/archive-transcript`, `/webhook/ticket-decided` and `/webhook/classify`
  act only on calls that carry `Authorization: Bearer $INTERNAL_API_TOKEN`. The
  checks are false while the token is empty, so an empty token closes them rather
  than opening them.
- **Object notifications carry the token in their URL.** The object store cannot
  send headers, so the chart gives it
  `http://n8n:5678/webhook/object-created?token=<INTERNAL_API_TOKEN>`, and WF2
  ignores notifications without it: nobody outside can make it re-index or
  classify objects.
- **Slack text is escaped.** Titles, questions, names, file names and what the
  language model wrote go into Slack with `&`, `<` and `>` escaped, so text from
  the chat cannot become a link or a mention. A refused click is a
  `slack.click_refused` event (one a minute per reason), which does not mark Slack
  as failing.
