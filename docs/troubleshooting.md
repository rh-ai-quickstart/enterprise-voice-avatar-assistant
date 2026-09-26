# Troubleshooting

Symptoms first, then the cause, the command that confirms it, and the fix. Everything here was hit while building the demo cluster (OpenShift 4.20, OpenShift AI 3.5, 4x L4).

## Install and values

**`helm install` fails with "models.<x>.deploy is false but models.<x>.endpoint is empty".** Intentional: a model set to `deploy: false` needs `endpoint` (OpenAI-compatible base URL including `/v1`) and `servedModelName`. Check the names the endpoint serves: `curl <endpoint>/models`. Or run `MODELS=maas scripts/deploy.sh` to be prompted.

**Pods stay in `CreateContainerConfigError` with "secret not found".** The chart never creates secrets. Run `NAMESPACE=<project> scripts/create-secrets.sh`, then `oc get secret -n <project> | grep assistant-`.

**PodSecurity warnings ("would violate PodSecurity restricted") on `oc apply`.** A pod without `seccompProfile`, `runAsNonRoot`, dropped capabilities or `allowPrivilegeEscalation: false`. Every chart pod carries all four through the `assistant.securityContext` helper; a raw `oc run` does not. Use `scripts/test-services.sh` instead of ad hoc pods.

**`oc get application` returns the wrong kind.** The short name clashes with another CRD on OpenShift; use `oc get applications.argoproj.io -n openshift-gitops`.

**Service starts with `LIVEKIT_PORT=tcp://…` or similar in its environment and crashes.** Kubernetes service links inject variables named after every Service in the namespace. Every chart pod sets `enableServiceLinks: false`; keep that when adding pods.

**Image pull errors from Docker Hub (`toomanyrequests`).** Docker Hub's anonymous pull limit is shared by the whole cluster egress IP. Use images from `ghcr.io`, `quay.io` or `registry.redhat.io`; the chart already does (n8n and the VersityGW object store from ghcr, PostgreSQL from quay).

## Models and GPUs

**InferenceService predictor pod Pending, event "Insufficient nvidia.com/gpu".** Count what is allocated: `oc describe node <gpu node> | grep -A3 'nvidia.com/gpu'`, and `oc get pods -A -o json | jq '[.items[] | select(.spec.containers[].resources.limits."nvidia.com/gpu")] | .[] | .metadata.namespace + "/" + .metadata.name'`. Models served from the dashboard in other projects count too. Free a GPU or point the model at an existing endpoint (`deploy: false`).

**Model pod restarts on every chart change, needing a second GPU during the rollout.** KServe defaults to a rolling update. The chart sets `deploymentStrategy: {type: Recreate}` and `serving.kserve.io/autoscalerClass: external` on every InferenceService; keep them when adding models.

**Llama crash-loops with a KV cache allocation error.** Without `--max-model-len` vLLM reserves cache for the full 128k context, too much for a 24 GiB card. The chart passes `--max-model-len=16384`.

**BGE-M3 fails with "unrecognized arguments: --task=embed".** vLLM 0.21 and later removed `--task`. The chart uses `--runner=pooling`, valid from vLLM 0.10; check the version in the pod: `oc exec <bge pod> -c kserve-container -- python -c "import vllm; print(vllm.__version__)"`.

**Granite Guardian refuses to start: "default chat template is no longer allowed".** The modelcar ships no chat template. The chart mounts `chart/files/granite-guardian-3.3-chat-template.jinja` and passes `--chat-template`; the verdict format is `<score>yes|no</score>`.

**Requests to a reused Whisper (LLMInferenceService) fail with TLS errors.** OpenShift AI 3.5 LLMInferenceService workloads serve HTTPS on 8000 with a service-CA certificate. Use an `https://` endpoint and trust `/var/run/secrets/kubernetes.io/serviceaccount/service-ca.crt` (`SERVICE_CA_FILE` in the config map; `NODE_EXTRA_CA_CERTS` for n8n).

**Models fail after a GPU operator or driver change.** Pin the vLLM runtime image to a digest verified with your driver; `chart/values.yaml` carries one verified with GPU operator 25.3.4 and 26.3.3 (driver 580, CUDA 13). Read your versions: `oc get nodes -L nvidia.com/cuda.driver-version.full,nvidia.com/cuda.runtime-version.full`.

## Ingestion and documents

**PDF ingestion fails with `ImportError: libGL.so.1`.** Docling's OCR dependency pulls `opencv-python`, whose wheel needs a GL library the UBI image lacks. The ingestion image swaps it for `opencv-python-headless` at build time; if you rebuild the image, keep that step in `services/ingestion/Containerfile`.

