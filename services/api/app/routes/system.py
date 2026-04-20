"""System routes that stay stable across API versions."""

from fastapi import APIRouter


def build_system_router(*, environment: str) -> APIRouter:
    router = APIRouter(tags=["system"])

    @router.get("/health", operation_id="getApiHealth")
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "service": "api",
            "environment": environment,
        }

    return router
