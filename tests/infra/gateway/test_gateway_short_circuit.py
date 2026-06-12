"""Gateway short-circuit trace lifecycle tests."""

from __future__ import annotations

import asyncio
from typing import Any, cast

from app.infra.gateway.capability_gateway import CapabilityGateway
from app.ports.adapter import AdapterResult
from app.ports.capability_gateway import ExecutionResult, RequestOrgContext
from app.ports.capability_registry import CapabilitySpec
from app.ports.identity_mapping import IdentityCheckResult
from app.ports.policy_guard import PolicyDecision


class FakeRegistry:
    def __init__(self, capability: CapabilitySpec | None) -> None:
        self.capability = capability

    async def get(self, capability_id: str) -> CapabilitySpec | None:
        return self.capability


class FakeIdentityMapping:
    def __init__(self, bind_status: str) -> None:
        self.bind_status = bind_status

    async def resolve_execution_identity(
        self,
        ai_user_id: str,
        target_system: str,
        execution_identity: str,
        request_context: RequestOrgContext,
    ) -> IdentityCheckResult:
        return IdentityCheckResult(
            bind_status=cast(Any, self.bind_status),
            target_system=cast(Any, target_system),
            execution_identity=cast(Any, execution_identity),
        )


class FakePolicyGuard:
    def __init__(self, decision: str) -> None:
        self.decision = decision

    async def decide(
        self,
        ai_user_id: str,
        capability_id: str,
        arguments: dict[str, Any],
        request_context: RequestOrgContext,
    ) -> PolicyDecision:
        return PolicyDecision(decision=cast(Any, self.decision))


class FakeTrace:
    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []
        self.gateway_calls: list[dict[str, Any]] = []
        self.finalizes: list[dict[str, Any]] = []

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
                "event_type": event_type,
                "status": status,
                "capability_id": capability_id,
                "error_code": error_code,
                "attributes": attributes or {},
            }
        )

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
        self.gateway_calls.append({"status": status, "error_code": error_code})

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
        self.finalizes.append({"status": status, "error_code": error_code})


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


def _capability_spec() -> CapabilitySpec:
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
        target_system="oa",
        execution_identity="user_delegated",
        binding_required=True,
    )


def _execute_gateway(
    *,
    capability: CapabilitySpec | None = None,
    bind_status: str = "active",
    policy_decision: str = "allow",
) -> tuple[ExecutionResult, FakeTrace, SentinelAdapter]:
    trace = FakeTrace()
    adapter = SentinelAdapter()
    gateway = CapabilityGateway(
        adapter=adapter,
        capability_registry=FakeRegistry(capability),
        identity_mapping=FakeIdentityMapping(bind_status),
        policy_guard=FakePolicyGuard(policy_decision),
        trace_port=trace,
    )
    result = asyncio.run(
        gateway.execute_capability(
            "task-001",
            "session-001",
            "ai-user-001",
            "oa.workflow_status.get",
            {},
            RequestOrgContext(request_id="trace-001"),
        )
    )
    return result, trace, adapter


def _events(trace: FakeTrace) -> list[str]:
    return [step["event_type"] for step in trace.steps]


def _assert_absent(trace: FakeTrace, absent_events: set[str]) -> None:
    events = set(_events(trace))
    assert events.isdisjoint(absent_events)


def test_no_capability_found_short_circuit_emits_only_allowed_events() -> None:
    result, trace, adapter = _execute_gateway(capability=None)

    assert result.status == "no_capability_found"
    assert adapter.call_count == 0
    assert _events(trace) == ["no_capability_found"]
    _assert_absent(
        trace,
        {
            "identity_check",
            "policy_checked",
            "adapter_called",
            "gateway_pre_recorded",
            "gateway_post_recorded",
        },
    )
    assert trace.finalizes == []


def test_policy_denied_short_circuit_emits_policy_block_without_adapter() -> None:
    result, trace, adapter = _execute_gateway(
        capability=_capability_spec(),
        policy_decision="deny",
    )

    assert result.status == "denied"
    assert result.error_code == "policy_denied"
    assert adapter.call_count == 0
    assert _events(trace) == [
        "identity_check",
        "policy_checked",
        "blocked_by_policy",
    ]
    _assert_absent(trace, {"adapter_called", "gateway_post_recorded", "task_completed"})
    assert trace.finalizes == []


def test_identity_unbound_short_circuit_skips_policy_and_adapter() -> None:
    result, trace, adapter = _execute_gateway(
        capability=_capability_spec(),
        bind_status="unbound",
    )

    assert result.status == "binding_required"
    assert result.error_code == "identity_unbound"
    assert adapter.call_count == 0
    assert _events(trace) == ["identity_check", "blocked_by_identity"]
    _assert_absent(trace, {"policy_checked", "adapter_called"})
    assert trace.finalizes == []


def test_needs_binding_scope_short_circuit_skips_adapter() -> None:
    result, trace, adapter = _execute_gateway(
        capability=_capability_spec(),
        bind_status="needs_binding_scope",
    )

    assert result.status == "binding_required"
    assert result.error_code == "needs_binding_scope"
    assert adapter.call_count == 0
    assert _events(trace) == ["identity_check", "blocked_by_identity"]
    _assert_absent(trace, {"adapter_called"})
    assert trace.finalizes == []


def test_confirm_required_short_circuit_skips_adapter_post_and_completion() -> None:
    result, trace, adapter = _execute_gateway(
        capability=_capability_spec(),
        policy_decision="confirm",
    )

    assert result.status == "waiting_user"
    assert result.error_code == "confirm_required"
    assert adapter.call_count == 0
    assert _events(trace) == ["identity_check", "policy_checked", "confirm_required"]
    _assert_absent(trace, {"adapter_called", "gateway_post_recorded", "task_completed"})
    assert trace.finalizes == []
