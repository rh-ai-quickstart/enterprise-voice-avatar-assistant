# Using scripts/setup.sh

`scripts/setup.sh` takes a freshly provisioned OpenShift AI cluster to a working demo from
the bastion host. It discovers what the cluster already has, shows the state of nine steps,
runs the remaining ones in order, and saves its progress so it can be stopped and resumed at
any point. The README's [Setup](../README.md#setup) section says what the environment must
look like and how to get there; this page is the reference for the script itself: how to
call it, what each step checks and changes, where it keeps its files, and what to do when a
step stops.

## Run it

1. SSH to the bastion host with the host, user and password from the provisioning e-mail:

   ```bash
   ssh lab-user@bastion.<guid>.<base domain>
   ```

   Keep the e-mail at hand: if the bastion is not logged in to the cluster, or its session
   expired, the script asks for the API URL (it proposes the one it finds on the host), the
   user (`kubeadmin`) and the password, and logs in for you.

2. Clone the repository:

   ```bash
   git clone https://github.com/rh-ai-quickstart/enterprise-voice-avatar-assistant.git ~/enterprise-voice-avatar-assistant && cd ~/enterprise-voice-avatar-assistant
   ```

3. Run the setup and follow it:

   ```bash
   scripts/setup.sh
   ```

   It logs in if the bastion is not, prints the state of the cluster and of every step, then
   runs the remaining steps one after the other. It stops only where it needs something from
   you (the keys and browser actions in step 5) or when a step fails, with the reason and the
   commands that show more; run it again and it resumes at that step. `scripts/setup.sh --status`
   only shows the state, `scripts/setup.sh --step N` runs one step again, `scripts/setup.sh --yes`
   never prompts. Progress and discovered facts are in `~/.assistant-setup/state.env`, logs of
   each step in `~/.assistant-setup/logs/`.

## Quick reference

```bash
scripts/setup.sh            # discover, show the progress, run every remaining step in order
scripts/setup.sh --status   # discovery and progress only; changes nothing
scripts/setup.sh --step 6   # run step 6 (again), then stop
scripts/setup.sh --yes      # never prompt: keys come from ~/secrets.env, missing ones are skipped; no Let's Encrypt e-mail
scripts/setup.sh --reset    # forget the saved progress; the cluster is not touched
scripts/setup.sh --help     # the same list
```

| Environment variable | Default | Meaning |
|---|---|---|
| `PROJECT` | `voice-avatar-assistant` | Namespace the demo is deployed into |
| `LLM_NAME` | `llama-32-3b-instruct` | Name of the InferenceService the cluster already serves; found in any namespace |
| `SECRETS_FILE` | `~/secrets.env` | The one file holding every key (see step 5) |
| `STATE_DIR` | `~/.assistant-setup` | Progress, logs, the profile overlay and the pasted service account key |
| `DEBUG` | unset | `DEBUG=1` also writes a full command trace next to the run log |
| `PROFILE`, `REMOTE_*` | unset | `PROFILE=remote` with `REMOTE_LLM_ENDPOINT`, `REMOTE_LLM_MODEL`, `REMOTE_STT_ENDPOINT`, `REMOTE_EMB_ENDPOINT` (optional `REMOTE_STT_MODEL`, `REMOTE_EMB_MODEL`) uses remote OpenAI-compatible endpoints instead of GPU models |

Files the script writes, all under the home directory of the bastion user:

| File | Content |
|---|---|
| `~/.assistant-setup/state.env` | Saved facts and progress: API URL and user of the login, versions, GPU count, domain, the language model's namespace, the profile, `STEP_N_DONE` and `STEP_N_LAST_RUN` per step |
| `~/.assistant-setup/logs/setup-<timestamp>.log` | Everything one run printed; `bootstrap-<timestamp>.log` and `deploy-<timestamp>.log` next to it hold the output of steps 3 and 6 |
| `~/.assistant-setup/values-object.json` | The deployment profile: a small JSON overlay Argo CD merges over `chart/values-demo-cluster.yaml` (model shares, vLLM arguments, guardrails off) |
| `~/.assistant-setup/google-sa.json` | The Google service account key pasted in step 5, mode 600 |
| `~/secrets.env` | Every key and token, mode 600; created from `secrets.env.example` |
| `~/assistant-cluster.env` | Written by the deploy step: apps domain, language model endpoint and served model name, for `scripts/demo-preflight.sh` |
| `~/bin/helm` | Installed by the script when the bastion has no `helm` |

## A run, from the top

Every run starts the same way, whatever the options:

1. **Login.** If `oc whoami` fails (fresh bastion, expired token), the script lists the API
   URLs it can find on the host, proposes the first one, asks for the user (`kubeadmin`) and
   the password with hidden input, and logs in; it retries without TLS verification when the
   API certificate is self-signed. The URL and user are saved for the next run, the password
   is not.
