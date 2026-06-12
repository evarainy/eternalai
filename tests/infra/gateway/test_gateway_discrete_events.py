"""Gateway discrete trace event tests."""

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
    async def get(self, capability_id: str) -> CapabilitySpec:
        return CapabilitySpec(
            capability_id=capability_id,
            name="Mock capability",
            type="query",
            input_schema_digest="input-digest",
            output_schema_digest="output-digest",
            risk_level="low",
            owner="phase0",
            version="0.1.0",
            status="active",
            short_description="Mock capability",
            target_system="oa",
            execution_identity="user_delegated",
            binding_required=True,
        )


class ActiveIdentityMapping:
    async def resolve_execution_identity(
        self,
        ai_user_id: str,
        target_system: str,
        execution_identity: str,
        request_context: RequestOrgContext,
    ) -> IdentityCheckResult:
        return IdentityCheckResult(
            bind_status="active",
            target_system=cast(Any, target_system),
            execution_identity=cast(Any, execution_identity),
        )


class AllowPolicyGuard:
    async def decide(
        self,
        ai_user_id: str,
        capability_id: str,
        arguments: dict[str, Any],
        request_context: RequestOrgContext,
    ) -> PolicyDecision:
        return PolicyDecision(decision="allow")


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
        self.gateway_calls.append(
            {
                "status": status,
                "capability_id": capability_id,
                "error_code": error_code,
                "attributes": attributes or {},
            }
        )

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
        self.finalizes.append(
            {
                "status": status,
                "capability_id": capability_id,
                "error_code": error_code,
                "attributes": attributes or {},
            }
        )


class FakeAdapter:
    def __init__(self, result: AdapterResult) -> None:
        self.result = result
        self.call_count = 0

    async def execute(
        self,
        capability_id: str,
        arguments: dict[str, Any],
        execution_context: dict[str, Any],
    ) -> AdapterResult:
        self.call_count += 1
        return self.result


def _execute_gateway(adapter_result: AdapterResult) -> tuple[ExecutionResult, FakeTrace]:
    trace = FakeTrace()
    gateway = CapabilityGateway(
        adapter=FakeAdapter(adapter_result),
        capability_registry=FakeRegistry(),
        identity_mapping=ActiveIdentityMapping(),
        policy_guard=AllowPolicyGuard(),
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
    return result, trace


def _events(trace: FakeTrace) -> list[str]:
    return [step["event_type"] for step in trace.steps]


def test_gateway_emits_discrete_success_events_around_adapter_call() -> None:
    result, trace = _execute_gateway(
        AdapterResult(status="success", data={"workflow_id": "OA-WF-2026-0001"})
    )

    assert result.status == "completed"
    assert _events(trace) == [
        "identity_check",
        "policy_checked",
        "gateway_pre_recorded",
        "adapter_called",
        "gateway_post_recorded",
    ]
    assert len(trace.gateway_calls) == 1


def test_gateway_timeout_sub_order_maps_adapter_error_after_post_record() -> None:
    result, trace = _execute_gateway(
        AdapterResult(status="error", error_code="adapter_timeout")
    )

    assert result.status == "timeout"
    assert _events(trace)[-3:] == [
        "adapter_called",
        "gateway_post_recorded",
        "adapter_error_mapped",
    ]
    assert "task_completed" not in _events(trace)
