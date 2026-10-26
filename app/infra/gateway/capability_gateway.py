"""Capability gateway pass-through skeleton for Phase 0."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping
from time import monotonic
from typing import Any, cast
from uuid import UUID

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from app.mcp.connections import McpExecutionContextFactory
from app.mcp.diagnostics import record_mcp_event
from app.mcp.models import McpFailure, ServiceConfig
from app.ports.adapter import AdapterPort, AdapterResult
from app.ports.browser_gateway import (
    BrowserGatewayAdmission,
    BrowserGatewayDiagnostic,
    BrowserGatewayInvocation,
    BrowserGatewayRegistration,
    browser_request_scope,
)
from app.ports.capability_gateway import (
    ErrorCode,
    ExecutionResult,
    ExecutionStatus,
    RequestOrgContext,
)
from app.ports.capability_registry import CapabilityRegistryPort
from app.ports.human_gate import HumanGatePort, VersionBindingMismatchError
from app.ports.identity_mapping import IdentityMappingPort
from app.ports.mcp import McpAuthorizationContext
from app.ports.policy_guard import PolicyGuardPort
from app.ports.request_context import GovernedPolicyContext
from app.ports.trace import TraceEventStatus, TraceEventType, TracePort
from app.ports.workflow_store import GovernedWorkflowAuthorization
from app.version_binding import capability_version_bindings

_CHAT_STAGE_LOGGER = logging.getLogger("uvicorn.error.chat_stages")
_CHAT_STAGE_NODES = frozenset({
    'candidate', 'catalog', 'credential_read', 'execution', 'fixed_reply', 'identity', 'intent',
    'model', 'normalization', 'oa_result', 'oa_route', 'provider_transport'
})
_CHAT_STAGE_OUTCOMES = frozenset({
    'blocked', 'failed', 'ok', 'skipped', 'start', 'unknown'
})
_CHAT_STAGE_REASONS = frozenset({
    'adapter_error', 'adapter_http_500', 'adapter_payload_invalid', 'adapter_timeout',
    'ambiguous', 'argument_validation', 'binding_required', 'blank_input',
    'capability_not_found', 'catalog_invalid', 'completed', 'completion_returned',
    'confirm_required', 'context_length_exceeded', 'credential_not_required',
    'credential_resolved', 'd_form_consumed', 'denied', 'empty', 'empty_response', 'failed',
    'http_server', 'identity_expired', 'identity_revoked', 'identity_unbound', 'internal_error',
    'invalid_request', 'low_confidence', 'match_capability', 'match_none',
    'model_not_available', 'no_active_capability_registered', 'no_capability_found',
    'no_matching_capability', 'no_unique_active_candidate', 'no_visible_capability', 'none',
    'not_configured', 'over_budget', 'payload_invalid', 'payload_received', 'permission_denied',
    'policy_denied', 'provider_error', 'provider_returned', 'rate_limited', 'ready',
    'request_error', 'response_received', 'running', 'schema_invalid',
    'structured_output_error', 'timeout', 'unknown', 'unparsed', 'upstream_permission_denied',
    'visible_empty', 'visible_present', 'waiting_user'
})
_CHAT_STAGE_CAPABILITIES = frozenset({
    'oa.list_pending_workflows', 'oa.list_system_messages'
})


def _record_chat_stage(
    trace_id: object, node: str, outcome: str, *, reason: object = None,
    capability_id: object = None, elapsed_ms: int | None = None,
    http_status: object = None,
) -> None:
    # Project only code-owned enums and Runtime's existing server UUID trace.
    try:
        if type(trace_id) is not str or node not in _CHAT_STAGE_NODES:
            return
        trace = UUID(trace_id)
        if trace.version != 4 or str(trace) != trace_id:
            return
        _CHAT_STAGE_LOGGER.info(
            "chat_stage node=%s outcome=%s capability=%s reason=%s "
            "http_status=%s elapsed_ms=%s trace=%s",
            node, outcome if outcome in _CHAT_STAGE_OUTCOMES else "unknown",
            capability_id if type(capability_id) is str
            and capability_id in _CHAT_STAGE_CAPABILITIES else "none" if capability_id is None
            else "other",
            reason if type(reason) is str and reason in _CHAT_STAGE_REASONS
            else "none" if reason is None else "unknown",
            http_status if type(http_status) is int and 100 <= http_status <= 599 else "null",
            elapsed_ms if type(elapsed_ms) is int and 0 <= elapsed_ms <= 86_400_000
            else "null", trace.hex,
        )
    except Exception:
        # Diagnostics must not replace the existing business result.
        return


_SAFE_ERROR_TYPE = re.compile(r"[A-Za-z][A-Za-z0-9]{0,63}")
_SAFE_ARGUMENT_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}")
# Schema keywords whose verdict may still change once a binding is resolved.
_BINDING_DERIVED_KEYWORDS = frozenset({"required"})


def _map_adapter_status(adapter_result: AdapterResult) -> ExecutionStatus:
    if (
        adapter_result.status == "accepted"
        and type(adapter_result.browser_admission) is BrowserGatewayAdmission
    ):
        return "running"
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
    if (
        adapter_result.error_code == "policy_denied"
        and adapter_result.browser_diagnostic is not None
    ):
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
        unbound_task_capability_ids: frozenset[str] = frozenset(),
        *,
        tenant_id: str,
        mcp_contexts: McpExecutionContextFactory | None = None,
        mcp_profiles: Mapping[str, ServiceConfig] | None = None,
        mcp_scope_allowed: Callable[[str, str, str, str, str], bool] | None = None,
    ) -> None:
        if not tenant_id.strip():
            raise ValueError("source_profile_configuration_invalid")
        self._tenant_id = tenant_id
        self._mcp_contexts = mcp_contexts
        self._mcp_profiles = dict(mcp_profiles or {})
        self._mcp_scope_allowed = mcp_scope_allowed
        self._adapter = adapter
        self._adapters = adapters
        self._capability_registry = capability_registry
        self._identity_mapping = identity_mapping
        self._policy_guard = policy_guard
        self._trace_port = trace_port
        self._human_gate_port = human_gate_port
        self._unbound_task_capability_ids = unbound_task_capability_ids
        self._browser_dispatch: tuple[BrowserGatewayRegistration, AdapterPort] | None = None

    def install_browser_dispatch(
        self, registration: BrowserGatewayRegistration, adapter: AdapterPort,
    ) -> None:
        if self._browser_dispatch is not None or registration.tenant_id != self._tenant_id:
            raise ValueError("browser_gateway_installation_invalid")
        self._browser_dispatch = registration, adapter

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
        *,
        workflow_authorization: GovernedWorkflowAuthorization | None = None,
    ) -> ExecutionResult:
        trace_id = request_context.request_id
        oa_read = capability_id in _CHAT_STAGE_CAPABILITIES
        if oa_read:
            _record_chat_stage(trace_id, "oa_route", "start", capability_id=capability_id)
        if request_context.tenant_id != self._tenant_id:
            if oa_read:
                _record_chat_stage(trace_id, "oa_result", "blocked", reason="policy_denied",
                                   capability_id=capability_id)
            return ExecutionResult(status="denied", error_code="policy_denied", trace_id=trace_id)

        capability_spec = None
        credential_ref: str | None = None
        mcp_context: McpAuthorizationContext | None = None
        service_config_id: str | None = None
        if self._capability_registry is not None:
            capability_spec = await self._capability_registry.get(capability_id)
        browser_invocation: BrowserGatewayInvocation | None = None
        browser_adapter: AdapterPort | None = None
        if (
            self._browser_dispatch is not None
            and capability_id == self._browser_dispatch[0].capability_id
        ):
            registered, browser_adapter = self._browser_dispatch
            scope = browser_request_scope.get()
            if capability_spec is None or capability_spec.status != "active":
                if oa_read:
                    _record_chat_stage(trace_id, "oa_result", "blocked",
                                       reason="capability_not_found",
                                       capability_id=capability_id)
                return ExecutionResult(
                    status="no_capability_found",
                    error_code="capability_not_found",
                    trace_id=trace_id,
                    browser_diagnostic=BrowserGatewayDiagnostic(
                        "publication", "capability_unavailable"
                    ),
                )
            if capability_spec.version != registered.capability_version:
                if oa_read:
                    _record_chat_stage(trace_id, "oa_result", "blocked",
                                       reason="capability_candidate_stale",
                                       capability_id=capability_id)
                return ExecutionResult(
                    status="failed",
                    error_code="capability_candidate_stale",
                    trace_id=trace_id,
                    browser_diagnostic=BrowserGatewayDiagnostic("publication", "publication_stale"),
                )
            if (
                scope is None
                or capability_spec is None
                or capability_spec.version != registered.capability_version
                or capability_spec.target_system != "oa"
                or capability_spec.type
                != ("action" if registered.effect == "fill_stop" else "query")
                or capability_spec.status != "active"
                or not capability_spec.binding_required
                or capability_spec.execution_identity != "user_delegated"
                or scope.verified.principal.org_ctx.tenant_id != registered.tenant_id
                or scope.verified.principal.ai_user_id != registered.user_id
                or ai_user_id != registered.user_id
                or session_id != scope.bound_session
                or not scope.matches(scope.verified.principal, session_id)
                or scope.attempt.task_id != task_id
                or scope.attempt.trace_id != trace_id
            ):
                if oa_read:
                    _record_chat_stage(trace_id, "oa_result", "blocked", reason="policy_denied",
                                       capability_id=capability_id)
                return ExecutionResult(
                    status="denied",
                    error_code="policy_denied",
                    trace_id=trace_id,
                    browser_diagnostic=BrowserGatewayDiagnostic("context", "context_mismatch"),
                )
            browser_invocation = BrowserGatewayInvocation(
                scope,
                capability_id,
                capability_spec.version,
                task_id,
                trace_id,
            )
        if self._capability_registry is not None:
            if capability_spec is None:
                await self._record_step(
                    trace_id,
                    task_id,
                    session_id,
                    request_context.tenant_id,
                    ai_user_id,
                    "no_capability_found",
                    "blocked",
                    capability_id=capability_id,
                    error_code="capability_not_found",
                )
                if oa_read:
                    _record_chat_stage(trace_id, "oa_result", "blocked",
                                       reason="capability_not_found",
                                       capability_id=capability_id)
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
                        allow_unbound=(capability_id in self._unbound_task_capability_ids),
                    )
                except (ValueError, VersionBindingMismatchError):
                    if self._trace_port is not None:
                        await self._trace_port.record_gateway_call(
                            trace_id=trace_id,
                            task_id=task_id,
                            session_id=session_id,
                            tenant_id=request_context.tenant_id,
                            ai_user_id=ai_user_id,
                            status="failed",
                            capability_id=capability_id,
                            error_code="internal_error",
                        )
                    if oa_read:
                        _record_chat_stage(trace_id, "oa_result", "blocked",
                                           reason="internal_error",
                                           capability_id=capability_id)
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
                    request_context.tenant_id,
                    ai_user_id,
                    capability_id,
                    caller_diagnostics,
                )

        if capability_spec is not None and capability_spec.target_system == "business_platform":
            route_started = monotonic()
            route_profile = None
            route_tool = ""
            try:
                if self._mcp_contexts is None or capability_spec.status != "active":
                    raise McpFailure("mcp_authorization_invalid")
                mapping = await self._mcp_contexts.store.mapping(
                    capability_id, capability_spec.version
                )
                if mapping is None or capability_spec.type == "workflow":
                    raise McpFailure("mcp_workflow_authorization_invalid")
                if mapping.internal_only and workflow_authorization is None:
                    raise McpFailure("mcp_workflow_authorization_invalid")
                service_config_id = mapping.service_config_id
                route_profile = self._mcp_profiles.get(service_config_id)
                route_tool = mapping.remote_tool
                if self._mcp_scope_allowed is not None and not self._mcp_scope_allowed(
                    request_context.tenant_id,
                    ai_user_id,
                    capability_id,
                    service_config_id,
                    mapping.remote_tool,
                ):
                    raise McpFailure("mcp_authorization_invalid")
                mcp_context = await self._mcp_contexts.build(
                    task_id=task_id,
                    chat_session_id=session_id,
                    user_id=ai_user_id,
                    tenant_id=request_context.tenant_id,
                    mapping=mapping,
                    authorization=workflow_authorization,
                )
                if mapping.internal_only:
                    assert workflow_authorization is not None
                    await self._mcp_contexts.verify_write(
                        mcp_context, workflow_authorization, arguments
                    )
                    request_context = GovernedPolicyContext(
                        **request_context.model_dump(),
                        operation_id=workflow_authorization.operation_id,
                        leaf_capability_id=capability_id,
                        attempt_id=workflow_authorization.attempt_id,
                    )
            except McpFailure as exc:
                identity_code = {
                    "mcp_identity_unbound": "identity_unbound",
                    "mcp_identity_pending": "identity_unbound",
                    "mcp_identity_expired": "identity_expired",
                    "mcp_identity_revoked": "identity_revoked",
                }.get(exc.code)
                route_error = cast(ErrorCode, identity_code or "policy_denied")
                for event in (
                    ("identity_check", "blocked_by_identity")
                    if identity_code
                    else ("policy_checked", "blocked_by_policy")
                ):
                    await self._record_step(
                        trace_id,
                        task_id,
                        session_id,
                        request_context.tenant_id,
                        ai_user_id,
                        event,
                        "blocked",
                        capability_id=capability_id,
                        error_code=route_error,
                    )
                record_mcp_event(
                    "gateway_route",
                    "failed",
                    profile=route_profile,
                    tool=route_tool,
                    task_id=task_id,
                    trace_id=trace_id,
                    error_code=route_error,
                    started_at=route_started,
                )
                if oa_read:
                    _record_chat_stage(trace_id, "oa_result", "blocked", reason=route_error,
                                       capability_id=capability_id)
                return ExecutionResult(
                    status="binding_required" if identity_code else "denied",
                    error_code=route_error,
                    trace_id=trace_id,
                )
            record_mcp_event(
                "gateway_route",
                "success",
                profile=route_profile,
                tool=route_tool,
                task_id=task_id,
                trace_id=trace_id,
                started_at=route_started,
            )

        # Registered D uses its admission's live HTTP-session and browser-binding
        # checks; enabling that binding deliberately clears the old OA cookie session.
        d_form_identity_via_admission = (
            browser_invocation is not None
            and browser_adapter is not None
            and capability_id == "browser.form.fill_stop"
            and self._browser_dispatch is not None
            and self._browser_dispatch[0].source_kind == "registered_site"
            and self._browser_dispatch[0].effect == "fill_stop"
        )
        if (
            capability_spec is not None
            and capability_spec.target_system is not None
            and self._identity_mapping is not None
            and not d_form_identity_via_admission
        ):
            if oa_read:
                _record_chat_stage(trace_id, "identity", "start", capability_id=capability_id)
            identity_result = await self._identity_mapping.resolve_execution_identity(
                ai_user_id=ai_user_id,
                target_system=capability_spec.target_system,
                execution_identity=capability_spec.execution_identity,
                request_context=request_context,
                **(
                    {"service_config_id": service_config_id}
                    if service_config_id is not None
                    else {}
                ),
            )
            identity_result_matches_request = (
                identity_result.target_system == capability_spec.target_system
                and identity_result.execution_identity == capability_spec.execution_identity
            )
            if identity_result.bind_status != "active" or not identity_result_matches_request:
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
                    request_context.tenant_id,
                    ai_user_id,
                    "identity_check",
                    "blocked",
                    capability_id=capability_id,
                    error_code=error_code,
                )
                await self._record_step(
                    trace_id,
                    task_id,
                    session_id,
                    request_context.tenant_id,
                    ai_user_id,
                    "blocked_by_identity",
                    "blocked",
                    capability_id=capability_id,
                    error_code=error_code,
                )
                # binding_required: designated ExecutionStatus for identity failures
                if oa_read:
                    _record_chat_stage(trace_id, "oa_result", "blocked", reason=error_code,
                                       capability_id=capability_id)
                return ExecutionResult(
                    status="binding_required",
                    error_code=error_code,
                    trace_id=trace_id,
                    browser_diagnostic=(
                        BrowserGatewayDiagnostic(
                            "binding",
                            "identity_expired"
                            if error_code == "identity_expired"
                            else "identity_revoked"
                            if error_code == "identity_revoked"
                            else "identity_unbound"
                            if error_code == "identity_unbound"
                            else "context_mismatch",
                        )
                        if browser_invocation is not None
                        else None
                    ),
                )
            await self._record_step(
                trace_id,
                task_id,
                session_id,
                request_context.tenant_id,
                ai_user_id,
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
                    request_context.tenant_id,
                    ai_user_id,
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
                    request_context.tenant_id,
                    ai_user_id,
                    "policy_checked",
                    "blocked",
                    capability_id=capability_id,
                    error_code="policy_denied",
                )
                await self._record_step(
                    trace_id,
                    task_id,
                    session_id,
                    request_context.tenant_id,
                    ai_user_id,
                    "blocked_by_policy",
                    "blocked",
                    capability_id=capability_id,
                    error_code="policy_denied",
                )
                if oa_read:
                    _record_chat_stage(trace_id, "oa_result", "blocked", reason="policy_denied",
                                       capability_id=capability_id)
                return ExecutionResult(
                    status="denied",
                    error_code="policy_denied",
                    trace_id=trace_id,
                    browser_diagnostic=BrowserGatewayDiagnostic("binding", "policy_denied")
                    if browser_invocation is not None
                    else None,
                )
            if policy_decision.decision == "confirm" and not (
                browser_invocation is not None
                and self._browser_dispatch is not None
                and self._browser_dispatch[0].effect == "fill_stop"
            ):
                await self._record_step(
                    trace_id,
                    task_id,
                    session_id,
                    request_context.tenant_id,
                    ai_user_id,
                    "policy_checked",
                    "blocked",
                    capability_id=capability_id,
                    error_code="confirm_required",
                )
                await self._record_step(
                    trace_id,
                    task_id,
                    session_id,
                    request_context.tenant_id,
                    ai_user_id,
                    "confirm_required",
                    "blocked",
                    capability_id=capability_id,
                    error_code="confirm_required",
                )
                if oa_read:
                    _record_chat_stage(trace_id, "oa_result", "blocked", reason="confirm_required",
                                       capability_id=capability_id)
                return ExecutionResult(
                    status="waiting_user",
                    error_code="confirm_required",
                    trace_id=trace_id,
                )
            await self._record_step(
                trace_id,
                task_id,
                session_id,
                request_context.tenant_id,
                ai_user_id,
                "policy_checked",
                "blocked" if policy_decision.decision == "confirm" else "ok",
                capability_id=capability_id,
                error_code="confirm_required" if policy_decision.decision == "confirm" else None,
            )

        if self._adapters is not None:
            target = capability_spec.target_system if capability_spec is not None else None
            if target is None or target not in self._adapters:
                await self._record_step(
                    trace_id,
                    task_id,
                    session_id,
                    request_context.tenant_id,
                    ai_user_id,
                    "no_capability_found",
                    "blocked",
                    capability_id=capability_id,
                    error_code="capability_not_found",
                )
                if oa_read:
                    _record_chat_stage(trace_id, "oa_result", "blocked",
                                       reason="capability_not_found",
                                       capability_id=capability_id)
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
                    request_context.tenant_id,
                    ai_user_id,
                    "no_capability_found",
                    "blocked",
                    capability_id=capability_id,
                    error_code="capability_not_found",
                )
                if oa_read:
                    _record_chat_stage(trace_id, "oa_result", "blocked",
                                       reason="capability_not_found",
                                       capability_id=capability_id)
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
                tenant_id=request_context.tenant_id,
                ai_user_id=ai_user_id,
                status="ok",
                capability_id=capability_id,
                error_code=None,
            )

        if browser_adapter is not None:
            selected_adapter = browser_adapter
        execution_context: dict[str, Any] = {}
        if oa_read:
            execution_context["trace_id"] = trace_id
            _record_chat_stage(trace_id, "oa_route", "ok", reason="ready",
                               capability_id=capability_id)
            if (capability_spec is None or capability_spec.target_system is None
                    or self._identity_mapping is None):
                _record_chat_stage(trace_id, "identity", "skipped", reason="not_configured",
                                   capability_id=capability_id)
        if browser_invocation is not None:
            execution_context["browser_invocation"] = browser_invocation
        if mcp_context is not None:
            execution_context["mcp_authorization"] = mcp_context
            execution_context["trace_id"] = trace_id
            execution_context["workflow_authorization"] = workflow_authorization
        if credential_ref is not None:
            execution_context["credential_ref"] = credential_ref
        if "mock_error_mode" in arguments:
            execution_context["mock_error_mode"] = arguments["mock_error_mode"]

        adapter_result = await selected_adapter.execute(
            capability_id,
            arguments,
            execution_context,
        )
        if adapter_result.status == "accepted" and (
            browser_invocation is None
            or type(adapter_result.browser_admission) is not BrowserGatewayAdmission
            or adapter_result.browser_admission.owner != browser_invocation.owner
        ):
            adapter_result = AdapterResult(
                status="permission_denied",
                error_code="policy_denied",
                browser_diagnostic=BrowserGatewayDiagnostic("context", "context_mismatch"),
            )

        result = ExecutionResult(
            status=_map_adapter_status(adapter_result),
            data=adapter_result.data,
            error_code=adapter_result.error_code,
            trace_id=trace_id,
            mcp_outcome=adapter_result.mcp_outcome,
            mcp_read=adapter_result.mcp_read,
            browser_admission=adapter_result.browser_admission,
            browser_diagnostic=adapter_result.browser_diagnostic,
        )
        trace_attributes = (
            adapter_result.trace_metadata.model_dump(mode="json")
            if adapter_result.trace_metadata is not None
            else {}
        )
        if result.browser_diagnostic is not None:
            trace_attributes.update(result.browser_diagnostic.attributes())
        await self._record_step(
            trace_id,
            task_id,
            session_id,
            request_context.tenant_id,
            ai_user_id,
            "adapter_called",
            "ok",
            capability_id=capability_id,
            error_code=result.error_code,
            attributes=trace_attributes or None,
        )
        await self._record_step(
            trace_id,
            task_id,
            session_id,
            request_context.tenant_id,
            ai_user_id,
            "gateway_post_recorded",
            _map_execution_to_trace_status(result.status),
            capability_id=capability_id,
            error_code=result.error_code,
        )
        if result.status not in {"completed", "running"}:
            await self._record_step(
                trace_id,
                task_id,
                session_id,
                request_context.tenant_id,
                ai_user_id,
                "adapter_error_mapped",
                "failed",
                capability_id=capability_id,
                error_code=result.error_code,
            )

        if oa_read:
            _record_chat_stage(trace_id, "oa_result",
                               "ok" if result.status == "completed" else "failed",
                               reason=result.error_code or result.status,
                               capability_id=capability_id)
        return result

    async def _reject_invalid_arguments(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
        tenant_id: str,
        ai_user_id: str,
        capability_id: str,
        diagnostics: dict[str, Any],
    ) -> ExecutionResult:
        browser = (
            self._browser_dispatch is not None
            and capability_id == self._browser_dispatch[0].capability_id
        )
        code: ErrorCode = "adapter_payload_invalid" if browser else "adapter_error"
        if capability_id in _CHAT_STAGE_CAPABILITIES:
            _record_chat_stage(trace_id, "oa_result", "blocked", reason=code,
                               capability_id=capability_id)
        if self._trace_port is not None:
            await self._trace_port.record_gateway_call(
                trace_id=trace_id,
                task_id=task_id,
                session_id=session_id,
                tenant_id=tenant_id,
                ai_user_id=ai_user_id,
                status="failed",
                capability_id=capability_id,
                error_code=code,
                attributes=diagnostics,
            )
        return ExecutionResult(
            status="failed",
            error_code=code,
            trace_id=trace_id,
            browser_diagnostic=BrowserGatewayDiagnostic("input", "input_invalid")
            if browser
            else None,
        )

    async def _record_step(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
        tenant_id: str,
        ai_user_id: str,
        event_type: TraceEventType,
        status: TraceEventStatus,
        capability_id: str | None = None,
        error_code: ErrorCode | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        if capability_id in _CHAT_STAGE_CAPABILITIES and event_type == "identity_check":
            _record_chat_stage(trace_id, "identity", status, reason=error_code or "ready",
                               capability_id=capability_id)
        if self._trace_port is None:
            return
        await self._trace_port.record_step(
            trace_id=trace_id,
            task_id=task_id,
            session_id=session_id,
            tenant_id=tenant_id,
            ai_user_id=ai_user_id,
            event_type=event_type,
            status=status,
            capability_id=capability_id,
            error_code=error_code,
            attributes=attributes,
        )


def _map_execution_to_trace_status(status: ExecutionStatus) -> TraceEventStatus:
    if status in {"completed", "running"}:
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