**Uploads do not trigger n8n.** The object store (VersityGW) sends a notification for every new object to `http://n8n:5678/webhook/object-created` (`VGW_EVENT_WEBHOOK_URL` on the `object-store` Deployment), and WF2 acts on the buckets in `EVENT_BUCKETS` (`objectStore.eventBuckets`: `documents` and `inbox`). Check that WF2 is active and look at its executions (`scripts/n8n-executions.sh`); an upload that left no execution did not reach n8n: `oc logs deploy/object-store` shows failed deliveries.

**Documents missing after the move from MinIO.** The chart's MinIO images disappeared from Docker Hub and quay.io in September 2026, and the object store is now VersityGW, with a new volume (`object-store-data`). Load the documents again with `scripts/load-sample-docs.sh`; the old `minio-data` volume is kept (not pruned) until you delete it: `oc delete pvc minio-data -n <project>`.

**A document shows up twice in citations.** Documents are keyed by bucket and object name; the same content under two names is two documents. List them: `oc exec deploy/rag-api -- .venv/bin/python -c "import urllib.request; print(urllib.request.urlopen('http://ingestion:8080/v1/documents').read().decode())"`, delete one with `DELETE http://ingestion:8080/v1/documents/<doc_id>`. Re-uploading under the same name replaces all chunks.

**The loader indexed a README or other stray file.** `scripts/load-sample-docs.sh` uploads every `.md`, `.docx` and `.pdf` in `data/sample-docs/` except `README.md`; pass file names to upload a subset.

## Voice, LiveKit and TURN

**Browser voice shows "Room: connecting" then fails with "could not establish pc connection".** Media is not reaching the SFU. Browsers behind corporate networks need TURN over TLS on 443: the chart exposes it through a passthrough Route (`livekit-turn-<ns>.<domain>`) with the certificate in `livekit.turn.tlsSecret`. Test the port: `openssl s_client -connect livekit-turn-<ns>.<domain>:443 </dev/null`.

**TURN relay allocated but no media flows (LiveKit log "permission denied" for a private IP).** LiveKit refuses TURN permissions for private peer addresses, which is what the SFU's own pod IP is. `livekit.turn.allowRestrictedPeerCidrs` must contain the pod network (the chart default lists the RFC 1918 ranges).

**Voice panel stays on "Connecting…" and the label never changes.** The room is connected but no agent joined: LiveKit dispatches an agent once, when the room is created, so a worker restart at that instant leaves the room without one. Click End voice, then Start (new room, new dispatch). Confirm the worker is registered: `oc logs deploy/voice-agent | grep "registered worker"`.

**Second voice session after End voice does nothing.** Fixed in the agent: it closes the room when the person leaves and ends the avatar provider's conversation. If you run an older image, the stale room keeps the old agent; delete the room with the LiveKit CLI or restart the voice agent.

**Frontend shows a blank page after the first message.** Cached `index.html` from an older build with a different asset hash. The nginx config serves `index.html` with `no-cache`; hard-refresh once after an upgrade.

**The assistant says "Sorry, I could not reach the knowledge base just now".** The voice agent's fallback when its call to the RAG API fails; the cause is the traceback under `streamed RAG API call failed` in `oc logs deploy/voice-agent`. A `RemoteProtocolError: incomplete chunked read` means the RAG API dropped the stream, and `oc logs deploy/rag-api | grep -A 40 "Exception in ASGI"` shows why. Images before `sha-48e2dab` did this after every request filed from a conversation (the ticket's timestamps were not JSON-serialisable in the stream's final line); since then the agent also retries once with a whole answer before apologising. To exercise the path without a browser: `POST /v1/chat/stream` from the rag-api pod with `{"message": "my laptop is broken", "mode": "voice", "session_id": "check-1"}` must end with one `final` line carrying the ticket.

## n8n, Slack and Google

**`scripts/import-workflows.sh` fails with 401.** The n8n API key was not set or is the placeholder. Create one under Settings, n8n API, export it as `N8N_API_KEY`.

**Workflow cannot be published: "Missing required credential".** A Slack node without a credential. The Slack credential is created automatically from `SLACK_BOT_TOKEN` in the integrations secret at first start; if the secret was filled later, restart n8n (`oc rollout restart deploy/n8n`) and re-import the workflows. If the editor loops on "Autosave failed", unpublish the workflow first, attach the credential, then publish.

**Slack approval card has no buttons.** The n8n Slack node needs the Block Kit wrapper `{"blocks": [...]}`; a bare array is silently dropped. WF4 builds the wrapper; keep it if you edit the card.

**Slack interactivity URL field is missing in the app settings.** Socket Mode is on. Turn it off; the request URL must be `https://<n8n host>/webhook/slack-interactions`.

**Button clicks reach n8n but the ticket does not change.** Look at the WF4 execution in n8n; a 409 from `PATCH /v1/tickets/<ref>` means an illegal state transition (for example approving a ticket that is already fulfilled).

