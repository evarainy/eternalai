"""Integration tests for the Phase 0 capability gateway skeleton."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.execution_fabric.mock_adapters.hikvision_ivms.mock_hikvision_ivms_adapter import (
    MockHikvisionIVMSAdapter,
)
from app.execution_fabric.mock_adapters.oa.mock_oa_adapter import MockOAAdapter
from app.execution_fabric.mock_adapters.u8.mock_u8_adapter import MockU8Adapter
from app.infra.gateway.capability_gateway import CapabilityGateway
from app.ports.adapter import AdapterResult
from app.ports.capability_gateway import ExecutionResult, RequestOrgContext
from app.ports.capability_registry import CapabilitySpec
from app.ports.identity_mapping import IdentityCheckResult
from app.ports.policy_guard import PolicyDecision


def _execute_gateway(
    arguments: dict[str, Any],
    request_id: str = "trace-001",
) -> ExecutionResult:
    gateway = CapabilityGateway(MockOAAdapter())
    request_context = RequestOrgContext(request_id=request_id)

    return asyncio.run(
        gateway.execute_capability(
            "task-001",
            "session-001",
            "ai-user-001",
            "oa.workflow_status.get",
            arguments,
            request_context,
        )
    )


def test_happy_path_returns_execution_result_with_adapter_data_and_trace_id() -> None:
    result = _execute_gateway({"mock_current_step": "manager_review"})

    assert isinstance(result, ExecutionResult)
    assert result.status == "completed"
    assert result.data == {
        "workflow_id": "wf-mock-001",
        "current_step": "manager_review",
        "approver": "mock-approver",
    }
    assert result.error_code is None
    assert result.trace_id == "trace-001"


def test_trace_id_accepts_arbitrary_request_id_without_generation_or_format_lock() -> None:
    result = _execute_gateway({}, request_id="arbitrary-trace-xyz-123")

    assert isinstance(result, ExecutionResult)
    assert result.trace_id == "arbitrary-trace-xyz-123"


@pytest.mark.parametrize(
    ("mock_error_mode", "expected_status", "expected_error_code"),
    (
        ("timeout", "timeout", "adapter_timeout"),
        ("permission_denied", "denied", "upstream_permission_denied"),
        ("malformed_json", "failed", "adapter_payload_invalid"),
        ("empty_response", "failed", "adapter_empty_response"),
        ("http_500", "failed", "adapter_http_500"),
        ("missing_required_field", "failed", "adapter_missing_required_field"),
    ),
)
def test_error_modes_return_execution_result_with_mapped_status_and_error_code(
    mock_error_mode: str,
    expected_status: str,
    expected_error_code: str,
) -> None:
    result = _execute_gateway(
        {"mock_error_mode": mock_error_mode},
        request_id="trace-error-001",
    )

    assert isinstance(result, ExecutionResult)
    assert result.status == expected_status
    assert result.error_code == expected_error_code
    assert result.trace_id == "trace-error-001"
    assert result.data is None


def _capability_spec(
    target_system: str | None = "oa",
    *,
    input_schema: dict[str, Any] | None = None,
) -> CapabilitySpec:
    return CapabilitySpec(
        capability_id="oa.workflow_status.get",
        name="Workflow Status",
        type="query",
        input_schema=input_schema or {},
        input_schema_digest="input-digest",
        output_schema_digest="output-digest",
        risk_level="low",
        owner="phase0",
        version="0.1.0",
        status="active",
        short_description="Mock workflow status query",
        target_system=target_system,
        execution_identity="user_delegated",
        binding_required=target_system is not None,
    )


def _capability_spec_for_system(target_system: str) -> CapabilitySpec:
    return CapabilitySpec(
        capability_id=f"{target_system}.mock.op",
        name=f"Mock {target_system} op",
        type="query",
        input_schema_digest="input-digest",
        output_schema_digest="output-digest",
        risk_level="low",
        owner="phase0",
        version="0.1.0",
        status="active",
        short_description=f"Mock {target_system} operation",
        target_system=target_system,
        execution_identity="user_delegated",
        binding_required=True,
    )


def _identity_result(
    bind_status: str,
    *,
    binding_id: str | None = None,
    target_system: str = "oa",
    execution_identity: str = "user_delegated",
) -> IdentityCheckResult:
    return IdentityCheckResult(
        bind_status=bind_status,
        binding_id=binding_id,
        target_system=target_system,
        execution_identity=execution_identity,
    )


class FakeRegistry:
    def __init__(self, capability: CapabilitySpec | None) -> None:
        self._capability = capability
        self.call_count = 0
        self.last_capability_id: str | None = None

    async def get(self, capability_id: str) -> CapabilitySpec | None:
        self.call_count += 1
        self.last_capability_id = capability_id
        return self._capability


class FakeIdentityMapping:
    def __init__(self, result: IdentityCheckResult) -> None:
        self._result = result
        self.call_count = 0
        self.last_call: dict[str, Any] | None = None

    async def resolve_execution_identity(
        self,
        ai_user_id: str,
        target_system: str,
        execution_identity: str,
        request_context: RequestOrgContext,
    ) -> IdentityCheckResult:
        self.call_count += 1
        self.last_call = {
            "ai_user_id": ai_user_id,
            "target_system": target_system,
            "execution_identity": execution_identity,
            "request_context": request_context,
        }
        return self._result


class FakePolicyGuard:
    def __init__(self, decision: PolicyDecision) -> None:
        self._decision = decision
        self.call_count = 0
        self.last_call: dict[str, Any] | None = None

    async def decide(
        self,
        ai_user_id: str,
        capability_id: str,
        arguments: dict[str, Any],
        request_context: RequestOrgContext,
    ) -> PolicyDecision:
        self.call_count += 1
        self.last_call = {
            "ai_user_id": ai_user_id,
            "capability_id": capability_id,
            "arguments": arguments,
            "request_context": request_context,
        }
        return self._decision


class FakeTrace:
    def __init__(self, call_log: list[str] | None = None) -> None:
        self._call_log = call_log
        self.steps: list[dict[str, Any]] = []
        self.record_gateway_call_count = 0
        self.finalize_task_trace_count = 0
        self.record_gateway_call_kwargs: dict[str, Any] | None = None
        self.finalize_task_trace_kwargs: dict[str, Any] | None = None

    async def record_step(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
        event_type: str,
        status: str,
        capability_id: str | None = None,
        error_code: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        self.steps.append(
            {
                "trace_id": trace_id,
                "task_id": task_id,
                "session_id": session_id,
                "event_type": event_type,
                "status": status,
                "capability_id": capability_id,
                "error_code": error_code,
                "attributes": attributes or {},
            }
        )
        if self._call_log is not None:
            self._call_log.append(f"record_step:{event_type}")

    async def record_gateway_call(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
        status: str,
        capability_id: str | None = None,
        error_code: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        self.record_gateway_call_count += 1
        self.record_gateway_call_kwargs = {
            "trace_id": trace_id,
            "task_id": task_id,
            "session_id": session_id,
            "status": status,
            "capability_id": capability_id,
            "error_code": error_code,
            "attributes": attributes,
        }
        self.steps.append(
            {
                "trace_id": trace_id,
                "task_id": task_id,
                "session_id": session_id,
                "event_type": "gateway_pre_recorded",
                "status": status,
                "capability_id": capability_id,
                "error_code": error_code,
                "attributes": attributes or {},
            }
        )
        if self._call_log is not None:
            self._call_log.append("record_gateway_call")

    async def finalize_task_trace(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
        status: str,
        capability_id: str | None = None,
        error_code: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        self.finalize_task_trace_count += 1
        self.finalize_task_trace_kwargs = {
            "trace_id": trace_id,
            "task_id": task_id,
            "session_id": session_id,
            "status": status,
            "capability_id": capability_id,
            "error_code": error_code,
            "attributes": attributes,
        }


class SentinelAdapter:
    def __init__(self) -> None:
        self.call_count = 0

    async def execute(
        self,
        capability_id: str,
        arguments: dict[str, Any],
        execution_context: dict[str, Any],
    ) -> AdapterResult:
        self.call_count += 1
        raise AssertionError("adapter must not be called on short-circuit path")


class FakeAdapter:
    def __init__(
        self,
        result: AdapterResult | None = None,
        call_log: list[str] | None = None,
    ) -> None:
        self._result = result or AdapterResult(
            status="success",
            data={},
            error_code=None,
        )
        self._call_log = call_log
        self.call_count = 0
        self.last_execution_context: dict[str, Any] | None = None

    async def execute(
        self,
        capability_id: str,
        arguments: dict[str, Any],
        execution_context: dict[str, Any],
    ) -> AdapterResult:
        self.call_count += 1
        self.last_execution_context = dict(execution_context)
        if self._call_log is not None:
            self._call_log.append("adapter")
        return self._result


def _execute_gateway_with_ports(
    gateway: CapabilityGateway,
    arguments: dict[str, Any] | None = None,
) -> ExecutionResult:
    request_context = RequestOrgContext(request_id="trace-short-001")

    return asyncio.run(
        gateway.execute_capability(
            "task-001",
            "session-001",
            "ai-user-001",
            "oa.workflow_status.get",
            arguments or {},
            request_context,
        )
    )


def _assert_trace_finalized(
    trace: FakeTrace,
    status: str,
    error_code: str | None,
) -> None:
    assert trace.finalize_task_trace_count == 1
    assert trace.finalize_task_trace_kwargs is not None
    assert trace.finalize_task_trace_kwargs["trace_id"] == "trace-short-001"
    assert trace.finalize_task_trace_kwargs["task_id"] == "task-001"
    assert trace.finalize_task_trace_kwargs["session_id"] == "session-001"
    assert trace.finalize_task_trace_kwargs["status"] == status
    assert trace.finalize_task_trace_kwargs["error_code"] == error_code
    attributes = trace.finalize_task_trace_kwargs.get("attributes") or {}
    assert "arguments" not in attributes


def _assert_trace_not_finalized(trace: FakeTrace) -> None:
    assert trace.finalize_task_trace_count == 0
    assert trace.finalize_task_trace_kwargs is None


def _assert_step_events(trace: FakeTrace, events: list[str]) -> None:
    assert [step["event_type"] for step in trace.steps] == events
    for step in trace.steps:
        assert step["trace_id"] == "trace-short-001"
        assert step["task_id"] == "task-001"
        assert step["session_id"] == "session-001"
        attributes = step.get("attributes") or {}
        assert "arguments" not in attributes


def test_capability_registry_missing_short_circuits_without_adapter_and_records_trace() -> None:
    registry = FakeRegistry(None)
    trace = FakeTrace()
    adapter = SentinelAdapter()
    gateway = CapabilityGateway(adapter, registry, trace_port=trace)

    result = _execute_gateway_with_ports(gateway)

    assert result.status == "no_capability_found"
    assert result.error_code == "capability_not_found"
    assert result.trace_id == "trace-short-001"
    assert adapter.call_count == 0
    assert trace.record_gateway_call_count == 0
    _assert_step_events(trace, ["no_capability_found"])
    _assert_trace_not_finalized(trace)


def test_registry_input_schema_rejects_extra_argument_before_identity_and_policy(
    caplog: pytest.LogCaptureFixture,
) -> None:
    canary = "must-not-enter-trace-log-or-result"
    registry = FakeRegistry(
        _capability_spec(
            input_schema={
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            }
        )
    )
    identity_mapping = FakeIdentityMapping(_identity_result("active"))
    policy_guard = FakePolicyGuard(PolicyDecision(decision="allow"))
    adapter = SentinelAdapter()
    trace = FakeTrace()
    gateway = CapabilityGateway(
        adapter,
        registry,
        identity_mapping,
        policy_guard,
        trace,
    )

    result = _execute_gateway_with_ports(gateway, {"user": canary})

    assert result.status == "failed"
    assert result.error_code == "adapter_error"
    assert result.data is None
    assert registry.call_count == 1
    # An extra property is decided by the caller's payload alone, so identity is
    # never consulted: otherwise the two different error codes would leak whether
    # the caller holds a binding.
    assert identity_mapping.call_count == 0
    assert policy_guard.call_count == 0
    assert adapter.call_count == 0
    assert trace.record_gateway_call_count == 1
    _assert_step_events(trace, ["gateway_pre_recorded"])
    assert trace.steps[-1]["attributes"] == {
        "error_path": "$.arguments",
        "error_type": "additionalProperties",
        "argument_keys": ["user"],
    }
    serialized = repr((result, trace.steps))
    assert canary not in serialized
    assert canary not in caplog.text


def test_registry_input_schema_defers_missing_required_argument_until_identity() -> None:
    registry = FakeRegistry(
        _capability_spec(
            input_schema={
                "type": "object",
                "properties": {"account_set_id": {"type": "string"}},
                "required": ["account_set_id"],
                "additionalProperties": False,
            }
        )
    )
    identity_mapping = FakeIdentityMapping(_identity_result("active"))
    policy_guard = FakePolicyGuard(PolicyDecision(decision="allow"))
    adapter = SentinelAdapter()
    trace = FakeTrace()
    gateway = CapabilityGateway(
        adapter,
        registry,
        identity_mapping,
        policy_guard,
        trace,
    )

    result = _execute_gateway_with_ports(gateway, {})

    assert result.status == "failed"
    assert result.error_code == "adapter_error"
    # GT-012 depends on this ordering: a binding can be what supplies
    # account_set_id, so `required` must not preempt the identity answer.
    assert identity_mapping.call_count == 1
    assert policy_guard.call_count == 0
    assert adapter.call_count == 0
    _assert_step_events(trace, ["identity_check", "gateway_pre_recorded"])
    assert trace.steps[-1]["attributes"] == {
        "error_path": "$.arguments",
        "error_type": "required",
        "argument_keys": [],
    }


@pytest.mark.parametrize("bind_status", ["active", "unbound"])
def test_invalid_argument_answer_is_identical_with_and_without_a_binding(
    bind_status: str,
) -> None:
    registry = FakeRegistry(
        _capability_spec(
            input_schema={
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            }
        )
    )
    identity_mapping = FakeIdentityMapping(_identity_result(bind_status))
    policy_guard = FakePolicyGuard(PolicyDecision(decision="allow"))
    adapter = SentinelAdapter()
    trace = FakeTrace()
    gateway = CapabilityGateway(
        adapter,
        registry,
        identity_mapping,
        policy_guard,
        trace,
    )

    result = _execute_gateway_with_ports(gateway, {"user": "probe"})

    # Same illegal payload, both binding states: one answer, so nothing about the
    # caller's binding scope can be read off the reply.
    assert result.status == "failed"
    assert result.error_code == "adapter_error"
    assert identity_mapping.call_count == 0
    assert adapter.call_count == 0
    _assert_step_events(trace, ["gateway_pre_recorded"])
    assert trace.steps[-1]["error_code"] == "adapter_error"
    assert trace.steps[-1]["attributes"] == {
        "error_path": "$.arguments",
        "error_type": "additionalProperties",
        "argument_keys": ["user"],
    }


def test_registry_input_schema_accepts_empty_arguments_through_existing_path() -> None:
    registry = FakeRegistry(
        _capability_spec(
            target_system=None,
            input_schema={
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        )
    )
    policy_guard = FakePolicyGuard(PolicyDecision(decision="allow"))
    adapter = FakeAdapter()
    trace = FakeTrace()
    gateway = CapabilityGateway(
        adapter,
        registry,
        policy_guard=policy_guard,
        trace_port=trace,
    )

    result = _execute_gateway_with_ports(gateway, {})

    assert result.status == "completed"
    assert result.error_code is None
    assert policy_guard.call_count == 1
    assert adapter.call_count == 1
    assert trace.record_gateway_call_count == 1
    _assert_step_events(
        trace,
        ["policy_checked", "gateway_pre_recorded", "adapter_called", "gateway_post_recorded"],
    )


def test_identity_unbound_short_circuits_without_adapter_and_records_trace() -> None:
    registry = FakeRegistry(_capability_spec())
    identity_mapping = FakeIdentityMapping(_identity_result("unbound"))
    policy_guard = FakePolicyGuard(PolicyDecision(decision="allow"))
    trace = FakeTrace()
    adapter = SentinelAdapter()
    gateway = CapabilityGateway(
        adapter,
        registry,
        identity_mapping,
        policy_guard,
        trace,
    )

    result = _execute_gateway_with_ports(gateway)

    assert result.status == "binding_required"
    assert result.error_code == "identity_unbound"
    assert adapter.call_count == 0
    assert trace.record_gateway_call_count == 0
    _assert_step_events(trace, ["identity_check", "blocked_by_identity"])
    _assert_trace_not_finalized(trace)
    assert policy_guard.call_count == 0


def test_identity_expired_short_circuits_without_adapter_and_records_trace() -> None:
    registry = FakeRegistry(_capability_spec())
    identity_mapping = FakeIdentityMapping(_identity_result("expired"))
    policy_guard = FakePolicyGuard(PolicyDecision(decision="allow"))
    trace = FakeTrace()
    adapter = SentinelAdapter()
    gateway = CapabilityGateway(
        adapter,
        registry,
        identity_mapping,
        policy_guard,
        trace,
    )

    result = _execute_gateway_with_ports(gateway)

    assert result.status == "binding_required"
    assert result.error_code == "identity_expired"
    assert adapter.call_count == 0
    assert trace.record_gateway_call_count == 0
    _assert_step_events(trace, ["identity_check", "blocked_by_identity"])
    _assert_trace_not_finalized(trace)
    assert policy_guard.call_count == 0


def test_identity_revoked_short_circuits_without_adapter_and_records_trace() -> None:
    registry = FakeRegistry(_capability_spec())
    identity_mapping = FakeIdentityMapping(_identity_result("revoked"))
    policy_guard = FakePolicyGuard(PolicyDecision(decision="allow"))
    trace = FakeTrace()
    adapter = SentinelAdapter()
    gateway = CapabilityGateway(
        adapter,
        registry,
        identity_mapping,
        policy_guard,
        trace,
    )

    result = _execute_gateway_with_ports(gateway)

    assert result.status == "binding_required"
    assert result.error_code == "identity_revoked"
    assert adapter.call_count == 0
    assert trace.record_gateway_call_count == 0
    _assert_step_events(trace, ["identity_check", "blocked_by_identity"])
    _assert_trace_not_finalized(trace)
    assert policy_guard.call_count == 0


def test_identity_needs_binding_scope_short_circuits_without_adapter_and_records_trace(
) -> None:
    registry = FakeRegistry(_capability_spec())
    identity_mapping = FakeIdentityMapping(_identity_result("needs_binding_scope"))
    policy_guard = FakePolicyGuard(PolicyDecision(decision="allow"))
    trace = FakeTrace()
    adapter = SentinelAdapter()
    gateway = CapabilityGateway(
        adapter,
        registry,
        identity_mapping,
        policy_guard,
        trace,
    )

    result = _execute_gateway_with_ports(gateway)

    assert result.status == "binding_required"
    assert result.error_code == "needs_binding_scope"
    assert adapter.call_count == 0
    assert trace.record_gateway_call_count == 0
    _assert_step_events(trace, ["identity_check", "blocked_by_identity"])
    _assert_trace_not_finalized(trace)
    assert policy_guard.call_count == 0


def test_identity_verification_failed_maps_to_identity_unbound_and_records_trace() -> None:
    registry = FakeRegistry(_capability_spec())
    identity_mapping = FakeIdentityMapping(_identity_result("verification_failed"))
    policy_guard = FakePolicyGuard(PolicyDecision(decision="allow"))
    trace = FakeTrace()
    adapter = SentinelAdapter()
    gateway = CapabilityGateway(
        adapter,
        registry,
        identity_mapping,
        policy_guard,
        trace,
    )

    result = _execute_gateway_with_ports(gateway)

    assert result.status == "binding_required"
    assert result.error_code == "identity_unbound"
    assert adapter.call_count == 0
    assert trace.record_gateway_call_count == 0
    _assert_step_events(trace, ["identity_check", "blocked_by_identity"])
    _assert_trace_not_finalized(trace)
    assert policy_guard.call_count == 0


def test_policy_deny_short_circuits_without_adapter_and_records_trace() -> None:
    registry = FakeRegistry(_capability_spec())
    identity_mapping = FakeIdentityMapping(_identity_result("active"))
    policy_guard = FakePolicyGuard(PolicyDecision(decision="deny"))
    trace = FakeTrace()
    adapter = SentinelAdapter()
    gateway = CapabilityGateway(
        adapter,
        registry,
        identity_mapping,
        policy_guard,
        trace,
    )

    result = _execute_gateway_with_ports(gateway)

    assert result.status == "denied"
    assert result.error_code == "policy_denied"
    assert adapter.call_count == 0
    assert trace.record_gateway_call_count == 0
    _assert_step_events(
        trace,
        ["identity_check", "policy_checked", "blocked_by_policy"],
    )
    _assert_trace_not_finalized(trace)


def test_policy_confirm_short_circuits_without_adapter_and_records_trace() -> None:
    registry = FakeRegistry(_capability_spec())
    identity_mapping = FakeIdentityMapping(_identity_result("active"))
    policy_guard = FakePolicyGuard(PolicyDecision(decision="confirm"))
    trace = FakeTrace()
    adapter = SentinelAdapter()
    gateway = CapabilityGateway(
        adapter,
        registry,
        identity_mapping,
        policy_guard,
        trace,
    )

    result = _execute_gateway_with_ports(gateway)

    assert result.status == "waiting_user"
    assert result.error_code == "confirm_required"
    assert adapter.call_count == 0
    assert trace.record_gateway_call_count == 0
    _assert_step_events(
        trace,
        ["identity_check", "policy_checked", "confirm_required"],
    )
    _assert_trace_not_finalized(trace)


def test_happy_path_runs_prechecks_and_records_trace_without_task_finalize() -> None:
    call_log: list[str] = []
    registry = FakeRegistry(_capability_spec())
    identity_mapping = FakeIdentityMapping(_identity_result("active"))
    policy_guard = FakePolicyGuard(PolicyDecision(decision="allow"))
    trace = FakeTrace(call_log)
    adapter = FakeAdapter(
        AdapterResult(status="success", data={"k": "v"}, error_code=None),
        call_log,
    )
    gateway = CapabilityGateway(
        adapter,
        registry,
        identity_mapping,
        policy_guard,
        trace,
    )

    result = _execute_gateway_with_ports(gateway)

    assert call_log.index("record_gateway_call") < call_log.index("adapter")
    assert call_log.index("adapter") < call_log.index("record_step:adapter_called")
    assert call_log.index("adapter") < call_log.index("record_step:gateway_post_recorded")
    assert result.status == "completed"
    assert result.error_code is None
    assert result.data == {"k": "v"}
    assert adapter.call_count == 1
    assert trace.record_gateway_call_count == 1
    assert trace.record_gateway_call_kwargs is not None
    assert trace.record_gateway_call_kwargs["status"] == "ok"
    assert trace.record_gateway_call_kwargs["error_code"] is None
    record_attributes = trace.record_gateway_call_kwargs.get("attributes") or {}
    assert "arguments" not in record_attributes
    _assert_trace_not_finalized(trace)


def test_active_user_delegated_oa_binding_injects_only_server_mapping_ref() -> None:
    credential_ref = "oa-session-v1:usr_v1_safe-surrogate"
    registry = FakeRegistry(_capability_spec())
    identity_mapping = FakeIdentityMapping(
        _identity_result("active", binding_id=credential_ref)
    )
    policy_guard = FakePolicyGuard(PolicyDecision(decision="allow"))
    trace = FakeTrace()
    adapter = FakeAdapter()
    gateway = CapabilityGateway(
        adapter,
        registry,
        identity_mapping,
        policy_guard,
        trace,
    )

    result = _execute_gateway_with_ports(gateway)

    assert result.status == "completed"
    assert adapter.last_execution_context == {"credential_ref": credential_ref}
    assert credential_ref not in repr(trace.steps)


@pytest.mark.parametrize(
    ("target_system", "execution_identity"),
    (
        ("u8", "user_delegated"),
        ("oa", "system_scope"),
    ),
)
def test_active_identity_result_with_mismatched_domain_fails_before_policy_or_adapter(
    target_system: str,
    execution_identity: str,
) -> None:
    registry = FakeRegistry(_capability_spec())
    identity_mapping = FakeIdentityMapping(
        _identity_result(
            "active",
            binding_id="oa-session-v1:wrong-domain",
            target_system=target_system,
            execution_identity=execution_identity,
        )
    )
    policy_guard = FakePolicyGuard(PolicyDecision(decision="allow"))
    trace = FakeTrace()
    adapter = SentinelAdapter()
    gateway = CapabilityGateway(
        adapter,
        registry,
        identity_mapping,
        policy_guard,
        trace,
    )

    result = _execute_gateway_with_ports(gateway)

    assert result.status == "binding_required"
    assert result.error_code == "identity_unbound"
    assert policy_guard.call_count == 0
    assert adapter.call_count == 0
    assert trace.record_gateway_call_count == 0
    _assert_step_events(trace, ["identity_check", "blocked_by_identity"])
    assert "wrong-domain" not in repr(trace.steps)


def test_client_credential_ref_argument_is_never_copied_to_execution_context() -> None:
    registry = FakeRegistry(_capability_spec())
    identity_mapping = FakeIdentityMapping(_identity_result("active"))
    adapter = FakeAdapter()
    gateway = CapabilityGateway(
        adapter,
        registry,
        identity_mapping,
        FakePolicyGuard(PolicyDecision(decision="allow")),
        FakeTrace(),
    )

    result = _execute_gateway_with_ports(
        gateway,
        {"credential_ref": "client-supplied-must-be-ignored"},
    )

    assert result.status == "completed"
    assert adapter.last_execution_context == {}


def test_adapter_session_expiry_maps_to_binding_required_reauthentication_path() -> None:
    adapter = FakeAdapter(
        AdapterResult(status="error", data=None, error_code="identity_expired")
    )
    gateway = CapabilityGateway(adapter)

    result = _execute_gateway_with_ports(gateway)

    assert result.status == "binding_required"
    assert result.error_code == "identity_expired"


def test_target_system_none_skips_identity_mapping_and_continues_to_policy_and_adapter() -> None:
    registry = FakeRegistry(_capability_spec(target_system=None))
    identity_mapping = FakeIdentityMapping(_identity_result("active"))
    policy_guard = FakePolicyGuard(PolicyDecision(decision="allow"))
    trace = FakeTrace()
    adapter = FakeAdapter()
    gateway = CapabilityGateway(
        adapter,
        registry,
        identity_mapping,
        policy_guard,
        trace,
    )

    result = _execute_gateway_with_ports(gateway)

    assert identity_mapping.call_count == 0
    assert policy_guard.call_count == 1
    assert adapter.call_count == 1
    assert trace.record_gateway_call_count == 1
    _assert_step_events(
        trace,
        [
            "policy_checked",
            "gateway_pre_recorded",
            "adapter_called",
            "gateway_post_recorded",
        ],
    )
    _assert_trace_not_finalized(trace)
    assert result.status == "completed"


def test_adapters_path_dispatches_oa_target_to_oa_adapter() -> None:
    registry = FakeRegistry(_capability_spec(target_system="oa"))
    oa_adapter = MockOAAdapter()
    u8_sentinel = SentinelAdapter()
    hik_sentinel = SentinelAdapter()
    gateway = CapabilityGateway(
        adapters={"oa": oa_adapter, "u8": u8_sentinel, "hikvision_ivms": hik_sentinel},
        capability_registry=registry,
    )

    result = asyncio.run(
        gateway.execute_capability(
            "task-1",
            "session-1",
            "ai-1",
            "oa.workflow_status.get",
            {},
            RequestOrgContext(request_id="t-oa-1"),
        )
    )

    assert result.status == "completed"
    assert result.error_code is None
    assert result.data is not None
    assert "workflow_id" in result.data
    assert "approver" in result.data


def test_adapters_path_dispatches_u8_target_to_u8_adapter() -> None:
    registry = FakeRegistry(_capability_spec_for_system("u8"))
    oa_sentinel = SentinelAdapter()
    u8_adapter = MockU8Adapter()
    hik_sentinel = SentinelAdapter()
    gateway = CapabilityGateway(
        adapters={"oa": oa_sentinel, "u8": u8_adapter, "hikvision_ivms": hik_sentinel},
        capability_registry=registry,
    )

    result = asyncio.run(
        gateway.execute_capability(
            "task-1",
            "session-1",
            "ai-1",
            "u8.document.get",
            {},
            RequestOrgContext(request_id="t-u8-1"),
        )
    )

    assert result.status == "completed"
    assert result.error_code is None
    assert result.data is not None
    assert "account_set_id" in result.data


def test_adapters_path_dispatches_hikvision_target_to_hikvision_adapter() -> None:
    registry = FakeRegistry(_capability_spec_for_system("hikvision_ivms"))
    oa_sentinel = SentinelAdapter()
    u8_sentinel = SentinelAdapter()
    hik_adapter = MockHikvisionIVMSAdapter()
    gateway = CapabilityGateway(
        adapters={"oa": oa_sentinel, "u8": u8_sentinel, "hikvision_ivms": hik_adapter},
        capability_registry=registry,
    )

    result = asyncio.run(
        gateway.execute_capability(
            "task-1",
            "session-1",
            "ai-1",
            "hik.device.status",
            {},
            RequestOrgContext(request_id="t-hik-1"),
        )
    )

    assert result.status == "completed"
    assert result.error_code is None
    assert result.data is not None
    assert "device_domain_id" in result.data


def test_adapters_path_target_system_none_returns_no_capability_found_without_calling_adapter() -> None:  # noqa: E501
    registry = FakeRegistry(_capability_spec(target_system=None))
    sentinel = SentinelAdapter()
    trace = FakeTrace()
    gateway = CapabilityGateway(
        adapters={"oa": sentinel, "u8": sentinel, "hikvision_ivms": sentinel},
        capability_registry=registry,
        trace_port=trace,
    )

    result = asyncio.run(
        gateway.execute_capability(
            "task-1",
            "session-1",
            "ai-1",
            "cap-1",
            {},
            RequestOrgContext(request_id="t-none-1"),
        )
    )

    assert result.status == "no_capability_found"
    assert result.error_code == "capability_not_found"
    assert result.trace_id == "t-none-1"
    assert trace.record_gateway_call_count == 0
    assert [step["event_type"] for step in trace.steps] == ["no_capability_found"]


def test_adapters_path_calls_only_the_matching_adapter_not_others() -> None:
    registry = FakeRegistry(_capability_spec_for_system("u8"))
    oa_fake = FakeAdapter()
    u8_fake = FakeAdapter(
        AdapterResult(
            status="success",
            data={
                "account_set_id": "x",
                "document_no": "D1",
                "document_status": "posted",
                "amount": 1.0,
                "currency": "CNY",
            },
            error_code=None,
        )
    )
    hik_fake = FakeAdapter()
    gateway = CapabilityGateway(
        adapters={"oa": oa_fake, "u8": u8_fake, "hikvision_ivms": hik_fake},
        capability_registry=registry,
    )

    result = asyncio.run(
        gateway.execute_capability(
            "task-1",
            "session-1",
            "ai-1",
            "u8.doc",
            {},
            RequestOrgContext(request_id="t-only-1"),
        )
    )

    assert u8_fake.call_count == 1
    assert oa_fake.call_count == 0
    assert hik_fake.call_count == 0
    assert result.status == "completed"
    assert result.error_code is None
    assert result.data is not None
    assert "account_set_id" in result.data


@pytest.mark.parametrize(
    ("mock_error_mode", "expected_status", "expected_error_code"),
    (
        ("timeout", "timeout", "adapter_timeout"),
        ("permission_denied", "denied", "upstream_permission_denied"),
        ("malformed_json", "failed", "adapter_payload_invalid"),
        ("empty_response", "failed", "adapter_empty_response"),
        ("http_500", "failed", "adapter_http_500"),
        ("missing_required_field", "failed", "adapter_missing_required_field"),
    ),
)
def test_adapters_path_error_modes_return_mapped_status_and_error_code(
    mock_error_mode: str,
    expected_status: str,
    expected_error_code: str,
) -> None:
    registry = FakeRegistry(_capability_spec_for_system("oa"))
    gateway = CapabilityGateway(
        adapters={
            "oa": MockOAAdapter(),
            "u8": SentinelAdapter(),
            "hikvision_ivms": SentinelAdapter(),
        },
        capability_registry=registry,
    )

    result = asyncio.run(
        gateway.execute_capability(
            "task-1",
            "session-1",
            "ai-1",
            "oa.workflow_status.get",
            {"mock_error_mode": mock_error_mode},
            RequestOrgContext(request_id="t-err-1"),
        )
    )

    assert result.status == expected_status
    assert result.error_code == expected_error_code
    assert result.data is None


def test_gateway_production_code_has_no_mock_adapter_imports() -> None:
    import os

    gateway_dir = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "..",
        "app",
        "infra",
        "gateway",
    )
    gateway_dir = os.path.normpath(gateway_dir)
    forbidden_patterns = [
        "MockOAAdapter",
        "MockU8Adapter",
        "MockHikvisionIVMSAdapter",
    ]
    hits = []

    for fname in os.listdir(gateway_dir):
        if fname.endswith(".py"):
            fpath = os.path.join(gateway_dir, fname)
            with open(fpath) as gateway_file:
                content = gateway_file.read()
            for pat in forbidden_patterns:
                if pat in content:
                    hits.append(f"{fname}: contains {pat}")

    assert not hits, f"Mock adapter imports found in production code: {hits}"
