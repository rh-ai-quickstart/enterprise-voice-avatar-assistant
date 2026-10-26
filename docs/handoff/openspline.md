# OpenSpline, the self-hosted avatar

[OpenSpline](https://github.com/openspline/openspline) (MIT; about a month old in 2026-10)
animates a portrait to speech on an NVIDIA GPU, using the SoulX-FlashHead models. It joins
the LiveKit room itself as a participant that publishes on the agent's behalf. It sits next
to Tavus, and setup step 2 chooses between them. Mohamed wanted both kept, with self-hosting
preferred.

## How it is wired

| Piece | Where |
|---|---|
| Voice agent provider | `services/voice-agent/app/avatars.py` (`build`, `openspline_portrait`) |
| Voice agent settings | `services/voice-agent/app/config.py`: `OPENSPLINE_URL`, `OPENSPLINE_QUALITY`, `OPENSPLINE_PORTRAIT` (fallback), `AVATAR_PORTRAIT_DIR` |
| SDK | `openspline==0.1.1` in `services/voice-agent/pyproject.toml`: `openspline.integrations.livekit.AvatarSession` |
| GPU worker | `chart/templates/openspline.yaml`: Deployment, Service, PVC `openspline-data`, ConfigMaps `openspline-workers` and `avatar-portraits` |
| Values | `openspline.*` in `chart/values.yaml` |
| Faces and posters | RAG API `services/rag-api/app/faces.py` (`portrait_path`, `catalog`, `poster`) |
| Integrations test | RAG API `services/rag-api/app/integrations.py` (`_test_openspline`: `/readyz`) |
| Preflight | `scripts/demo-preflight.sh`: worker loaded and free |
| Setup | step 2 (choice, GPUs, quality), step 5 (no key), step 6 (`AVATAR_PROVIDER` to deploy.sh) |
| Optional prebuilt image | `services/avatar-openspline/Containerfile` (`openspline.image`). CI does not build it |

**A voice session, step by step:**
1. The browser's face choice reaches the voice agent as the participant attribute
   `avatar_face`.
2. The agent picks `/portraits/<face>.jpg` (ConfigMap `avatar-portraits`), falling back to
   the bundled `default.png`.
3. The SDK uploads the portrait to `POST http://openspline:7860/v1/sessions`, which claims a
   worker. Each worker serves one session at a time; when none is free, the answer is 429
   (capacity) and the session runs audio-only.
4. The agent mints a LiveKit token for an `openspline-<hex>` identity (kind agent, attribute
   `lk.publish_on_behalf`) and hands it to the worker (`POST /v1/sessions/{id}/livekit`).
5. The worker joins `ws://livekit:7880`, publishes audio and 512x512 video at 25 fps, and
   receives the agent's speech over a LiveKit data stream (`DataStreamAudioOutput`).
6. At the end of the session the SDK's close deletes the session, which frees the worker.
   That makes End voice then Start work without a refresh. An abandoned session is freed
   after 120 s idle.

## Why it installs itself at first start
In-cluster image builds were impossible on TMM. BuildConfig build pods kept landing on the
broken worker, and every healthy CPU worker was full, even for a 500m request. A BuildConfig
cannot express "not on this node", only a node selector. CI could build an image, but a CUDA
PyTorch image is about 8 GB.

So the chart needs no image of its own. With `openspline.image` empty:
1. **Init container `install`** (stock `registry.access.redhat.com/ubi9/python-311`): the
   steps of OpenSpline's `install.sh`, pinned to commit `openspline.ref` (`6d53966`). It
   builds a uv virtual environment on the volume with hash-locked requirements and PyTorch
   cu126. A marker `installed-<ref>` makes later starts skip it. The uv cache is on the same
   volume, so the environment hard-links and the cache is deleted afterwards. It takes
   about 90 s; the volume holds about 6 GB.
2. **Init container `models`:** `openspline models --quality <q>` downloads SoulX-FlashHead
   (pinned revision) and wav2vec2 from Hugging Face into `/data/models`, with a marker
   `ready-<quality>`. Lite and LTX VAE take 7.8 GB; Pro and Wan VAE 6.5 GB; audio 0.7 GB.
   It took seconds on TMM.
3. **The server:**
   - patches its publish options (next section);
   - runs `openspline serve --config /config/workers.yaml`;
   - loads the model before it listens (about 15 s). The startup probe on `/healthz`
     covers that, and readiness is `/readyz`, which answers 503 until a worker is loaded.

The pod uses the Recreate strategy (one GPU, one RWO volume), the Kueue queue label,
`global.avoidNodes`, a memory-backed `/dev/shm` (2 Gi, 8 Gi for high quality), and the
restricted SCC.

## The patch to OpenSpline's LiveKit sink
`openspline_server/livekit.py` publishes its tracks without options:
- **No track source:** LiveKit reports it as "unknown". The browser's `useVoiceAssistant`
  only takes camera and microphone tracks, so the video stayed in the room unused. The
  frontend falls back to unknown-source tracks of the avatar worker (`useAvatarTracks` in
  `frontend/src/components/VoicePanel.tsx`).
- **LiveKit's default encoding:** simulcast with **300 kbps** at 512x512 and 225 kbps at
  360x360. Busy backgrounds (Emily: bookshelves, guitars, curls) came out as a smear.
- **The fix went too far once.** `b5dd698` sent one 2 Mbps stream with simulcast off. Over
  TURN on TCP, the lips then lagged the voice (2026-10-26, likely cause; see
  [status-and-todo.md](status-and-todo.md#avatar-slow-and-out-of-sync-2026-10-26)). Now:
  1 Mbps with simulcast on, both chart values.

The server container's start command runs a short Python snippet before `serve`. It edits
the installed `livekit.py` in place:
- `TrackPublishOptions(source=SOURCE_MICROPHONE)` for audio;
- `TrackPublishOptions(source=SOURCE_CAMERA, simulcast=$OPENSPLINE_VIDEO_SIMULCAST, video_encoding=VideoEncoding(max_bitrate=$OPENSPLINE_VIDEO_BITRATE, max_framerate=25))`
  for video. The values come from `openspline.simulcast` (on) and `openspline.videoBitrate`
  (1 Mbps), read from the environment when the worker publishes, so changing them needs no new
  patch.

It prints "publish options patched", or "already patched" on later starts. The patch is
written into the install on the volume, so it outlives the chart that wrote it: a file patched
by `b5dd698`, with simulcast fixed off, is rewritten and the log says "publish options
updated". If the call
changes upstream it prints a warning and leaves the defaults. It works for the volume
install and for a prebuilt image alike. Bumping `openspline.ref` means checking that the
patch still matches; the log says whether it did. Ideally upstream fixes both and the
patch goes away (to do, after asking Mohamed).

## The patch for each answer's pose
OpenSpline keeps the face's motion from one answer to the next. Even its `interrupt()` keeps it,
on purpose ("resetting to the portrait here causes a visible jump"). A new answer then started
from a face caught mid-word, and the lips trailed the voice, worst after an interruption
(Mohamed, 2026-10-26: voice first, lips late, mostly after interruptions, with headphones).

A second snippet in the start command adds `Session._reset_pose()` to `session.py`. It calls the
worker's `prepare` with the portrait, as `Session.start()` does, clears the audio history and
shows the portrait. When an answer ends (`turn_end`) or is interrupted, a reset becomes due; the
session's one inference loop runs it when it is next idle, so it never overlaps a GPU call and
the next answer does not wait for it. If that answer's first audio comes before the loop was idle,
the reset runs then. A failure is logged (`[pose] reset failed`) and the pose kept.
`openspline.resetPoseEachAnswer` (`OPENSPLINE_RESET_POSE_EACH_ANSWER`) switches it, and each reset
logs `[pose] reset to the portrait in <seconds>`; `scripts/voice-timings.sh` lists them.

The cost: OpenSpline's benchmark in the pod measured `prepare_seconds` 0.19 (cold, the first after
loading) and a first chunk of 0.37 s after it, against 0.20 s for the others. Done at the start of
each answer that would have added 0.2 to 0.35 s; after the answer it costs nothing on the voice's
path. The face goes back to rest after each answer, which Mohamed asked for.

**Between answers the face goes still.** OpenSpline's idle feeds near-silence (amplitude 1e-5) to
the model, which renders a calm, almost motionless face. Mohamed wants it never to look frozen.
That needs idle motion the model does not produce from silence (blinks, small head movements):
real presenter footage gives it for free (see [avatar-research.md](avatar-research.md)); to do.

## Faces
- **The list:** `openspline.faces` in `chart/values.yaml`: Meera, Lisa and Emily (female),
  Steve (male). Two to four faces, and the first is the default. The chart's `configmap.yaml`
  sends `AVATAR_FACES` from `openspline.faces` when the provider is openspline, else from
  `voiceAgent.faces` (Tavus).
- **The portraits:** `chart/files/portraits/<id>.jpg`. The chart puts them in the ConfigMap
  `avatar-portraits`, keeping them under about 700 KB (the 1 MiB limit includes base64), and
  mounts it at `/portraits` in the voice agent and the RAG API. It refuses to render when a
  face has no file, or when an id is not plain letters, digits, `-` or `_`.
- **Who uses them:**
  - the RAG API offers only faces whose portrait exists, and serves a 192x192 poster made
    from the portrait;
  - the voice agent uploads the portrait, and the voice follows the face's gender;
  - both only look up plain names, because the id comes from the browser.
- **The current pictures:** frames of OpenSpline's README GIFs (`media/examples/*.gif`, MIT),
  chosen with the mouth closed, cropped to head and shoulders, enlarged from 240 to 512, and
  lightly sharpened. They are soft: replacing them is to-do 2 in
  [status-and-todo.md](status-and-todo.md). OpenSpline's dashboard has the originals, but they
  are not under the repository's license. Ask before using them.
- **The fallback:** `services/voice-agent/app/portraits/default.png` is SoulX-FlashHead's
  `examples/girl.png` (Apache-2.0).

## Quality, GPUs and workers
- **`openspline.quality`:**
  - `low`: the Lite model with the LTX VAE, one GPU per session;
  - `high`: the Pro model with the Wan VAE, finer detail, two GPUs per session (sequence
    parallel through xfuser, which is in the install).

  Both render 512x512; OpenSpline fixes that size.
- **The workers:** the GPU count in `openspline.resources` makes them: one per GPU (low), one
  per pair (high), written to `workers.yaml`. A changed layout restarts the pod (a checksum
  annotation). The chart refuses high with fewer than two GPUs.
- **Setup's choice** (step 2, `AVATAR_QUALITY=auto`):
  - high when a queue has two whole GPUs free next to the models and Kokoro still gets its
    GPU slice;
  - low on one whole GPU otherwise.

  Kokoro on CPU costs seconds per sentence, more than the detail is worth. High also gets
  16 Gi requested and 64 Gi limit of memory for its two processes.

**Measured on one H200** (engine only: `openspline benchmark`, excludes transport and the
browser):

| Quality | Real-time factor | Per audio block | First block | GPU memory |
|---|---|---|---|---|
| low | 5.2x (129 fps) | 0.18 s per 0.96 s | 0.35 s | 6 GB |
| high | 1.07x (26.9 fps) | 1.03 s per 1.12 s | 1.26 s | 8 GB |

OpenSpline's README reports, on an A100: 64 fps low, 8 fps high. The GPU pair for high is
**not measured yet** (to-do 3). A MIG 3g.71gb slice for low is not tested either; at about
3/7 of the compute, it would land around 2x real time.

## Benchmark commands
Inside the running pod. It loads a second copy of the model next to the live worker; an H200
has the room.
```bash
P=$(oc get pods -l app.kubernetes.io/name=openspline -o name | head -1); P=${P#pod/}
oc cp chart/files/portraits/meera.jpg $P:/tmp/meera.jpg -c openspline
oc exec $P -c openspline -- env HOME=/tmp OPENSPLINE_MODEL_DIR=/data/models/avatar OPENSPLINE_AUDIO_MODEL_DIR=/data/models/audio OPENSPLINE_RUNTIME_DIR=/tmp/bench /data/openspline/venv/bin/openspline benchmark --portrait /tmp/meera.jpg --quality low --devices 0 --seconds 20
```
- **High on a pair:** `--quality high --devices 0 1`. The pod must have two GPUs: run setup
  step 2 and step 6 with two booked.
- **Download the weights for a quality** first:
  ```bash
  oc exec $P -c openspline -- bash -c 'HOME=/tmp HF_HOME=/data/.hf /data/openspline/venv/bin/openspline models --directory /data/models --quality high && touch /data/models/ready-high'
  ```
  The high weights are already on the volume.

## Limits and ideas
- **Resolution.** 512x512 is the model's size. A sharper look needs better portraits first,
  then high quality. Anything above 512 needs a super-resolution or face-restoration pass per
  frame inside OpenSpline's render loop: a fork, and a risk of flicker.
- **One session per worker.** More GPUs mean more workers; setup could ask for them
  (`AVATAR_SESSIONS`, to do).
- **The SDK pins** livekit-agents 1.8.3; the voice agent runs 1.8.9 (since Beyond Presence; 1.8.0
  before), and the data stream protocol matches.
- **Licenses:**
  - OpenSpline: MIT;
  - the SoulX-FlashHead weights: "other" (see the model card on Hugging Face);
  - the LTX VAE: Open-RAIL-M;
  - wav2vec2: Apache-2.0.
- **CUDA.** `--torch-backend cu126` suits Hopper and older GPUs; Blackwell needs cu128.
- **The log line** "Flash Attention library flash_attn not found, using pytorch attention" is
  expected. Adding flash_attn means compiling it against the hash-locked requirements, for a
  small gain.
