"""Admin Lite Registry management service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.admin.actions import (
    ADMIN_POLICY_CAPABILITY_BY_ACTION,
    AdminRegistryAction,
)
from app.ports.capability_registry import (
    CapabilityExecutionIdentity,
    CapabilityRegistryPort,
    CapabilityRiskLevel,
    CapabilitySpec,
    CapabilityStatus,
    CapabilityTargetSystem,
    CapabilityType,
)
from app.ports.policy_guard import ManagementPlanePolicyContext, PolicyGuardPort
from app.ports.trace import TraceEvent, TraceEventStatus, TracePort


class AdminCapabilityCreate(BaseModel):
    """Create payload without status; the service always creates draft entries."""

    model_config = ConfigDict(extra="forbid")

    capability_id: str
    name: str
    type: CapabilityType
    intent_tags: list[str] = Field(default_factory=list)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    input_schema_digest: str
    output_schema_digest: str
    risk_level: CapabilityRiskLevel
    owner: str
    version: str
    short_description: str
    target_system: CapabilityTargetSystem | None = None
    execution_identity: CapabilityExecutionIdentity
    binding_required: bool
    policy_digest: str | None = None

    def to_draft_spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            **self.model_dump(mode="python"),
            status="draft",
        )


class AdminCapabilityView(BaseModel):
    """Credential-safe Registry metadata returned by Admin Lite."""

    model_config = ConfigDict(extra="forbid")

    capability_id: str
    name: str
    type: CapabilityType
    intent_tags: list[str]
    input_schema_digest: str
    output_schema_digest: str
    risk_level: CapabilityRiskLevel
    owner: str
    version: str
    status: CapabilityStatus
    short_description: str
    target_system: CapabilityTargetSystem | None
    execution_identity: CapabilityExecutionIdentity
    binding_required: bool

    @classmethod
    def from_spec(cls, capability: CapabilitySpec) -> AdminCapabilityView:
        return cls.model_validate(
            capability.model_dump(
                include={
                    "capability_id",
                    "name",
                    "type",
                    "intent_tags",
                    "input_schema_digest",
                    "output_schema_digest",
                    "risk_level",
                    "owner",
                    "version",
                    "status",
                    "short_description",
                    "target_system",
                    "execution_identity",
                    "binding_required",
                }
            )
        )


@dataclass(frozen=True)
class AdminRequestContext:
    trace_id: str
    session_id: str
    ai_user_id: str
    roles: tuple[str, ...]


class AdminRoleNotAllowedError(RuntimeError):
    """Raised before Registry access when the role claim is insufficient."""


class AdminCapabilityNotFoundError(RuntimeError):
    """Raised only after an authorized Registry lookup misses."""


class AdminInvalidStatusTransitionError(RuntimeError):
    """Raised when a deprecated capability is asked to re-enter service."""


class AdminRegistryService:
    """Role-guarded Registry actions with no execution-surface dependency."""

    def __init__(
        self,
        *,
        capability_registry: CapabilityRegistryPort,
        policy_guard: PolicyGuardPort,
        trace_port: TracePort,
    ) -> None:
        self._capability_registry = capability_registry
        self._policy_guard = policy_guard
        self._trace_port = trace_port

    async def list_capabilities(
        self,
        context: AdminRequestContext,
    ) -> list[CapabilitySpec]:
        await self._authorize("list", context)
        capabilities = await self._capability_registry.list()
        await self._record(
            action="list",
            context=context,
            status="ok",
            decision="allow",
            attributes={"result_count": len(capabilities)},
        )
        return capabilities

    async def get_capability(
        self,
        capability_id: str,
        context: AdminRequestContext,
    ) -> CapabilitySpec:
        await self._authorize("get", context, capability_id=capability_id)
        capability = await self._capability_registry.get(capability_id)
        if capability is None:
            await self._record_not_found("get", capability_id, context)
            raise AdminCapabilityNotFoundError(capability_id)
        await self._record(
            action="get",
            context=context,
            status="ok",
            decision="allow",
            capability_id=capability_id,
        )
        return capability

    async def create_capability(
        self,
        payload: AdminCapabilityCreate,
        context: AdminRequestContext,
    ) -> CapabilitySpec:
        await self._authorize("create", context, capability_id=payload.capability_id)
        created = await self._capability_registry.create(payload.to_draft_spec())
        await self._record(
            action="create",
            context=context,
            status="ok",
            decision="allow",
            capability_id=created.capability_id,
            attributes={"before_status": None, "after_status": created.status},
        )
        return created

    async def enable_capability(
        self,
        capability_id: str,
        context: AdminRequestContext,
    ) -> CapabilitySpec:
        await self._authorize("enable", context, capability_id=capability_id)
        current = await self._get_for_transition("enable", capability_id, context)
        if current.status == "deprecated":
            await self._record_invalid_transition("enable", current, context)
            raise AdminInvalidStatusTransitionError(capability_id)
        enabled = (
            current
            if current.status == "active"
            else await self._capability_registry.update(
                capability_id,
                {"status": "active"},
            )
        )
        await self._record_transition("enable", current, enabled, context)
        return enabled

    async def disable_capability(
        self,
        capability_id: str,
        context: AdminRequestContext,
    ) -> CapabilitySpec:
        await self._authorize("disable", context, capability_id=capability_id)
        current = await self._get_for_transition("disable", capability_id, context)
        if current.status == "deprecated":
            await self._record_invalid_transition("disable", current, context)
            raise AdminInvalidStatusTransitionError(capability_id)
        disabled = (
            current
            if current.status == "disabled"
            else await self._capability_registry.disable(capability_id)
        )
        await self._record_transition("disable", current, disabled, context)
        return disabled

    async def _authorize(
        self,
        action: AdminRegistryAction,
        context: AdminRequestContext,
        *,
        capability_id: str | None = None,
    ) -> None:
        decision = await self._policy_guard.decide(
            ai_user_id=context.ai_user_id,
            capability_id=ADMIN_POLICY_CAPABILITY_BY_ACTION[action],
            arguments={},
            request_context=ManagementPlanePolicyContext(
                request_id=context.trace_id,
                roles=list(context.roles),
            ),
        )
        if decision.decision == "allow":
            return
        await self._record(
            action=action,
            context=context,
            status="blocked",
            decision=decision.decision,
            capability_id=capability_id,
            attributes={"reason_code": decision.reason_code or "policy_denied"},
        )
        raise AdminRoleNotAllowedError(decision.reason_code or "policy_denied")

    async def _get_for_transition(
        self,
        action: AdminRegistryAction,
        capability_id: str,
        context: AdminRequestContext,
    ) -> CapabilitySpec:
        capability = await self._capability_registry.get(capability_id)
        if capability is None:
            await self._record_not_found(action, capability_id, context)
            raise AdminCapabilityNotFoundError(capability_id)
        return capability

    async def _record_not_found(
        self,
        action: AdminRegistryAction,
        capability_id: str,
        context: AdminRequestContext,
    ) -> None:
        await self._record(
            action=action,
            context=context,
            status="failed",
            decision="allow",
            capability_id=capability_id,
            attributes={"reason_code": "capability_not_found"},
        )

    async def _record_invalid_transition(
        self,
        action: AdminRegistryAction,
        capability: CapabilitySpec,
        context: AdminRequestContext,
    ) -> None:
        await self._record(
            action=action,
            context=context,
            status="failed",
            decision="allow",
            capability_id=capability.capability_id,
            attributes={
                "reason_code": "invalid_status_transition",
                "before_status": capability.status,
                "after_status": capability.status,
            },
        )

    async def _record_transition(
        self,
        action: AdminRegistryAction,
        before: CapabilitySpec,
        after: CapabilitySpec,
        context: AdminRequestContext,
    ) -> None:
        await self._record(
            action=action,
            context=context,
            status="ok",
            decision="allow",
            capability_id=after.capability_id,
            attributes={
                "before_status": before.status,
                "after_status": after.status,
            },
        )

    async def _record(
        self,
        *,
        action: AdminRegistryAction,
        context: AdminRequestContext,
        status: TraceEventStatus,
        decision: str,
        capability_id: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        event_attributes: dict[str, Any] = {
            "action": action,
            "policy_capability_id": ADMIN_POLICY_CAPABILITY_BY_ACTION[action],
            "authorization_decision": decision,
            "role_claim_source": "request_header",
            "role_claim_authenticated": False,
        }
        event_attributes.update(attributes or {})
        await self._trace_port.record_event(
            TraceEvent(
                trace_id=context.trace_id,
                task_id=f"admin-request:{context.trace_id}",
                session_id=context.session_id,
                event_type="admin_action",
                status=status,
                capability_id=capability_id,
                attributes=event_attributes,
            )
        )


__all__ = (
    "AdminCapabilityCreate",
    "AdminCapabilityNotFoundError",
    "AdminCapabilityView",
    "AdminInvalidStatusTransitionError",
    "AdminRegistryService",
    "AdminRequestContext",
    "AdminRoleNotAllowedError",
)
