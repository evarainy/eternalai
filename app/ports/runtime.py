"""Runtime interface contract."""

from __future__ import annotations

from typing import Any, Literal, Protocol

from app.ports.response_envelope import ResponseEnvelope


class RuntimePort(Protocol):
    async def handle_user_message(
        self,
        channel: Literal["web", "cli", "api", "mock"],
        ai_user_id: str,
        session_id: str,
        message: str,
        client_capabilities: dict[str, Any],
    ) -> ResponseEnvelope: ...
