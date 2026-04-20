from functools import lru_cache

import uvicorn
from fastapi import FastAPI
from services.api.app.errors import register_exception_handlers
from services.api.app.routes import get_router_registrations
from services.runtime_config import APIServiceSettings


class APISettings(APIServiceSettings):
    """Backward-compatible API settings alias with shared runtime defaults."""


@lru_cache
def get_settings() -> APISettings:
    return APISettings()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name)
    register_exception_handlers(app)

    for registration in get_router_registrations(environment=settings.app_env):
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
