# History

What was done, in order, and why. Commits are on `main` unless marked otherwise. CI promote
commits (`ci: deploy images sha-<7> ...`) are left out. `git log` has the details; this page
has the reasons.

## 2026-09: the quickstart
- **2026-09-07: direction.**
  - Everything deploys from Helm templates in the repository. Infrastructure (PostgreSQL,
    Qdrant, the object store, n8n, LiveKit) gets hand-written templates that run under the
    restricted SCC; community charts hard-code UIDs.
  - The frontend is custom React with LiveKit. The RAG stack uses direct clients (openai,
    qdrant-client, Docling). LiveKit is self-hosted.
  - Argo CD is the team's delivery path, layered on top. The published quickstart must
    install as a regular user, with no cluster-admin requirement (an rh-ai-quickstart rule).
  - Layout: `chart/`, `deploy/argocd`, `deploy/bootstrap`, `scripts/`.
- **2026-09-07:** voice in the browser works through TURN over TLS: a passthrough Route, the
  wildcard certificate, and `allow_restricted_peer_cidrs` for the pod network.
- **2026-09-08:**
  - the repository moved to `rh-ai-quickstart/enterprise-voice-avatar-assistant` (public);
  - Tavus chosen as the avatar over Simli;
  - Slack, Google and n8n accounts created (see [environments.md](environments.md)).
- **2026-09-09:**
  - images moved from GHCR to quay.io, and CI promotes the tags into
    `chart/values-demo-cluster.yaml`;
  - sample documents for the fictional "Example Corp": industry- and location-neutral, EUR
    amounts, 15 files.
- **2026-09-10:**
  - setup step 5 became a guided checklist with verification (`739379d`, `c29aba7`);
  - requests from a conversation always wait for approval (`66cb015`);
  - the RAG API stream serialises tickets in JSON mode (`48e2dab`): datetimes broke the
    stream after every filed request;
  - docs: SETUP.md "Run it" and "Day two" (`6d61e36`).
- **2026-09-25 to 27: the admin portal**, built in five phases (spec in
  [admin-portal.md](../admin-portal.md)):
  - PR #6, phases 1 to 3: schema, sign-in, audit, activity events, the event stream,
    approvals, tickets, conversations, archive records;
  - PR #7: VersityGW instead of MinIO, whose images vanished from Docker Hub and quay.io;
    ingestion emits object-created events itself (`201e7e8`);
  - PR #8, phases 4 and 5: the remaining pages, Playwright end-to-end tests with a compose
    stack and fakes, docs (`617af99`).
  - Decisions:
    - New conversation keeps the transcript (no delete);
    - no retention policy;
    - the six open questions in the spec settled as written.
- **2026-09-27: PR #9, Tavus optional** (`50dda0f`).
  - Without a key, voice is audio-only with the default voice. The deploy scripts pass
    `avatarProvider=none`.
  - Housekeeping: ruff format enforced in CI, lazy-loaded portal pages and voice panel,
    `gap.recorded` events.
- **2026-09-29: PR #10** (`d2aa951`):
  - n8n workflow tests in CI (24 scenarios);
  - WF6 sends one reminder per wait;
  - fixes from a security review: tokens on the WF2/WF3 webhooks, internal routes closed
    without a token, Slack text escaped, safe session ids, portal frame headers, an
    ingestion queue cap.
  - Hardening items 7 to 9 were not chosen; they are on the to-do list.

## 2026-10-02: a fresh sandbox and false request claims
- Sandbox "6srtk" (1x L4, RHOAI 3.5.1): `scripts/setup.sh` passed all nine steps. The browser
  trial covered the Tavus avatar, a request approved in the portal, a spoken update, and the
  activity timeline.
