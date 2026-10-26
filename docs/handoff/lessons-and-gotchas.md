# Lessons and gotchas

Everything here cost time at least once. Each entry says what happens and what to do.

## The Mac (zsh, bash 3.2)
- **`read -p` in zsh means coprocess** ("no coprocess"). Use `read -rs "VAR?prompt: "`.
- **oh-my-zsh url-quote-magic.** A `;` pasted right after a URL becomes `\;`, even inside
  quotes, which leaves a `for>` prompt. Put a space after every URL, end the line with it, or
  follow it with ` &&`.
- **zsh does not split `$var` into words.** `for x in $list` sees one item; use an array or
  `bash -c`.
- **macOS ships bash 3.2:** no associative arrays and no `${var@Q}`. setup.sh re-executes
  itself under Homebrew's bash 5. Other scripts that need bash 4 are run by setup through
  that bash.
- **A Mac shell may point at another cluster.** Every command block starts with
  `export KUBECONFIG=~/.kube/tmm`. A login in a tab without it (the console's copied
  `oc login`, for example) writes `~/.kube/config` instead, and `~/.kube/tmm` keeps the
  expired token. Log in again with the export, or copy the file over.
- **BSD `sed -i` needs an argument** (`sed -i '' ...`); GNU sed does not. Prefer Python for
  in-place edits in scripts that run on both.
- **A new Mac has no `uv`.** The Python checks in CLAUDE.md need it: `brew install uv`.
- **macOS's `openssl` is LibreSSL.** Setup wants OpenSSL, so step 1 installs `openssl@3` with
  Homebrew on a new Mac. That is expected.

## OpenShift and Kubernetes
- **A node with broken container storage looks empty, so the scheduler favours it.**
  Symptom: `FailedCreatePodSandBox ... creating overlay mount ... input/output error` over
  and over. `deploy.sh` and `test-services.sh` detect it and add a `NotIn` affinity; the
  chart renders `global.avoidNodes` into every pod. Pods that bypass the chart need the same
  care:
  - BuildConfig build pods only take a node selector, which cannot express "not this node";
  - one-off test pods;
  - the ACME solver pod.
- **Shared clusters can be full.** On TMM every healthy CPU worker refused a 500m build pod
  ("Insufficient cpu"). Do not plan on in-cluster builds there; GPU pods run on the GPU
  nodes, with the booked CPU.
- **Kueue.** Pods without `kueue.x-k8s.io/queue-name` go to queue `default`, which has no
  GPUs. A GPU pod waits in `SchedulingGated` or Pending with a reason in
  `oc get workloads`. When the booking ends, the GPU pods are evicted.
- **"Booked until" is the end of the day's slot, not of the booking.** Setup prints the
  ClusterQueue's `rhai-tmm.dev/until` label, which moves forward each day while the booking
  lasts (seen on 2026-10-26: it moved a day and the GPU pods kept running). Do not tell
  Mohamed his booking ends at that time; check the queue again after it passes, or look at
  the console's GPU Booking page.
