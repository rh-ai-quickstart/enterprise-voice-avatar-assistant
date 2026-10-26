# Status and to-do list

As of 2026-10-26 evening (Mohamed's local time is UTC+4). The latest work on `main` is the
avatar switch (several avatars in the UI) with the OpenSpline sync fix, described below, and
Beyond Presence as a third avatar. It
changes the RAG API, the voice agent and the frontend, so CI builds new images and commits
their promotion to `main`; deploying it takes `git pull`, `scripts/setup.sh --step 2` and
`--step 6`. Until then the cluster runs images `sha-571acbf` with the chart of `b5dd698`.

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
| Avatar | OpenSpline, low quality | On one whole H200 (`nvidia.com/gpu: 1`). Faces Meera, Lisa, Steve and Emily. After the next deploy, Tavus is offered next to it in the UI (step 2 answered `openspline,tavus`) |
| Everything else | Default Kueue queue (CPU) | Frontend, RAG API, ingestion, voice agent, LiveKit, n8n, PostgreSQL, Qdrant, the object store (VersityGW) |
| Integrations | On | Slack, Google Docs archival, the admin portal |

The GPU booking is 1 whole H200 plus 2 MIG 3g.71gb slices. It appears as ClusterQueue
`user-<name>` behind LocalQueue `reserved`. Its `rhai-tmm.dev/until` label is the end of the
current daily slot and moves forward each day while the booking lasts. When the booking
ends, Kueue stops the GPU pods: Whisper, Kokoro and OpenSpline. Voice then stops; text chat
keeps working on Models-as-a-Service.

On 2026-10-25 the label was Mon 26 Oct 00:00. On 2026-10-26 at 14:00 it had moved to Tue 27
Oct 00:00, every workload was admitted, and Whisper, Kokoro and OpenSpline had run for 38 to
46 hours without a break: the booking carries on from day to day. Check it with
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

## Avatar slow and out of sync (2026-10-26)

Mohamed reported the avatar very slow, its lips no longer following the voice. What was found,
read-only, on the cluster:
- **The server side keeps up.** The voice agent hands audio to OpenSpline within 0.28 to
  0.39 s, and end of speech to first audio is still 1.5 to 1.8 s. OpenSpline renders each
  0.96 s chunk in about 0.18 s (four denoising steps of 0.03 s), on schedule.
- **What changed:** Helm revision 10 (2026-10-24 23:30 local) deployed `b5dd698`, which sends
  the video as one 2 Mbps stream with simulcast off. The sync was confirmed working at
  revision 8 (23:03), with LiveKit's 300 kbps simulcast.
- **Every browser session reaches LiveKit through TURN** (`"connectionType": "turn"` in the
  LiveKit log), over TLS on TCP 443, since UDP is not possible on TMM. TCP can only queue late
  video, and a single stream gives the server nothing lighter to send.
- **Likely cause, not yet confirmed:** the 2 Mbps single stream outruns the relayed path, so
  the video falls behind the audio. The fix: 1 Mbps with simulcast on
  (`openspline.videoBitrate`, `openspline.simulcast`). The patch on OpenSpline's volume reads
  simulcast from the environment now and rewrites the old patch, which fixed it off.

## Lip sync and phantom turns, after the switch (2026-10-26 evening)

Revision 11 (15:49 local) deployed the switch with the sync fix: OpenSpline's log says
"publish options updated: simulcast from OPENSPLINE_VIDEO_SIMULCAST", and it renders a 0.96 s
chunk in 0.18 s on average (0.26 s at most). Mohamed then reported, with headphones on:
- **Lips late, voice first, mostly after interruptions.** OpenSpline keeps the face's motion from
  the interrupted answer, so the new one starts from a face caught mid-word. The fix:
  `openspline.resetPoseEachAnswer`, which puts the face back in the portrait's pose after each
  answer or interruption, while it is idle (see
  [openspline.md](openspline.md#the-patch-for-each-answers-pose)). The reset takes about 0.2 s,
  off the voice's path.
- **Strong jitter and jerkiness in the first seconds of each voice session** (not of each
  answer). OpenSpline renders a chunk in 0.18 s on average and 0.26 s at most, so the GPU keeps
  up. Likely cause: simulcast, since LiveKit can start a viewer on the small layer over TURN and
  step up, with a key frame per step. Tavus streams HD over the same relay without trouble, so
  the relay may never have been too slow for one 2 Mbps stream. To try: a session with
  simulcast on, then one with `openspline.simulcast=false` at 1 Mbps; keep whichever starts
  smoothly. `chrome://webrtc-internals` shows the video's height and frame rate over the first
  seconds.
- **The face freezes to a calm pose between answers.** It should never look frozen; to do (see
  openspline.md).
- **Phantom "user" turns** ("some concepts.", "Take care."). With headphones they cannot be the
  avatar's echo: they are speech near the microphone (Mohamed continuing after a pause, or people
  nearby), and each one interrupts the answer. They happened with Tavus too.

`scripts/voice-timings.sh` prints all of these timings from the logs in one go.

## Last confirmed deploy

The voice agent ran `sha-5e063c2` after `scripts/setup.sh --step 6` at 23:03 local on
2026-10-24 (Helm revision 8). Every connectivity check passed. The browser was confirmed
working for:
- the OpenSpline video, in sync;
- the four faces in the picker;
- answers no longer cut off by noise.

Revision 10 (23:30) deployed images `sha-571acbf` (square avatar frame; the greeting once in the
chat) and the chart of `b5dd698` (2 Mbps in one stream); see the section above. `193c128`
(quality from the GPUs) is not deployed; it changes nothing with today's booking.

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

### 1. Deploy the avatar switch with Beyond Presence, and confirm the sync fix
- **Wait** for CI to build the images and commit their promotion to `main`.
- **Run:** `git pull`, then `scripts/setup.sh --step 2` (answer `openspline,tavus,bey`), then
  `--step 5` (it asks for the Beyond Presence key, hidden), then `--step 6`, then hard-refresh
  the page. Step 6 restarts the OpenSpline worker; its log
  should say "publish options updated: simulcast from OPENSPLINE_VIDEO_SIMULCAST".
- **Check:**
  - the lips follow the voice again with OpenSpline; if not, read the troubleshooting entry
    on lag (`docs/troubleshooting.md`) and try a lower `openspline.videoBitrate`;
  - the Avatar switch shows OpenSpline and Tavus, each with its faces, and the choice is
    remembered after a reload;
  - a Tavus session works (the free plan has 25 minutes a month and one stream);
  - a Beyond Presence session works with Nelly, Jerome and Laura, and each one's voice fits
    (their genders in `chart/values-demo-cluster.yaml` were guessed from the names); the admin
    portal's Integrations test confirms the avatar ids; end of speech to first audio stays
    within 1 to 2 s; and after "End voice" the session shows as ended in Bey Studio (Beyond
    Presence has no end call; its avatar leaves when the room is deleted, so billing stops);
  - after the move to LiveKit Agents 1.8.9 (for the Beyond Presence plugin): noise still gets no
    answer, words still interrupt the avatar, and end of speech to first audio stays 1 to 2 s;
  - Emily is still sharp at 1 Mbps;
  - the frame is square for OpenSpline and wide for Tavus and Beyond Presence;
  - the greeting appears once, a long answer plays to the end, and "wait, I meant laptops"
    still interrupts.
  - after an interruption the face snaps back to its resting pose and the lips follow the new
    answer; `NS=user-<name> scripts/voice-timings.sh` shows the `[pose]` resets and their cost;
- **If anything cuts off:** read the voice agent log (runbook in
  [setup-and-deploy.md](setup-and-deploy.md)); each session logs `avatar=<provider>`, and
  `scripts/voice-timings.sh` summarises the timings.

### 2. A self-hosted HD avatar (research track)
Mohamed wants professional, high-resolution avatars, self-hosted first. No open model renders
HD in real time on one H200 yet. The plan, the candidates and the sources are in
[avatar-research.md](avatar-research.md). It needs HD presenter footage and another whole H200,
both Mohamed's to provide.

### 3. Sharper portraits (waiting on Mohamed)
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

### 4. Measure high quality on a GPU pair
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

### 5. Smaller follow-ups
- **Hedra is gone from LiveKit Agents** (removed 2026-09-02). Drop it from
  `services/voice-agent/app/avatars.py`, the RAG API's `AVATAR_KEYS` and the chart's
  `validate.yaml`.
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
