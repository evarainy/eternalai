from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from app.infra.gateway.capability_gateway import CapabilityGateway
from app.infra.llm.mock_structured_output.mock_structured_output_provider import (
    MockStructuredOutputProvider,
)
from app.infra.observability.noop_trace_writer import NoopTraceWriter
from app.infra.sdui.response_envelope_builder import ResponseEnvelopeBuilder
from app.ports.adapter import AdapterResult
from app.ports.capability_gateway import ExecutionResult, ExecutionStatus, RequestOrgContext
from app.ports.response_envelope import ResponseEnvelope
from app.ports.task_store import SessionRecord, TaskRecord
from app.runtime.models import CapabilityRef
from app.runtime.runtime import RuntimeImpl


class CapturingLogger:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def debug(self, _message: str, *, extra: dict[str, Any]) -> None:
        self.events.append(cast(dict[str, Any], extra["trace_event"]))


class MemoryTaskStore:
    def __init__(self) -> None:
        self.created: list[TaskRecord] = []
        self.status_updates: list[tuple[str, str, str | None]] = []

    async def create_task(self, record: TaskRecord) -> TaskRecord:
        self.created.append(record)
        return record

    async def get_task(self, task_id: str) -> TaskRecord | None:
        return next((item for item in self.created if item.task_id == task_id), None)

    async def update_status(
        self,
        task_id: str,
        status: str,
        error_code: str | None = None,
    ) -> TaskRecord:
        self.status_updates.append((task_id, status, error_code))
        original = cast(TaskRecord, await self.get_task(task_id))
        return TaskRecord(
            task_id=task_id,
            session_id=original.session_id,
            ai_user_id=original.ai_user_id,
            status=cast(Any, status),
            trace_id=original.trace_id,
            error_code=error_code,
        )

    async def append_event(self, task_id: str, event: Any) -> None:
        return None


class ExistingSessionStore:
    async def create_session(self, record: SessionRecord) -> SessionRecord:
        return record

    async def get_session(self, session_id: str) -> SessionRecord | None:
        return SessionRecord(session_id=session_id)


class SuccessfulAdapter:
    async def execute(
        self,
        capability_id: str,
        arguments: dict[str, Any],
        execution_context: dict[str, Any],
    ) -> AdapterResult:
        return AdapterResult(status="success", data={"workflow_id": "synthetic-001"})


class ResultGateway:
    def __init__(self, result: ExecutionResult) -> None:
        self.result = result

    async def execute_capability(
        self,
        task_id: str,
        session_id: str,
        ai_user_id: str,
        capability_id: str,
        arguments: dict[str, Any],
        request_context: RequestOrgContext,
    ) -> ExecutionResult:
        return self.result


def _provider(message: str, *, malformed: bool = False) -> MockStructuredOutputProvider:
    provider = MockStructuredOutputProvider()
    if malformed:
        provider.register_malformed(message, CapabilityRef)
    else:
        provider.register(
            message,
            CapabilityRef,
            CapabilityRef(capability_id="oa.workflow_status.get", arguments={}),
        )
    return provider


def _run_runtime(
    gateway: Any,
    *,
    malformed: bool = False,
) -> tuple[ResponseEnvelope, list[dict[str, Any]], MemoryTaskStore]:
    logger = CapturingLogger()
    writer = NoopTraceWriter(logger=cast(Any, logger))
    task_store = MemoryTaskStore()
    message = "synthetic trace lifecycle request"
    runtime = RuntimeImpl(
        task_store=task_store,
        session_store=ExistingSessionStore(),
        gateway=gateway,
        trace_port=writer,
        structured_output=_provider(message, malformed=malformed),
        response_builder=ResponseEnvelopeBuilder(),
    )
    envelope = asyncio.run(
        runtime.handle_user_message(
            channel="mock",
            ai_user_id="synthetic-user",
            session_id="synthetic-session",
            message=message,
            client_capabilities={},
        )
    )
    return envelope, logger.events, task_store


def _event_types(events: list[dict[str, Any]]) -> list[str]:
    return [str(event["event_type"]) for event in events]


def test_real_writer_cross_layer_success_has_one_complete_lifecycle() -> None:
    logger = CapturingLogger()
    writer = NoopTraceWriter(logger=cast(Any, logger))
    task_store = MemoryTaskStore()
    message = "synthetic cross layer success"
    runtime = RuntimeImpl(
        task_store=task_store,
        session_store=ExistingSessionStore(),
        gateway=CapabilityGateway(adapter=SuccessfulAdapter(), trace_port=writer),
        trace_port=writer,
        structured_output=_provider(message),
        response_builder=ResponseEnvelopeBuilder(),
    )

    envelope = asyncio.run(
        runtime.handle_user_message(
            channel="mock",
            ai_user_id="synthetic-user",
            session_id="synthetic-session",
            message=message,
            client_capabilities={},
        )
    )

    event_types = _event_types(logger.events)
    assert envelope.status == "completed"
    assert event_types == [
        "task_created",
        "intent_parsed",
        "capability_selected",
        "gateway_pre_recorded",
        "adapter_called",
        "gateway_post_recorded",
        "response_envelope_created",
        "task_completed",
    ]
    assert event_types.count("task_created") == 1
    assert event_types.count("gateway_pre_recorded") == 1
    assert event_types.count("task_completed") == 1
    assert event_types.count("task_failed") == 0
    assert len({event["trace_id"] for event in logger.events}) == 1


@pytest.mark.parametrize(
    ("status", "error_code", "expected_terminal"),
    (
        ("completed", None, "task_completed"),
        ("waiting_user", None, None),
        ("denied", "policy_denied", "task_failed"),
        ("binding_required", "identity_unbound", "task_failed"),
        ("timeout", "adapter_timeout", "task_failed"),
        ("failed", "adapter_error", "task_failed"),
        ("no_capability_found", "capability_not_found", "task_failed"),
    ),
)
def test_real_writer_terminal_matrix_is_exact_and_last(
    status: ExecutionStatus,
    error_code: str | None,
    expected_terminal: str | None,
) -> None:
    _, events, _ = _run_runtime(
        ResultGateway(
            ExecutionResult(
                status=status,
                error_code=cast(Any, error_code),
                trace_id="synthetic-gateway-trace",
            )
        )
    )
    event_types = _event_types(events)

    terminals = [
        event_type
        for event_type in event_types
        if event_type in {"task_completed", "task_failed"}
    ]
    assert terminals == ([] if expected_terminal is None else [expected_terminal])
    assert event_types.count("task_completed") == (expected_terminal == "task_completed")
    assert event_types.count("task_failed") == (expected_terminal == "task_failed")
    if expected_terminal is not None:
        assert event_types[-2:] == ["response_envelope_created", expected_terminal]
    else:
        assert event_types[-1] == "response_envelope_created"


def test_real_writer_structured_output_parse_failure_has_one_failed_terminal() -> None:
    _, events, task_store = _run_runtime(
        ResultGateway(
            ExecutionResult(status="completed", trace_id="unused-synthetic-trace")
        ),
        malformed=True,
    )
    event_types = _event_types(events)

    assert event_types == [
        "task_created",
        "intent_parsed",
        "no_capability_found",
        "response_envelope_created",
        "task_failed",
    ]
    assert task_store.status_updates[-1][1] == "no_capability_found"
