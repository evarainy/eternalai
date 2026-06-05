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
from app.ports.trace import TracePort


def _map_adapter_status(adapter_result: AdapterResult) -> ExecutionStatus:
    if adapter_result.status == "success":
        return "completed"
    if adapter_result.error_code == "adapter_timeout":
        return "timeout"
    if adapter_result.error_code == "upstream_permission_denied":
        return "denied"
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
    """Minimal Gateway -> MockOAAdapter -> ExecutionResult pass-through."""

    def __init__(
        self,
        adapter: AdapterPort,
        capability_registry: CapabilityRegistryPort | None = None,
        identity_mapping: IdentityMappingPort | None = None,
        policy_guard: PolicyGuardPort | None = None,
        trace_port: TracePort | None = None,
    ) -> None:
        self._adapter = adapter
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
                if self._trace_port is not None:
                    await self._trace_port.finalize_task_trace(
                        trace_id=trace_id,
                        task_id=task_id,
                        session_id=session_id,
                        status="blocked",
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
                if self._trace_port is not None:
                    await self._trace_port.finalize_task_trace(
                        trace_id=trace_id,
                        task_id=task_id,
                        session_id=session_id,
                        status="blocked",
                        capability_id=capability_id,
                        error_code=error_code,
                    )
                # binding_required: designated ExecutionStatus for identity failures
                return ExecutionResult(
                    status="binding_required",
                    error_code=error_code,
                    trace_id=trace_id,
                )

        if self._policy_guard is not None:
            policy_decision = await self._policy_guard.decide(
                ai_user_id=ai_user_id,
                capability_id=capability_id,
                arguments=arguments,
                request_context=request_context,
            )
            if policy_decision.decision == "deny":
                if self._trace_port is not None:
                    await self._trace_port.finalize_task_trace(
                        trace_id=trace_id,
                        task_id=task_id,
                        session_id=session_id,
                        status="blocked",
                        capability_id=capability_id,
                        error_code="policy_denied",
                    )
                return ExecutionResult(
                    status="denied",
                    error_code="policy_denied",
                    trace_id=trace_id,
                )
            if policy_decision.decision == "confirm":
                if self._trace_port is not None:
                    await self._trace_port.finalize_task_trace(
                        trace_id=trace_id,
                        task_id=task_id,
                        session_id=session_id,
                        status="blocked",
                        capability_id=capability_id,
                        error_code="confirm_required",
                    )
                return ExecutionResult(
                    status="waiting_user",
                    error_code="confirm_required",
                    trace_id=trace_id,
                )

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

        adapter_result = await self._adapter.execute(
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

        if self._trace_port is not None:
            await self._trace_port.finalize_task_trace(
                trace_id=trace_id,
                task_id=task_id,
                session_id=session_id,
                status="ok" if result.status == "completed" else "failed",
                capability_id=capability_id,
                error_code=result.error_code,
            )

        return result
