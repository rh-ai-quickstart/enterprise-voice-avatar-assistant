# Status and to-do list

As of 2026-10-26 (Mohamed's local time is UTC+4). The last code commit on `main` is
`77a3215`. The images are promoted to `sha-571acbf` in `chart/values-demo-cluster.yaml`. The
later commits (`b5dd698`, `193c128`, `d943998`, `77a3215`) change only the chart, scripts and
docs, so they need no new image.

## Where it runs

**The shared Red Hat TMM cluster**, project `user-<name>` (see [environments.md](environments.md)).
Mohamed is admin of that one project only, so setup takes the namespace path: a Helm release
`assistant`, no Argo CD, no cluster-wide changes.

| Part | Where it runs | Notes |
|---|---|---|
| Language model | Models-as-a-Service `llama-32-3b` | Reached with a MaaS API key that setup step 5 creates |
| Embeddings | Models-as-a-Service `bge-m3` | |
| Speech to text | Whisper large-v3-turbo, InferenceService in the project | On a MIG slice `nvidia.com/mig-3g.71gb` from the booked queue `reserved` |
| Text to speech | Kokoro (`kokoro-fastapi-gpu`) | On the second MIG 3g.71gb slice. 45 to 115 ms per sentence |
| Avatar | OpenSpline, low quality | On one whole H200 (`nvidia.com/gpu: 1`). Faces Meera, Lisa, Steve and Emily |
| Everything else | Default Kueue queue (CPU) | Frontend, RAG API, ingestion, voice agent, LiveKit, n8n, PostgreSQL, Qdrant, the object store (VersityGW) |
| Integrations | On | Slack, Google Docs archival, the admin portal |

The GPU booking is 1 whole H200 plus 2 MIG 3g.71gb slices. It appears as ClusterQueue
`user-<name>` behind LocalQueue `reserved`. Its `rhai-tmm.dev/until` label is the end of the
current daily slot and moves forward each day while the booking lasts. When the booking
ends, Kueue stops the GPU pods: Whisper, Kokoro and OpenSpline. Voice then stops; text chat
keeps working on Models-as-a-Service.

On 2026-10-25 at 01:07 the queue showed that quota with the label at Mon 26 Oct 00:00, the end
of that daily slot. On 2026-10-26 the login token had expired, so nobody has checked since
whether the booking carried on. Before anything else, log in and run
`scripts/setup.sh --status` and `oc get workloads.kueue.x-k8s.io`.

## Last verified (2026-10-25, from a second Mac)

`scripts/setup.sh` on a new laptop, with the demo already deployed. It restored the local
state from the cluster and ran steps 1, 2, 3, 5, 7 and 9 without redeploying (details in
[setup-and-deploy.md](setup-and-deploy.md#another-laptop)). Step 9's preflight passed at
01:12 local:
- Whisper Ready, every pod running, the database tables present;
- the OpenSpline worker loaded and free;
- all 11 connectivity checks OK, including the chat completion on Models-as-a-Service;
- the public API proxy refuses internal routes, and the admin portal answers;
- all 7 n8n workflows active and all 7 webhooks registered;
- 10 documents indexed and 5 inbox files classified (step 8).

Step 2 came to the same layout as the running release: OpenSpline low on the whole H200,
Whisper and Kokoro on the two MIG slices. Nothing in the browser was tested that night.

## Last confirmed deploy

The voice agent ran `sha-5e063c2` after `scripts/setup.sh --step 6` at 23:03 local. Every
connectivity check passed. The browser was confirmed working for:
- the OpenSpline video;
- the four faces in the picker;
- answers no longer cut off by noise.

Pushed afterwards, not yet confirmed in the browser (they need `git pull` and `--step 6`
if Mohamed has not run it since; the run on 2026-10-25 skipped step 6, so it did not deploy
them):
- `571acbf`: square avatar frame; the greeting appears once in the chat;
- `b5dd698`: OpenSpline video at 2 Mbps in one stream (Emily was a smear at 300 kbps);
- `193c128`: OpenSpline quality chosen from the GPUs. It stays low with today's booking.

## Measured

| What | Value |
|---|---|
| End of speech to first audio (voice, end to end) | 1.1 to 2.0 s (was 7.3 to 11.4 s before the latency work) |
| Whisper transcription after the end of speech | 0.5 to 0.8 s |
| RAG API first token | 0.45 to 1.3 s (Models-as-a-Service) |
| Kokoro first audio | 0.06 to 0.13 s on the GPU slice (5.6 to 8.4 s per sentence on CPU before) |
| OpenSpline playback delay (audio handed over to the face speaking) | 0.28 to 0.44 s; about 1 s for the first greeting |
| OpenSpline engine, low quality, 1 H200 | 5.2x real time, 0.18 s per 0.96 s of audio, 6 GB of GPU memory |
| OpenSpline engine, high quality, 1 H200 | 1.07x real time, 1.03 s per 1.12 s of audio, first video after 1.26 s, 8 GB |
| OpenSpline first start (install, 8 GB of weights, model load) | About 2.5 minutes. Later starts take about a minute |

## To do, in priority order

### 1. Confirm the last three pushes in the browser
- **Run:** `git pull`, then `scripts/setup.sh --step 6`, then hard-refresh the page.
- **Check:**
  - the frame is square around the avatar;
  - the greeting appears once, even after ending and restarting voice;
  - Emily is sharp, no longer a smear;
  - a long answer ("What's in the IT service catalog?") plays to the end;
  - saying "wait, I meant laptops" during an answer still interrupts it.
- **If anything cuts off:** read the voice agent log (runbook in
  [setup-and-deploy.md](setup-and-deploy.md)).

### 2. Sharper portraits (waiting on Mohamed)
The four faces are single frames of OpenSpline's 240x240 example GIFs, enlarged to 512x512.
That is the main reason the avatar looks soft. OpenSpline renders exactly what it gets.

Mohamed is to provide, per face (Meera, Lisa, Steve, Emily, or replacements):
- at least 1024x1024 (512 minimum), JPEG or PNG;
- front-facing, head and shoulders, eyes open, mouth closed;
- even light, a calm background;
- proof that we may use them. The cleanest route is asking OpenSpline for the originals.
  Licensed stock or consenting colleagues also work. The dashboard's portraits are not
  covered by the repository's MIT license, so do not scrape them.

Processing, once the files are on the Mac:
- crop each to a square, head and shoulders, with the face filling a good part of the frame;
- resize to 512 (or keep up to 1024; OpenSpline resizes to 512 itself);
- save as `chart/files/portraits/<id>.jpg`, JPEG quality around 90;
- keep all pictures together under about 700 KB, because they go into one ConfigMap (the
  limit is 1 MiB after base64);
- benchmark one with `openspline benchmark --portrait` (see [openspline.md](openspline.md));
- update `chart/files/portraits/README.md` (the source and license of each picture);
- deploy with `--step 6`. Only the ConfigMap changes; the voice agent and RAG API pick up
  the files on restart. Do a `rollout restart` of both if the files changed but the names
  did not.
- New people instead of the four: edit `openspline.faces` in `chart/values.yaml` (id, name,
  gender). There are four at most, and the first is the default.

### 3. Measure high quality on a GPU pair
`193c128` makes setup choose high quality when two whole GPUs are free next to the models
and Kokoro keeps its slice. The pair is not measured yet.
- Book a second whole H200, then run `scripts/setup.sh --step 2`. It should report
  "OpenSpline in high quality ... on 2 x nvidia.com/gpu". Then run `--step 6`.
- Run the benchmark inside the pod with `--quality high --devices 0 1` (command in
  [openspline.md](openspline.md)). Comfortable means a real-time factor of about 1.8 or more
  and a first block under 0.6 s.
- If it falls short, change the `auto` rule in `step2_namespace` (setup.sh) to stay low,
  and say so in the docs.
- Try it in the browser. The high weights (Model_Pro, VAE_Wan) are already on the volume
  `openspline-data`, downloaded for the single-GPU benchmark.

### 4. Smaller follow-ups
- **Setup status after a profile change.** The status board shows step 6 as done even after
  step 2 changed the profile (for example a new avatar). It should say "profile changed: run
  --step 6". Compare a hash of `values-object.json` saved at the last step 6.
- **No current project in a new kubeconfig.** After `oc login` into a new `~/.kube/tmm`,
  `oc project -q` answered "no project has been set". With no project saved by an earlier
  run either, setup keeps its default project
  `voice-avatar-assistant`, which the user does not administer, and step 1 stops. The
  workaround is `oc project user-<name>` (or `PROJECT=user-<name>`). Setup could take the
  project when the user administers exactly one.
- **Step 5e on another laptop.** Step 5 asks "Was the app created just now from the
  manifest above?" again, because its answer lives only in the local `state.env`. Answering
  yes, as Mohamed did, skips the check of the Slack Interactivity URL. Setup could skip the
  question when the Slack keys were restored from the cluster for the same n8n host, or
  word it for an app made earlier.
- **Low quality on a MIG slice.** It might free the whole H200: a 3g.71gb slice has about 3/7
  of the compute, and low runs at 5.2x on the whole card. Not tested.
  `KUEUE_QUEUE=reserved GPU_RESOURCE=nvidia.com/mig-3g.71gb` only works for every model at
  once, so this needs an `AVATAR_GPU_RESOURCE` override in setup.
- **More than one avatar session at a time.** The chart already makes one worker per GPU
  (low) or per pair (high). Setup gives the avatar one or two GPUs. An `AVATAR_SESSIONS=n`
  setting would ask for n times as many.
- **Tell OpenSpline upstream.** Their LiveKit sink publishes without a track source and with
  LiveKit's default bitrate. The chart patches both at start (see [openspline.md](openspline.md)).
  An issue or PR upstream would let the patch go. That is outward-facing, so ask Mohamed first.
- **A prebuilt OpenSpline image.** It would mean a faster first start and no download from
  GitHub at start. `services/avatar-openspline/Containerfile` exists, but CI does not build
  it. A CUDA PyTorch image is about 8 GB, so the GitHub runner needs disk cleanup. The quay
  repository may need an org admin to create it or make it public.
- **Blackwell GPUs.** OpenSpline needs CUDA 12.8 there. The install pins `--torch-backend cu126`.
  Make it a chart value if the cluster gets B200s or RTX 50s.
- **Remaining latency item 7.** LiveKit media over UDP through a LoadBalancer instead of TURN
  over TLS. It needs cluster-admin, so it is not possible on TMM.
- **Higher resolution than 512x512.** Only possible with a super-resolution or
  face-restoration pass per frame inside OpenSpline's render loop. That means a fork, and a
  risk of flicker. Consider it only if better portraits and high quality are not enough.
- **Open hardening from the security review of PR #10** (not chosen at the time):
  - audit flood on 429;
  - rate limiter burst;
  - NetworkPolicy;
  - server-side logout;
  - an OpenShift login for the portal;
  - a voice-path test.
- **The cluster-admin path with OpenSpline.** It is untested. There OpenSpline gets one
  time-sliced share of a GPU next to the models, and setup warns about it.
- **Personal Gmail Drive.** A Google service account probably cannot create Docs in a
  personal Gmail Drive folder (no storage quota for service accounts), while the folder
  check passes. It was seen on an earlier sandbox and is not fixed.

## Moving to another laptop
No copying is needed: clone, `oc login`, `oc project user-<name>`, `scripts/setup.sh`. Setup
rebuilds `~/secrets.env`, the avoided nodes and `docs/handoff/private/environment.md` from the
cluster. It worked on a second Mac on 2026-10-25 (see
[setup-and-deploy.md](setup-and-deploy.md#another-laptop)). Mohamed's hand-written private
notes, with the accounts and the history of the nodes, are only in
`~/.assistant-setup/handoff-private-environment.md` on the first Mac.

## Not to forget
- Every push to `main` needs Mohamed's explicit "yes, push <sha>" (see
  [working-with-mohamed.md](working-with-mohamed.md)).
- A push that touches `services/` or `frontend/` builds images. The `promote` job then
  commits `ci: deploy images sha-<7> to the demo clusters` to `main`, so rebase on it before
  the next push.
- The Tavus key is still in the cluster secret. Tavus remains a choice in setup step 2
  (`AVATAR_PROVIDER=tavus`), with the four Phoenix-4.5 faces in `chart/values-demo-cluster.yaml`.
