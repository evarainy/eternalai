import json
import logging
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    app_name: str = "eternalai-worker"
    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql://eternalai:eternalai@postgres:5432/eternalai"
    redis_url: str = "redis://redis:6379/0"
    worker_queue: str = "bootstrap"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> WorkerSettings:
    return WorkerSettings()


def bootstrap_worker() -> dict[str, str]:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper())
    logging.getLogger("eternalai.worker").info("Worker bootstrap initialized")
    return {
        "status": "ok",
        "service": "worker",
        "queue": settings.worker_queue,
    }


def main() -> None:
    state = bootstrap_worker()
    print(json.dumps(state))


if __name__ == "__main__":
    main()
