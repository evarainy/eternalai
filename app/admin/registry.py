"""Admin Lite Registry management service."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal, cast, get_args

from pydantic import BaseModel, ConfigDict, Field

from app.admin.actions import (
    ADMIN_POLICY_CAPABILITY_BY_ACTION,
    AdminAction,
)
from app.admin.evidence import (
    AdminBindingListResponse,
    AdminBindingView,
    AdminTaskEventView,
    AdminTaskView,
    AdminTracePersistedView,
)
from app.ports.capability_registry import (
    CapabilityExecutionIdentity,
    CapabilityIntentTags,
    CapabilityName,
    CapabilityOwner,
    CapabilityRegistryPort,
    CapabilityRiskLevel,
    CapabilityShortDescription,
    CapabilitySpec,
    CapabilityStatus,
    CapabilityTargetSystem,
    CapabilityType,
)
from app.ports.identity_mapping import (
    IdentityBindStatus,
    IdentityCheckResult,
    IdentityMappingMutationError,
    IdentityMappingMutationResult,
    IdentityMappingPort,
    TargetSystem,
)
from app.ports.policy_guard import ManagementPlanePolicyContext, PolicyGuardPort
from app.ports.task_store import TASK_STORE_QUERY_LIMIT, TaskStorePort
from app.ports.trace import (
    TRACE_QUERY_LIMIT,
    TraceEvent,
    TraceEventStatus,
    TracePort,
    TraceQueryPort,
)


class AdminCapabilityCreate(BaseModel):
    """Create payload without status; the service always creates draft entries."""

    model_config = ConfigDict(extra="forbid")

    capability_id: str
    name: CapabilityName
    type: CapabilityType
    intent_tags: CapabilityIntentTags = Field(default_factory=list)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    input_schema_digest: str
    output_schema_digest: str
    risk_level: CapabilityRiskLevel
    owner: CapabilityOwner
    version: str
    short_description: CapabilityShortDescription
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
    principal_authenticated: bool = False


class AdminRoleNotAllowedError(RuntimeError):
    """Raised before Registry access when the role claim is insufficient."""


class AdminCapabilityNotFoundError(RuntimeError):
    """Raised only after an authorized Registry lookup misses."""


class AdminInvalidStatusTransitionError(RuntimeError):
    """Raised when a deprecated capability is asked to re-enter service."""


class AdminTaskFilterRequiredError(RuntimeError):
    """Raised after authorization when no bounded Task filter was supplied."""


class AdminTaskNotFoundError(RuntimeError):
    """Raised only after an authorized Task evidence lookup misses."""


class AdminBindingQueryInvalidError(RuntimeError):
    """Raised after authorization when Binding query parameters are invalid."""


class AdminTraceFilterRequiredError(RuntimeError):
    """Raised after authorization when no bounded Trace filter was supplied."""


class AdminRegistryService:
    """Role-guarded Registry actions with no execution-surface dependency."""

    def __init__(
        self,
        *,
        capability_registry: CapabilityRegistryPort,
        task_store: TaskStorePort,
        identity_mapping: IdentityMappingPort,
        policy_guard: PolicyGuardPort,
        trace_port: TracePort,
        trace_query: TraceQueryPort,
    ) -> None:
        self._capability_registry = capability_registry
        self._task_store = task_store
        self._identity_mapping = identity_mapping
        self._policy_guard = policy_guard
        self._trace_port = trace_port
        self._trace_query = trace_query

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

    async def list_tasks(
        self,
        context: AdminRequestContext,
        *,
        session_id: str | None = None,
        ai_user_id: str | None = None,
    ) -> list[AdminTaskView]:
        await self._authorize("tasks_list", context)
        if session_id is None and ai_user_id is None:
            await self._record(
                action="tasks_list",
                context=context,
                status="failed",
                decision="allow",
                attributes={"reason_code": "task_filter_required"},
            )
            raise AdminTaskFilterRequiredError("session_id or ai_user_id is required")
        tasks = await self._task_store.list_tasks(
            session_id=session_id,
            ai_user_id=ai_user_id,
        )
        views = [
            AdminTaskView.from_record(task)
            for task in tasks[:TASK_STORE_QUERY_LIMIT]
        ]
        await self._record(
            action="tasks_list",
            context=context,
            status="ok",
            decision="allow",
            attributes={"result_count": len(views)},
        )
        return views

    async def list_task_events(
        self,
        task_id: str,
        context: AdminRequestContext,
    ) -> list[AdminTaskEventView]:
        await self._authorize("task_events_list", context)
        if await self._task_store.get_task(task_id) is None:
            await self._record(
                action="task_events_list",
                context=context,
                status="failed",
                decision="allow",
                attributes={"reason_code": "task_not_found"},
            )
            raise AdminTaskNotFoundError(task_id)
        events = await self._task_store.list_events(task_id)
        views = [
            AdminTaskEventView.from_record(event)
            for event in events[:TASK_STORE_QUERY_LIMIT]
        ]
        await self._record(
            action="task_events_list",
            context=context,
            status="ok",
            decision="allow",
            attributes={"result_count": len(views)},
        )
        return views

    async def list_bindings(
        self,
        ai_user_id: str | None,
        context: AdminRequestContext,
        *,
        target_system: str | None = None,
        binding_scope: str | None = None,
        account_set_id: str | None = None,
        device_domain_id: str | None = None,
    ) -> AdminBindingListResponse:
        await self._authorize("bindings_list", context)
        if ai_user_id is None or not ai_user_id.strip():
            await self._record_invalid_binding_query("ai_user_id_required", context)
            raise AdminBindingQueryInvalidError("ai_user_id is required")
        if target_system is not None and target_system not in get_args(TargetSystem):
            await self._record_invalid_binding_query("target_system_invalid", context)
            raise AdminBindingQueryInvalidError("target_system is invalid")
        mappings = await self._identity_mapping.list_mappings(
            ai_user_id,
            target_system=cast(TargetSystem | None, target_system),
            binding_scope=binding_scope,
            account_set_id=account_set_id,
            device_domain_id=device_domain_id,
        )
        views = [
            AdminBindingView.from_result(mapping)
            for mapping in mappings[:TASK_STORE_QUERY_LIMIT]
        ]
        await self._record(
            action="bindings_list",
            context=context,
            status="ok",
            decision="allow",
            attributes={"result_count": len(views)},
        )
        return AdminBindingListResponse(ai_user_id=ai_user_id, items=views)

    async def list_traces(
        self,
        context: AdminRequestContext,
        *,
        trace_id: str | None = None,
        task_id: str | None = None,
        session_id: str | None = None,
    ) -> list[AdminTracePersistedView]:
        await self._authorize("traces_list", context)
        trace_id = _non_blank_filter(trace_id)
        task_id = _non_blank_filter(task_id)
        session_id = _non_blank_filter(session_id)
        if trace_id is None and task_id is None and session_id is None:
            await self._record(
                action="traces_list",
                context=context,
                status="failed",
                decision="allow",
                attributes={"reason_code": "trace_filter_required"},
            )
            raise AdminTraceFilterRequiredError(
                "trace_id, task_id, or session_id is required"
            )

        if trace_id is not None:
            events = await self._trace_query.list_events_by_trace(
                trace_id,
                task_id=task_id,
                session_id=session_id,
            )
        elif task_id is not None:
            events = await self._trace_query.list_events_by_task(
                task_id,
                session_id=session_id,
            )
        else:
            assert session_id is not None
            events = await self._trace_query.list_events_by_session(session_id)

        views = [
            AdminTracePersistedView.from_record(event)
            for event in events[:TRACE_QUERY_LIMIT]
        ]
        await self._record(
            action="traces_list",
            context=context,
            status="ok",
            decision="allow",
            attributes={"result_count": len(views)},
        )
        return views

    async def _record_invalid_binding_query(
        self,
        reason_code: str,
        context: AdminRequestContext,
    ) -> None:
        await self._record(
            action="bindings_list",
            context=context,
            status="failed",
            decision="allow",
            attributes={"reason_code": reason_code},
        )

    async def _authorize(
        self,
        action: AdminAction,
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
        action: AdminAction,
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
        action: AdminAction,
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
        action: AdminAction,
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
        action: AdminAction,
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
        action: AdminAction,
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
            "role_claim_source": (
                "authenticated_principal"
                if context.principal_authenticated
                else "unverified_context"
            ),
            "role_claim_authenticated": context.principal_authenticated,
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


class AdminBindingNotFoundError(RuntimeError):
    """Raised after an authorized binding mutation cannot resolve its target."""


class AdminBindingMutationUnavailableError(RuntimeError):
    """Raised without a storage exception chain when binding mutation fails."""


class AdminBindingMutationView(BaseModel):
    """Credential-safe binding projection returned by mutation endpoints."""

    model_config = ConfigDict(extra="forbid")

    binding_id: str
    target_system: Literal["oa"]
    execution_identity: Literal["user_delegated"]
    bind_status: Literal["revoked"]
    binding_scope: None
    account_set_id: None
    device_domain_id: None
    reason_code: Literal["identity_revoked"]

    @classmethod
    def from_result(cls, result: IdentityCheckResult) -> AdminBindingMutationView:
        if result.binding_id is None:
            raise ValueError("Binding mutation result is missing its safe reference.")
        return cls.model_validate(
            result.model_dump(
                include={
                    "binding_id",
                    "target_system",
                    "execution_identity",
                    "bind_status",
                    "binding_scope",
                    "account_set_id",
                    "device_domain_id",
                    "reason_code",
                }
            )
        )


class AdminBindingMutationResponse(BaseModel):
    """Fixed response contract for revoke and reset operations."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["revoke", "reset"]
    binding: AdminBindingMutationView
    changed: bool
    next_action: Literal["none", "reauthenticate"]


