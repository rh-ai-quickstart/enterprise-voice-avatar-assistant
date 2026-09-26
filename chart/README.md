# enterprise-voice-avatar-assistant Helm chart

Deploys the whole quickstart into one OpenShift project: the datastores (PostgreSQL, Qdrant, a VersityGW S3 object store), n8n with the workflows shipped in `files/n8n-workflows/`, a self-hosted LiveKit server with TURN over TLS, the application services built from this repository (frontend, RAG API, ingestion, voice agent), and, optionally, the models as vLLM InferenceServices on OpenShift AI. Everything runs under the restricted SCC as a regular project user; no cluster-admin permission is needed.

The top-level [README](../README.md) covers requirements, the deployment walkthrough, validation and troubleshooting. This file is the reference for the chart itself.

## Install

```bash
# secrets first (never created by the chart, so Argo CD cannot rotate them)
NAMESPACE=${PROJECT} ../scripts/create-secrets.sh

# models served by the chart (needs GPUs)
helm upgrade --install assistant . --namespace ${PROJECT} --set global.domain=${DOMAIN}

# or point any model at an existing OpenAI-compatible endpoint
helm upgrade --install assistant . --namespace ${PROJECT} --set global.domain=${DOMAIN} \
  --set models.llm.deploy=false --set models.llm.endpoint=https://host/v1 --set models.llm.servedModelName=<name>
```

`../scripts/deploy.sh` wraps the same steps (project and domain detection, secrets, install, waiting, URLs). For GitOps, `../deploy/argocd/` holds an Argo CD Application that renders this chart with `values.yaml` plus a per-cluster overrides file such as `values-demo-cluster.yaml`.

A model set to `deploy: false` without `endpoint` and `servedModelName` fails the install at render time with an explicit message (`templates/validate.yaml`).

## Test, upgrade, uninstall

```bash
helm test assistant -n ${PROJECT} --logs          # connectivity to every service and model
NS=${PROJECT} ../scripts/test-services.sh         # same test pod, also for Argo CD installs
helm upgrade assistant . -n ${PROJECT} -f my-values.yaml
helm uninstall assistant -n ${PROJECT}            # keeps PVCs and Secrets; delete them explicitly
```

## What the chart creates

| Component | Kind | Notes |
|---|---|---|
| postgres, qdrant, object-store | Deployment, Service, PVC | Fixed service names so workflows and configuration stay stable across release names |
| object-store | Route, ConfigMap | VersityGW's web UI at `/ui/`; the event filter (new objects only) for its notifications to n8n. The ingestion service creates the buckets |
| n8n | Deployment, PVC, Route | Init container imports and publishes the workflows on first start (`n8n.workflows`) |
| livekit | Deployment, Routes | WebSocket signaling Route plus a passthrough Route for TURN over TLS (`livekit.turn`) |
| llm, stt, embeddings, guardrails | ServingRuntime, InferenceService | Only when `deploy: true`; Recreate strategy and external autoscaler class so an update never needs a second GPU |
| tts | Deployment, Service | Kokoro on CPU with the OpenAI speech API |
| frontend, rag-api, ingestion, voice-agent | Deployment, Service (Route for the frontend) | Images from `images.registry`; each can pin its own `image` |
| assistant-config | ConfigMap | Every service URL and model endpoint; a checksum annotation restarts the application pods when it changes |
| assistant-test-services | Pod (Helm test hook) | The connectivity test |

Secrets are referenced by name (`secrets.*`) and must exist before the install; `../scripts/create-secrets.sh` creates them with generated passwords and the optional API keys from your environment.

## Values

Generated from `values.yaml` by `../scripts/gen-values-reference.py --write`; run it after changing values.

<!-- values-reference:begin -->

### `global`

| Key | Default | Description |
|---|---|---|
| `global.domain` | `""` | Wildcard apps domain of the cluster, e.g. apps.ocp.example.com. When set, Routes get deterministic hosts (<name>-<namespace>.<domain>) and n8n and LiveKit learn their public URLs. When empty, OpenShift assigns hosts and you must set n8n.publicHost and livekit.publicHost yourself. |
| `global.storageClassName` | `""` | Leave empty to use the cluster default StorageClass. |
| `global.imagePullPolicy` | `IfNotPresent` |  |