2. **Discovery.** A few seconds of `oc` calls: cluster version and nodes, GPUs and their
   memory, the operators (looked up in their own namespaces, which is fast), the
   DataScienceCluster and KServe, the language model and its GPU memory share, the project,
   Argo CD, the TURN secret, the secrets, the application state, the models, the pods, the
   routes, and the number of documents indexed.
3. **The status board.** One line per step with a mark: `✔` done, `○` to do, `!` needs
   attention, plus a detail in grey. Every mark is decided from the cluster, not from the
   progress file alone, so work done by hand or by an earlier run is recognised and not
   repeated.
4. **Tools.** `helm` is installed into `~/bin` when missing (from the cluster's own download
   site, else from get.helm.sh). `oc`, `jq`, `git`, `openssl`, `curl` and `python3` must exist;
   the RHDP bastion has them.
5. **The steps.** The first step that is not done runs, then the next, and so on. Each one
   prints a timestamped header and footer with its duration and exit code. The script stops
   when a step fails, or when a step finished but the cluster does not show it as done, and
   says what to look at. Run it again and it resumes at that step.

Prompts appear in exactly two places: the login, and step 5 for the keys and the browser
actions. Nothing else waits for Enter.

## The nine steps

### 1 Cluster and prerequisites

Checks only. Login and tools, cluster-admin permission, OpenShift 4.19.9 or later, OpenShift
AI 3.x (2.x stops the script), KServe `Managed`, the four operators (Node Feature Discovery,
NVIDIA GPU Operator, cert-manager, OpenShift GitOps; missing ones are installed in step 3),
the GPUs, the deployed language model with its current GPU memory share, and a default
StorageClass.

### 2 Deployment profile

Decided from the GPUs found, without a prompt:

