"""Configuration. Every setting is an environment variable; names match the Helm chart's config map and secrets."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Object storage (VersityGW in the chart, or any S3)
    s3_endpoint_url: str = "http://localhost:7070"
    s3_access_key: str = "assistant"
    s3_secret_key: str = "assistant-secret"
    s3_region: str = "us-east-1"
    s3_bucket: str = "documents"
    # Buckets created at startup when missing (the chart's objectStore.buckets)
    s3_buckets: str = "documents,inbox,transcripts"
    # Buckets whose object-created events are ingested. Events for other buckets
    # (for example the classification inbox) are acknowledged and ignored.
    ingest_buckets: str = "documents,transcripts"

    # Vector store
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "documents"

    # Embeddings (OpenAI-compatible)
    embeddings_base_url: str = "http://localhost:8080/v1"
    embeddings_model: str = "bge-m3"
    embeddings_api_key: str = "none"
    embed_batch_size: int = 16
    # Extra CA bundle trusted for in-cluster endpoints served over TLS (OpenShift service CA)
    service_ca_file: str | None = None

    # Optional PostgreSQL for the documents and jobs tables. Disabled when unset.
    database_url: str | None = None

    # Pipeline
    chunk_max_tokens: int = 512
    chunk_tokenizer: str = "BAAI/bge-m3"
    ocr_enabled: bool = False
    docling_artifacts_path: str | None = None
    max_concurrent_jobs: int = 2
    log_level: str = "INFO"

    @property
    def bucket_list(self) -> list[str]:
        return [b.strip() for b in self.s3_buckets.split(",") if b.strip()]

    @property
    def ingest_bucket_set(self) -> set[str]:
        return {b.strip() for b in self.ingest_buckets.split(",") if b.strip()}


settings = Settings()
