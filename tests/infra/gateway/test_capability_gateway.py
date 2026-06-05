"""Integration tests for the Phase 0 capability gateway skeleton."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.execution_fabric.mock_adapters.oa.mock_oa_adapter import MockOAAdapter
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


def _capability_spec(target_system: str | None = "oa") -> CapabilitySpec:
    return CapabilitySpec(
        capability_id="oa.workflow_status.get",
        name="Workflow Status",
        type="query",
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


def _identity_result(bind_status: str) -> IdentityCheckResult:
    return IdentityCheckResult(
        bind_status=bind_status,
        target_system="oa",
        execution_identity="user_delegated",
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
        self.record_gateway_call_count = 0
        self.finalize_task_trace_count = 0
        self.record_gateway_call_kwargs: dict[str, Any] | None = None
        self.finalize_task_trace_kwargs: dict[str, Any] | None = None

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

    async def execute(
        self,
        capability_id: str,
        arguments: dict[str, Any],
        execution_context: dict[str, Any],
    ) -> AdapterResult:
        self.call_count += 1
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


def test_capability_registry_missing_short_circuits_without_adapter_and_finalizes_trace() -> None:
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
    _assert_trace_finalized(trace, "blocked", "capability_not_found")


def test_identity_unbound_short_circuits_with_identity_unbound_without_adapter_and_finalizes_trace() -> None:  # noqa: E501
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
    _assert_trace_finalized(trace, "blocked", "identity_unbound")
    assert policy_guard.call_count == 0


def test_identity_expired_short_circuits_with_identity_expired_without_adapter_and_finalizes_trace() -> None:  # noqa: E501
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
    _assert_trace_finalized(trace, "blocked", "identity_expired")
    assert policy_guard.call_count == 0


def test_identity_revoked_short_circuits_with_identity_revoked_without_adapter_and_finalizes_trace() -> None:  # noqa: E501
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
    _assert_trace_finalized(trace, "blocked", "identity_revoked")
    assert policy_guard.call_count == 0


def test_identity_needs_binding_scope_short_circuits_with_needs_binding_scope_without_adapter_and_finalizes_trace() -> None:  # noqa: E501
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
    _assert_trace_finalized(trace, "blocked", "needs_binding_scope")
    assert policy_guard.call_count == 0


def test_identity_verification_failed_maps_to_identity_unbound_without_adapter_and_finalizes_trace() -> None:  # noqa: E501
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
    _assert_trace_finalized(trace, "blocked", "identity_unbound")
    assert policy_guard.call_count == 0


def test_policy_deny_short_circuits_without_adapter_and_finalizes_trace() -> None:
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
    _assert_trace_finalized(trace, "blocked", "policy_denied")


def test_policy_confirm_short_circuits_without_adapter_and_finalizes_trace() -> None:
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
    _assert_trace_finalized(trace, "blocked", "confirm_required")


def test_happy_path_runs_prechecks_records_trace_before_adapter_and_finalizes_after_adapter() -> None:  # noqa: E501
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
    _assert_trace_finalized(trace, "ok", None)


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
    _assert_trace_finalized(trace, "ok", None)
    assert result.status == "completed"
