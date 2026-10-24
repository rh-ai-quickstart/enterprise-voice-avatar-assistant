"""Configuration. Every setting is an environment variable; names match the Helm chart's config map and secrets."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LiveKit server (the worker registers here and joins rooms as they are created)
    livekit_url: str = "ws://localhost:7880"
    livekit_api_key: str = "devkey"
    livekit_api_secret: str = "secret"
    # Public wss:// URL of the LiveKit Route; cloud avatar providers join the room through it
    livekit_public_url: str | None = None
    # The worker's HTTP port, used for the health probe
    agent_port: int = 8081

    # RAG API: every user turn goes through it so voice shares grounding, memory, and guardrails with text
    rag_api_url: str = "http://rag-api:8080"
    rag_timeout_seconds: float = 90.0
    # How often the agent asks the RAG API for outcome notices (ticket decisions) to speak
    notification_poll_seconds: float = 3.0
    # Speak while the RAG API is still generating (POST /v1/chat/stream). Off when an output
    # guardrail is configured, since the verdict only exists once the answer is complete.
    rag_stream: bool = True
    guardrails_provider: str = "none"

    # Speech to text (OpenAI-compatible transcription endpoint, Whisper on vLLM)
    stt_base_url: str = "http://localhost:8000/v1"
    stt_model: str = "whisper-large-v3-turbo"
    stt_api_key: str = "none"
    stt_language: str = "en"

    # Text to speech: any OpenAI-compatible speech endpoint (Kokoro in the chart)
    tts_base_url: str = "http://tts:8880/v1"
    tts_model: str = "kokoro"
    tts_voice: str = "af_heart"
    # Voices chosen for avatar faces by gender (see faces.py); a face can pin its own voice
    tts_voice_female: str = "af_bella"
    tts_voice_male: str = "am_michael"
    tts_api_key: str = "none"
    tts_speed: float = 1.0
    # Audio format asked from the speech endpoint: raw PCM needs no encoding there and no
    # decoding here (Kokoro and OpenAI both offer it); mp3, wav, opus, aac or flac otherwise
    tts_response_format: str = "pcm"

    # Direct LLM, used only if the RAG API is unavailable
    llm_base_url: str = "http://localhost:8080/v1"
    llm_model: str = "llama-3.1-8b-instruct"
    llm_api_key: str = "none"

    # Avatar: none | openspline | simli | tavus | hedra
    avatar_provider: str = "none"
    # Give up on the avatar and answer audio-only if the provider has not joined within this time
    avatar_start_timeout_seconds: float = 25.0
    # With an avatar, words transcribed while it speaks that interrupt it: its stream cannot pause
    # and resume, so a sound alone must not stop it
    avatar_interruption_min_words: int = 1
    simli_api_key: str | None = None
    simli_face_id: str | None = None
    tavus_api_key: str | None = None
    tavus_face_id: str | None = None  # stock or custom face, e.g. r3f4182ef554
    # Faces the browser can choose from, JSON list of {id, name, gender|voice}; see faces.py
    avatar_faces: str = "[]"
    tavus_pal_id: str | None = None  # optional; the plugin's stock PAL is used when empty
    # Older Tavus names (replica -> face, persona -> PAL); still accepted
    tavus_replica_id: str | None = None
    tavus_persona_id: str | None = None
    hedra_api_key: str | None = None
    hedra_avatar_image: str | None = None
    # OpenSpline, the self-hosted avatar (the chart's openspline worker on a GPU): no key, it joins
    # the room through the in-cluster LiveKit address. low is one GPU; high needs a GPU pair.
    openspline_url: str = "http://openspline:7860"
    openspline_quality: str = "low"
    openspline_portrait: str = str(Path(__file__).parent / "portraits" / "default.png")
    # The faces' portraits, <face id>.jpg (the chart mounts its ConfigMap avatar-portraits here);
    # a face without one falls back to openspline_portrait
    avatar_portrait_dir: str = "/portraits"

    # Extra CA bundle trusted for in-cluster endpoints served over TLS (OpenShift service CA)
    service_ca_file: str | None = None

    greeting: str = (
        "Hello, I am the company assistant. Ask me about a policy, a procedure, or a request "
        "and I will answer from our documents."
    )
    # Spoken instead of `greeting` when the person gave their name; {name} is their first name
    greeting_named: str = (
        "Hi {name}, welcome. I am the company assistant. Ask me about a policy, a procedure, "
        "or a request and I will answer from our documents."
    )
    instructions: str = (
        "You are a helpful voice assistant for employees. Keep answers short and natural to listen to."
    )
    # Seconds of silence after which the VAD reports the end of speech
    vad_min_silence_seconds: float = 0.3
    # Shortest sound the VAD counts as speech: clicks, coughs and short noises below it start no turn
    vad_min_speech_seconds: float = 0.25
    # End-of-turn model on the audio (LiveKit's turn-detector-v1-mini, runs inside the worker):
    # with it the short silences above do not cut people off mid-sentence. "none" switches it off.
    turn_detector: str = "v1-mini"
    # Seconds of silence before a user turn is considered finished
    min_endpointing_delay: float = 0.3
    # Start the answer on the interim transcript before the turn is final (saves a few hundred ms)
    preemptive_generation: bool = True
    log_level: str = "INFO"


settings = Settings()
