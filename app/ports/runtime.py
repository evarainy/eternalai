"""Runtime interface contract."""

from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.contracts.sdui.models import UserAction
from app.ports.agent_orchestration import ConfirmationPreview
from app.ports.auth import Principal
from app.ports.capability_gateway import ExecutionResult
from app.ports.capability_registry import CapabilitySpec
from app.ports.human_gate import TaskVersionBindingManifest, VersionBinding
from app.ports.response_envelope import ResponseEnvelope
from app.ports.response_projection_contract import ProjectionContractSnapshot


class DFormCollectionReference(BaseModel):
    """A reference only; authenticated ownership is rechecked on every reply."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    task_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,96}$")
    version: int = Field(strict=True, ge=1, le=32)


d_form_collection_reference: ContextVar[DFormCollectionReference | None] = ContextVar(
    "d_form_collection_reference", default=None,
)
chat_client_request_id: ContextVar[str | None] = ContextVar(
    "chat_client_request_id", default=None,
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


@dataclass(frozen=True)
class ConfirmationOwner:
    """Account identity plus bound session; never the login token fingerprint."""

    tenant_id: str
    session_id: str
    ai_user_id: str


@dataclass(frozen=True)
class WaitingProposal:
    owner: ConfirmationOwner
    task_id: str
    trace_id: str
    response_id: str
    capability: CapabilitySpec
    arguments: Mapping[str, Any]
    execution: ExecutionResult
    binding_manifest: TaskVersionBindingManifest
    intent_version_binding: VersionBinding
    projection: ProjectionContractSnapshot


@dataclass(frozen=True)
class StagedWaiting:
    response_id: str
    execution: ExecutionResult
    confirmation: ConfirmationPreview | None
    token: object  # Coordinator-internal handle; opaque outside the port.


class ConfirmationCoordinatorPort(Protocol):
    """Deterministic confirmation entry points shared by both runtimes."""

    async def handle_confirmation_message(
        self,
        owner: ConfirmationOwner,
        message: str,
        *,
        client_request_id: str | None,
    ) -> ResponseEnvelope | None: ...

    async def handle_confirmation_action(
        self,
        owner: ConfirmationOwner,
        action: UserAction,
    ) -> ResponseEnvelope: ...

    async def stage_waiting(self, proposal: WaitingProposal) -> StagedWaiting: ...

    def publish_waiting(self, staged: StagedWaiting) -> bool: ...

    async def abandon_waiting(self, staged: StagedWaiting) -> None: ...


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
    "ConfirmationCoordinatorPort",
    "ConfirmationOwner",
    "DFormCollectionReference",
    "RuntimePort",
    "RuntimeSessionBusyError",
    "StagedWaiting",
    "UserActionOutcome",
    "WaitingProposal",
    "chat_client_request_id",
    "d_form_collection_reference",
)
