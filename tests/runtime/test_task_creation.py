"""Runtime task creation and capability reference tests."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import ValidationError

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
            tenant_id=created.tenant_id,
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
        tenant_id: str | None = None,
    ) -> list[TaskRecord]:
        return []

    async def list_events(self, task_id: str) -> list[TaskEventRecord]:
        return []


class SpySessionStore:
    def __init__(self) -> None:
        self.created: list[SessionRecord] = []

    async def create_session(self, record: SessionRecord) -> SessionRecord:
        self.created.append(record)
        return record

    async def get_session(self, session_id: str) -> SessionRecord | None:
        return None


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
        self.calls: list[dict[str, Any]] = []

    async def execute_capability(
        self,
        task_id: str,
        session_id: str,
        ai_user_id: str,
        capability_id: str,
        arguments: dict[str, Any],
        request_context: RequestOrgContext,
    ) -> ExecutionResult:
        self.calls.append(
            {
                "task_id": task_id,
                "session_id": session_id,
                "ai_user_id": ai_user_id,
                "capability_id": capability_id,
                "arguments": arguments,
                "request_context": request_context,
            }
        )
        return self.result


def _runtime_for_message() -> tuple[
    RuntimeImpl,
    SpyTaskStore,
    SpySessionStore,
    SpyGateway,
]:
    task_store = SpyTaskStore()
    session_store = SpySessionStore()
    trace_port = SpyTracePort()
    structured_output = MockStructuredOutputProvider()
    structured_output.register(
        "test message",
        CapabilityRef,
        CapabilityRef(capability_id="test.cap"),
    )
    gateway = SpyGateway(ExecutionResult(status="completed", trace_id="gw-trace"))
    runtime = RuntimeImpl(
        task_store=task_store,
        session_store=session_store,
        capability_registry=StaticCapabilityRegistry("test.cap"),
        gateway=gateway,
        trace_port=trace_port,
        llm_provider=MockLLMProvider(),
        structured_output=structured_output,
        intent_model="test-intent-model",
        response_builder=ResponseEnvelopeBuilder(),
    )
    return runtime, task_store, session_store, gateway


def test_capability_ref_accepts_required_fields_and_defaults_arguments() -> None:
    default_ref = CapabilityRef(capability_id="some.capability")
    explicit_ref = CapabilityRef(capability_id="x", arguments={"key": "val"})

    assert default_ref.capability_id == "some.capability"
    assert default_ref.arguments == {}
    assert explicit_ref.arguments == {"key": "val"}


def test_capability_ref_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        CapabilityRef(capability_id="x", unknown_field="y")


def test_handle_user_message_creates_running_task_executes_gateway_and_completes_task() -> None:
    async def exercise_runtime() -> ResponseEnvelope:
        runtime, task_store, session_store, gateway = _runtime_for_message()

        result = await runtime.handle_user_message(
            channel="web",
            principal=runtime_principal("ai-user-1"),
            session_id="session-1",
            message="test message",
            client_capabilities={},
        )

        assert len(task_store.created) == 1
        assert task_store.created[0].status == "running"
        assert task_store.created[0].tenant_id == "tenant-test"
        assert session_store.created == [SessionRecord(session_id="session-1")]
        assert len(gateway.calls) == 1
        assert gateway.calls[0]["capability_id"] == "test.cap"
        assert task_store.status_updates[-1][1] == "completed"
        assert isinstance(result, ResponseEnvelope)
        assert result.status == "completed"
        return result

    asyncio.run(exercise_runtime())


def test_handle_user_message_rejects_blank_tenant_before_task_store_write() -> None:
    async def exercise_runtime() -> None:
        runtime, task_store, _, gateway = _runtime_for_message()

        with pytest.raises(ValidationError, match="tenant_id must not be blank"):
            await runtime.handle_user_message(
                channel="web",
                principal=runtime_principal("ai-user-1", tenant_id=" "),
                session_id="session-blank-tenant",
                message="test message",
                client_capabilities={},
            )

        assert task_store.created == []
        assert gateway.calls == []

    asyncio.run(exercise_runtime())
