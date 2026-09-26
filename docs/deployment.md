# Deployment guide

For a complete walk-through on a freshly provisioned cluster, including the platform prerequisites, GPU sharing, creating every key and the Argo CD path, run `scripts/setup.sh` on the bastion host as described in the README's [Setup](../README.md#setup) section; [SETUP.md](SETUP.md) is the reference for that script. This guide documents the individual steps and options.

The [README](../README.md) shows the one-command install. This page has the manual Helm steps behind it, the remote-model and Argo CD variants, the optional third-party accounts, and how to handle the generated secrets. Every chart value is documented in [../chart/README.md](../chart/README.md).

## Manual installation with Helm

1. Clone the repository:

```bash
git clone https://github.com/rh-ai-quickstart/enterprise-voice-avatar-assistant.git
cd enterprise-voice-avatar-assistant
```

2. Create a new OpenShift project and note the cluster apps domain:

```bash
PROJECT="voice-avatar-assistant"
DOMAIN=$(oc get ingresses.config.openshift.io cluster -o jsonpath='{.spec.domain}')
oc new-project ${PROJECT}
```

3. Create the secrets. The script generates passwords for PostgreSQL, the object store, n8n, and LiveKit, and stores any API keys you export beforehand (the variable names are listed in the script header). Secrets are never stored in git.

```bash
NAMESPACE=${PROJECT} scripts/create-secrets.sh
```

4. Install the chart. `global.domain` gives Routes, n8n, and LiveKit stable public URLs. Pick one of the two model options.

**Option A: deploy the models with the chart (default)**