- **A bug:** voice "I want to order a new laptop" got the intent "question" from Llama 3.2 3B,
  and the answer claimed it had logged a request. **PR #11** (`eeaed86`):
  - a no-actions rule on every answer prompt;
  - intent detection sees the previous turn only with a `REQ-` reference;
  - a sentence-level guard drops false claims;
  - `scripts/eval-requests.sh`. Result: intent 22/22, false claims 0/18.
- Setup fixes: Skip at step 5a turns Slack off without asking for the token again; step 8
  counts only indexed documents (`da11618`, `ba77201`).

## 2026-10-23: the Red Hat TMM shared cluster
Mohamed asked for "everything 100% automated in setup.sh", on a cluster where the login
administers one project only.
- **`7db549d`: the namespace path in setup.sh, and macOS support.**
  - Detection: access, project, domain from the console URL, Kueue GPU offers, MaaS models.
  - Step 2: Models-as-a-Service where it has the model, else a chart model on a booked slice.
  - Step 4: TURN through a namespaced cert-manager Issuer.
  - Step 5: creates the MaaS key.
  - Step 6: a Helm install through `deploy.sh`.
  - On a Mac: Homebrew, bash 5, the tools, and a re-exec.
- **`b530336`: the helm test and the node guard.**
  - The test trusts public endpoints and sends keys; it had passed only the service CA to
    curl.
  - `deploy.sh` moves pods off a node that cannot start containers; the chart value
    `global.avoidNodes` keeps them off.

## 2026-10-24: reliability, latency, the self-hosted avatar
- **`24f85b5`:** the first two documents raced to create the Qdrant collection and one got a
  409. Fix: a lock, and accepting 409.
- **`195252a`:** setup reads the avoided nodes at startup, so step 9's test pod avoids them.
- **`3d4503a`:** `test-services.sh` restarts the test pod elsewhere after three sandbox
  failures. The ACME solver avoids the listed nodes too.
- **`a262c76`:** the RAG API had started before PostgreSQL and never created its tables (a
  500 on filing a request). Both services now retry creating the schema in the background,
  and the preflight checks the tables.
- **`82db5a9`: voice latency, the seven items.**
  - Kokoro thread cap and GPU image; per-sentence TTS; PCM; shorter turn end with the local
    turn detector; RAG API keep-alive, connection pool and early stream.
  - End to end went from 7.3–11.4 s to 1.1–2.0 s. See [voice-pipeline.md](voice-pipeline.md).
- **`c7830f2`:** the install notes say which GPU Kokoro runs on.
- **`87ad851`: phantom turns, and End voice then Start.**
  - VAD minimum speech 0.25 s, a noise filter.
  - A new room for every voice start; Tavus failed on a reused room name.
- **`2ad0935`: OpenSpline next to Tavus**, chosen in setup step 2.
  - Self-hosted on a whole GPU; it installs itself onto a volume at first start because
    in-cluster builds were impossible.
  - The voice agent provider, the RAG API integrations test, the deploy wait, the preflight
    check.
  - Benchmarked on the booked H200: low quality at 5.2x real time.
- **`072440a`: the face did not show, and noise still got answers.**
  - OpenSpline publishes without a track source, so the frontend now takes unknown-source
    tracks of the avatar worker.
  - Preemptive generation reached the RAG API before the noise check, so `llm_node` skips
    noise too; the filter grew.
- **`722aa30`: four faces in the picker for OpenSpline** (Meera, Lisa, Steve, Emily).
  - Portraits are chart files in a ConfigMap, mounted in the voice agent and the RAG API.
  - The voice follows the face's gender.
- **`5e063c2`: answers cut off mid-sentence.**
  - An avatar cannot pause, so every sound interrupted. With an avatar, only words
    interrupt, and noise never becomes words (`stt_node`).
- **`571acbf`: the frontend.** The avatar frame takes the video's shape, square for
  OpenSpline; the chat shows a repeated greeting once.
- **`b5dd698`:** OpenSpline's video at 2 Mbps in one stream instead of LiveKit's 300 kbps
  simulcast. Emily was a smear. The chart patches OpenSpline's publish call at start.
