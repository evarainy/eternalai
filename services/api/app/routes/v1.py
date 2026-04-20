"""Versioned public API route group for future business endpoints."""

from fastapi import APIRouter
from services.api.app.routes.messages import router as messages_router
from services.api.app.routes.sessions import router as sessions_router
from services.api.app.routes.tasks import router as tasks_router


def build_v1_router() -> APIRouter:
    router = APIRouter(prefix="/v1")
    router.include_router(sessions_router)
    router.include_router(messages_router)
    router.include_router(tasks_router)
    return router
