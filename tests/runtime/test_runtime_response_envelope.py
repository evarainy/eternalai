"""Runtime response envelope and trace sequence tests."""

from __future__ import annotations

import asyncio
from typing import Any

from app.infra.llm.mock_llm.mock_llm_provider import MockLLMProvider
from app.infra.llm.mock_structured_output.mock_structured_output_provider import (
    MockStructuredOutputProvider,
)
from app.infra.sdui.response_envelope_builder import ResponseEnvelopeBuilder
from app.ports.capability_gateway import ExecutionResult, RequestOrgContext
from app.ports.response_envelope import ResponseEnvelope
from app.ports.task_store import SessionRecord, TaskEventRecord, TaskRecord
from app.runtime.models import CapabilityRef
from app.runtime.runtime import RuntimeImpl
from tests.runtime.principal_fakes import runtime_principal
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
            status=status,
            trace_id=created.trace_id,
            error_code=error_code,
        )

    async def append_event(self, task_id: str, event: Any) -> None:
        return None

    async def list_tasks(
        self,
        *,
        session_id: str | None = None,
        ai_user_id: str | None = None,
    ) -> list[TaskRecord]:
        return []

    async def list_events(self, task_id: str) -> list[TaskEventRecord]:
        return []


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
    **_owner: Any,
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
    **_owner: Any,
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

    async def record_policy_decision(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
        status: str,
        capability_id: str | None = None,
        error_code: str | None = None,
        attributes: dict[str, Any] | None = None,
    **_owner: Any,
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
    **_owner: Any,
    ) -> None:
        return None

    async def finalize_task_trace(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
        status: str,
        capability_id: str | None = None,
        error_code: str | None = None,
        attributes: dict[str, Any] | None = None,
    **_owner: Any,
    ) -> None:
        return None


class SpyGateway:
    def __init__(self, result: ExecutionResult) -> None:
        self.result = result
        self.calls = 0

    async def execute_capability(
        self,
        task_id: str,
        session_id: str,
        ai_user_id: str,
        capability_id: str,
        arguments: dict[str, Any],
        request_context: RequestOrgContext,
    ) -> ExecutionResult:
        self.calls += 1
        return self.result


def _make_runtime(
    *,
    message: str = "trace message",
    malformed: bool = False,
    gateway_result: ExecutionResult | None = None,
) -> tuple[RuntimeImpl, SpyTaskStore, SpyTracePort, SpyGateway]:
    task_store = SpyTaskStore()
    trace_port = SpyTracePort()
    structured_output = MockStructuredOutputProvider()
    if malformed:
        structured_output.register_malformed(message, CapabilityRef)
    else:
        structured_output.register(
            message,
            CapabilityRef,
            CapabilityRef(capability_id="trace.cap"),
        )
    gateway = SpyGateway(
        gateway_result or ExecutionResult(status="completed", trace_id="trace-gateway")
    )
    runtime = RuntimeImpl(
        task_store=task_store,
        session_store=ExistingSessionStore(),
        capability_registry=StaticCapabilityRegistry("trace.cap"),
        gateway=gateway,
        trace_port=trace_port,
        llm_provider=MockLLMProvider(),
        structured_output=structured_output,
        intent_model="test-intent-model",
        response_builder=ResponseEnvelopeBuilder(),
    )
    return runtime, task_store, trace_port, gateway


def _run_runtime(
    runtime: RuntimeImpl,
    *,
    message: str = "trace message",
) -> ResponseEnvelope:
    async def exercise_runtime() -> ResponseEnvelope:
        return await runtime.handle_user_message(
            channel="api",
            principal=runtime_principal("ai-user-1"),
            session_id="session-1",
            message=message,
            client_capabilities={},
        )

    return asyncio.run(exercise_runtime())


def test_happy_path_trace_event_sequence_matches_spec() -> None:
    runtime, _task_store, trace_port, _gateway = _make_runtime()

    result = _run_runtime(runtime)

    assert isinstance(result, ResponseEnvelope)
    assert [step["event_type"] for step in trace_port.steps] == [
        "task_created",
        "intent_parsed",
        "capability_selected",
        "response_envelope_created",
        "task_completed",
        "evaluation_recorded",
    ]


def test_parse_error_trace_event_sequence_matches_failed_spec() -> None:
    runtime, _task_store, trace_port, _gateway = _make_runtime(malformed=True)

    result = _run_runtime(runtime)

    assert isinstance(result, ResponseEnvelope)
    assert [step["event_type"] for step in trace_port.steps] == [
        "task_created",
        "intent_parsed",
        "response_envelope_created",
        "task_failed",
        "evaluation_recorded",
    ]


def test_parse_error_skips_gateway_and_returns_failed_envelope() -> None:
    runtime, task_store, _trace_port, gateway = _make_runtime(malformed=True)

    result = _run_runtime(runtime)

    assert gateway.calls == 0
    assert task_store.status_updates[-1][1:] == ("failed", "internal_error")
    assert isinstance(result, ResponseEnvelope)
    assert result.status == "failed"
    assert "模型返回的查询结果暂时无法识别" in result.message
    assert "Admin Lite" not in result.message


def test_response_envelope_is_pydantic_model_not_bare_dict() -> None:
    runtime, _task_store, _trace_port, _gateway = _make_runtime()

    result = _run_runtime(runtime)

    assert isinstance(result, ResponseEnvelope)
    assert not isinstance(result, dict)