The chart creates InferenceServices on OpenShift AI for the LLM (Llama 3.1 8B Instruct, W4A16), Whisper, and the embeddings model, plus a CPU text-to-speech service. This needs three GPUs; see [Minimum hardware requirements](../README.md#minimum-hardware-requirements).

```bash
helm install assistant chart --namespace ${PROJECT} \
  --set global.domain=${DOMAIN}
```

**Option B: bring your own model endpoints (MaaS)**

Point any model at an existing OpenAI-compatible endpoint instead. Endpoints include the protocol and the `/v1` path. API keys are read by the secrets script from `LLM_API_KEY`, `STT_API_KEY`, `EMBEDDINGS_API_KEY`, and `TTS_API_KEY`.

```bash
helm install assistant chart --namespace ${PROJECT} \
  --set global.domain=${DOMAIN} \
  --set models.llm.deploy=false \
  --set models.llm.endpoint=https://LLM_ENDPOINT/v1 \
  --set models.llm.servedModelName=LLM_MODEL_NAME \
  --set models.stt.deploy=false \
  --set models.stt.endpoint=https://STT_ENDPOINT/v1 \
  --set models.stt.servedModelName=STT_MODEL_NAME \
  --set models.embeddings.deploy=false \
  --set models.embeddings.endpoint=https://EMBEDDINGS_ENDPOINT/v1 \
  --set models.embeddings.servedModelName=EMBEDDINGS_MODEL_NAME
```

The two options mix per model, for example a MaaS LLM with Whisper deployed locally. For longer configurations copy `chart/values.yaml`, edit it, and pass it with `-f my-values.yaml`. Guardrails, the avatar provider, and the integrations are configured through the same file. Every value with its default and meaning is listed in [chart/README.md](../chart/README.md).

5. The n8n workflows are imported and published automatically when n8n starts (the `n8n.workflows` values control this), the Slack credential is created from `SLACK_BOT_TOKEN`, and the `n8n-setup` job creates the owner account (credentials in the `assistant-n8n` secret) and an API key (secret `assistant-n8n-api`) that the scripts use. Google Docs needs no credential in n8n: the RAG API writes the documents with a service account. The repository is the source of truth for the workflows: with the default `importPolicy: onChange`, a change to `chart/files/n8n-workflows/` restarts n8n and re-imports and re-publishes them at the next sync (edits made in the n8n editor are overwritten); `scripts/import-workflows.sh` does the same by hand.

```bash
echo https://$(oc get route/n8n -n ${PROJECT} --template='{{.spec.host}}')
```

## Deploying with Argo CD

Scripted: `scripts/setup.sh` (README, [Setup](../README.md#setup)) runs `scripts/bootstrap-cluster.sh` (cluster admin, once) then `scripts/deploy-argocd.sh`, which sets the apps domain, the language model's endpoint and the GPU profile on the application, so `chart/values-demo-cluster.yaml` holds nothing cluster-specific. The steps below are what the scripts do.

If the OpenShift GitOps operator is installed, Argo CD can own the deployment and keep it in sync with the `main` branch. It renders the same chart, so nothing differs from a manual install.

```bash
oc label namespace ${PROJECT} argocd.argoproj.io/managed-by=openshift-gitops
oc apply -f deploy/argocd/appproject.yaml
oc apply -f deploy/argocd/application.yaml
```

See [deploy/argocd/README.md](../deploy/argocd/README.md) for per-cluster values files.

## Model endpoint validation

The chart refuses to install when a model is set to `deploy: false` without an endpoint and a served model name, and prints what to set. After the install, `helm test` checks every configured model from inside the cluster (model lists, an embeddings call, and an LLM chat completion) plus the datastores and services:

```bash
helm test assistant -n ${PROJECT} --logs
```

For Argo CD deployments there is no Helm release to test; `NS=${PROJECT} scripts/test-services.sh` renders the same test pod from the chart, runs it, and prints the results.

## Third-party accounts and keys

Everything below is optional; the assistant runs without any of it. Keys go into the `assistant-integrations` secret (created by `scripts/create-secrets.sh` from environment variables, or updated later with `oc set data secret/assistant-integrations -n ${PROJECT} KEY=value`), never into git.

**Tavus (avatar video).** Sign up at [platform.tavus.io](https://platform.tavus.io) and create an API key under the developer settings; put it in the secret as `TAVUS_API_KEY`. Pick a stock face in the [face library](https://maker.tavus.io/dev/faces); its ID starts with `r` and is not secret, so set it in values as `voiceAgent.extraEnv.TAVUS_FACE_ID` next to `voiceAgent.avatarProvider: tavus`. To let people switch faces in the UI, list up to four under `voiceAgent.faces` with an `id`, a `name` and a `gender` (`female` or `male`); the browser remembers the last choice; the voice agent then speaks with the matching Kokoro voice from `models.tts.voices`, or with the `voice` pinned on the face. Tavus publishes no gender for its faces, which is why it is declared here. `scripts/list-tavus-faces.sh` prints the stock faces with their IDs, reading the key from the secret. The free plan includes 25 conversational minutes per month and one concurrent stream, so rehearse with `avatarProvider: none` and switch Tavus on for the avatar runs. The provider receives only the assistant's synthesized speech; the microphone audio stays in the cluster.

**Slack (notifications and approvals).** Create an app from `n8n/slack-app-manifest.json` (in the repository root) at [api.slack.com/apps](https://api.slack.com/apps) (*Create New App*, *From a manifest*). Under *OAuth & Permissions* install it to the workspace and copy the *Bot User OAuth Token* (`xoxb-…`) into the secret as `SLACK_BOT_TOKEN`; n8n creates its Slack credential from it on first start. The manifest sets the Interactivity request URL to `https://<n8n host>/webhook/slack-interactions` (replace `N8N_HOST` before pasting; for an app reused on another cluster, update it under *Interactivity & Shortcuts*) and keeps Socket Mode off. The channels `#assistant-ingestion`, `#assistant-documents`, `#assistant-approvals`, `#assistant-tickets`, and `#assistant-knowledge-gaps` are created and joined by `scripts/setup.sh` with the token (scopes `channels:manage`, `channels:join`); by hand, create them and invite the app to each.

**Google Docs (transcript archival).** The RAG API creates the transcript document through the Drive API with a Google Cloud service account, so no OAuth client, consent screen or sign-in is needed. In [Google Cloud console](https://console.cloud.google.com) create a project, enable the *Google Drive API*, create a service account (no roles needed) and download a JSON key for it. In Google Drive create a folder for the transcripts and share it with the service account's e-mail address (`client_email` in the key file) as Editor. `scripts/setup.sh` asks you to paste the key file's content and stores it for you; by hand, put the file's path in `secrets.env` as `GOOGLE_SERVICE_ACCOUNT_FILE` (the secrets script stores its content as `GOOGLE_SERVICE_ACCOUNT_JSON`) and the folder id, the part of the folder URL after `/folders/`, as `GOOGLE_DOCS_FOLDER_ID`. The archival workflow (WF5) receives the document link from the RAG API and only re-ingests the transcript and posts to Slack.

**Simli and Hedra (alternative avatar providers).** Same pattern as Tavus with `SIMLI_API_KEY` and `SIMLI_FACE_ID`, or `HEDRA_API_KEY` and `HEDRA_AVATAR_IMAGE`, and the matching `voiceAgent.avatarProvider`.

## Working with the generated secrets

`scripts/create-secrets.sh` creates seven Secrets in the project and never overwrites an existing one unless `FORCE=1` is set. Argo CD does not manage them, so they survive syncs.

| Secret | Keys |
|---|---|
| `assistant-postgres` | `POSTGRESQL_USER`, `POSTGRESQL_PASSWORD`, `POSTGRESQL_DATABASE`, `DATABASE_URL` |
| `assistant-object-store` | `S3_ACCESS_KEY`, `S3_SECRET_KEY` (also the login of the object store's web UI) |
| `assistant-n8n` | `N8N_ENCRYPTION_KEY`, `N8N_OWNER_EMAIL`, `N8N_OWNER_PASSWORD` (the owner account the `n8n-setup` job creates; the job also writes the API key it creates into `assistant-n8n-api`) |
| `assistant-qdrant` | `QDRANT_API_KEY` |
| `assistant-livekit` | `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` |
| `assistant-models` | `LLM_API_KEY`, `STT_API_KEY`, `TTS_API_KEY`, `EMBEDDINGS_API_KEY`, `GUARDRAILS_API_KEY`, `HF_TOKEN` |
| `assistant-integrations` | `SLACK_BOT_TOKEN`, `TAVUS_API_KEY`, `TAVUS_FACE_ID`, `TAVUS_PAL_ID`, `SIMLI_API_KEY`, `SIMLI_FACE_ID`, `GOOGLE_SERVICE_ACCOUNT_JSON`, `GOOGLE_DOCS_FOLDER_ID` |

Read a value, for example the object store's web UI login:

```bash
oc extract secret/assistant-object-store -n ${PROJECT} --to=-
```

Add or rotate one key without touching the others, then restart the pod that reads it (the config map and secrets are read at start):

```bash
oc set data secret/assistant-integrations -n ${PROJECT} TAVUS_API_KEY=<value>
oc rollout restart deployment/voice-agent -n ${PROJECT}
```

Back up `assistant-n8n`: losing `N8N_ENCRYPTION_KEY` makes every credential stored in n8n unreadable. `FORCE=1 scripts/create-secrets.sh` regenerates all passwords and is only for a fresh install; on a running deployment it would lock the services out of PostgreSQL and the object store.
