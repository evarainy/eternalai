"""Runtime trace threading and terminal-event behavior tests."""

from __future__ import annotations

import asyncio
from typing import Any, cast

from app.infra.gateway.capability_gateway import CapabilityGateway
from app.infra.llm.mock_structured_output.mock_structured_output_provider import (
    MockStructuredOutputProvider,
)
from app.infra.sdui.response_envelope_builder import ResponseEnvelopeBuilder
from app.ports.adapter import AdapterResult
from app.ports.capability_gateway import ExecutionResult, RequestOrgContext
from app.ports.capability_registry import CapabilitySpec
from app.ports.identity_mapping import IdentityCheckResult
from app.ports.policy_guard import PolicyDecision
from app.ports.response_envelope import ResponseEnvelope
from app.ports.task_store import SessionRecord, TaskRecord
from app.runtime.models import CapabilityRef
from app.runtime.runtime import RuntimeImpl
from tests.runtime.registry_fakes import StaticCapabilityRegistry


class SpyTaskStore:
    def __init__(self) -> None:
        self.created: list[TaskRecord] = []
        self.status_updates: list[tuple[str, str, str | None]] = []

    async def create_task(self, record: TaskRecord) -> TaskRecord:
        self.created.append(record)
        return record

    async def get_task(self, task_id: str) -> TaskRecord | None:
        return None

    async def update_status(
        self,
        task_id: str,
        status: str,
        error_code: str | None = None,
    ) -> TaskRecord:
        self.status_updates.append((task_id, status, error_code))
        created = self.created[0]
        return TaskRecord(
            task_id=task_id,
            session_id=created.session_id,
            ai_user_id=created.ai_user_id,
            status=cast(Any, status),
            trace_id=created.trace_id,
            error_code=error_code,
        )

    async def append_event(self, task_id: str, event: Any) -> None:
        return None


class ExistingSessionStore:
    async def create_session(self, record: SessionRecord) -> SessionRecord:
        return record

    async def get_session(self, session_id: str) -> SessionRecord | None:
        return SessionRecord(session_id=session_id)