### `images`

Application images built from this repository and published by CI as <registry>/enterprise-voice-avatar-assistant-<service>:<tag> for frontend, rag-api, ingestion and voice-agent. Each component can override its full image reference with its own `image` value.

| Key | Default | Description |
|---|---|---|
| `images.registry` | `quay.io/rh-ai-quickstart` |  |
| `images.tag` | `latest` |  |

### `secrets`

Names of the pre-created Secrets. Keys per secret: postgres:     POSTGRESQL_USER, POSTGRESQL_PASSWORD, POSTGRESQL_DATABASE, DATABASE_URL objectStore:  S3_ACCESS_KEY, S3_SECRET_KEY n8n:          N8N_ENCRYPTION_KEY livekit:      LIVEKIT_API_KEY, LIVEKIT_API_SECRET models:       LLM_API_KEY, STT_API_KEY, TTS_API_KEY, EMBEDDINGS_API_KEY, GUARDRAILS_API_KEY, HF_TOKEN integrations: SLACK_BOT_TOKEN, SIMLI_API_KEY, SIMLI_FACE_ID, TAVUS_API_KEY, TAVUS_FACE_ID, TAVUS_PAL_ID (optional), GOOGLE_SERVICE_ACCOUNT_JSON, GOOGLE_DOCS_FOLDER_ID

| Key | Default | Description |
|---|---|---|
| `secrets.postgres` | `assistant-postgres` | Name of the pre-created Secret holding the keys listed above. |
| `secrets.objectStore` | `assistant-object-store` | Name of the pre-created Secret holding the keys listed above. |
| `secrets.n8n` | `assistant-n8n` | Name of the pre-created Secret holding the keys listed above. |
| `secrets.livekit` | `assistant-livekit` | Name of the pre-created Secret holding the keys listed above. |
| `secrets.models` | `assistant-models` | Name of the pre-created Secret holding the keys listed above. |
| `secrets.integrations` | `assistant-integrations` | Name of the pre-created Secret holding the keys listed above. |

### `postgres`

Datastores and infrastructure. All run under the restricted SCC (no fixed UID).

| Key | Default | Description |
|---|---|---|
| `postgres.enabled` | `true` | Deploy this component. |
| `postgres.image` | `quay.io/sclorg/postgresql-16-c9s:20260902` | Container image (full reference). |
| `postgres.storage` | `10Gi` | Size of the persistent volume. |
| `postgres.resources.requests.cpu` | `250m` | CPU request. |
| `postgres.resources.requests.memory` | `512Mi` | Memory request. |
| `postgres.resources.limits.cpu` | `"1"` | CPU limit. |
| `postgres.resources.limits.memory` | `1Gi` | Memory limit. |

### `qdrant`

| Key | Default | Description |
|---|---|---|
| `qdrant.enabled` | `true` | Deploy this component. |
| `qdrant.image` | `docker.io/qdrant/qdrant:v1.19.1` | Container image (full reference). |
| `qdrant.collection` | `documents` | Qdrant collection for document chunks. |
| `qdrant.storage` | `10Gi` | Size of the persistent volume. |
| `qdrant.apiKeySecret` | `""` | Name of a Secret with key QDRANT_API_KEY. When set, Qdrant requires the key and the application services receive it. scripts/create-secrets.sh creates assistant-qdrant for this purpose. |
| `qdrant.route` | `false` | Expose the Qdrant HTTP API and dashboard (/dashboard) through a Route. Only enable together with apiKeySecret. |
| `qdrant.publicHost` | `""` | Public hostname; computed from `global.domain` when empty. |
| `qdrant.resources.requests.cpu` | `250m` | CPU request. |
| `qdrant.resources.requests.memory` | `512Mi` | Memory request. |
| `qdrant.resources.limits.cpu` | `"1"` | CPU limit. |
| `qdrant.resources.limits.memory` | `2Gi` | Memory limit. |

### `objectStore`

