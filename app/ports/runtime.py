"""Runtime interface contract."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.contracts.sdui.models import UserAction
from app.ports.auth import Principal
from app.ports.response_envelope import ResponseEnvelope


class DFormCollectionReference(BaseModel):
    """A reference only; authenticated ownership is rechecked on every reply."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    task_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,96}$")
    version: int = Field(strict=True, ge=1, le=32)


d_form_collection_reference: ContextVar[DFormCollectionReference | None] = ContextVar(
    "d_form_collection_reference", default=None,
)

type UserActionOutcome = Literal[
    "accepted",
    "action_gate_unavailable",
    "no_pending_action",
    "action_binding_incomplete",
    "action_reference_mismatch",
    "action_pending_changed",
    "action_already_claimed",
    "action_stale",
    "action_version_conflict",
    "cancelled",
    "confirmation_invalidated",
]


class RuntimeSessionBusyError(RuntimeError):
    """The conversation session already has a turn in progress (HTTP 409)."""


class RuntimePort(Protocol):
    async def handle_user_message(
        self,
        channel: Literal["web", "cli", "api", "mock"],
        principal: Principal,
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


__all__ = (
    "DFormCollectionReference",
    "RuntimePort",
    "RuntimeSessionBusyError",
    "UserActionOutcome",
    "d_form_collection_reference",
)
