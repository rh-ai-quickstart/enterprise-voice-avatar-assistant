# Architecture

An enterprise assistant that answers from company documents by text or by voice, with a
talking avatar, on Red Hat OpenShift AI. The README's "Architecture diagrams" and "How it
works" sections are the user-facing description. This page is the map a developer needs:
what each part is, which port it listens on, and how a turn moves through it.

## Components (one Helm release, `chart/`)

| Component | Code | Port | Role |
|---|---|---|---|
| frontend | `frontend/` (React, Vite, nginx) | 8080 | Chat with citations, voice panel, face picker; the admin portal at `/admin`. nginx serves the app and proxies `/api` to the RAG API through an allowlist (`frontend/nginx/`) |
| rag-api | `services/rag-api` (FastAPI) | 8080 | Answers (retrieval, LLM, citations, guardrails), conversation memory, intent detection (question or request), tickets and approvals, notices, knowledge gaps, voice tokens, faces and posters, admin API, integrations page |
| ingestion | `services/ingestion` (FastAPI, Docling) | 8080 | Parses uploaded documents, chunks, embeds (bge-m3) into Qdrant, emits object-created events |
| voice-agent | `services/voice-agent` (LiveKit Agents 1.8) | 8081 (health) | Joins each voice room: VAD, Whisper, turn detection, the RAG API stream, Kokoro, and the avatar |
| livekit | upstream image | 7880 (ws), 7881 (tcp), 7882 (udp), 5349 (TURN/TLS) | WebRTC SFU. Browsers reach media through TURN over TLS on a passthrough Route |
| openspline | upstream OpenSpline, installed at first start | 7860 | Self-hosted avatar: animates a portrait to the agent's speech on a GPU and publishes video into the room. Only when `voiceAgent.avatarProvider=openspline` |
| tts | Kokoro (`ghcr.io/remsky/kokoro-fastapi-*`) | 8880 | OpenAI-compatible speech endpoint |
| models | KServe InferenceServices (`chart/templates/models-kserve.yaml`) | 8080 | Whisper, and optionally the LLM, BGE-M3 and Granite Guardian; or external endpoints (Models-as-a-Service) |
| n8n | upstream image | 5678 | Seven workflows (below). The `n8n-setup` hook Job creates the owner, the API key and the credentials, and imports and publishes the workflows |
| postgres | RHEL PostgreSQL image | 5432 | Conversations, messages, tickets, activity events, documents, ingestion jobs |
| qdrant | upstream image | 6333 | Vector store |
| object-store | VersityGW (S3 on a PVC) | 7070 | Uploaded documents (replaced MinIO when its images vanished in 2026-09) |

Images built from this repository are published by CI to
`quay.io/rh-ai-quickstart/enterprise-voice-avatar-assistant-<service>` (frontend, rag-api,
ingestion, voice-agent). OpenSpline has no image: see [openspline.md](openspline.md).

## A text turn
1. The browser POSTs to `/api/v1/chat/stream` on the frontend Route. nginx forwards only
   allowlisted routes.
2. The RAG API detects the intent, question or request. The intent detection sees the
   previous turn, but only when it carries a `REQ-` reference.
3. **A question:** retrieval in Qdrant (bge-m3), an answer from the LLM with citations,
   streamed sentence by sentence. Guardrails are `none` in the demo.
