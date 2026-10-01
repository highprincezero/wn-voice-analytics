from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://voice:voice@localhost:5432/voice"
    jwt_secret: str = "dev-only-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24

    llm_provider: str = "mock"
    safety_provider: str = "mock"
    blob_provider: str = "azurite"
    analysis_mode: str = "celery"
    broker: str = "celery"

    azure_storage_connection_string: str = ""
    azure_storage_container: str = "voice"
    # When set, transcription loads the audio with the Azurite MCP fetch_audio tool.
    mcp_audio_url: str = ""

    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_api_version: str = "2025-03-01-preview"
    azure_openai_transcribe_deployment: str = "gpt-4o-transcribe"
    azure_openai_chat_deployment: str = "gpt-5-mini"
    azure_openai_chat_temperature: float | None = None

    azure_content_safety_endpoint: str = ""
    azure_content_safety_key: str = ""
    content_safety_block_severity: int = 4

    service_bus_connection_string: str = ""
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"
    redis_url: str = ""
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 120
    rate_limit_window_seconds: int = 60
    rate_limit_backend: str = "redis"
    rollup_schedule_seconds: int = 900

    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str = ""
    otel_service_name: str = "voice-analytics-api"

    partition_modulus: int = 16
    chunk_chars: int = 4000
    max_chunks: int = 20
    # Group summaries are written in parallel, this many model calls at a time.
    summary_workers: int = 6
    # Upload times in chat replies (to tell same-name recordings apart) use this zone.
    display_timezone: str = "Asia/Manila"
    mock_stage_delay_sec: float = 0.0
    max_upload_bytes: int = 20 * 1024 * 1024
    home_region: str = "local"

    @field_validator("azure_openai_chat_temperature", mode="before")
    @classmethod
    def blank_chat_temperature(cls, value):
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
