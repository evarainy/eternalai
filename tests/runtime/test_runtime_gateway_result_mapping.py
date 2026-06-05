"""Runtime gateway status mapping tests."""

from __future__ import annotations

import asyncio
from typing import Any

from app.infra.llm.mock_structured_output.mock_structured_output_provider import (
    MockStructuredOutputProvider,
)
from app.infra.sdui.response_envelope_builder import ResponseEnvelopeBuilder
from app.ports.capability_gateway import ExecutionResult, RequestOrgContext
from app.ports.response_envelope import ResponseEnvelope
from app.ports.task_store import SessionRecord, TaskRecord
from app.runtime.models import CapabilityRef
from app.runtime.runtime import RuntimeImpl


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


class ExistingSessionStore:
    async def create_session(self, record: SessionRecord) -> SessionRecord:
        return record

    async def get_session(self, session_id: str) -> SessionRecord | None:
        return SessionRecord(session_id=session_id)


class SpyTracePort:
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
        return None

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
    ) -> None:
        return None


class SpyGateway:
    def __init__(self, result: ExecutionResult) -> None:
        self.result = result
        self.calls: list[str] = []

    async def execute_capability(
        self,
        task_id: str,
        session_id: str,
        ai_user_id: str,
        capability_id: str,
        arguments: dict[str, Any],
        request_context: RequestOrgContext,
    ) -> ExecutionResult:
        self.calls.append(capability_id)
        return self.result


def _run_mapping(result: ExecutionResult) -> tuple[ResponseEnvelope, SpyTaskStore, SpyGateway]:
    async def exercise_runtime() -> tuple[ResponseEnvelope, SpyTaskStore, SpyGateway]:
        task_store = SpyTaskStore()
        structured_output = MockStructuredOutputProvider()
        structured_output.register(
            "mapped message",
            CapabilityRef,
            CapabilityRef(capability_id="mapped.cap", arguments={"key": "value"}),
        )
        gateway = SpyGateway(result)
        runtime = RuntimeImpl(
            task_store=task_store,
            session_store=ExistingSessionStore(),
            gateway=gateway,
            trace_port=SpyTracePort(),
            structured_output=structured_output,
            response_builder=ResponseEnvelopeBuilder(),
        )

        envelope = await runtime.handle_user_message(
            channel="mock",
            ai_user_id="ai-user-1",
            session_id="session-1",
            message="mapped message",
            client_capabilities={},
        )
        return envelope, task_store, gateway

    return asyncio.run(exercise_runtime())


def test_denied_result_maps_to_failed_task_blocked_envelope_and_single_gateway_call() -> None:
    result, task_store, gateway = _run_mapping(
        ExecutionResult(status="denied", trace_id="tr1")
    )

    assert task_store.status_updates[-1][1] == "failed"
    assert isinstance(result, ResponseEnvelope)
    assert result.status == "blocked"
    assert result.ui.component_type == "operator_handback_card"
    assert len(gateway.calls) == 1


def test_binding_required_result_maps_to_failed_task_blocked_envelope_with_operator_handback(
) -> None:
    result, task_store, _gateway = _run_mapping(
        ExecutionResult(
            status="binding_required",
            error_code="identity_unbound",
            trace_id="tr2",
        )
    )

    assert task_store.status_updates[-1][1] == "failed"
    assert isinstance(result, ResponseEnvelope)
    assert result.status == "blocked"
    assert result.ui.component_type in {"binding_required_card", "operator_handback_card"}


def test_timeout_result_maps_to_failed_task_failed_envelope_and_preserves_trace_id() -> None:
    result, task_store, _gateway = _run_mapping(
        ExecutionResult(status="timeout", trace_id="gw-timeout-trace")
    )

    assert task_store.status_updates[-1][1] == "failed"
    assert isinstance(result, ResponseEnvelope)
    assert result.status == "failed"
    assert result.trace_id is not None
    assert result.trace_id != ""


def test_failed_result_maps_to_failed_task_failed_envelope() -> None:
    result, task_store, _gateway = _run_mapping(
        ExecutionResult(status="failed", trace_id="tr4")
    )

    assert task_store.status_updates[-1][1] == "failed"
    assert isinstance(result, ResponseEnvelope)
    assert result.status == "failed"


def test_gateway_no_capability_found_maps_to_no_capability_task_and_envelope() -> None:
    result, task_store, _gateway = _run_mapping(
        ExecutionResult(status="no_capability_found", trace_id="tr5")
    )

    assert task_store.status_updates[-1][1] == "no_capability_found"
    assert isinstance(result, ResponseEnvelope)
    assert result.status == "no_capability_found"


def test_waiting_user_result_maps_to_waiting_user_task_and_confirm_card() -> None:
    result, task_store, _gateway = _run_mapping(
        ExecutionResult(status="waiting_user", trace_id="tr6")
    )

    assert task_store.status_updates[-1][1] == "waiting_user"
    assert isinstance(result, ResponseEnvelope)
    assert result.status == "waiting_user"
    assert result.ui.component_type == "confirm_card"
