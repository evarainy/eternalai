from functools import lru_cache

import uvicorn
from fastapi import FastAPI
from pydantic_settings import BaseSettings, SettingsConfigDict


class ASRSettings(BaseSettings):
    app_name: str = "eternalai-asr"
    app_env: str = "development"
    log_level: str = "INFO"
    asr_host: str = "0.0.0.0"
    asr_port: int = 8010
    asr_provider: str = "sensevoice"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> ASRSettings:
    return ASRSettings()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name)

    @app.get("/health", tags=["system"])
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "service": "asr",
            "provider_preference": settings.asr_provider,
        }

    return app


app = create_app()


if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(
        "services.asr.app.main:app",
        host=settings.asr_host,
        port=settings.asr_port,
        reload=False,
    )
