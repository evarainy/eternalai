"""Capability gateway pass-through skeleton for Phase 0."""

from __future__ import annotations

from typing import Any, cast

from app.ports.adapter import AdapterPort, AdapterResult
from app.ports.capability_gateway import (
    ErrorCode,
    ExecutionResult,
    ExecutionStatus,
    RequestOrgContext,
)
from app.ports.capability_registry import CapabilityRegistryPort
from app.ports.identity_mapping import IdentityMappingPort
from app.ports.policy_guard import PolicyGuardPort
from app.ports.trace import TraceEventStatus, TraceEventType, TracePort


def _map_adapter_status(adapter_result: AdapterResult) -> ExecutionStatus:
    if adapter_result.status == "success":
        return "completed"
    if adapter_result.error_code == "adapter_timeout":
        return "timeout"
    if adapter_result.error_code == "upstream_permission_denied":
        return "denied"
    if adapter_result.error_code in {
        "adapter_payload_invalid",
        "adapter_empty_response",
        "adapter_http_500",
        "adapter_missing_required_field",
        "adapter_error",
    }:
        return "failed"
    return "failed"


def _map_identity_error(bind_status: str) -> str:
    mapping = {
        "unbound": "identity_unbound",
        "expired": "identity_expired",
        "revoked": "identity_revoked",
        "needs_binding_scope": "needs_binding_scope",
        "verification_failed": "identity_unbound",
    }
    return mapping.get(bind_status, "identity_unbound")


