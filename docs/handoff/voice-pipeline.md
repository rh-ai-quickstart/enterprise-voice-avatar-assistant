# Voice pipeline: latency, noise, interruptions

The voice agent is a LiveKit Agents 1.8 worker (`services/voice-agent/agent.py`). This page
records how it got from 7 to 11 seconds per answer down to 1 to 2, the noise problems found
on a real microphone, and the LiveKit behaviour behind each fix.

## Latency

**Before** (2026-10-24 morning, TMM, Kokoro on CPU): 7.3 to 11.4 s from the end of speech to
the first audio. Verified causes, largest first:
1. **Kokoro on CPU: 5.6 to 8.4 s per sentence.** PyTorch started one thread per node core
   (about 80) in an 8-CPU quota, and the container was throttled.
2. **Sentence 1 waited for sentence 2.** The blingfire StreamAdapter only flushed a sentence
   when the next one began.
3. **VAD silence.** Silero waited for 0.55 s of silence before ending speech.
4. **Batch Whisper.** It transcribes only after speech ends.
5. **mp3 from the TTS endpoint.** `openai.TTS` asked for mp3, encoded there and decoded
   here.
6. **Connection setup.** The RAG API opened new connections per call and checked the Qdrant
   collection every time.

**The seven fixes** (`82db5a9`, `c7830f2`; item 7 not done):
1. **Kokoro threads.** `models.tts.threads` (default: the CPU limit) sets `OMP_NUM_THREADS`
   and `MKL_NUM_THREADS`: 7.9 s fell to 0.7 s per sentence.
2. **Kokoro on a GPU slice.** Setup puts it on a leftover slice with
   `kokoro-fastapi-gpu:v0.8.2`: 45 to 115 ms per sentence.
3. **A `FlushSentinel` after each sentence** in `llm_node`, so synthesis starts at once. It
   reaches only the TTS, not the avatar's audio output; one answer is one audio segment.
4. **Raw PCM from Kokoro** (`TTS_RESPONSE_FORMAT=pcm`).
5. **Shorter turn end:**
   - VAD `min_silence` 0.3 s;
   - minimum endpointing delay 0.3 s;
   - the local turn detector `v1-mini` (`livekit.agents.inference.TurnDetector`, prewarmed
     with `init_eot`), so short pauses do not cut people off.
6. **RAG API:**
   - httpx keep-alive 300 s;
   - a psycopg connection pool;
   - the Qdrant collection check cached;
   - the answer stream opened before the intent check finishes (closed again when it turns
     out to be a request);
   - preemptive generation on.
7. **Not done: LiveKit media over UDP.** It needs a LoadBalancer Service, which needs
   cluster-admin.

**After** (TMM, Kokoro on a MIG slice):

| Stage | Time |
|---|---|
| End of speech to first audio | 1.1 to 2.0 s (`latency: ...` and `e2e_latency` in the log) |
| End of turn and transcription | 0.3 to 0.6 s turn end, 0.5 to 0.8 s Whisper |
| RAG API first token (llama-32-3b on MaaS) | 0.45 to 1.3 s |
| Kokoro first audio | 0.06 to 0.13 s |
| OpenSpline playback delay | 0.28 to 0.44 s (about 1 s for the first greeting) |

With Tavus, `playback_latency` reads 0: its data stream counts playback from the first frame
sent, so Tavus's rendering time is not visible in the log.

## Noise and phantom turns
On a real microphone, Whisper turns short noises and silence into text:
- `-`, `Oh.`, `Wow.`, `Thank you.`;
- `I love you, I love you.`, `- Oh, really?`, `I'm sorry, I'm sorry`.

Each one used to become a question: an answer, a chat bubble, and a knowledge gap.

**The defences, in order** (`87ad851`, `072440a`, `5e063c2`):
1. **VAD minimum speech 0.25 s.** Clicks and coughs start no turn.
2. **`Assistant.stt_node` drops noise.** It drops final transcripts that
   `helpers.is_noise_transcript` recognises, so noise never becomes words:
   - no words at all;
   - a lone filler (`NOISE_WORDS`);
   - a stock phrase (`NOISE_PHRASES`: "thank you", "i love you", "oh really", ...);
   - a short subtitle-style line starting with `-` (three words at most);
   - a clause of two or more words repeated word for word.
