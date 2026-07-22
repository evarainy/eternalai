"""Canonical Runtime composition and formal application smoke tests."""

from __future__ import annotations

import asyncio
from typing import Any, cast

from fastapi.testclient import TestClient

from app.composition import build_runtime
from app.infra.llm.mock_llm.mock_llm_provider import MockLLMProvider
from app.infra.observability.noop_trace_writer import NoopTraceWriter
from app.main import create_app
from app.memory import SessionMemory
from app.ports.capability_gateway import ExecutionResult
from app.ports.structured_output import StructuredOutputResult
from app.ports.task_store import SessionRecord, TaskRecord
from app.runtime.models import CapabilityRef
from tests.runtime.registry_fakes import StaticCapabilityRegistry


class RecordingTaskStore:
    def __init__(self) -> None:
        self.created: list[TaskRecord] = []

    async def create_task(self, record: TaskRecord) -> TaskRecord:
        self.created.append(record)
        return record

    async def get_task(self, task_id: str) -> TaskRecord | None:
        return None

    async def update_status(
        self, task_id: str, status: str, error_code: str | None = None
    ) -> TaskRecord:
        return self.created[-1].model_copy(
            update={"status": status, "error_code": error_code}
        )

    async def append_event(self, task_id: str, event: Any) -> None:
        return None


class RecordingSessionStore:
    def __init__(self) -> None:
        self.created: list[SessionRecord] = []

    async def create_session(self, record: SessionRecord) -> SessionRecord:
        self.created.append(record)
        return record

    async def get_session(self, session_id: str) -> SessionRecord | None:
        return None


class CompletedGateway:
    def __init__(self, capability_registry: StaticCapabilityRegistry) -> None:
        self.capability_registry = capability_registry

    async def execute_capability(self, *args: Any, **kwargs: Any) -> ExecutionResult:
        return ExecutionResult(
            status="completed",
            data={"result": "ok"},
            trace_id="gateway-trace",
        )


class DeterministicStructuredOutput:
    def __init__(self) -> None:
        self.trace_metadata: list[dict[str, Any]] = []

    async def parse_to_schema(
        self,
        raw_response: str,
        schema_type: type[Any],
        trace_metadata: dict[str, Any] | None = None,
    ) -> StructuredOutputResult:
        self.trace_metadata.append(dict(trace_metadata or {}))
        return StructuredOutputResult(
            parsed=CapabilityRef(capability_id="synthetic.query", arguments={})
        )


class RecordingTracePort:
    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []

    @property
    def event_types(self) -> list[str]:
        return [str(step["event_type"]) for step in self.steps]

    def set_sanitizer(self, hook: Any) -> None:
        return None

    async def record_event(self, event: Any) -> None:
        self.steps.append(cast(dict[str, Any], event.model_dump()))

    async def start_task_trace(
        self, trace_id: str, task_id: str, session_id: str
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
        self.steps.append({"event_type": event_type})

    async def record_policy_decision(self, *args: Any, **kwargs: Any) -> None:
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
        self.steps.append({"event_type": "gateway_pre_recorded"})

    async def finalize_task_trace(self, *args: Any, **kwargs: Any) -> None:
        return None


def _valid_body() -> dict[str, Any]:
    return {
        "channel": "web",
        "ai_user_id": "ai-user-1",
        "session_id": "session-1",
        "message": "hello",
        "client_capabilities": {},
    }


def test_formal_http_smoke_uses_builder_backed_runtime() -> None:
    task_store = RecordingTaskStore()
    session_store = RecordingSessionStore()
    trace_port = RecordingTracePort()
    capability_registry = StaticCapabilityRegistry("synthetic.query")
    gateway = CompletedGateway(capability_registry)
    llm_provider = MockLLMProvider()
    structured_output = DeterministicStructuredOutput()
    session_memory = SessionMemory()
    runtime = build_runtime(
        task_store=task_store,
        session_store=session_store,
        capability_registry=capability_registry,
        gateway=gateway,
        trace_port=trace_port,
        llm_provider=llm_provider,
        structured_output=structured_output,
        intent_model="test-intent-model",
        session_memory=session_memory,
    )

    response = TestClient(create_app(runtime)).post(
        "/api/v1/runtime/handle", json=_valid_body()
    )

    assert response.status_code == 200
    envelope = response.json()
    assert envelope["status"] == "completed"
    assert envelope["schema_version"] == "phase0.sdui.v1"
    assert envelope["data"] == {"result": "ok"}
    assert task_store.created
    assert session_store.created
    assert runtime._capability_registry is capability_registry
    assert runtime._session_memory is session_memory
    assert gateway.capability_registry is capability_registry
    assert llm_provider.calls[0]["model"] == "test-intent-model"
    assert llm_provider.calls[0]["response_format"] == {"type": "json_object"}
    assert structured_output.trace_metadata == [
        {
            "trace_id": task_store.created[0].trace_id,
            "task_id": task_store.created[0].task_id,
        }
    ]
    assert "session-1" not in repr(structured_output.trace_metadata)
    assert "task_created" in trace_port.event_types
    assert "response_envelope_created" in trace_port.event_types
    assert "task_completed" in trace_port.event_types


class CapturingTraceLogger:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def debug(self, _message: str, *, extra: dict[str, Any]) -> None:
        self.events.append(cast(dict[str, Any], extra["trace_event"]))


async def _record_representative_semantic_sequence(trace_port: Any) -> None:
    await trace_port.start_task_trace("trace-equivalence", "task-equivalence", "session")
    await trace_port.record_step(
        "trace-equivalence", "task-equivalence", "session", "task_created", "ok"
    )
    await trace_port.record_gateway_call(
        "trace-equivalence",
        "task-equivalence",
        "session",
        "ok",
        "oa.synthetic.query",
    )
    await trace_port.record_step(
        "trace-equivalence",
        "task-equivalence",
        "session",
        "response_envelope_created",
        "ok",
    )
    await trace_port.record_step(
        "trace-equivalence", "task-equivalence", "session", "task_completed", "ok"
    )
    await trace_port.finalize_task_trace(
        "trace-equivalence", "task-equivalence", "session", "ok"
    )


def test_golden_trace_double_matches_real_writer_semantic_sequence() -> None:
    golden_trace = RecordingTracePort()
    logger = CapturingTraceLogger()
    real_writer = NoopTraceWriter(logger=cast(Any, logger))

    asyncio.run(_record_representative_semantic_sequence(golden_trace))
    asyncio.run(_record_representative_semantic_sequence(real_writer))

    assert [step["event_type"] for step in golden_trace.steps] == [
        event["event_type"] for event in logger.events
    ] == [
        "task_created",
        "gateway_pre_recorded",
        "response_envelope_created",
        "task_completed",
    ]
