from contextlib import asynccontextmanager
from functools import lru_cache

import uvicorn
from pydantic import AliasChoices, Field
from fastapi import FastAPI
from services.api.app.errors import register_exception_handlers
from services.api.app.repositories.factory import build_session_task_repository
from services.api.app.routes import get_router_registrations
from services.runtime_config import APIServiceSettings


class APISettings(APIServiceSettings):
    """Backward-compatible API settings alias with shared runtime defaults."""

    persistence_backend: str = Field(
        default="memory",
        validation_alias=AliasChoices("persistence_backend", "API_PERSISTENCE_BACKEND", "PERSISTENCE_BACKEND"),
    )


@lru_cache
def get_settings() -> APISettings:
    return APISettings()


def create_app(*, settings: APISettings | None = None) -> FastAPI:
    runtime_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        repository = build_session_task_repository(runtime_settings)
        app.state.session_task_repository = repository
        try:
            yield
        finally:
            repository.close()

    app = FastAPI(title=runtime_settings.app_name, lifespan=lifespan)
    register_exception_handlers(app)

    for registration in get_router_registrations(environment=runtime_settings.app_env):
        app.include_router(registration.router)

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