**Webhooks report "not registered" right after an n8n restart.** Activation takes a few seconds after `/healthz` turns green. Re-probe: `curl https://<n8n host>/webhook/chat` should answer "not registered for GET requests", which means it exists.

**Google sign-in expires or asks for consent.** Not applicable any more: the transcript document is created by the RAG API with a service account key, so no sign-in or consent screen is involved. If a Google Doc is not created, see the archival entry below.

**WF5 fails at "Transcript to file" with "The value in transcript is not set".** The "Prepare document" node no longer finds the transcript in the RAG API's response; re-import the shipped workflow if you edited it by hand.

**A request was approved without a Slack card.** The chart ships with `ragApi.requestsRequireApproval: true`, which sends every request filed from a conversation to Slack. With it off, the language model decides per request (`needs_approval`) and WF4 approves the others at once with the note "no approval required"; a small model can judge a laptop repair as needing none. The model's own judgment is kept in the ticket payload as `model_needs_approval`.

**A confirmation such as "place the request" opened a second ticket.** The intent detector now sees the assistant's previous message; a reply that only confirms a request that was just logged is treated as a follow-up. Older images classify the latest message alone.

## Avatar

**The face never appears, audio only, log says the avatar provider failed to start.** The agent falls back to audio after `avatar_start_timeout_seconds`. Check `oc logs deploy/voice-agent | grep -i avatar`. Common causes: `TAVUS_API_KEY` or `TAVUS_FACE_ID` missing from the integrations secret (or `voiceAgent.extraEnv`), the provider cannot reach the public LiveKit URL (`LIVEKIT_PUBLIC_URL` must be the `wss://` Route), or the plan's single stream is still held by a previous conversation. List and end stale conversations with the Tavus API: `GET https://tavusapi.com/v2/conversations?status=active`, then `POST …/conversations/<id>/end`, using `x-api-key`.

**Answers take long to start speaking.** The agent logs one line per turn, `latency: end of speech to first audio 1.2s`, plus `first token after …` for the RAG API. Answers are spoken sentence by sentence while the model generates (`/v1/chat/stream`); if the log says `whole answers from now on`, the RAG API image is older than the agent. Contributors to the delay, in order: the language model's generation speed (shared GPU), Kokoro's CPU allotment (`models.tts.resources`), the avatar provider's round trip. `RAG_STREAM=false` on the voice agent restores whole answers, which is also what happens automatically when a guardrail provider is configured.

**n8n asks for an owner account, or the scripts have no API key.** The chart's `n8n-setup` job creates both after every sync; `oc logs job/n8n-setup -n <project>` says what it did. The owner e-mail and password are in the `assistant-n8n` secret (`oc extract secret/assistant-n8n --keys=N8N_OWNER_PASSWORD --to=-`), the API key in `assistant-n8n-api`. If the owner was created by hand with another password, the job leaves n8n alone: create an API key under Settings > n8n API and put it in that secret.

**No Google Doc for an archived transcript.** `oc logs deploy/rag-api | grep -i "google"` shows Drive's answer. Usual causes: the folder is not shared with the service account's e-mail as Editor, the Drive API is not enabled in the Google Cloud project, or `GOOGLE_SERVICE_ACCOUNT_JSON` is empty (`secrets.env` needs `GOOGLE_SERVICE_ACCOUNT_FILE`). Archival still re-ingests the transcript without the document.

**Voice or face does not match.** With faces listed in `voiceAgent.faces`, the voice follows the face's `gender` through `models.tts.voices` (`af_*`, `bf_*` female; `am_*`, `bm_*` male) or its pinned `voice`; check the gender declared for that face. Without a list, the voice is `models.tts.voice` and the face `voiceAgent.extraEnv.TAVUS_FACE_ID`. The voice agent logs `face=<id> voice=<name>` when it joins a room. All of these are config map changes that restart the voice agent.

**The face picker does not appear under the avatar frame.** It shows only with two to four entries in `voiceAgent.faces` (extra entries are ignored with a warning) and `voiceAgent.avatarProvider: tavus`; `GET /api/v1/voice/faces` on the frontend Route returns what the page sees. Names missing from the values come from Tavus when `TAVUS_API_KEY` is in the integrations secret. The face pictures are stills the RAG API cuts from the Tavus thumbnail videos at startup (`GET /api/v1/voice/faces/<id>/poster`, cached in the pod under `/tmp/face-posters`); initials show until a still is ready or when the key is missing. `oc logs deploy/rag-api | grep poster` tells which faces failed.

**The approval outcome is not spoken.** Notices are delivered to the conversation that filed the request. A ticket created with curl or in another browser session cannot reach the live voice room; file the request in the same session.