class CapabilityGateway:
    """Minimal Gateway -> AdapterPort -> ExecutionResult pass-through."""

    def __init__(
        self,
        adapter: AdapterPort | None = None,
        capability_registry: CapabilityRegistryPort | None = None,
        identity_mapping: IdentityMappingPort | None = None,
        policy_guard: PolicyGuardPort | None = None,
        trace_port: TracePort | None = None,
        adapters: dict[str, AdapterPort] | None = None,
    ) -> None:
        self._adapter = adapter
        self._adapters = adapters
        self._capability_registry = capability_registry
        self._identity_mapping = identity_mapping
        self._policy_guard = policy_guard
        self._trace_port = trace_port

    async def execute_capability(
        self,
        task_id: str,
        session_id: str,
        ai_user_id: str,
        capability_id: str,
        arguments: dict[str, Any],
        request_context: RequestOrgContext,
    ) -> ExecutionResult:
        trace_id = request_context.request_id

        capability_spec = None
        if self._capability_registry is not None:
            capability_spec = await self._capability_registry.get(capability_id)
            if capability_spec is None:
                await self._record_step(
                    trace_id,
                    task_id,
                    session_id,
                    "no_capability_found",
                    "blocked",
                    capability_id=capability_id,
                    error_code="capability_not_found",
                )
                return ExecutionResult(
                    status="no_capability_found",
                    error_code="capability_not_found",
                    trace_id=trace_id,
                )

        if (
            capability_spec is not None
            and capability_spec.target_system is not None
            and self._identity_mapping is not None
        ):
            identity_result = await self._identity_mapping.resolve_execution_identity(
                ai_user_id=ai_user_id,
                target_system=capability_spec.target_system,
                execution_identity=capability_spec.execution_identity,
                request_context=request_context,
            )
            if identity_result.bind_status != "active":
                error_code = cast(
                    ErrorCode,
                    _map_identity_error(identity_result.bind_status),
                )
                await self._record_step(
                    trace_id,
                    task_id,
                    session_id,
                    "identity_check",
                    "blocked",
                    capability_id=capability_id,
                    error_code=error_code,
                )
                await self._record_step(
                    trace_id,
                    task_id,
                    session_id,
                    "blocked_by_identity",
                    "blocked",
                    capability_id=capability_id,
                    error_code=error_code,
                )
                # binding_required: designated ExecutionStatus for identity failures
                return ExecutionResult(
                    status="binding_required",
                    error_code=error_code,
                    trace_id=trace_id,
                )
            await self._record_step(
                trace_id,
                task_id,
                session_id,
                "identity_check",
                "ok",
                capability_id=capability_id,
            )

        if self._policy_guard is not None:
            policy_decision = await self._policy_guard.decide(
                ai_user_id=ai_user_id,
                capability_id=capability_id,
                arguments=arguments,
                request_context=request_context,
            )
            if policy_decision.decision == "deny":
                await self._record_step(
                    trace_id,
                    task_id,
                    session_id,
                    "policy_checked",
                    "blocked",
                    capability_id=capability_id,
                    error_code="policy_denied",
                )
                await self._record_step(
                    trace_id,
                    task_id,
                    session_id,
                    "blocked_by_policy",
                    "blocked",
                    capability_id=capability_id,
                    error_code="policy_denied",
                )
                return ExecutionResult(
                    status="denied",
                    error_code="policy_denied",
                    trace_id=trace_id,
                )
            if policy_decision.decision == "confirm":
                await self._record_step(
                    trace_id,
                    task_id,
                    session_id,
                    "policy_checked",
                    "blocked",
                    capability_id=capability_id,
                    error_code="confirm_required",
                )
                await self._record_step(
                    trace_id,
                    task_id,
                    session_id,
                    "confirm_required",
                    "blocked",
                    capability_id=capability_id,
                    error_code="confirm_required",
                )
                return ExecutionResult(
                    status="waiting_user",
                    error_code="confirm_required",
                    trace_id=trace_id,
                )
            await self._record_step(
                trace_id,
                task_id,
                session_id,
                "policy_checked",
                "ok",
                capability_id=capability_id,
            )

        if self._adapters is not None:
            target = capability_spec.target_system if capability_spec is not None else None
            if target is None or target not in self._adapters:
                await self._record_step(
                    trace_id,
                    task_id,
                    session_id,
                    "no_capability_found",
                    "blocked",
                    capability_id=capability_id,
                    error_code="capability_not_found",
                )
                return ExecutionResult(
                    status="no_capability_found",
                    error_code="capability_not_found",
                    trace_id=trace_id,
                )
            selected_adapter = self._adapters[target]
        else:
            if self._adapter is None:
                await self._record_step(
                    trace_id,
                    task_id,
                    session_id,
                    "no_capability_found",
                    "blocked",
                    capability_id=capability_id,
                    error_code="capability_not_found",
                )
                return ExecutionResult(
                    status="no_capability_found",
                    error_code="capability_not_found",
                    trace_id=trace_id,
                )
            selected_adapter = self._adapter

        if self._trace_port is not None:
            await self._trace_port.record_gateway_call(
                trace_id=trace_id,
                task_id=task_id,
                session_id=session_id,
                status="ok",
                capability_id=capability_id,
                error_code=None,
            )

        execution_context: dict[str, Any] = {}
        if "mock_error_mode" in arguments:
            execution_context["mock_error_mode"] = arguments["mock_error_mode"]

        adapter_result = await selected_adapter.execute(
            capability_id,
            arguments,
            execution_context,
        )

        result = ExecutionResult(
            status=_map_adapter_status(adapter_result),
            data=adapter_result.data,
            error_code=adapter_result.error_code,
            trace_id=trace_id,
        )
        await self._record_step(
            trace_id,
            task_id,
            session_id,
            "adapter_called",
            "ok",
            capability_id=capability_id,
            error_code=result.error_code,
        )
        await self._record_step(
            trace_id,
            task_id,
            session_id,
            "gateway_post_recorded",
            _map_execution_to_trace_status(result.status),
            capability_id=capability_id,
            error_code=result.error_code,
        )
        if result.status != "completed":
            await self._record_step(
                trace_id,
                task_id,
                session_id,
                "adapter_error_mapped",
                "failed",
                capability_id=capability_id,
                error_code=result.error_code,
            )

        return result

    async def _record_step(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
        event_type: TraceEventType,
        status: TraceEventStatus,
        capability_id: str | None = None,
        error_code: ErrorCode | None = None,
    ) -> None:
        if self._trace_port is None:
            return
        await self._trace_port.record_step(
            trace_id=trace_id,
            task_id=task_id,
            session_id=session_id,
            event_type=event_type,
            status=status,
            capability_id=capability_id,
            error_code=error_code,
        )


def _map_execution_to_trace_status(status: ExecutionStatus) -> TraceEventStatus:
    if status == "completed":
        return "ok"
    if status in {"denied", "binding_required", "no_capability_found", "waiting_user"}:
        return "blocked"
    return "failed"
