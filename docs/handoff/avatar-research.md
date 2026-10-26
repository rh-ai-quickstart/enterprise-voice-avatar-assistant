# Avatar research: beyond OpenSpline

Why this exists: on 2026-10-26 Mohamed asked for avatars that look professional for office and
customer-support use, at a much higher quality and resolution than OpenSpline, with tight lip
sync. OpenSpline cannot get there: its engine, SoulX-FlashHead, renders 512x512 and nothing
larger (see [openspline.md](openspline.md)). This page holds what the search found, what was
decided, and the plan for the self-hosted track.

## Decision (2026-10-26)
- **The UI offers several avatars.** It ships with OpenSpline and Tavus; the person picks one
  above its faces before each voice session (`voiceAgent.avatarProviders`, see
  [architecture.md](architecture.md#avatar-providers)).
- **The third avatar is a self-hosted research track**, not a hosted vendor. Mohamed chose it
  over Beyond Presence, Anam and bitHuman. No new account and no new key, but no
  demo-ready result soon either.
- **Later the same day, Mohamed asked for Beyond Presence too.** It is now offered in the UI
  next to OpenSpline and Tavus (avatar provider `bey`, faces in `voiceAgent.beyFaces`), while the
  self-hosted track stays the long-term plan.

## What the search found (late October 2026)
Every latency, resolution and quality figure below is the vendor's or the paper's own; none was
measured here.

**No open model renders a full 720p or 1080p frame in real time on one H200.** The generative
models that go above 512 need two to eight GPUs per session:

| Model | Licence | Resolution | Real time, on what |
|---|---|---|---|
| SoulX-FlashHead (OpenSpline's engine) | code Apache-2.0, weights partly Open-RAIL-M | 512x512 | Lite 5.2x on one H200 (measured here), Pro 1.07x |
| SoulX-LiveAct 18B (March 2026, same lab) | Apache-2.0 on the model card; none in the repo README | 720x416, half body | 20 fps on two H100/H200 with FP8; latency not published |
| AvatarForcing 1.3B | Apache-2.0 (README) | 832x480 | 34 ms per frame, GPU not stated |
| Alibaba LiveAvatar 14B | Apache-2.0 | 720x400 | 45 fps on five H800; first frame 1.2 s |
| SoulX-FlashTalk 14B | Apache-2.0 | not stated | 32 fps on eight H800 |

**The realistic self-hosted route to a professional HD look regenerates only the mouth or face
over real HD footage** of a presenter (paste-back), so the rest of the frame stays real 1080p
video:
- **MuseTalk 1.5** (MIT): a 256x256 mouth region, 30+ fps on a V100. Mature; the mouth stays
  softer than the frame.
- **Lip Forcing** (Apache-2.0, July 2026): a 512x512 face region; the 14B model fits in about
  37 GB on one H200, frame rate not published. A faster 1.3B model is announced, not released.

**Ruled out:** EchoMimic v3, JoyVASA, Hallo2/3 (not real time); Hallo-Live and TaoMate (driven
by text, so they cannot follow our TTS); Avatar-Forever (LTX-2 community licence with a revenue
cap); Ditto (bundles InsightFace detectors, whose commercial terms need checking first).

**Hosted, for comparison:**
- **Tavus Phoenix-4.5** (2026-09-10): a full half-body frame in one pass, 1080p on every tier,
  100+ stock faces, $0.26 to $0.35 a minute. Our integration already uses it.
- **Beyond Presence** (Genesis 2.0): 1080p at 35 fps, about 100 ms. It animates our own Kokoro
  audio (speech-to-video), €0.09 to €0.18 a minute, EU company with SOC 2 and GDPR. The only
  vendor advertising on-premises deployment, though its FAQ calls the offer cloud-based (ask
  sales). Nine stock avatars. Plugin `livekit-plugins-bey` 1.8.9, which needs
  `livekit-agents>=1.8.9`; the voice agent moved from 1.8.0 to 1.8.9 for it (the OpenSpline client
  0.1.1 does not pin livekit-agents).
- **Anam** (1152x768, about 150 to 180 ms, $0.11 to $0.16) and **HeyGen LiveAvatar** (720p,
  1080p on request) are the other serious ones.
- **Gone or changed:** Hedra was removed from LiveKit Agents on 2026-09-02 (our `avatars.py`
  still lists it); bitHuman retired its self-hosted GPU container on 2026-09-25 and refuses
  free keys since 2026-10-19; Soul Machines has been in receivership since February 2026.

LiveKit's avatar docs list 16 providers, all on `livekit-agents[x]~=1.8` (1.8.9 was the latest,
on 2026-10-24). They also describe custom avatar workers built on `AvatarRunner`, which is the
hook for a self-hosted worker of our own.

## The self-hosted track: plan
1. **Footage first.** Paste-back needs a few minutes of consented 1080p video of a presenter
   in office dress, looking at the camera, with a calm background, and proof that we may use
   it. Without it, only the generative models above remain. Ask Mohamed.
2. **GPUs.** Today's booking (one whole H200, two MIG 3g.71gb slices) is fully used by
   OpenSpline, Whisper and Kokoro. A research worker needs another whole H200 (two for
   LiveAct). Booking is Mohamed's call.
3. **Benchmark before building.** In a throwaway pod on the booked GPU, as was done for
   OpenSpline (see [openspline.md](openspline.md#benchmark-commands)), measure each candidate
   on our audio: real-time factor, time to the first video block, GPU memory, and the
   resolution it holds. Targets: a real-time factor of 1.8 or more, a first block under
   0.6 s, and lips that visibly match. Order: Lip Forcing 14B and MuseTalk 1.5 over the
   footage, then SoulX-LiveAct on a GPU pair, then AvatarForcing.
4. **Then a worker.** The winner gets a Deployment like `chart/templates/openspline.yaml`
   (installed at start onto a volume, since in-cluster builds fail on TMM), a provider entry in
   `services/voice-agent/app/avatars.py` (the audio reaches it the way it reaches OpenSpline),
   its faces in `services/rag-api/app/faces.py`, its name in the chart's `validate.yaml`, and a
   choice in setup step 2. The UI switch needs nothing more: it shows every provider the RAG
   API offers.
5. **Licences.** Check each model's weights and every detector or face-parsing model the
   pipeline downloads (InsightFace, for example) for commercial use.

## Sources
- LiveKit avatars: https://docs.livekit.io/agents/models/avatar/
- SoulX: https://github.com/Soul-AILab/SoulX-FlashHead, https://github.com/Soul-AILab/SoulX-LiveAct,
  https://github.com/Soul-AILab/SoulX-FlashTalk
- https://github.com/KlingAIResearch/AvatarForcing, https://github.com/Alibaba-Quark/LiveAvatar
- https://github.com/cvlab-kaist/LipForcing, https://github.com/TMElyralab/MuseTalk
- https://github.com/antgroup/ditto-talkinghead, https://github.com/leeruibin/avatarforever
- https://github.com/NVIDIA/Audio2Face-3D, https://github.com/NVIDIA-AI-Blueprints/digital-human
- https://www.tavus.io/blog/phoenix-4-5, https://www.beyondpresence.ai/enterprise,
  https://anam.ai/pricing, https://help.heygen.com/en/articles/12758866-liveavatar-faq
- https://docs.bithuman.ai/models
