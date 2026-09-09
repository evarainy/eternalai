"""Single-turn selection, execution coordination and response organization.

Runtime retains identity, HumanGate decisions and task/checkpoint lifecycle.
The adapter owns no Runtime callback or per-request state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping, Protocol

from app.ports.capability_gateway import ExecutionResult, RequestOrgContext
from app.ports.capability_registry import (
    CapabilitySpec,
    CapabilityTargetSystem,
    CapabilityType,
)
from app.ports.human_gate import VersionBinding
from app.ports.response_envelope import ResponseEnvelope
from app.ports.response_projection_contract import ProjectionContractSnapshot


class OrchestrationContractError(RuntimeError):
    """Internal orchestration input contract violation; not a binding conflict."""


@dataclass(frozen=True, slots=True)
class AgentCapabilitySelection:
    capability: CapabilitySpec
    rule: Literal["exact_id", "unique_intent_tag"]


@dataclass(frozen=True, slots=True)
class AgentTaskVersionBindings:
    bindings: tuple[VersionBinding, ...]
    projection_binding: VersionBinding


@dataclass(frozen=True, slots=True)
class AgentResponseContext:
    response_id: str
    task_id: str
    session_id: str
    trace_id: str
    capability_id: str


@dataclass(frozen=True, slots=True)
class ConfirmationPreview:
    capability_id: str
    operation_summary: str
    target_system: CapabilityTargetSystem | None
    field_names: tuple[str, ...]
    displayed_argument_values: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if type(self.capability_id) is not str or type(self.operation_summary) is not str:
            raise TypeError("Confirmation preview identifiers and summary must be strings")
        if self.target_system is not None and (
            type(self.target_system) is not str
            or self.target_system not in ("oa", "u8", "hikvision_ivms")
        ):
            raise TypeError("Confirmation preview target must be a supported system or None")
        if type(self.field_names) is not tuple or any(
            type(name) is not str for name in self.field_names
        ):
            raise TypeError("Confirmation preview fields must be an immutable string tuple")
        if type(self.displayed_argument_values) is not tuple or any(
            type(pair) is not tuple
            or len(pair) != 2
            or any(type(value) is not str for value in pair)
            for pair in self.displayed_argument_values
        ):
            raise TypeError("Confirmation preview values must be immutable string pairs")
        names = [name for name, _ in self.displayed_argument_values]
        if len(set(names)) != len(names):
            raise ValueError("Confirmation preview displayed keys must be unique")

    def to_payload(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "operation_summary": self.operation_summary,
            "target_system": self.target_system,
            "field_names": list(self.field_names),
            "displayed_argument_values": dict(self.displayed_argument_values),
        }


class AgentOrchestrationPort(Protocol):
    async def select_capability(
        self,
        *,
        capability_id: str,
        target_system: CapabilityTargetSystem | None,
        capability_type: CapabilityType | None,
    ) -> AgentCapabilitySelection | None: ...

    async def resolve_task_version_bindings(
        self,
        *,
        capability: CapabilitySpec,
        intent_version_binding: VersionBinding,
    ) -> AgentTaskVersionBindings: ...

    async def execute_capability(
        self,
        *,
        task_id: str,
        session_id: str,
        ai_user_id: str,
        capability: CapabilitySpec,
        arguments: dict[str, Any],
        request_context: RequestOrgContext,
    ) -> ExecutionResult: ...

    async def resume_capability(
        self,
        *,
        task_id: str,
        confirmed: bool,
        expected_action_digest: str | None = None,
    ) -> ExecutionResult: ...

    def prepare_confirmation(
        self,
        *,
        capability_id: str,
        arguments: Mapping[str, Any],
        capability: CapabilitySpec | None,
    ) -> ConfirmationPreview: ...

    def build_response(
        self,
        *,
        context: AgentResponseContext,
        execution: ExecutionResult,
        projection: ProjectionContractSnapshot | None,
        confirmation: ConfirmationPreview | None = None,
    ) -> ResponseEnvelope: ...


__all__ = (
    "AgentCapabilitySelection",
    "AgentOrchestrationPort",
    "AgentResponseContext",
    "AgentTaskVersionBindings",
    "ConfirmationPreview",
    "OrchestrationContractError",
)
