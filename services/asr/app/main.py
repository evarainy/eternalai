from functools import lru_cache

import uvicorn
from fastapi import FastAPI
from services.runtime_config import ASRServiceSettings


class ASRSettings(ASRServiceSettings):
    """Backward-compatible ASR settings alias with shared runtime defaults."""


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
