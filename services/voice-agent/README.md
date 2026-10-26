# Voice agent

A [LiveKit Agents](https://docs.livekit.io/agents/) worker. It registers with
the LiveKit server and joins every new room. For each user turn:

1. Silero VAD detects the end of speech (users can interrupt the assistant).
2. Whisper transcribes the audio through the OpenAI-compatible transcription API.
3. The RAG API answers in voice mode, so voice shares grounding, memory, and
   guardrails with text chat. The room name `session-<id>` maps to the RAG
   session `<id>`, and the participant identity `user-<name>` to the user id.
4. The answer and its citations are published on the room data channel
   (topic `assistant`) for the UI, then spoken through the TTS endpoint.
5. With an avatar provider configured, the provider publishes the audio and a
   lip-synced video track instead of the agent publishing audio directly.

## Configuration

| Variable | Default | Notes |
|---|---|---|
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | dev defaults | LiveKit server |
| `RAG_API_URL` | `http://rag-api:8080` | answers come from `/v1/chat/stream` with `mode=voice`, spoken sentence by sentence while generated (`/v1/chat` when `RAG_STREAM=false` or a guardrail provider is set) |
| `RAG_STREAM`, `GUARDRAILS_PROVIDER` | `true`, `none` | streaming is off when an output guardrail exists, since its verdict needs the whole answer |
| `PREEMPTIVE_GENERATION`, `MIN_ENDPOINTING_DELAY` | `true`, `0.4` | start answering on the interim transcript; silence before a turn ends |
| `STT_BASE_URL`, `STT_MODEL`, `STT_API_KEY`, `STT_LANGUAGE` | Whisper defaults | OpenAI-compatible transcription |
| `TTS_BASE_URL`, `TTS_MODEL`, `TTS_VOICE`, `TTS_SPEED` | Kokoro defaults | |
| `TTS_VOICE_FEMALE`, `TTS_VOICE_MALE` | `af_bella`, `am_michael` | voices picked by the chosen face's gender (`app/faces.py`) |
| `AVATAR_PROVIDER` | `none` | `none`, `openspline`, `simli`, `tavus`, `hedra` |
| `AVATAR_PROVIDERS` | empty | The avatars offered in the UI, comma-separated, the first being the default (empty: `AVATAR_PROVIDER` alone); the browser's choice arrives as the participant attribute `avatar_provider`, and a provider without its key is left out |
| `SIMLI_API_KEY`, `SIMLI_FACE_ID` | unset | Simli avatar |
| `TAVUS_API_KEY`, `TAVUS_FACE_ID`, `TAVUS_PAL_ID` | unset | Tavus avatar (`TAVUS_REPLICA_ID`, `TAVUS_PERSONA_ID` still accepted) |
| `AVATAR_FACES_OPENSPLINE`, `AVATAR_FACES_TAVUS` | empty | Each provider's faces, a JSON list of `{id, name, gender\|voice}`; the browser's choice arrives as the participant attribute `avatar_face` |
| `AVATAR_FACES` | `[]` | The default provider's faces when it has no list of its own |
| `HEDRA_API_KEY`, `HEDRA_AVATAR_IMAGE` | unset | Hedra avatar |
| `SERVICE_CA_FILE` | unset | extra CA for in-cluster TLS endpoints |
| `GREETING`, `GREETING_NAMED` | see `app/config.py` | `GREETING_NAMED` takes `{name}`, the first name from the token's display name |
| `AGENT_PORT` | `8081` | health endpoint of the worker |

## Run locally

```bash
uv sync
uv run python agent.py download-files
uv run python agent.py dev          # against a LiveKit dev server (livekit-server --dev)
uv run pytest
```
