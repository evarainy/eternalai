"""Shared runtime settings used by the bootstrap services."""

from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

RuntimeEnvironment = Literal["development", "test", "staging", "production"]
AuthMode = Literal["disabled", "review_required"]

_ALLOWED_LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}

COMMON_SETTINGS_CONFIG = SettingsConfigDict(
    env_file=".env",
    env_file_encoding="utf-8",
    case_sensitive=False,
    extra="ignore",
)


class SharedRuntimeSettings(BaseSettings):
    """Normalized env policy shared by API, worker, and ASR services."""

    app_env: RuntimeEnvironment = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql://eternalai:eternalai@postgres:5432/eternalai"
    redis_url: str = "redis://redis:6379/0"
    llm_provider: str = "replace-with-provider"
    llm_model: str = "replace-with-model-id"
    openai_api_key: str = ""
    auth_mode: AuthMode = "disabled"

    model_config = COMMON_SETTINGS_CONFIG

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in _ALLOWED_LOG_LEVELS:
            allowed = ", ".join(sorted(_ALLOWED_LOG_LEVELS))
            raise ValueError(f"log_level must be one of: {allowed}")
        return normalized


class APIServiceSettings(SharedRuntimeSettings):
    app_name: str = "eternalai-api"
    api_host: str = "0.0.0.0"
    api_port: int = 8000


class WorkerServiceSettings(SharedRuntimeSettings):
    app_name: str = "eternalai-worker"
    worker_queue: str = "bootstrap"


class ASRServiceSettings(SharedRuntimeSettings):
    app_name: str = "eternalai-asr"
    asr_host: str = "0.0.0.0"
    asr_port: int = 8010
    asr_provider: str = "sensevoice"