class AdminBindingMutationService:
    """Role-guarded binding mutations isolated from existing Registry behavior."""

    def __init__(
        self,
        *,
        identity_mapping: IdentityMappingPort,
        policy_guard: PolicyGuardPort,
        trace_port: TracePort,
    ) -> None:
        self._identity_mapping = identity_mapping
        self._policy_guard = policy_guard
        self._trace_port = trace_port

    async def revoke_binding(
        self,
        binding_id: str,
        context: AdminRequestContext,
    ) -> AdminBindingMutationResponse:
        return await self._mutate("revoke", binding_id, context)

    async def reset_binding(
        self,
        binding_id: str,
        context: AdminRequestContext,
    ) -> AdminBindingMutationResponse:
        return await self._mutate("reset", binding_id, context)

    async def _mutate(
        self,
        operation: Literal["revoke", "reset"],
        binding_id: str,
        context: AdminRequestContext,
    ) -> AdminBindingMutationResponse:
        action = _binding_admin_action(operation)
        next_action = _binding_next_action(operation)
        await self._authorize(
            action,
            binding_id=binding_id,
            next_action=next_action,
            context=context,
        )

        mutation_result: IdentityMappingMutationResult | None = None
        mutation_failed = False
        try:
            if operation == "revoke":
                mutation_result = await self._identity_mapping.revoke_mapping(binding_id)
            else:
                mutation_result = await self._identity_mapping.reset_mapping(binding_id)
        except IdentityMappingMutationError:
            mutation_failed = True

        if mutation_failed:
            await self._record(
                action=action,
                context=context,
                status="failed",
                decision="allow",
                binding_id=binding_id,
                next_action=next_action,
                reason_code="binding_mutation_unavailable",
            )
            raise AdminBindingMutationUnavailableError(
                "Binding mutation provider is unavailable."
            ) from None

        if mutation_result is None:
            await self._record(
                action=action,
                context=context,
                status="failed",
                decision="allow",
                binding_id=binding_id,
                next_action=next_action,
                reason_code="binding_not_found",
            )
            raise AdminBindingNotFoundError("Binding was not found.") from None

        if not _valid_revoked_mapping(mutation_result.mapping):
            await self._record(
                action=action,
                context=context,
                status="failed",
                decision="allow",
                binding_id=binding_id,
                next_action=next_action,
                reason_code="binding_mutation_unavailable",
            )
            raise AdminBindingMutationUnavailableError(
                "Binding mutation provider returned an invalid result."
            ) from None

        response = AdminBindingMutationResponse(
            action=operation,
            binding=AdminBindingMutationView.from_result(mutation_result.mapping),
            changed=mutation_result.changed,
            next_action=next_action,
        )
        await self._record(
            action=action,
            context=context,
            status="ok",
            decision="allow",
            binding_id=response.binding.binding_id,
            previous_bind_status=mutation_result.previous_bind_status,
            after_bind_status=response.binding.bind_status,
            changed=response.changed,
            next_action=response.next_action,
        )
        return response

    async def _authorize(
        self,
        action: AdminAction,
        *,
        binding_id: str,
        next_action: Literal["none", "reauthenticate"],
        context: AdminRequestContext,
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
            binding_id=binding_id,
            next_action=next_action,
            reason_code=decision.reason_code or "policy_denied",
        )
        raise AdminRoleNotAllowedError(decision.reason_code or "policy_denied")

    async def _record(
        self,
        *,
        action: AdminAction,
        context: AdminRequestContext,
        status: TraceEventStatus,
        decision: str,
        binding_id: str | None = None,
        previous_bind_status: IdentityBindStatus | None = None,
        after_bind_status: IdentityBindStatus | None = None,
        changed: bool | None = None,
        next_action: Literal["none", "reauthenticate"] | None = None,
        reason_code: str | None = None,
    ) -> None:
        attributes: dict[str, Any] = {
            "action": action,
            "policy_capability_id": ADMIN_POLICY_CAPABILITY_BY_ACTION[action],
            "authorization_decision": decision,
            "role_claim_source": (
                "authenticated_principal"
                if context.principal_authenticated
                else "unverified_context"
            ),
            "role_claim_authenticated": context.principal_authenticated,
        }
        safe_binding_id = _safe_binding_reference(binding_id)
        if safe_binding_id is not None:
            attributes["binding_id"] = safe_binding_id
        if previous_bind_status is not None:
            attributes["previous_bind_status"] = previous_bind_status
        if after_bind_status is not None:
            attributes["after_bind_status"] = after_bind_status
        if changed is not None:
            attributes["changed"] = changed
        if next_action is not None:
            attributes["next_action"] = next_action
        if reason_code is not None:
            attributes["reason_code"] = reason_code
        await self._trace_port.record_event(
            TraceEvent(
                trace_id=context.trace_id,
                task_id=f"admin-request:{context.trace_id}",
                session_id=context.session_id,
                event_type="admin_action",
                status=status,
                attributes=attributes,
            )
        )