S3-compatible object storage: VersityGW (https://github.com/versity/versitygw), an Apache 2.0 S3 gateway that keeps the objects as files on its volume. Every new object is announced to n8n (WF2), which ingests or classifies the ones in `eventBuckets`.

| Key | Default | Description |
|---|---|---|
| `objectStore.enabled` | `true` | Deploy this component. |
| `objectStore.image` | `ghcr.io/versity/versitygw:v1.8.0` | Container image (full reference). |
| `objectStore.buckets` | `[documents, inbox, transcripts]` | Buckets the ingestion service creates when they are missing. |
| `objectStore.eventBuckets` | `[documents, inbox]` | Buckets whose new objects WF2 acts on: documents are indexed, inbox files classified. (transcripts are indexed by the archival workflow itself.) |
| `objectStore.storage` | `20Gi` | Size of the persistent volume. |
| `objectStore.console.route` | `true` | Expose through an OpenShift Route. |
| `objectStore.console.publicHost` | `""` | Public hostname; computed from `global.domain` when empty. |
| `objectStore.resources.requests.cpu` | `100m` | CPU request. |
| `objectStore.resources.requests.memory` | `128Mi` | Memory request. |
| `objectStore.resources.limits.cpu` | `"1"` | CPU limit. |
| `objectStore.resources.limits.memory` | `512Mi` | Memory limit. |

### `n8n`

| Key | Default | Description |
|---|---|---|
| `n8n.enabled` | `true` | Deploy this component. |
| `n8n.image` | `ghcr.io/n8n-io/n8n:2.37.11` | Container image (full reference). |
| `n8n.payloadSizeMaxMb` | `16` | Maximum webhook payload in MiB. |
| `n8n.publicHost` | `""` | Public hostname. Computed from global.domain when empty. |
| `n8n.storage` | `5Gi` | Size of the persistent volume. |
| `n8n.timezone` | `Europe/Amsterdam` | Time zone for schedules and logs. |
| `n8n.extraEnv` | `{}` | Extra environment variables passed to n8n (string values). |
| `n8n.workflows.autoImport` | `true` | Import the workflows in chart/files/n8n-workflows and publish them when n8n starts. |
| `n8n.workflows.importPolicy` | `onChange` | onChange: whenever the shipped files change, the repository being the source of truth (edits made in the editor are overwritten); once: first start only (marker on the data volume); always: every start. |
| `n8n.workflows.slackCredentialId` | `AssistantSlack01` | Credential id the Slack nodes reference; created from SLACK_BOT_TOKEN in the integrations secret when present. |
| `n8n.setup.enabled` | `true` | Deploy this component. |
| `n8n.setup.image` | `registry.access.redhat.com/ubi9/python-312:latest` | Container image (full reference). |
| `n8n.setup.apiKeySecret` | `assistant-n8n-api` |  |
| `n8n.setup.ownerFirstName` | `Assistant` |  |
| `n8n.setup.ownerLastName` | `Admin` |  |
| `n8n.resources.requests.cpu` | `250m` | CPU request. |
| `n8n.resources.requests.memory` | `512Mi` | Memory request. |
| `n8n.resources.limits.cpu` | `"1"` | CPU limit. |
| `n8n.resources.limits.memory` | `1Gi` | Memory limit. |

### `livekit`

| Key | Default | Description |
|---|---|---|
| `livekit.enabled` | `true` | Deploy this component. |
| `livekit.image` | `docker.io/livekit/livekit-server:v1.13.6` | Container image (full reference). |
| `livekit.logLevel` | `info` | LiveKit server log level: debug shows TURN allocations and ICE candidate pairs. |
| `livekit.publicHost` | `""` | Public hostname for the WebSocket signaling Route. Computed from global.domain when empty. |
| `livekit.turn.enabled` | `false` | Deploy this component. |
| `livekit.turn.publicHost` | `""` | Public hostname; computed from `global.domain` when empty. |
| `livekit.turn.tlsSecret` | `""` |  |
| `livekit.turn.allowRestrictedPeerCidrs` | `[10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16]` | LiveKit denies TURN relay permissions for private peer addresses unless they are listed here. Inside a pod the media server's own ICE candidate is its private pod IP, so the pod network must be allowed or no media flows. The defaults cover the RFC 1918 ranges used by OpenShift pod networks. |
| `livekit.resources.requests.cpu` | `500m` | CPU request. |
| `livekit.resources.requests.memory` | `512Mi` | Memory request. |
| `livekit.resources.limits.cpu` | `"2"` | CPU limit. |
| `livekit.resources.limits.memory` | `2Gi` | Memory limit. |

### `models`

Models. Each model has a `deploy` toggle: true creates a vLLM ServingRuntime and InferenceService on OpenShift AI in this namespace; false uses `endpoint`, an existing OpenAI-compatible base URL (include /v1). `servedModelName` is the model name clients send in requests.

| Key | Default | Description |
|---|---|---|
| `models.runtimeImage` | `registry.redhat.io/rhaii/vllm-cuda-rhel9@sha256:ba060ec145e1b7ac3a3b25b89fc75f7d146746156990ff5858235eaa9d158e32` |  |
| `models.llm.deploy` | `true` | true: serve the model on OpenShift AI from this chart; false: use `endpoint`. |
| `models.llm.name` | `llama-3-1-8b-instruct` | InferenceService name (also the in-cluster service name). |
| `models.llm.displayName` | `Llama 3.1 8B Instruct (W4A16)` | Name shown in the OpenShift AI dashboard. |
| `models.llm.servedModelName` | `llama-3.1-8b-instruct` | Model name clients send in requests. |
| `models.llm.storageUri` | `oci://registry.redhat.io/rhelai1/modelcar-llama-3-1-8b-instruct-quantized-w4a16:1.5` | Red Hat AI validated modelcar, about 6 GiB of weights. Pulled with the cluster's registry.redhat.io pull secret (present on every OpenShift install). Alternative without that registry: hf://RedHatAI/Meta-Llama-3.1-8B-Instruct-FP8 |
| `models.llm.args` | `[--max-model-len=16384, --gpu-memory-utilization=0.90, --enable-auto-tool-choice, --tool-call-parser=llama3_json]` | Without a limit vLLM reserves KV cache for the full 128k window, which does not fit next to the weights on a 24 GiB GPU and the engine fails to start. |
| `models.llm.resources.requests.cpu` | `"2"` | CPU request. |
| `models.llm.resources.requests.memory` | `12Gi` | Memory request. |
| `models.llm.resources.requests.nvidia.com/gpu` | `"1"` | GPUs requested (per model pod). |
| `models.llm.resources.limits.cpu` | `"4"` | CPU limit. |
| `models.llm.resources.limits.memory` | `24Gi` | Memory limit. |
| `models.llm.resources.limits.nvidia.com/gpu` | `"1"` | GPU limit (per model pod). |
| `models.llm.endpoint` | `""` | OpenAI-compatible base URL including /v1, used when `deploy` is false. |
| `models.stt.deploy` | `true` | true: serve the model on OpenShift AI from this chart; false: use `endpoint`. |
| `models.stt.name` | `whisper-large-v3-turbo` | InferenceService name (also the in-cluster service name). |
| `models.stt.displayName` | `Whisper large-v3-turbo (W4A16)` | Name shown in the OpenShift AI dashboard. |
| `models.stt.servedModelName` | `whisper-large-v3-turbo` | Model name clients send in requests. |
| `models.stt.storageUri` | `oci://registry.redhat.io/rhelai1/modelcar-whisper-large-v3-turbo-quantized-w4a16:1.5` | Where the weights come from (oci:// modelcar or hf:// repository). |
| `models.stt.args` | `[]` | Extra vLLM arguments. |
| `models.stt.resources.requests.cpu` | `"1"` | CPU request. |
| `models.stt.resources.requests.memory` | `8Gi` | Memory request. |
| `models.stt.resources.requests.nvidia.com/gpu` | `"1"` | GPUs requested (per model pod). |
| `models.stt.resources.limits.cpu` | `"2"` | CPU limit. |
| `models.stt.resources.limits.memory` | `12Gi` | Memory limit. |
| `models.stt.resources.limits.nvidia.com/gpu` | `"1"` | GPU limit (per model pod). |
| `models.stt.endpoint` | `""` | OpenAI-compatible base URL including /v1, used when `deploy` is false. |
| `models.embeddings.deploy` | `true` | true: serve the model on OpenShift AI from this chart; false: use `endpoint`. |
| `models.embeddings.name` | `bge-m3` | InferenceService name (also the in-cluster service name). |
| `models.embeddings.displayName` | `BGE-M3 embeddings` | Name shown in the OpenShift AI dashboard. |
| `models.embeddings.servedModelName` | `bge-m3` | Model name clients send in requests. |
| `models.embeddings.storageUri` | `hf://BAAI/bge-m3` | Where the weights come from (oci:// modelcar or hf:// repository). |
| `models.embeddings.args` | `[--runner=pooling, --max-model-len=8192]` | Extra vLLM arguments. |
| `models.embeddings.resources.requests.cpu` | `"1"` | CPU request. |
| `models.embeddings.resources.requests.memory` | `6Gi` | Memory request. |
| `models.embeddings.resources.requests.nvidia.com/gpu` | `"1"` | GPUs requested (per model pod). |
| `models.embeddings.resources.limits.cpu` | `"2"` | CPU limit. |
| `models.embeddings.resources.limits.memory` | `12Gi` | Memory limit. |
| `models.embeddings.resources.limits.nvidia.com/gpu` | `"1"` | GPU limit (per model pod). |
| `models.embeddings.endpoint` | `""` | OpenAI-compatible base URL including /v1, used when `deploy` is false. |
| `models.guardrails.provider` | `none` | Provider used by the RAG API: none \| granite-guardian \| llama-guard \| trustyai |
| `models.guardrails.deploy` | `false` | true: serve the model on OpenShift AI from this chart; false: use `endpoint`. |
| `models.guardrails.name` | `granite-guardian-3-3-8b` | InferenceService name (also the in-cluster service name). |
| `models.guardrails.displayName` | `Granite Guardian 3.3 8B` | Name shown in the OpenShift AI dashboard. |
| `models.guardrails.servedModelName` | `granite-guardian-3.3-8b` | Model name clients send in requests. |
| `models.guardrails.storageUri` | `oci://quay.io/redhat-ai-services/modelcar-catalog:granite-guardian-3.3-8b` | Where the weights come from (oci:// modelcar or hf:// repository). |
| `models.guardrails.chatTemplateFile` | `files/granite-guardian-3.3-chat-template.jinja` | The modelcar ships no chat template and vLLM refuses to guess one, so the official template from the model's Hugging Face repo is mounted from chart/files. |
| `models.guardrails.args` | `[--max-model-len=8192, --gpu-memory-utilization=0.90]` | Extra vLLM arguments. |
| `models.guardrails.resources.requests.cpu` | `"2"` | CPU request. |
| `models.guardrails.resources.requests.memory` | `12Gi` | Memory request. |
| `models.guardrails.resources.requests.nvidia.com/gpu` | `"1"` | GPUs requested (per model pod). |
| `models.guardrails.resources.limits.cpu` | `"4"` | CPU limit. |
| `models.guardrails.resources.limits.memory` | `24Gi` | Memory limit. |
| `models.guardrails.resources.limits.nvidia.com/gpu` | `"1"` | GPU limit (per model pod). |
| `models.guardrails.endpoint` | `""` | OpenAI-compatible base URL including /v1, used when `deploy` is false. |
| `models.tts.deploy` | `true` | Kokoro exposes the OpenAI speech API on CPU. Set deploy=false and endpoint to use another OpenAI-compatible TTS service. |
| `models.tts.image` | `ghcr.io/remsky/kokoro-fastapi-cpu:v0.8.2` | Container image (full reference). |
| `models.tts.servedModelName` | `kokoro` | Model name clients send in requests. |
| `models.tts.voice` | `af_heart` | Kokoro voice (af_*/bf_* female, am_*/bm_* male). |
| `models.tts.voices.female` | `af_bella` | Voice for faces declared female. |
| `models.tts.voices.male` | `am_michael` | Voice for faces declared male. |
| `models.tts.resources.requests.cpu` | `"1"` | CPU request. |
| `models.tts.resources.requests.memory` | `2Gi` | Memory request. |
| `models.tts.resources.limits.cpu` | `"4"` | CPU limit. |
| `models.tts.resources.limits.memory` | `4Gi` | Memory limit. |
| `models.tts.endpoint` | `""` | OpenAI-compatible base URL including /v1, used when `deploy` is false. |

### `frontend`

Application services built from this repository.

| Key | Default | Description |
|---|---|---|
| `frontend.enabled` | `true` | Deploy this component. |
| `frontend.image` | `""` | Container image (full reference). |
| `frontend.replicas` | `1` | Number of pods. |
| `frontend.route` | `true` | Expose through an OpenShift Route. |
| `frontend.publicHost` | `""` | Public hostname; computed from `global.domain` when empty. |
| `frontend.resources.requests.cpu` | `100m` | CPU request. |
| `frontend.resources.requests.memory` | `128Mi` | Memory request. |
| `frontend.resources.limits.cpu` | `500m` | CPU limit. |
| `frontend.resources.limits.memory` | `256Mi` | Memory limit. |

### `ragApi`

| Key | Default | Description |
|---|---|---|
| `ragApi.enabled` | `true` | Deploy this component. |
| `ragApi.image` | `""` | Container image (full reference). |
| `ragApi.replicas` | `1` | Number of pods. |
| `ragApi.topK` | `5` | Number of chunks retrieved per question. |
| `ragApi.requestsRequireApproval` | `true` | Every request filed from chat or voice waits for a decision on its Slack card. false: the language model decides per request whether approval is needed, and requests it judges as free of cost, access or permission changes are approved by the workflow at once. |
| `ragApi.resources.requests.cpu` | `500m` | CPU request. |
| `ragApi.resources.requests.memory` | `1Gi` | Memory request. |
| `ragApi.resources.limits.cpu` | `"2"` | CPU limit. |
| `ragApi.resources.limits.memory` | `2Gi` | Memory limit. |

### `ingestion`

| Key | Default | Description |
|---|---|---|
| `ingestion.enabled` | `true` | Deploy this component. |
| `ingestion.image` | `""` | Container image (full reference). |
| `ingestion.replicas` | `1` | Number of pods. |
| `ingestion.resources.requests.cpu` | `"1"` | CPU request. |
| `ingestion.resources.requests.memory` | `2Gi` | Memory request. |
| `ingestion.resources.limits.cpu` | `"4"` | CPU limit. |
| `ingestion.resources.limits.memory` | `8Gi` | Memory limit. |

### `voiceAgent`

| Key | Default | Description |
|---|---|---|
| `voiceAgent.enabled` | `true` | Deploy this component. |
| `voiceAgent.image` | `""` | Container image (full reference). |
| `voiceAgent.replicas` | `1` | Number of pods. |
| `voiceAgent.avatarProvider` | `none` | none \| simli \| tavus \| hedra (see services/voice-agent/app/avatars.py) |
| `voiceAgent.extraEnv` | `{}` | Extra environment for the worker; explicit entries win over the config map and secrets, so non-secret provider settings such as TAVUS_FACE_ID can be pinned here. |
| `voiceAgent.faces` | `[]` | Up to four faces the person can choose in the UI before a voice session (Tavus). Each entry has id (the Tavus face id), name (shown in the UI; taken from Tavus when empty) and gender (female \| male, which selects the voice from models.tts.voices) or voice (pins a TTS voice). The first entry is the default; an empty list means the single face in TAVUS_FACE_ID. scripts/list-tavus-faces.sh prints the stock faces with their ids. |
| `voiceAgent.resources.requests.cpu` | `500m` | CPU request. |
| `voiceAgent.resources.requests.memory` | `1Gi` | Memory request. |
| `voiceAgent.resources.limits.cpu` | `"2"` | CPU limit. |
| `voiceAgent.resources.limits.memory` | `2Gi` | Memory limit. |
<!-- values-reference:end -->
