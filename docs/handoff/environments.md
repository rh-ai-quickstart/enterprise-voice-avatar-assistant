# Environments

Two kinds of cluster have run the demo, and `scripts/setup.sh` detects which one it is on
from the login's permissions. A cluster-admin gets the full path. A user who administers
one project gets the namespace path. CI and the registry are shared by both.

Exact internal values for the Red Hat TMM cluster are kept out of this public repository.
Locally they are in `docs/handoff/private/environment.md` (gitignored), or read them off the
cluster as shown below.

## 1. RHDP sandbox clusters (cluster-admin path)

Temporary clusters from the Red Hat Demo Platform, usually three days long. Each trial is a
fresh `scripts/setup.sh` on the sandbox's bastion host, from a new clone. Nothing carries
over: `~/secrets.env`, the n8n owner and the cluster secrets all start empty.

Facts that held across them:
- OpenShift 4.19 or later with OpenShift AI 3.x. Setup stops on 2.x: one sandbox had
  RHOAI 2.25, where the deployment mode is `RawDeployment` instead of `Standard`.
- One node with NVIDIA L4 GPUs; the earliest sandbox had four. Setup's `gpu` profile
  advertises each GPU four times through time-slicing (the GPU Operator ClusterPolicy). The
  language model takes 45 to 55% of a card, Whisper 15% and BGE-M3 12%.
- The full path, in order:
  - installs the operators that are missing (`deploy/bootstrap`: NFD, GPU Operator,
    cert-manager, OpenShift GitOps);
  - sets KServe `Managed`;
  - lowers the GPU share of an already deployed language model;
  - registers an Argo CD Application (`deploy/argocd`) that syncs `chart/` from `main`
    with `chart/values-demo-cluster.yaml` and the setup overlay as parameters.
- Images update by GitOps: CI rewrites the tags in `chart/values-demo-cluster.yaml` on
  `main`, and Argo CD rolls them out.
- Clusters used so far, all gone now:
  - sandbox "6s896": 4x L4, RHOAI 3.5;
  - "6qzw6": the "AI v3" item, 1x L4, RHOAI 2.25. A branch there used OpenAI and ElevenLabs
    for every model; that work is not in `main`;
  - "6srtk": 1x L4, RHOAI 3.5.1, Llama 3.2 3B pre-deployed. All nine steps passed there
    on 2026-10-02.

Gotchas from these clusters:
- **Docker Hub pull limits.** Prefer `ghcr.io`, `quay.io` and `registry.redhat.io` images.
- **Service-link variables.** Kubernetes service-link environment variables (`LIVEKIT_PORT`
  and so on) break LiveKit. Every pod sets `enableServiceLinks: false`.
- **HTTPS model endpoints.** RHOAI 3.5 `LLMInferenceService` workloads serve HTTPS on port
  8000 with a service-CA certificate. Services trust it through `SERVICE_CA_FILE`
  (`/var/run/secrets/kubernetes.io/serviceaccount/service-ca.crt`); n8n through
  `NODE_EXTRA_CA_CERTS`.
- **GPU taint.** GPU nodes can carry the taint `nvidia.com/gpu=True:NoSchedule`, which model
  pods tolerate.
- **Granite Guardian.** The 3.3 modelcar has no chat template; the chart mounts
  `chart/files/granite-guardian-3.3-chat-template.jinja`.
- **TURN.** LiveKit TURN uses the cluster's wildcard certificate, copied into
  `livekit-turn-tls`. `turn.allow_restricted_peer_cidrs` must include the pod network:
  LiveKit refuses TURN permissions to private peer IPs, the SFU's own pod IP among them.

## 2. The Red Hat TMM shared cluster (namespace path)

An internal, shared, multi-tenant cluster (IBM Cloud, Kubernetes 1.35). Each user is admin of
one project named `user-<name>`. It is not cluster-admin, and there is no SSH or bastion: run
`oc login --token` from the laptop, or use the console's web terminal. The demo currently
runs here (see [status-and-todo.md](status-and-todo.md)).

What the project can and cannot do:
- **It can:** create routes (with custom hosts), PVCs, secrets, jobs, roles,
  InferenceServices, ServingRuntimes, BuildConfigs, cert-manager Issuers and Certificates.
- **It cannot:** read cluster config such as `ingresses.config.openshift.io`. Setup takes
  the apps domain from the console URL instead.
- **It cannot list nodes.** Find out where pods run with `oc get pods -o wide`.
- **Storage:** the default StorageClass is IBM VPC block, with a **10 GB minimum** per
  volume. Setup sets n8n's volume to 10Gi.

**Kueue manages the project.**
- Pods without a queue label get the LocalQueue `default`: CPU and memory, no GPUs.
- **GPUs** (H200, whole or MIG: 7g full, 3g.71gb, 2g.35gb, 1g.18gb) come only from the
  console's GPU Booking page.
- A booking appears as ClusterQueue `user-<name>` with the label
  `rhai-tmm.dev/until=<epoch>`. The label is the end of the current daily slot, and moves
  forward each day while the booking lasts. The project gets a LocalQueue `reserved` for it.
- `unreserved` can borrow idle slices without a booking, but can be preempted.
- A GPU pod needs the label `kueue.x-k8s.io/queue-name: reserved`. Setup adds it through
  `models.podLabels`, `models.tts.podLabels` and `openspline.podLabels`.
