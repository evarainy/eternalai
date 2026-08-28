"""Runtime interface contract."""

from __future__ import annotations

from typing import Any, Literal, Protocol, TypeAlias

from app.contracts.sdui.models import UserAction
from app.ports.auth import Principal
from app.ports.response_envelope import ResponseEnvelope

UserActionOutcome: TypeAlias = Literal[
    "accepted",
    "action_gate_unavailable",
    "no_pending_action",
    "action_binding_incomplete",
    "action_reference_mismatch",
    "action_pending_changed",
    "action_already_claimed",
    "action_stale",
    "action_version_conflict",
]


class RuntimePort(Protocol):
    async def handle_user_message(
        self,
        channel: Literal["web", "cli", "api", "mock"],
        ai_user_id: str,
        session_id: str,
        message: str,
        client_capabilities: dict[str, Any],
    ) -> ResponseEnvelope: ...

    async def handle_user_action(
        self,
        channel: Literal["web", "cli", "api", "mock"],
        principal: Principal,
        session_id: str,
        action: UserAction,
    ) -> ResponseEnvelope: ...


__all__ = ("RuntimePort", "UserActionOutcome")
