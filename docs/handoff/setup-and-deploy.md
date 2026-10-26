# Setup, deploy and operations

[SETUP.md](../SETUP.md) is the user guide for `scripts/setup.sh`. This page adds what a
maintainer needs: how the script is built, every switch, the state it keeps, what
`deploy.sh` does around Helm, and a runbook for the running system.

## scripts/setup.sh

**Usage:**
- `scripts/setup.sh`: discover, show progress, run every remaining step;
- `--status`: discovery only;
- `--step N`: one step;
- `--yes`: never prompt;
- `--reset`: forget progress; the cluster is untouched.

**Each run:**
- logs to `~/.assistant-setup/logs/setup-<timestamp>.log`; `DEBUG=1` adds a trace;
- keeps progress in `~/.assistant-setup/state.env`;
- checks the cluster before each step, so a step done by hand is recognised.

**On macOS** (bash 3.2) the top of the script is bash 3.2 safe. It installs Homebrew (after
asking), bash 5 and the tools (oc, helm, jq, ...), then re-executes itself under Homebrew's
bash. The rest of the script and the scripts it calls need bash 4 or later: associative
arrays, `${var@Q}`.

**The path follows the access:**
- **cluster:** `oc auth can-i '*' '*' --all-namespaces` succeeds: RHDP sandbox, Argo CD;
- **namespace:** anything less: the TMM cluster, Helm.

`ACCESS=namespace` forces the namespace path; `PROJECT` picks the project, defaulting to the
current one when you administer it. With no project saved by an earlier run and no current
project (a new kubeconfig after `oc login`), it stays at `voice-avatar-assistant`; set one
with `oc project <name>`.

### Steps on the namespace path
1. **Access and prerequisites.** Tools; admin rights in the project (`can-i` on
   rolebindings and secrets); every kind the chart creates; KServe in the project; the
   Kueue queues with their GPU quota and booking end; the MaaS models; a default
   StorageClass; cert-manager. The apps domain comes from the console URL.
2. **Deployment profile.**
   - **Per model** (LLM, STT, EMB): `REMOTE_<X>_ENDPOINT` if given; else the MaaS model
     matching `MAAS_<X>_MODEL`; else the chart serves it on the smallest GPU or MIG slice of
     a queue that fits (`assign_gpus`: LLM needs 24 GB, Whisper and BGE-M3 10 GB, Kokoro
     4 GB, the avatar a whole card).
   - **The avatar question:** tavus, bey, openspline, several (`openspline,tavus,bey`: the UI
     offers each, the first preselected) or none. The suggestion is `openspline,tavus` when a whole
     GPU is free. `AVATAR_CHOICE` in `state.env` holds the answer; `avatar_has`,
     `avatar_list` and `avatar_default` read it. With several, the overlay gets
     `voiceAgent.avatarProviders`, and step 6 passes the list to `deploy.sh` as
     `AVATAR_PROVIDER=openspline,tavus`.
   - **OpenSpline's quality:** high on a GPU pair when one is free and Kokoro keeps its
     slice, else low (`AVATAR_QUALITY` overrides).
   - **Kokoro** gets a leftover slice with the GPU image (`TTS_GPU=false` keeps it on CPU).
   - **Writes** `~/.assistant-setup/values-object.json` (see below).