- **A new kubeconfig has no current project after `oc login` on TMM** ("no project has been
  set"). On a new laptop, setup then falls back to its default project name and step 1
  stops with "not an admin". Run `oc project user-<name>` once after the first login.
- **IBM VPC block volumes have a 10 GB minimum.** A 5Gi PVC fails to provision.
- **Service-link environment variables break LiveKit** (`LIVEKIT_PORT` and so on). Keep
  `enableServiceLinks: false`.
- **`curl --cacert` replaces the system trust store** instead of adding to it. Concatenate
  the service CA onto the system bundle (as `app/tls.py` and the helm test do); otherwise
  public endpoints like Models-as-a-Service fail TLS.
- **Model endpoints.** RHOAI `LLMInferenceService` serves HTTPS with the service CA.
  InferenceServices in raw mode expose `<name>-predictor:8080`.
- **A recreated pod can land on the bad node again.** Restarting one Deployment by hand
  without the affinity (rag-api, once) put its new pod on the broken node.
- **Login tokens on TMM expire after some hours.** `oc` answers "Unauthorized" mid-task.
  Have Mohamed log in again.

## Helm
- **Helm 4.3 is on Mohamed's Mac.** It refuses to take over a field another manager owns
  with a different value; an `oc scale` to zero is an example. When you apply chart objects
  by hand for a test, add the ownership annotations (`meta.helm.sh/release-name`,
  `meta.helm.sh/release-namespace`), and delete what you changed before the next Helm run.
  Unchanged objects, a PVC for instance, are adopted cleanly.
- **Helm renders large numbers in scientific notation.** `{{ 2000000 | quote }}` gives
  `"2e+06"`. Use `{{ $v | int64 | quote }}`.
- **`.Files.Glob ... | b64enc` works for binary files**, but a ConfigMap tops out at 1 MiB
  including base64, so keep portraits under about 700 KB in all.
- **PVCs survive uninstall** with `helm.sh/resource-policy: keep`. That is wanted for data
  and for the OpenSpline install, but remember it when "starting fresh".
- **Helm waits only for the hook Job**, not the Deployments. `deploy.sh` has its own waits.
- **A patch written into a volume outlives the chart that wrote it.** OpenSpline's publish
  patch edits the install on `openspline-data`, and an upgraded chart finds the old patch
  there. Make such patches recognise their earlier versions, and read tunable values from the
  environment instead of baking them into the file.

## LiveKit Agents (voice agent)
- **Preemptive generation runs `llm_node` before `on_user_turn_completed`**, and
  `StopResponse` does not cancel it. Filter noise in `stt_node`, and again in `llm_node`.
- **Avatars cannot pause** (`DataStreamAudioOutput`), so `resume_false_interruption` is
  ignored and every sound interrupts the answer. With an avatar, gate interruptions on words
  (`min_words`).
- **`useVoiceAssistant` only takes Camera and Microphone tracks.** An avatar that publishes
  with source "unknown" shows nothing. OpenSpline did; the frontend falls back, and the chart
  patches OpenSpline.
- **Reusing a room name breaks Tavus on restart.** Use a new room for every voice start.
- **The per-sentence `FlushSentinel` only flushes the TTS.** The audio output is flushed
  once per answer, in `generation.py`, so the avatar sees one segment.
- **`playback_finished called more times than playback segments were captured`.** The
  avatar answers both the segment end and `clear_buffer` after an interruption. It is
  harmless once interruptions are rare.
- **The voice agent logs each line twice** (plain and JSON), and room teardown prints long
  `remote_session` tracebacks that mean nothing. Filter with `grep -v '^{'`.

## Whisper
- **Whisper hallucinates on noise and silence:** "-", "Thank you.", "I love you, I love you."
  and subtitle-style lines with a leading dash. Treat its output on short audio as
  untrusted. The filter lives in `helpers.is_noise_transcript`.
- **Batch transcription adds 0.5 to 0.8 s** after the end of speech. A streaming STT would
  remove most of it.

## Kokoro
- **PyTorch on CPU starts one thread per node core**, far beyond the container's CPU limit,
  and gets throttled to seconds per sentence. Set `OMP_NUM_THREADS` and `MKL_NUM_THREADS` to
  the limit (`models.tts.threads`).
- **The GPU image runs under the restricted SCC** and needs only a small slice (4 GB is
  enough).

## OpenSpline
- **No container image upstream.** It installs with `install.sh` into a venv. The chart
  repeats those steps into a volume at first start.
- **The server loads the model before it listens.** Use a startup probe on `/healthz` with a
  long budget, and readiness on `/readyz`.
- **One session per worker.** A second parallel session gets 429 and runs audio-only.
- **The LiveKit sink's defaults.** No track source, and 300 kbps simulcast. Both are patched
  at start (see [openspline.md](openspline.md)).
- **Input quality is output quality.** A blurry 240 px portrait gives a blurry avatar,
  whatever the GPU.
- **High quality on one H200 barely keeps up** (1.07x). Do not make it the default on a
  single GPU.
- **Keep simulcast on for the avatar video.** With one stream, the LiveKit server has nothing
  lighter to send a slow viewer. On TMM every browser comes through TURN over TCP, and after
  `b5dd698` (2 Mbps, simulcast off) the lips lagged the voice while OpenSpline itself kept up.
  That is the likely cause; confirm it after the fix is deployed.
- **Ask how the desync looks before patching.** Which comes first (voice or lips), when (every
  answer, drifting, after interruptions), and headphones or speakers. "Voice first, mostly after
  interruptions, with headphones" ruled out echo and the network and pointed at OpenSpline's
  motion carried over from the interrupted answer.
- **When the avatar seems slow, measure each hop before guessing.** `playback_latency` in the
  voice agent log, `denoise per step` in OpenSpline's log, and `"connectionType"` in LiveKit's
  log told apart the GPU, the agent and the network within minutes.

## Process
- **Rebase after every image build.** A push touching `services/` or `frontend/` triggers a
  promote commit on `main`.
- **Check facts before writing them.** Workflow purposes, which PVCs are kept, a library's
  real behaviour: read the code or the source map (for npm packages, the `.js.map` holds the
  TypeScript source). Several fixes here came from reading LiveKit's installed source in
  `.venv/lib/python3.12/site-packages/livekit/agents/`.
- **A screenshot often shows a second problem** (a repeated greeting, a phantom bubble).
  Mention it and fix it too.
- **The repository is public.** Internal hostnames, user names, group names and robot
  accounts go in `docs/handoff/private/`, never in git.
- **Commands with `eval` may be refused** by an agent harness's safety checks. Prefer
  explicit values files or arguments.
