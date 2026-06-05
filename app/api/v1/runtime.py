"""Runtime API router factory."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app.ports.response_envelope import ResponseEnvelope
from app.ports.runtime import RuntimePort


class HandleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: Literal["web", "cli", "api", "mock"]
    ai_user_id: str
    session_id: str
    message: str
    client_capabilities: dict[str, Any] = Field(default_factory=dict)


def make_router(runtime: RuntimePort) -> APIRouter:
    router = APIRouter()

    @router.post("/handle")
    async def handle(body: HandleRequest) -> dict[str, Any]:
        envelope: ResponseEnvelope = await runtime.handle_user_message(
            channel=body.channel,
            ai_user_id=body.ai_user_id,
            session_id=body.session_id,
            message=body.message,
            client_capabilities=body.client_capabilities,
        )
        return envelope.model_dump()

    return router
