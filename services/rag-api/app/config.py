"""Configuration. Every setting is an environment variable; names match the Helm chart's config map and secrets."""

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_SYSTEM_PROMPT = """You are {assistant_name}, an enterprise assistant that answers questions using the company documents provided as context.

Rules:
- Answer only from the context. If the context does not contain the answer, say that you could not find it in the company documents and suggest contacting the IT service desk.
- Cite the context passages you used with their number in square brackets, for example [1] or [2][3]. Every factual statement needs a citation.
- Be precise and concise. Do not invent policies, numbers, dates, or names.
- If the user asks for something unrelated to the company documents, briefly say what you can help with instead."""

VOICE_STYLE = """
This answer will be spoken aloud by a voice assistant. Use two or three short sentences, plain words, no markdown, no lists, no URLs. Keep the citation numbers in square brackets."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    assistant_name: str = "the Example Corp assistant"
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    blocked_message: str = (
        "I can't help with that request. If you think this is a mistake, please contact the IT service desk."
    )

    # Models (OpenAI-compatible base URLs including /v1)
    llm_base_url: str = "http://localhost:8080/v1"
    llm_model: str = "llama-3.1-8b-instruct"
    llm_api_key: str = "none"
    llm_temperature: float = 0.2
    answer_max_tokens: int = 700
    # Spoken answers stay short: two or three sentences (see VOICE_STYLE); halves generation time on a small GPU
    voice_max_tokens: int = 120

    embeddings_base_url: str = "http://localhost:8081/v1"
    embeddings_model: str = "bge-m3"
    embeddings_api_key: str = "none"

    # none | granite-guardian | llama-guard | trustyai
    guardrails_provider: str = "none"
    guardrails_base_url: str = ""
    guardrails_model: str = "granite-guardian-3.3-8b"
    guardrails_api_key: str = "none"
    guardrails_detector: str = "granite_guardian"
    guardrails_fail_open: bool = True

    service_ca_file: str | None = None

    # Retrieval
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "documents"
    rag_top_k: int = 5
    rag_min_score: float = 0.3
    max_context_chars: int = 12000
    history_turns: int = 8
    # Detect service requests in chat/voice and file tickets from the conversation
    request_intent_detection: bool = True
    # Every request filed from a conversation waits for a decision in Slack. Off: the classifier
    # decides per request (needs_approval) and the rest is approved by the workflow at once.
    requests_require_approval: bool = True
    # Follow-ups with at most this many words are retrieved together with the previous question
    followup_max_words: int = 6
    snippet_chars: int = 300

    # Memory, tickets, documents
    database_url: str | None = None

    # Voice
    livekit_url: str = "ws://livekit:7880"
    livekit_public_url: str = ""
    livekit_api_key: str = "devkey"
    livekit_api_secret: str = "secret"
    voice_token_ttl_seconds: int = 3600
    # Avatar faces offered in the UI (see faces.py); the voice agent applies the same catalog
    avatar_provider: str = "none"
    avatar_faces: str = "[]"
    tavus_face_id: str | None = None
    tavus_api_key: str | None = None
    tts_voice: str = "af_heart"
    tts_voice_female: str = "af_bella"
    tts_voice_male: str = "am_michael"

    # Integrations the deployment turned on (integrations.*.enabled in the chart). Off, the portal
    # is the only surface for approvals and notices; on with a key missing, it reports misconfigured.
    slack_enabled: bool = False
    google_docs_enabled: bool = False

    # Admin portal (docs/admin-portal.md): one shared password, a display name per sign-in
    admin_enabled: bool = True
    admin_password: str = ""
    admin_session_secret: str = ""
    admin_session_hours: int = 8
    # Browsers treat http://localhost as secure, so this only needs turning off for plain-HTTP hosts
    admin_cookie_secure: bool = True
    # Bearer token for every route that is not public (n8n, the ingestion service, scripts).
    # Empty disables the check, for local development only; the chart always sets one.
    internal_api_token: str = ""

    # Neighbours
    ingestion_url: str = "http://ingestion:8080"
    n8n_url: str = "http://n8n:5678"
    n8n_request_webhook_path: str = "/webhook/request-intake"
    n8n_archive_webhook_path: str = "/webhook/archive-transcript"
    # Transcript archival to Google Docs with a service account (see gdocs.py); both empty = off
    google_service_account_json: str | None = None
    google_docs_folder_id: str | None = None

    # SLA escalation
    sla_reminder_minutes: int = 60
    sla_escalation_minutes: int = 240

    # Knowledge gap detection
    gap_score_threshold: float = 0.45

    cors_origins: str = "*"
    log_level: str = "INFO"


settings = Settings()