3. **Project check.** The queue still has quota; warns when the booking ends within two hours.
4. **TURN certificate.** A namespaced cert-manager Issuer (Let's Encrypt HTTP-01) and a
   Certificate for `livekit-turn-<project>.<domain>`. The solver pod avoids listed nodes.
   After ten minutes without a certificate it records `TURN_SKIPPED` and goes on.
5. **Keys and integrations.**
   - Guided prompts with hidden input: n8n owner; Slack app, channels, signing secret and
     request URL; Tavus only if Tavus was chosen; the Google service account and folder.
   - Each key is verified against its service.
   - The MaaS key is created.
   - `scripts/create-secrets.sh` writes the cluster secrets and keeps existing ones.
6. **Deploy with Helm.** `scripts/deploy.sh -f chart/values-demo-cluster.yaml -f ~/.assistant-setup/values-object.json`,
   with `AVATAR_PROVIDER`, `AVOID_NODES` and `AVOID_NODES_FILE`, and `livekit.turn.enabled=false`
   without the TURN secret.
7. **n8n workflows.** The `n8n-setup` Job ran: owner, API key, seven workflows active.
8. **Sample documents.** `scripts/load-sample-docs.sh`, then waits for ten indexed
   documents (with chunks; inbox files are counted apart).
9. **Verification.** `scripts/demo-preflight.sh`: models Ready, pods, the OpenSpline worker
   loaded and free, the database tables, the connectivity test pod, the public `/api` proxy,
   the admin portal, the seven n8n webhooks.

The cluster path differs in steps 1 to 3 (operators, GPU time-slicing, Argo CD) and step 6
(`scripts/deploy-argocd.sh`). See SETUP.md.

### Environment variables setup reads

| Variable | Effect |
|---|---|
| `PROJECT`, `ACCESS`, `DOMAIN` | Target project, forced path, apps domain. The environment wins over saved state |
| `PROFILE=remote` + `REMOTE_LLM_ENDPOINT`, `REMOTE_LLM_MODEL`, `REMOTE_STT_*`, `REMOTE_EMB_*` | Every model is a remote endpoint (cluster path), or that model is (namespace path) |
| `MAAS_LLM_MODEL`, `MAAS_STT_MODEL`, `MAAS_EMB_MODEL` | Patterns for the MaaS model names (defaults `llama-32-3b`, `whisper.*`, `bge-m3`) |
| `KUEUE_QUEUE`, `GPU_RESOURCE` | Use this queue, and this GPU resource for every chart model (bypasses the quota fit) |
| `TTS_GPU=false` | Keep Kokoro on CPU |
| `KOKORO_GPU_IMAGE` | Kokoro's GPU image (default `ghcr.io/remsky/kokoro-fastapi-gpu:v0.8.2`) |
| `AVATAR_PROVIDER` | `tavus`, `openspline` or `none`. Answers step 2; a typo stops the run |
| `AVATAR_QUALITY` | `low`, `high` or `auto` (default) for OpenSpline |
| `AVOID_NODES` | Space-separated node names to keep every pod off. `~/.assistant-setup/avoid-nodes` wins |
| `ACME_EMAIL` | The e-mail for Let's Encrypt (default: the login) |
| `SLACK_ENABLED`, `GOOGLE_DOCS_ENABLED` | Override the integration flags that follow the keys |
| `SECRETS_FILE`, `STATE_DIR` | Default `~/secrets.env`, `~/.assistant-setup`. Point them at a scratch copy to test step 2 without touching the real state |
| `LLM_NAME`, `LLM_NS` | Cluster path: the already deployed language model to reuse |
| `DEBUG=1` | A full command trace next to the log |

### State it keeps (`~/.assistant-setup/state.env`)
- **Step 2:** `PROFILE`; `MODEL_<LLM|STT|EMB>_{SRC,URL,NAME,GPU}`; `MODEL_TTS_GPU`;
  `GPU_QUEUE`; `AVATAR_CHOICE`; `AVATAR_GPU`, `AVATAR_GPUS`, `AVATAR_LEVEL` (OpenSpline's GPU
  resource, count and quality).
- **Progress:** `STEP_<n>_DONE` (timestamps), `TURN_SKIPPED`, `AVOID_NODES`, and discovered
  facts.
- **Next to it:** `values-object.json` (the overlay), `avoid-nodes`, `google-sa.json`,
  `logs/`.

The overlay setup writes on the namespace path, for example:
```json
{"models": {"llm": {"deploy": false, "endpoint": ".../llama-32-3b/v1", "servedModelName": "llama-32-3b"},
            "stt": {"deploy": true, "resources": {"limits": {"nvidia.com/gpu": null, "nvidia.com/mig-3g.71gb": "1"}}},
            "embeddings": {"deploy": false, "endpoint": ".../bge-m3/v1"},
            "podLabels": {"kueue.x-k8s.io/queue-name": "reserved"},
            "tts": {"image": "ghcr.io/remsky/kokoro-fastapi-gpu:v0.8.2", "resources": {"...": "mig-3g.71gb: 1"}}},
 "n8n": {"storage": "10Gi"},
 "voiceAgent": {"avatarProvider": "openspline", "avatarProviders": ["openspline", "tavus"]},
 "openspline": {"quality": "low", "resources": {"limits": {"nvidia.com/gpu": "1"}},
                "podLabels": {"kueue.x-k8s.io/queue-name": "reserved"}}}
```

To try step 2 without touching the real state:
`STATE_DIR=/tmp/x SECRETS_FILE=/tmp/x/none.env scripts/setup.sh --step 2 --yes`, after copying
`state.env` there. It only reads from the cluster.

### Another laptop
Nothing needs copying between machines. Right after the target is confirmed (in a full run
and with `--step N`; `--status` stays read-only), `restore_local_state` rebuilds from the
cluster what only the first machine had. It fills in only what is missing:
- **Keys:** `~/secrets.env` gets the keys it lacks from the secrets created before:
  - `assistant-integrations`: Slack, Tavus, Simli, the Google folder;
  - `assistant-models`: model keys other than `none`;
  - `assistant-n8n`: the owner's e-mail and password.

  The Google service account JSON is written back to `~/.assistant-setup/google-sa.json` and
  named in `GOOGLE_SERVICE_ACCOUNT_FILE`. `MAAS_API_KEY` is taken from the model secret when
  `LLM_BASE_URL` or `EMBEDDINGS_BASE_URL` in `assistant-config` points at Models-as-a-Service.
  Only key names are printed. The n8n encryption key, the passwords and the internal tokens
  stay in the cluster, because `create-secrets.sh` keeps them anyway.
- **Avoided nodes:** `~/.assistant-setup/avoid-nodes` is rebuilt from the `metadata.name NotIn`
  node exclusions on the release's Deployments.
- **Private doc:** `docs/handoff/private/environment.md` gets the API and console URLs, the
  domain, the user and groups, the project and access, the login command, Models-as-a-Service
  and its models, the GPU queues and booking end, the avoided nodes, and where each GPU pod
  runs.

Tested as a new laptop (empty state, no `~/secrets.env`) against TMM: ten values restored,
the avoided node, and the doc written. A second run changed nothing.

**On a real second Mac** (2026-10-25, demo already deployed on TMM):
- Homebrew was installed already. `brew install openshift-cli helm jq bash`, then the
  `oc login` from [environments.md](environments.md#2-the-red-hat-tmm-shared-cluster-namespace-path)
  into a new `~/.kube/tmm`.
- **The new kubeconfig had no current project** (`oc project -q`: "no project has been
  set"). Without one, setup keeps its default project `voice-avatar-assistant`, which the
  user does not administer, so step 1 would stop with "not an admin". `oc project
  user-<name>` fixed it; `PROJECT=user-<name>` works too.
- The status board showed steps 4, 6 and 8 done, because those are read from the cluster.
  Steps 1, 2, 3, 5, 7 and 9 showed as to do, because their marks live in the local
  `state.env`.
- After the confirmation, the full run restored ten keys, the avoided node and the private
  doc. It then ran steps 1, 2, 3, 5, 7 and 9 and skipped the rest, so nothing was
  redeployed. It took about eight minutes, prompts included.
- Step 1 installed `openssl@3` with Homebrew: the `openssl` that macOS ships is LibreSSL,
  and setup wants OpenSSL.
- Step 2 suggested `openspline` and came to the same layout as the running release, so
  `--step 6` was not needed.
- Step 5 asked only question 5e (is the Slack app new from the manifest?). It is worded for
  a first run. With an app made earlier, answer no: setup then shows the Interactivity URL
  to check. Answering yes skips that check. Step 5 then rewrote `assistant-integrations`,
  `assistant-models` and `assistant-n8n` from the restored file, with the same values and
  the passwords kept.
- Step 9's preflight passed.

## scripts/deploy.sh
A Helm install for a regular user, usable on its own. It takes these variables:
- `PROJECT`, `DOMAIN`, `RELEASE` (default `assistant`);
- `MODELS=maas` (prompt for remote models);
- `WAIT`, `RUN_TESTS`, `HELM_TIMEOUT` (default 20m);
- `LOG_FILE`;
- `AVOID_NODES`, `AVOID_NODES_FILE`;
- `AVATAR_PROVIDER`.

What it does:
1. Creates the secrets (`scripts/create-secrets.sh`, which keeps existing ones).
2. Sets the integration flags from the keys present: Slack, Google Docs.
3. Picks the avatar:
   - `AVATAR_PROVIDER` wins.
   - Unset or `tavus` without a Tavus key becomes `none`. OpenSpline needs no key.
4. Runs `helm upgrade --install` **in the background**. Every 20 s, `avoid_bad_nodes` looks
   for pods with four or more `FailedCreatePodSandBox` events on one node. For each one it:
   - gives the Deployment or InferenceService a `NotIn` node affinity;
   - deletes the stuck pods;
   - adds the node to `AVOID_NODES_FILE`.
5. Waits up to 10 minutes for the Deployments, then up to 40 for the InferenceServices. It
   reports Kueue workloads that are not admitted, with the reason.
6. Waits up to 40 more for the OpenSpline worker, printing every minute the container at
   work (install, models, the server) and its last log line.
7. Waits for every pod, then prints the URLs.
8. With `RUN_TESTS=1`, renders the chart's test pod with the same values and runs it through
   `scripts/test-services.sh`, which retries on another node when the sandbox fails three
   times.

It exits non-zero on any FAIL. Helm only waits for the `n8n-setup` hook Job, not for the
Deployments; the script's own waits do that.

## Runbook (TMM, from the Mac)
Start with `oc project -q` (the Mac's default kubeconfig is the TMM one). If `oc` says
"Unauthorized", log in again (the private environment file has the exact command).

```bash
scripts/setup.sh --status                       # the nine steps at a glance
oc get pods -o wide                             # where everything runs
oc get workloads.kueue.x-k8s.io                 # GPU pods waiting for Kueue, and why
oc get isvc                                     # Whisper Ready?
scripts/demo-preflight.sh -f chart/values-demo-cluster.yaml   # everything, including the OpenSpline worker
```

**Voice problems: read the voice agent.** Each line is logged twice, once plain and once as
JSON; drop the JSON copies:
```bash
oc logs deploy/voice-agent --since=20m | grep -v '^{' | grep -E 'voice-agent|avatar|noise|interrupt|playback|WARN|ERROR' | grep -v remote_session
```
- `joined room ... face=<id> voice=<voice>`: the face and the voice chosen.
- `avatar provider openspline started`: the avatar is up. About 5 s the first time, under
  1 s after that.
- `ignoring noise transcript '-'`: Whisper's text for a sound, dropped.
- `timings (...)`: per-turn `transcription_delay`, `llm_node_ttft`, `tts_node_ttfb`,
  `playback_latency`, `e2e_latency`.
- `latency: end of speech to first audio 1.45s`: the headline number.
- The tracebacks from `remote_session` when a room closes are harmless noise from LiveKit.

**OpenSpline:**
```bash
oc logs deploy/openspline -c install       # first start only: the install onto the volume
oc logs deploy/openspline -c models        # first start or a new quality: the weights
oc logs deploy/openspline -c openspline    # "publish options patched", the worker start, sessions
oc exec deploy/openspline -c openspline -- curl -s localhost:7860/readyz   # ready / occupied per worker
oc logs deploy/livekit --since=30m | grep openspline | grep 'mediaTrack published'   # bitrate and layers of the avatar video
curl -s https://frontend-<project>.<domain>/api/v1/voice/faces | jq .   # what the face picker gets
```

**Restarts and cleanups:**
```bash
oc rollout restart deployment/voice-agent deployment/rag-api   # after changing portraits or config
oc rollout restart deployment/openspline                       # reapply the patch, reload the worker
helm uninstall assistant                                       # remove the demo; the PVCs stay (resource-policy keep)
oc delete pvc -l app.kubernetes.io/instance=assistant          # then the data, if wanted
```

The chart keeps PVCs on uninstall (`helm.sh/resource-policy: keep`): postgres, qdrant, n8n,
object-store, openspline-data. The OpenSpline volume holds the install and the weights
(about 20 GB with both qualities). Keep it to start in a minute instead of about three.
