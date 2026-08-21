"""Capability gateway pass-through skeleton for Phase 0."""

from __future__ import annotations

import re
from typing import Any, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from app.ports.adapter import AdapterPort, AdapterResult
from app.ports.capability_gateway import (
    ErrorCode,
    ExecutionResult,
    ExecutionStatus,
    RequestOrgContext,
)
from app.ports.capability_registry import CapabilityRegistryPort
from app.ports.human_gate import HumanGatePort, VersionBindingMismatchError
from app.ports.identity_mapping import IdentityMappingPort
from app.ports.policy_guard import PolicyGuardPort
from app.ports.trace import TraceEventStatus, TraceEventType, TracePort
from app.version_binding import capability_version_bindings

_SAFE_ERROR_TYPE = re.compile(r"[A-Za-z][A-Za-z0-9]{0,63}")
_SAFE_ARGUMENT_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}")
# Schema keywords whose verdict may still change once a binding is resolved.
_BINDING_DERIVED_KEYWORDS = frozenset({"required"})


def _map_adapter_status(adapter_result: AdapterResult) -> ExecutionStatus:
    if adapter_result.status == "success":
        return "completed"
    if adapter_result.error_code in {
        "identity_unbound",
        "identity_expired",
        "identity_revoked",
        "needs_binding_scope",
    }:
        return "binding_required"
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
        human_gate_port: HumanGatePort | None = None,
    ) -> None:
        self._adapter = adapter
        self._adapters = adapters
        self._capability_registry = capability_registry
        self._identity_mapping = identity_mapping
        self._policy_guard = policy_guard
        self._trace_port = trace_port
        self._human_gate_port = human_gate_port

    def assert_production_wiring(self) -> None:
        """Fail closed when production assembly omitted a Gateway layer."""

        adapters = self._adapters
        if (
            self._capability_registry is None
            or self._identity_mapping is None
            or self._policy_guard is None
            or self._trace_port is None
            or self._human_gate_port is None
            or adapters is None
            or adapters.get("oa") is None
        ):
            raise RuntimeError(
                "Production CapabilityGateway wiring is incomplete"
            )

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
        credential_ref: str | None = None
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
            if self._human_gate_port is not None:
                try:
                    await self._human_gate_port.assert_task_bindings(
                        task_id,
                        capability_version_bindings(capability_spec),
                    )
                except (ValueError, VersionBindingMismatchError):
                    if self._trace_port is not None:
                        await self._trace_port.record_gateway_call(
                            trace_id=trace_id,
                            task_id=task_id,
                            session_id=session_id,
                            status="failed",
                            capability_id=capability_id,
                            error_code="internal_error",
                        )
                    return ExecutionResult(
                        status="failed",
                        error_code="internal_error",
                        trace_id=trace_id,
                    )
        # Argument validation is split in two on purpose. Everything that only
        # depends on what the caller sent -- additionalProperties, type, enum,
        # format -- is decided here, before identity: leaving it downstream let a
        # caller read binding scope off the differing error codes (schema error vs
        # identity error), which is an oracle. Only `required` waits for identity,
        # because a missing argument may be one the binding supplies (GT-012 asks
        # for binding-scope clarification there, not a schema error).
        if capability_spec is not None:
            caller_diagnostics = _validate_arguments(
                capability_spec.input_schema,
                arguments,
                binding_derived=False,
            )
            if caller_diagnostics is not None:
                return await self._reject_invalid_arguments(
                    trace_id,
                    task_id,
                    session_id,
                    capability_id,
                    caller_diagnostics,
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
            identity_result_matches_request = (
                identity_result.target_system == capability_spec.target_system
                and identity_result.execution_identity
                == capability_spec.execution_identity
            )
            if (
                identity_result.bind_status != "active"
                or not identity_result_matches_request
            ):
                bind_status = (
                    identity_result.bind_status
                    if identity_result.bind_status != "active"
                    else "verification_failed"
                )
                error_code = cast(
                    ErrorCode,
                    _map_identity_error(bind_status),
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
            if (
                capability_spec.target_system == "oa"
                and capability_spec.execution_identity == "user_delegated"
                and identity_result.binding_id is not None
            ):
                credential_ref = identity_result.binding_id

        # Second half of the split above: `required` runs after identity because
        # the binding may be what supplies the missing argument.
        if capability_spec is not None:
            binding_diagnostics = _validate_arguments(
                capability_spec.input_schema,
                arguments,
                binding_derived=True,
            )
            if binding_diagnostics is not None:
                return await self._reject_invalid_arguments(
                    trace_id,
                    task_id,
                    session_id,
                    capability_id,
                    binding_diagnostics,
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
        if credential_ref is not None:
            execution_context["credential_ref"] = credential_ref
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
            attributes=(
                adapter_result.trace_metadata.model_dump(mode="json")
                if adapter_result.trace_metadata is not None
                else None
            ),
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

    async def _reject_invalid_arguments(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
        capability_id: str,
        diagnostics: dict[str, Any],
    ) -> ExecutionResult:
        if self._trace_port is not None:
            await self._trace_port.record_gateway_call(
                trace_id=trace_id,
                task_id=task_id,
                session_id=session_id,
                status="failed",
                capability_id=capability_id,
                error_code="adapter_error",
                attributes=diagnostics,
            )
        return ExecutionResult(
            status="failed",
            error_code="adapter_error",
            trace_id=trace_id,
        )

    async def _record_step(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
        event_type: TraceEventType,
        status: TraceEventStatus,
        capability_id: str | None = None,
        error_code: ErrorCode | None = None,
        attributes: dict[str, Any] | None = None,
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
            attributes=attributes,
        )


def _map_execution_to_trace_status(status: ExecutionStatus) -> TraceEventStatus:
    if status == "completed":
        return "ok"
    if status in {"denied", "binding_required", "no_capability_found", "waiting_user"}:
        return "blocked"
    return "failed"


def _validate_arguments(
    input_schema: dict[str, Any],
    arguments: dict[str, Any],
    *,
    binding_derived: bool,
) -> dict[str, Any] | None:
    """Report the first schema violation belonging to one validation stage.

    ``binding_derived=False`` answers only from the caller's own payload;
    ``binding_derived=True`` answers the keywords a binding may still satisfy.
    """
    error: ValidationError | None
    try:
        Draft202012Validator.check_schema(input_schema)
        error = next(
            (
                candidate
                for candidate in Draft202012Validator(input_schema).iter_errors(
                    arguments
                )
                if (candidate.validator in _BINDING_DERIVED_KEYWORDS)
                is binding_derived
            ),
            None,
        )
    except SchemaError:
        # A malformed registry schema is not caller-supplied either way, so it is
        # answered in the caller-only stage and never reaches the binding stage.
        if binding_derived:
            return None
        error_type = "schema_error"
    else:
        if error is None:
            return None
        raw_error_type = error.validator
        error_type = (
            raw_error_type
            if isinstance(raw_error_type, str)
            and _SAFE_ERROR_TYPE.fullmatch(raw_error_type) is not None
            else "validation_error"
        )
    return {
        "error_path": "$.arguments",
        "error_type": error_type,
        "argument_keys": [
            key if _SAFE_ARGUMENT_KEY.fullmatch(key) is not None else "[REDACTED]"
            for key in sorted(arguments)[:32]
        ],
    }