| Found | Result |
|---|---|
| One or more GPUs with at least 20 GB | Profile `gpu`: every GPU is used and advertised four times through time-slicing; the language model gets 55% of a card (the cluster's Llama 3.2 3B, or Llama 3.1 8B 4-bit deployed by the chart at 45% when no model is found), Whisper 15%, BGE-M3 12%; no guardrail model |
| No GPU | The script stops and prints what is required, what the cluster has, and the two options: a GPU node, or `PROFILE=remote` with the `REMOTE_*` endpoints |
| A GPU with less than 20 GB | Stops with the same kind of message |
| `PROFILE=remote` in the environment | Every model is a remote endpoint; the endpoints are saved, the keys go into `~/secrets.env` in step 5 |

The result is `~/.assistant-setup/values-object.json`. The values file in git stays
cluster-neutral: no domain, no endpoint, no key.

### 3 Cluster bootstrap

Runs `scripts/bootstrap-cluster.sh` with the profile's GPU slices and the model's share
(log `bootstrap-<timestamp>.log`). Cluster-admin work, in order: access and facts; operators
(installs what is missing from `deploy/bootstrap/operators`, never touches an installed
OpenShift AI); KServe `Managed`; the Node Feature Discovery instance and the GPU node labels;
the GPU Operator ClusterPolicy with the time-slicing ConfigMap, until the node advertises
four `nvidia.com/gpu`; the deployed language model, whose vLLM `--gpu-memory-utilization` is
lowered to 0.55 with a `Recreate` deployment strategy so the old pod releases the card before
the new one starts (the step waits until exactly one predictor pod runs with the new share and
is Ready, and prints the pod state and its last log line every 30 seconds); Argo CD ready;
the project with its Argo CD and OpenShift AI dashboard labels and the AppProject;
`scripts/check-prereqs.sh` as a summary. Takes 5 to 15 minutes on a fresh cluster, about a
minute when everything is in place.

### 4 TURN certificate

Voice through corporate networks needs TURN over TLS with a trusted certificate. The step
reads the cluster's ingress wildcard certificate, checks that it covers `*.<apps domain>` and
verifies as trusted, and copies it into the project as secret `livekit-turn-tls`. When the
wildcard is self-signed it asks for an e-mail address and requests a certificate from Let's
Encrypt through cert-manager instead (the apps domain must be reachable from the internet);
an empty answer skips TURN, voice then works on open networks only, and `--step 4` can be
run later.

### 5 Keys and integrations

The only step that needs you in a browser, on your laptop. It creates `~/secrets.env` from
`secrets.env.example` on the first run, then walks through the manual actions one at a time:
each is printed with its number, the script waits for Enter (or `Skip`) where you have to do
something, asks for the resulting value with hidden input, and checks the value against the
service before moving on. Nothing is trusted on your word: a wrong token, an unshared folder
or a revoked key is reported at once and asked again. At the end the secrets in the project
are created or refreshed with `scripts/create-secrets.sh`.

| Part | Browser action | What the script checks or does |
|---|---|---|
| n8n owner (5o) | none | Asks for the owner e-mail (default `admin@example.com`) and a password (Enter generates one; n8n wants 8 characters with a number and a capital). The chart's job creates the account with them. Once the account exists, the cluster's values are copied into `~/secrets.env`; change the password in the n8n UI, then in the file |
| Slack app (5a, 5b) | Create the app from the manifest the script prints (also saved as `~/slack-app-manifest.json`, with this cluster's n8n host already in it), install it, paste the Bot User OAuth Token | Calls Slack with the token and prints the app and workspace names; a rejected token is asked again |
| Slack channels (5c) | none, normally | Lists the channels, creates the five missing ones and joins them (scopes `channels:manage` and `channels:join` from the manifest). An app installed with fewer scopes gets the instruction and a confirmation instead |
| Slack request URL (5d) | For an app created from this manifest: none. For a reused app: set Interactivity & Shortcuts > Request URL to `https://<n8n host>/webhook/slack-interactions` | Asks which case it is; remembers the host it was confirmed for, so a re-run on the same cluster does not ask again |
| Tavus (5e) | Create an API key | Calls the Tavus API with it; a rejected key is asked again |
| Google key (5f, 5g) | Enable the Drive API, create a service account and a JSON key, paste the file's content (finish with a line containing only `}`; a path to the file also works) | Validates the JSON, then obtains an access token with it; a revoked key or disabled account is asked again |
| Google folder (5h) | Share a Drive folder with the service account's e-mail as Editor, paste the folder's URL or id | Reads the folder through the Drive API as the service account and checks it can add files; an unshared folder or a wrong id is asked again |
| Remote model keys (profile `remote` only) | none | `LLM_API_KEY`, `STT_API_KEY`, `EMBEDDINGS_API_KEY` |

There is no Google OAuth client and no redirect URL: the RAG API writes the documents with
the service account. Enter alone never skips a value; `Skip` at any prompt leaves that
integration off, with a warning that says how to add it later (put the value in
`~/secrets.env`, then `--step 5` and `--step 6`). Values already in the file are not asked
again but are still verified, so a run interrupted here continues where it stopped, and a key
that stopped working is caught on the next run. With `--yes` nothing is asked: keys in the
file are used, missing ones are reported as skipped. No key is ever typed into `oc` or pasted
into a manifest.

### 6 Deploy with Argo CD

Runs `scripts/deploy-argocd.sh` (log `deploy-<timestamp>.log`): checks; finds the language
model and probes its `/v1/models` from inside the cluster; creates the secrets from the file;
registers the Argo CD application with the repository, the values file, the profile overlay,
and the apps domain, the model endpoint and the served model name as Helm parameters; deletes
routes created earlier with another host so Argo CD recreates them; then waits for the sync
operation to succeed (not only for Synced/Healthy: the `n8n-setup` hook Job runs inside the
sync), for every InferenceService and pod, prints the URLs, and runs the connectivity test
pod (`scripts/test-services.sh`, rendered with `helm`). While waiting it prints the
application state every 40 seconds with the sync message, the first error line of any
crash-looping pod, and the health message of degraded resources.

A sync held by a failing hook Job is ended and started again at the latest revision, and a
sync that ended in `Failed` or `Error` is retried once. 10 to 20 minutes on a fresh cluster,
mostly the Whisper and BGE-M3 downloads; about a minute afterwards.

### 7 n8n workflows

Nothing to click in n8n: the chart's `n8n-setup` Job created the owner account (e-mail and
generated password in secret `assistant-n8n`) and an API key (secret `assistant-n8n-api`),
and the n8n init container imported the Slack credential and the seven workflows and
published them. The step waits up to ten minutes for the key, reads it from the secret,
lists every workflow with its state through the n8n API, and fails when one is inactive with
the command that shows why. It ends by printing the n8n URL, the owner e-mail and the command
that prints the password.

### 8 Sample documents

`scripts/load-sample-docs.sh` uploads the fifteen sample files to the object store: policies and
procedures into `documents`, invoices and contracts into `inbox`. The step waits until at
least ten documents are indexed (up to fifteen minutes, with a count every minute) and prints
`scripts/check-index.sh`. The inbox files show no chunks until their classification card is
approved in Slack (`#assistant-documents`); that is expected.

### 9 Verification

`scripts/demo-preflight.sh` with the same values, overlay and parameters as the deployment:
models Ready, no pod outside Running or Completed, the Argo CD state, the connectivity test
pod, and the six n8n webhooks registered. On `PRE-FLIGHT OK` the step prints the frontend
URL, the n8n URL with its login, and the pointer to [docs/demo-script.md](demo-script.md).

## Resuming, re-running, starting over

- **Resume.** `scripts/setup.sh` again. The status board shows where it stopped and the run
  continues there. A step already done by hand is recognised (the marks come from the
  cluster) and skipped.
- **Re-run one step.** `scripts/setup.sh --step N`, then `scripts/setup.sh` to continue. The
  usual reasons: a key changed (`--step 5`, then `--step 6`), a fix landed in the repository
  (`git pull`, then `--step 6`: it refreshes the application and re-syncs), the model share
  needs another look (`--step 3`).
- **Start over on the same cluster.** `scripts/setup.sh --reset` forgets the progress file
  only; the next run rediscovers everything and, since the cluster still has it all, mostly
  confirms it. To redeploy from nothing, delete the Argo CD application and the project first
  (README, [Delete](../README.md#delete)).
- **A new cluster.** Clone, `scripts/setup.sh`. The state directory is per bastion, so a new
  bastion starts clean. Tokens, the service account key and the Drive folder stay valid; copy
  the old `~/secrets.env` to the new bastion to skip re-pasting them. The Slack request URL
  contains the domain: step 5 asks whether the app is new or reused and, for a reused one,
  shows the URL to set and waits for the confirmation.

## Day two

- **Application updates.** Every push to `main` that touches the services builds images and
  commits their tags into `chart/values-demo-cluster.yaml`; Argo CD syncs within minutes.
- **A changed key.** Edit `~/secrets.env`, then `scripts/setup.sh --step 5` (rewrites the
  integrations, model-key and n8n secrets, generated passwords are kept) and
  `oc rollout restart deployment/n8n deployment/rag-api deployment/voice-agent -n voice-avatar-assistant`.
- **Chart values** (faces, voices, model shares) are commits to `chart/values-demo-cluster.yaml`;
  `scripts/deploy-argocd.sh` accepts `REPO_URL` and `TARGET_REVISION` for a fork or a branch.
- **Workflows.** A commit that changes `chart/files/n8n-workflows/` restarts n8n at the next
  sync, which re-imports and re-publishes the workflows; edits made in the n8n editor are
  overwritten by that, so make them in the files.
- **The profile.** `~/.assistant-setup/values-object.json` is merged over the values file by
  Argo CD; to change a model share, edit it and run `scripts/setup.sh --step 6`, or change the
  defaults in `scripts/setup.sh` (`write_values_object`) for every cluster.

## When a step stops

The step prints `FAIL` with the reason and, where useful, a `debug:` line with the commands
that show more. The run log has everything that was printed; the bootstrap and deploy steps
have their own logs next to it. Symptoms seen on real clusters, with what the script does
about them now and what to check if it happens again:

| Symptom | Cause | What to do |
|---|---|---|
| Step 3 waits for the model "to run only with the new share", then Whisper and BGE-M3 crash-loop in step 6 with `Free memory on device … less than desired` | The old model pod with the 95% share is still on the card | The bootstrap sets the `Recreate` strategy, deletes the stale pod and restarts a crash-looping new one. If it still times out: `oc get pods -n my-first-model` and the pod log it prints |
| Step 6: `helm is required` | No `helm` on the bastion | The script installs it into `~/bin`; if both download sites are unreachable, install it by hand and run again |
| Step 7: `job n8n-setup is failing` with `401` | The job could not keep n8n's login cookie | Fixed in the job; the step prints the last log lines of the job. After a fix in the repository: `git pull`, `--step 6` (re-syncs and re-runs the job) |
| Step 7: a workflow `INACTIVE` | The import container could not publish it, usually a missing credential or an older workflow file on the volume | `oc logs deploy/n8n -c import-workflows`. Workflows are re-imported whenever the shipped files change; a workflow edited in the n8n editor is overwritten by that |
| Step 6 sits on `waiting for completion of hook batch/Job/n8n-setup` | The hook Job keeps failing and holds the sync | The deploy ends that sync and starts a new one at the latest revision; the job's log says why it failed |
| Step 1: `OpenShift AI 2.x found` or `not installed` | The environment is not the assumed one | Provision the environment described in the README's [What is assumed](../README.md#what-is-assumed) |
| Step 2: `not enough GPUs` or `not enough GPU memory` | No usable GPU | Add a GPU node, or run with remote endpoints (`PROFILE=remote`) |
| Login fails | Wrong API URL or password, or the API certificate is not trusted yet | The values are in the provisioning e-mail; the script already retries without TLS verification |

More symptoms, grouped by area, are in [docs/troubleshooting.md](troubleshooting.md).

## What the script does not do

- It never prints a key or a password; typed secrets stay hidden and the status shows `<set>`.
- It never edits the repository: the domain, the endpoint and the profile go on the Argo CD
  application, not into `chart/values-demo-cluster.yaml`.
- It does not create the Slack app, the Tavus account or the Google Cloud project; those need
  your browser and your accounts.
- It does not undeploy anything; see [Delete](../README.md#delete) in the README.