class SpyTracePort:
    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []

    def set_sanitizer(self, hook: Any) -> None:
        return None

    async def record_event(self, event: Any) -> None:
        return None

    async def start_task_trace(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
    ) -> None:
        return None

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

    async def record_policy_decision(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
        status: str,
        capability_id: str | None = None,
        error_code: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        return None

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
        await self.record_step(
            trace_id,
            task_id,
            session_id,
            "gateway_pre_recorded",
            status,
            capability_id,
            error_code,
            attributes,
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
        return None


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
            target_system="u8",
            execution_identity="user_delegated",
            binding_required=True,
        )


class FakeIdentityMapping:
    def __init__(self) -> None:
        self.last_context: RequestOrgContext | None = None

    async def resolve_execution_identity(
        self,
        ai_user_id: str,
        target_system: str,
        execution_identity: str,
        request_context: RequestOrgContext,
    ) -> IdentityCheckResult:
        self.last_context = request_context
        return IdentityCheckResult(
            bind_status="active",
            target_system=cast(Any, target_system),
            execution_identity=cast(Any, execution_identity),
        )


class FakePolicyGuard:
    def __init__(self) -> None:
        self.last_context: RequestOrgContext | None = None

    async def decide(
        self,
        ai_user_id: str,
        capability_id: str,
        arguments: dict[str, Any],
        request_context: RequestOrgContext,
    ) -> PolicyDecision:
        self.last_context = request_context
        return PolicyDecision(decision="allow")


class FakeAdapter:
    async def execute(
        self,
        capability_id: str,
        arguments: dict[str, Any],
        execution_context: dict[str, Any],
    ) -> AdapterResult:
        return AdapterResult(
            status="success",
            data={"document_no": "U8-AP-2026-0033", "document_status": "posted"},
        )


class SpyGateway:
    def __init__(self, result: ExecutionResult) -> None:
        self.result = result
        self.last_context: RequestOrgContext | None = None

    async def execute_capability(
        self,
        task_id: str,
        session_id: str,
        ai_user_id: str,
        capability_id: str,
        arguments: dict[str, Any],
        request_context: RequestOrgContext,
    ) -> ExecutionResult:
        self.last_context = request_context
        return self.result


def _structured_output(
    message: str,
    capability_id: str = "u8.get_document_status",
    arguments: dict[str, Any] | None = None,
) -> MockStructuredOutputProvider:
    provider = MockStructuredOutputProvider()
    provider.register(
        message,
        CapabilityRef,
        CapabilityRef(capability_id=capability_id, arguments=arguments or {}),
    )
    return provider


def _run_runtime(
    *,
    gateway: Any,
    trace_port: SpyTracePort,
    structured_output: MockStructuredOutputProvider,
    message: str = "trace message",
) -> tuple[ResponseEnvelope, SpyTaskStore]:
    async def exercise_runtime() -> tuple[ResponseEnvelope, SpyTaskStore]:
        task_store = SpyTaskStore()
        runtime = RuntimeImpl(
            task_store=task_store,
            session_store=ExistingSessionStore(),
            capability_registry=StaticCapabilityRegistry("u8.get_document_status"),
            gateway=gateway,
            trace_port=trace_port,
            structured_output=structured_output,
            response_builder=ResponseEnvelopeBuilder(),
        )
        envelope = await runtime.handle_user_message(
            channel="web",
            ai_user_id="ai-user-1",
            session_id="session-1",
            message=message,
            client_capabilities={},
        )
        return envelope, task_store

    return asyncio.run(exercise_runtime())


def _events(trace_port: SpyTracePort) -> list[str]:
    return [step["event_type"] for step in trace_port.steps]


def test_runtime_and_gateway_share_trace_id_and_gateway_steps_are_visible() -> None:
    trace_port = SpyTracePort()
    identity_mapping = FakeIdentityMapping()
    policy_guard = FakePolicyGuard()
    gateway = CapabilityGateway(
        adapter=FakeAdapter(),
        capability_registry=FakeRegistry(),
        identity_mapping=identity_mapping,
        policy_guard=policy_guard,
        trace_port=trace_port,
    )

    _run_runtime(
        gateway=gateway,
        trace_port=trace_port,
        structured_output=_structured_output("trace message"),
    )

    event_types = _events(trace_port)
    assert event_types == [
        "task_created",
        "intent_parsed",
        "capability_selected",
        "identity_check",
        "policy_checked",
        "gateway_pre_recorded",
        "adapter_called",
        "gateway_post_recorded",
        "response_envelope_created",
        "task_completed",
    ]
    trace_ids = {step["trace_id"] for step in trace_port.steps}
    assert len(trace_ids) == 1
    assert identity_mapping.last_context is not None
    assert policy_guard.last_context is not None
    assert identity_mapping.last_context.request_id == next(iter(trace_ids))
    assert policy_guard.last_context.request_id == next(iter(trace_ids))


def test_runtime_injects_scope_arguments_into_request_org_context() -> None:
    trace_port = SpyTracePort()
    gateway = SpyGateway(ExecutionResult(status="completed", trace_id="gw-trace"))

    _run_runtime(
        gateway=gateway,
        trace_port=trace_port,
        structured_output=_structured_output(
            "scoped message",
            arguments={
                "account_set_id": "acctset_hunan_01",
                "resource_scope": "acctset_hunan_01",
                "device_domain_id": "prison_area_a",
            },
        ),
        message="scoped message",
    )

    assert gateway.last_context is not None
    assert gateway.last_context.account_set_id == "acctset_hunan_01"
    assert gateway.last_context.resource_scope == "acctset_hunan_01"
    assert gateway.last_context.device_domain_id == "prison_area_a"


def test_runtime_terminal_events_follow_execution_status_not_envelope_status() -> None:
    cases = (
        (ExecutionResult(status="waiting_user", trace_id="tr-wait"), set(), False),
        (
            ExecutionResult(status="denied", error_code="policy_denied", trace_id="tr-deny"),
            {"task_failed"},
            True,
        ),
        (
            ExecutionResult(
                status="binding_required",
                error_code="identity_unbound",
                trace_id="tr-bind",
            ),
            {"task_failed"},
            True,
        ),
    )

    for exec_result, required_events, forbids_completed in cases:
        trace_port = SpyTracePort()
        _run_runtime(
            gateway=SpyGateway(exec_result),
            trace_port=trace_port,
            structured_output=_structured_output(f"message {exec_result.status}"),
            message=f"message {exec_result.status}",
        )
        events = set(_events(trace_port))

        assert required_events.issubset(events)
        assert "task_completed" not in events if forbids_completed else True
        if exec_result.status == "waiting_user":
            assert "task_completed" not in events
            assert "task_failed" not in events