3. **`on_user_turn_completed` checks again** and raises `StopResponse`. That covers a turn
   assembled from several transcripts.
4. **`llm_node` skips noise.** Preemptive generation starts the answer from the final
   transcript before `on_user_turn_completed` runs. LiveKit 1.8 does not cancel that
   generation when `StopResponse` is raised: `agent_activity.py` just returns. So our
   `llm_node`, which calls the RAG API, ran anyway.

The filter's tests are in `services/voice-agent/tests/test_helpers.py` and
`test_agent_noise.py`. Add any new hallucination seen on the cluster to the tests and to
`NOISE_PHRASES`.

## Interruptions with an avatar
**The symptom:** answers cut off mid-sentence, with OpenSpline and Tavus alike.

**The cause:** every session logged
> resume_false_interruption is enabled but audio output does not support pause, it will be ignored

- **Without an avatar,** LiveKit handles a sound during an answer by pausing the audio, and
  resumes when no words follow (a "false interruption").
- **With an avatar,** the audio goes to it as a data stream (`DataStreamAudioOutput`), which
  cannot pause. So any sound the VAD caught interrupted outright: LiveKit sends the avatar
  `clear_buffer`, and OpenSpline drops the rest of the answer.

**The fix** (`5e063c2`): when an avatar is active, the session is created with
`turn_handling["interruption"] = {"min_words": AVATAR_INTERRUPTION_MIN_WORDS (1), "resume_false_interruption": False}`.
- Only transcribed words interrupt, and noise never becomes words (`stt_node`).
- Barge-in still works, about half a second after the person stops speaking, because
  Whisper is batch.
- The deprecated `allow_interruptions` argument is gone; interruptions are on by default.
- Headphones avoid the problem at its source: the avatar's voice from the speakers no longer
  reaches the microphone.

## Rooms, greetings, faces
- **Rooms.** Every voice start gets a new room, `session-<conversation id>.<random>`
  (`87ad851`). With a fixed name, ending and restarting voice failed with Tavus: the old
  Tavus participant lingered in the room, and Tavus reported
  `exception_encountered_during_conversation_startup`.
- **Greetings.** The greeting is spoken at every voice start. The frontend shows a given
  greeting once per conversation (`571acbf`).
- **Faces.** The face picker sends the face in the token request. The voice agent reads it
  from the participant attribute `avatar_face`, validates it against `AVATAR_FACES`, and picks
  the voice by gender (`TTS_VOICE_FEMALE` `af_bella`, `TTS_VOICE_MALE` `am_michael`).

## The frontend side (`frontend/src/components/VoicePanel.tsx`)
- **`useAvatarTracks`** wraps `useVoiceAssistant`. It also takes the avatar worker's tracks
  with source "unknown": OpenSpline published without a source before the chart's patch.
- **The avatar frame** takes the video's aspect ratio (`--avatar-aspect`) from the track's
  dimensions, square for OpenSpline. Before the video arrives it uses the provider's shape.
  It narrows instead of adding side bars when the 62vh height cap applies.
- **Chat bubbles for voice** come from the agent's data channel `assistant`
  (`assistant.answer` payloads), not from LiveKit transcriptions. That is why noise that
  never reaches the RAG API leaves no bubble.

## LiveKit Agents 1.8 facts worth knowing
- **`Agent.default.stt_node(self, audio, model_settings)`** is the hook for filtering speech
  events. Non-streaming STT is wrapped in a `StreamAdapter` with the VAD there.
- **Preemptive generation** runs `llm_node` before the turn is committed. `StopResponse` in
  `on_user_turn_completed` does not cancel it.
- **`TurnHandlingOptions`** has the keys `endpointing`, `interruption`,
  `preemptive_generation` and `turn_detection`. `InterruptionOptions` takes `min_words`,
  `min_duration` (0.5), `resume_false_interruption` and `false_interruption_timeout` (2.0).
- **`DataStreamAudioOutput`** (avatars) cannot pause. `flush()` closes the audio segment, and
  the avatar answers each segment and each `clear_buffer` with `playback_finished`. A
  mismatch logs "playback_finished called more times than playback segments were captured".
- **`useVoiceAssistant`** (components-react 2.9) picks the agent, then the worker whose
  `lk.publish_on_behalf` equals the agent's identity. It only takes Camera and Microphone
  sources.