class AdminRegistryServiceWithBindingMutations(AdminRegistryService):
    """Admin Registry composition that adds only the approved binding mutations."""

    def __init__(
        self,
        *,
        capability_registry: CapabilityRegistryPort,
        task_store: TaskStorePort,
        identity_mapping: IdentityMappingPort,
        policy_guard: PolicyGuardPort,
        trace_port: TracePort,
        trace_query: TraceQueryPort,
        binding_mutations: AdminBindingMutationService,
    ) -> None:
        super().__init__(
            capability_registry=capability_registry,
            task_store=task_store,
            identity_mapping=identity_mapping,
            policy_guard=policy_guard,
            trace_port=trace_port,
            trace_query=trace_query,
        )
        self._binding_mutations = binding_mutations

    async def revoke_binding(
        self,
        binding_id: str,
        context: AdminRequestContext,
    ) -> AdminBindingMutationResponse:
        return await self._binding_mutations.revoke_binding(binding_id, context)

    async def reset_binding(
        self,
        binding_id: str,
        context: AdminRequestContext,
    ) -> AdminBindingMutationResponse:
        return await self._binding_mutations.reset_binding(binding_id, context)


__all__ = (
    "AdminBindingMutationResponse",
    "AdminBindingMutationService",
    "AdminBindingMutationUnavailableError",
    "AdminBindingMutationView",
    "AdminBindingNotFoundError",
    "AdminBindingQueryInvalidError",
    "AdminCapabilityCreate",
    "AdminCapabilityNotFoundError",
    "AdminCapabilityView",
    "AdminInvalidStatusTransitionError",
    "AdminRegistryService",
    "AdminRegistryServiceWithBindingMutations",
    "AdminRequestContext",
    "AdminRoleNotAllowedError",
    "AdminTaskFilterRequiredError",
    "AdminTaskNotFoundError",
    "AdminTraceFilterRequiredError",
)


_OA_BINDING_ID_PATTERN = re.compile(
    r"^oa-session-v1:usr_v1_[A-Za-z0-9_-]{43}$"
)


def _binding_admin_action(
    operation: Literal["revoke", "reset"],
) -> AdminAction:
    if operation == "revoke":
        return "bindings_revoke"
    return "bindings_reset"


def _binding_next_action(
    operation: Literal["revoke", "reset"],
) -> Literal["none", "reauthenticate"]:
    if operation == "revoke":
        return "none"
    return "reauthenticate"


def _safe_binding_reference(binding_id: str | None) -> str | None:
    if binding_id is None or _OA_BINDING_ID_PATTERN.fullmatch(binding_id) is None:
        return None
    return binding_id


def _valid_revoked_mapping(mapping: IdentityCheckResult) -> bool:
    return (
        _safe_binding_reference(mapping.binding_id) is not None
        and mapping.target_system == "oa"
        and mapping.execution_identity == "user_delegated"
        and mapping.bind_status == "revoked"
        and mapping.binding_scope is None
        and mapping.account_set_id is None
        and mapping.device_domain_id is None
        and mapping.reason_code == "identity_revoked"
    )


def _non_blank_filter(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return value
