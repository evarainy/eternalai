from fastapi import FastAPI

from app.api.v1.health import router as health_router
from app.api.v1.runtime import make_router
from app.ports.runtime import RuntimePort


def create_app(runtime: RuntimePort | None = None) -> FastAPI:
    application = FastAPI(title="EternalAI", version="0.1.0")
    application.include_router(health_router, prefix="/api/v1")
    application.include_router(make_router(runtime), prefix="/api/v1/runtime")
    return application


app = create_app()
