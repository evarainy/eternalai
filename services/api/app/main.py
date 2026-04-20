from functools import lru_cache

import uvicorn
from fastapi import FastAPI
from pydantic_settings import BaseSettings, SettingsConfigDict


class APISettings(BaseSettings):
    app_name: str = "eternalai-api"
    app_env: str = "development"
    log_level: str = "INFO"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    database_url: str = "postgresql://eternalai:eternalai@postgres:5432/eternalai"
    redis_url: str = "redis://redis:6379/0"
    llm_provider: str = "replace-with-provider"
    llm_model: str = "replace-with-model-id"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> APISettings:
    return APISettings()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name)

    @app.get("/health", tags=["system"])
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "service": "api",
            "environment": settings.app_env,
        }

    return app


app = create_app()


if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(
        "services.api.app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
    )