- When a booking ends, Kueue evicts the GPU pods.

**Models-as-a-Service (MaaS)** is on the same cluster.
- It serves `llama-32-3b`, `bge-m3` and a few larger models, with no Whisper.
- The inference URL is `https://maas.<apps domain>/prelude-maas/<model>/v1`; the served name
  is the model id.
- The login belongs to two subscriptions. Calls need the AI BU GPU subscription, either as a
  header (the services cannot send custom headers) or through an API key bound to it.
- Setup step 5 creates that key with `POST /maas-api/v1/api-keys`
  (`{name, description, subscription, expiresIn}`) and saves it as `MAAS_API_KEY` in
  `~/secrets.env`. It prints the expiry; run `--step 5` again after it expires.

**TURN on TMM:**
- The cluster's wildcard certificate is in `openshift-ingress`, which the project cannot
  read.
- Setup step 4 creates a namespaced cert-manager Issuer (Let's Encrypt, HTTP-01) for
  `livekit-turn-<project>.<apps domain>`. The certificate came in about a minute.
- On failure, setup continues without TURN: voice then works on open networks only.

**Known cluster problems** (node names are in the private file):
- **A CPU worker with broken container storage.** Every pod sandbox there fails with
  `creating overlay mount ... input/output error`, and the scheduler favours it because it
  looks empty. `deploy.sh` and `test-services.sh` detect it and keep pods off it. The list
  is in `~/.assistant-setup/avoid-nodes`; the chart value is `global.avoidNodes`.
- **Full CPU workers.** The healthy ones had no CPU left for a 500m build pod. In-cluster
  image builds (BuildConfigs) were impossible; that is why OpenSpline installs itself at
  start (see [openspline.md](openspline.md)).
- **A stall.** Another worker stalled for about two minutes once (probes timed out); kubelet
  restarts recovered it.
- **Login tokens expire after some hours.** `oc` then answers "Unauthorized", even mid-task.

To read the internal values off the cluster when you are logged in:
```bash
oc whoami --show-server; oc whoami --show-console; oc project -q
oc get localqueue -o wide
oc get clusterqueue -o jsonpath='{range .items[*]}{.metadata.name} until={.metadata.labels.rhai-tmm\.dev/until}{"\n"}{end}'
```

## CI/CD (GitHub Actions, `.github/workflows`)
- **`ci.yaml` on every push and PR:**
  - Helm chart: lint, and renders per integration and portal combination;
  - per-service tests for ingestion, rag-api and voice-agent: ruff check, ruff format
    check, pytest. rag-api runs with a PostgreSQL service;
  - frontend: tests, build, and the nginx `/api` allowlist test;
  - n8n workflow tests;
  - Playwright end to end against a compose stack with fakes (`frontend/e2e`).
- **`build-images.yaml` on pushes to `main` touching `services/**` or `frontend/**`:**
  - builds the four images and pushes them to `quay.io/rh-ai-quickstart/enterprise-voice-avatar-assistant-<service>`,
    tagged `sha-<7>` and `latest`;
  - then a `promote` job rewrites the image lines of every `chart/values-demo-cluster*.yaml`
    and commits `ci: deploy images sha-<7> to the demo clusters` to `main`;
  - pushes touching only the chart, scripts or docs build nothing.
- **Access:**
  - Quay credentials are the repository secrets `QUAY_USERNAME` and `QUAY_PASSWORD` (a robot
    account with write access to the four repositories).
  - Mohamed has push access to the org repository, but no admin. Repository secrets, package
    visibility, topics and branch protection need an org admin (Sander can help).
- **GHCR is no longer used.** Images moved to quay.io on 2026-09-09.

## External accounts
None of these are in git. Never ask for or print their secrets.

| Service | What exists | Where its secret lives |
|---|---|---|
| Slack | Workspace for the fictional "Example Corp", app "Enterprise Assistant", channels `#assistant-ingestion`, `-documents`, `-approvals`, `-tickets`, `-knowledge-gaps`. Interactivity URL: the n8n Route plus `/webhook/slack-interactions` (Socket Mode off) | `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET` in `~/secrets.env` and the `assistant-integrations` secret; the n8n Slack credential |
| Google | A Cloud project under Mohamed's account. A service account writes transcripts to a shared Drive folder (no OAuth client) | `GOOGLE_SERVICE_ACCOUNT_FILE` (the JSON key, kept as `~/.assistant-setup/google-sa.json`), `GOOGLE_DOCS_FOLDER_ID` |
| Tavus | Personal free plan: 25 conversational minutes a month, one concurrent stream. Faces in `chart/values-demo-cluster.yaml` (Phoenix-4.5: Gloria, Victor, Lucas, Helen) | `TAVUS_API_KEY` |
| Models-as-a-Service (TMM) | An API key per cluster, created by setup step 5 | `MAAS_API_KEY` |
| n8n | Owner account and API key, per cluster, created by the `n8n-setup` Job | `assistant-n8n` secret (back it up: losing `N8N_ENCRYPTION_KEY` makes stored credentials unreadable) |
| quay.io | Four public repositories under `rh-ai-quickstart`, a robot account for CI | GitHub repository secrets |
| OpenSpline | No account. Self-hosted; the weights come from Hugging Face without a token | none |

`secrets.env.example` lists every key the scripts read.