- **`193c128`: OpenSpline quality from the GPUs.**
  - Setup picks high (the Pro model) on a pair of whole GPUs when Kokoro keeps its slice,
    else low. `AVATAR_QUALITY` overrides it. The chart builds the workers from the quality
    and the GPU count.
  - High on one H200 measured 1.07x real time, which is why it needs the pair.
- **2026-10-25:**
  - this handoff folder, and `CLAUDE.md` (`d943998`);
  - setup rebuilds what only the first machine had from the cluster, so another laptop
    needs no copying: `~/secrets.env`, the avoided nodes, and the private environment notes
    (`77a3215`);
  - verified on a real second Mac with the demo already deployed: Homebrew `oc`, login,
    `oc project` (a new kubeconfig had none), then a full `scripts/setup.sh`. It restored ten
    keys, the avoided node and the private doc, ran steps 1, 2, 3, 5, 7 and 9 without
    redeploying, and the preflight passed. Two rough edges went into the to-do list: no
    current project in a new kubeconfig, and question 5e asked again.
- **2026-10-26:** the handoff notes brought up to date with the second-laptop run
  (`cd3fc8a`). After a new login, the queue's slot label had moved a day forward and the GPU
  pods had kept running: the booking continues past each slot end. A check of the nodes from
  the project (`a794a45`): the broken node stays avoided, one CPU worker is watched after a DNS
  failure restarted n8n.
- **2026-10-26 evening: the avatar switch.**
  - Mohamed reported the avatar slow and out of sync. OpenSpline and the voice agent kept up;
    the likely cause is `b5dd698`'s single 2 Mbps stream over TURN on TCP. Back to simulcast,
    at 1 Mbps; the patch on the volume now reads simulcast from the environment.
  - He asked for much higher quality and resolution, and for a way to try Tavus, OpenSpline
    and a third avatar in the UI. The search ([avatar-research.md](avatar-research.md)) found
    no open model with real-time HD on one GPU; he chose a self-hosted research track over a
    hosted third vendor.
  - Several avatars in the UI: `voiceAgent.avatarProviders`, an Avatar switch above the faces,
    the provider in the token, per-session providers in the voice agent, setup step 2
    accepting `openspline,tavus` (`d2dd3c4`).
  - Claude on pull requests: `.github/workflows/claude.yaml` answers "@claude", with the
    workflow's `GITHUB_TOKEN` instead of a GitHub App (`d56a08b`). Mohamed added the secret
    (`CLAUDE_CODE_OAUTH_TOKEN`); on PR #12 Claude answered `@claude review this PR` within
    seconds, as `github-actions[bot]`. Pushing a workflow file needed the `workflow` scope on
    the `gh` login (`gh auth refresh --scopes workflow`).
  - PR #13 merged as `756396d`; its images are `sha-756396d` (`b2db8ef`).
  - Lips late after interruptions: every OpenSpline answer starts from the portrait's pose
    (`openspline.resetPoseEachAnswer`, a patch to `session.py`), and `scripts/voice-timings.sh`
    collects the timings of voice sessions from the logs.
  - PR #12: the demo script for the avatar switch, with the five suggestions of its `@claude`
    review, squash-merged as `11f1532`.
  - Beyond Presence as a third avatar (`bey`, PR #13): the LiveKit plugin, its key in setup step 5 and
    the integrations secret, three stock avatars in `voiceAgent.beyFaces`,
    `scripts/list-bey-avatars.sh`, and a live check on the Integrations page. The voice agent
    moved to LiveKit Agents 1.8.9 for the plugin (the `openai` SDK to 3.x with it).

## Measurements to compare against
Kept in [status-and-todo.md](status-and-todo.md) ("Measured"), [voice-pipeline.md](voice-pipeline.md)
and [openspline.md](openspline.md).