4. **A request:** a ticket is filed and waits for approval in the admin portal, or in Slack
   when it is on (WF4). The answer never claims to have logged something unless a ticket
   exists. A sentence-level guard enforces this (PR #11).
5. Memory, activity events and knowledge gaps go to PostgreSQL.

## A voice turn
1. The browser asks the RAG API for a voice token (`/v1/voice/token`, with the chosen face).
   The room is `session-<conversation id>.<random>`, a new room for every voice start.
   Reusing the name broke Tavus when ending and restarting voice.
2. LiveKit dispatches the voice agent, which joins and starts the avatar.
   - **Tavus:** a cloud participant joins through the public LiveKit URL.
   - **OpenSpline:** the GPU worker joins through the in-cluster address.

   Both publish on the agent's behalf (the `lk.publish_on_behalf` attribute).
3. Audio in:
   - Silero VAD: 0.3 s of silence ends speech; at least 0.25 s counts as speech.
   - Whisper: batch, through LiveKit's StreamAdapter.
   - Turn detector v1-mini: decides whether the turn is over.
   - `Assistant.stt_node`: drops Whisper's noise transcripts.
   - `on_user_turn_completed`: checks again.
4. `Assistant.llm_node` replaces the LLM with the RAG API stream, the same answer path as
   text. It skips noise too, because preemptive generation reaches it before the turn check.
5. Text to speech: Kokoro returns raw PCM. A `FlushSentinel` after each sentence starts
   synthesis without waiting for the next sentence.
6. Audio out:
   - **With an avatar:** a data stream to the avatar (`DataStreamAudioOutput`), which
     renders and publishes video and audio.
   - **Without:** the agent publishes audio itself.
7. The agent sends each finished turn (question, answer, citations, ticket) on the data
   channel `assistant`. The browser turns these into chat bubbles. The greeting has
   `kind: "greeting"` and is shown once per conversation.
8. A notification loop polls the RAG API for ticket decisions and speaks them.

Details, measurements and the pitfalls are in [voice-pipeline.md](voice-pipeline.md).

## Documents
1. **Upload:** in the admin portal, or `scripts/load-sample-docs.sh` (15 sample documents in
   `data/sample-docs`, the fictional "Example Corp").
2. **Ingestion:** Docling parses, then the ingestion service chunks and embeds into Qdrant,
   and emits the object-created event itself.
3. **n8n:** WF2 (document ingestion) and WF3 (classification and extraction) post to Slack.

## n8n workflows (`chart/files/n8n-workflows`)

| Workflow | Does |
|---|---|
| WF1 chat orchestration | A chat webhook that asks the RAG API and responds (a chat entry for other channels) |
| WF2 document ingestion | Reacts to object-created events: inbox files go to WF3 for classification, the others to ingestion; waits for the job, records it, posts to `#assistant-ingestion` |
| WF3 classification and extraction | Classifies a document (invoices etc.), posts to `#assistant-documents`, optionally forwards to a downstream system |
| WF4 request intake and approval | Approval cards in `#assistant-approvals`, portal decisions, Slack signature check |
| WF5 transcript archival | Archives conversations to Google Docs, reports back to the RAG API |
| WF6 SLA escalation | One reminder per wait for stale tickets |
| WF7 knowledge gap digest | Weekdays at 9 AM, a digest of unanswered questions to `#assistant-knowledge-gaps` |

Webhooks that the RAG API calls carry the internal token. Tests: `n8n/tests/run.sh`, 24
scenarios with Slack on and off.

## Avatar providers
`voiceAgent.avatarProvider` picks one: `none`, `openspline` (self-hosted, no key), `tavus`,
`simli` or `hedra` (cloud, by key).
- **Faces:** for Tavus they come from `voiceAgent.faces`; for OpenSpline from
  `openspline.faces`, with portraits in `chart/files/portraits`.
- **Voice:** the face's gender picks the Kokoro voice (`models.tts.voices`), unless the face
  pins one.
- **Missing key:** a cloud provider without its key counts as `none`, and the deploy
  scripts set `none` when there is no Tavus key.

## Configuration
- **Single source:** every setting is a chart value (`chart/values.yaml`; reference in
  `chart/README.md`). The services read environment variables from the `assistant-config`
  ConfigMap and the secrets.
- **Per cluster:** `chart/values-demo-cluster.yaml` pins the image tags (rewritten by CI) and
  demo choices such as the Tavus faces.
- **Per environment:** setup writes its choices to `~/.assistant-setup/values-object.json`,
  layered on top.
