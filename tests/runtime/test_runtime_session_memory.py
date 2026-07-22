"""Runtime integration for bounded, isolated Session Memory."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.infra.sdui.response_envelope_builder import ResponseEnvelopeBuilder
from app.memory import SessionMemory, SessionMemoryKey
from app.ports.capability_gateway import ExecutionResult, RequestOrgContext
from app.ports.llm_provider import LLMCompletionResponse, LLMMessage
from app.ports.structured_output import StructuredOutputResult
from app.ports.task_store import SessionRecord, TaskRecord
from app.runtime.runtime import RuntimeImpl
from tests.runtime.registry_fakes import StaticCapabilityRegistry


class RecordingTaskStore:
    def __init__(self) -> None:
        self.tasks: dict[str, TaskRecord] = {}

    async def create_task(self, record: TaskRecord) -> TaskRecord:
        self.tasks[record.task_id] = record
        return record

    async def get_task(self, task_id: str) -> TaskRecord | None:
        return self.tasks.get(task_id)

    async def update_status(
        self,
        task_id: str,
        status: str,
        error_code: str | None = None,
    ) -> TaskRecord:
        updated = self.tasks[task_id].model_copy(
            update={"status": status, "error_code": error_code}
        )
        self.tasks[task_id] = updated
        return updated

    async def append_event(self, task_id: str, event: Any) -> None:
        return None


class InMemorySessionStore:
    def __init__(self) -> None:
        self.sessions: dict[str, SessionRecord] = {}

    async def create_session(self, record: SessionRecord) -> SessionRecord:
        self.sessions[record.session_id] = record
        return record

    async def get_session(self, session_id: str) -> SessionRecord | None:
        return self.sessions.get(session_id)


class RecordingTracePort:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def set_sanitizer(self, hook: Any) -> None:
        return None

    async def record_event(self, event: Any) -> None:
        self.events.append(event.model_dump())

    async def start_task_trace(
        self, trace_id: str, task_id: str, session_id: str
    ) -> None:
        self.events.append({"event_type": "trace_started"})

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
        self.events.append(
            {
                "event_type": event_type,
                "status": status,
                "capability_id": capability_id,
                "error_code": error_code,
                "attributes": attributes,
            }
        )

    async def record_policy_decision(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def record_gateway_call(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def finalize_task_trace(self, *args: Any, **kwargs: Any) -> None:
        self.events.append({"event_type": "trace_finalized"})


class JsonStructuredOutput:
    async def parse_to_schema(
        self,
        raw_response: str,
        schema_type: type[Any],
        trace_metadata: dict[str, Any] | None = None,
    ) -> StructuredOutputResult:
        return StructuredOutputResult(parsed=json.loads(raw_response))


class MemoryAwareLLMProvider:
    def __init__(self, first_arguments: dict[str, Any] | None = None) -> None:
        self.calls: list[list[LLMMessage]] = []
        self._first_arguments = dict(first_arguments or {})

    async def complete(
        self,
        messages: list[LLMMessage],
        model: str,
        response_format: dict[str, Any] | None = None,
    ) -> LLMCompletionResponse:
        self.calls.append(list(messages))
        user_message = messages[-1].content
        if user_message == "first request":
            capability_id = "oa.get_workflow_status"
            arguments = self._first_arguments
        elif any(
            '"session_memory"' in item.content
            and '"capability_id":"oa.get_workflow_status"' in item.content
            for item in messages
        ):
            capability_id = "oa.get_workflow_status"
            arguments = {}
        else:
            capability_id = "unknown.followup"
            arguments = {}
        return LLMCompletionResponse(
            content=json.dumps(
                {
                    "capability_id": capability_id,
                    "arguments": arguments,
                    "target_system": None,
                    "capability_type": None,
                }
            ),
            model_used=model,
        )

    async def chat(
        self,
        messages: list[LLMMessage],
        model: str,
        response_format: dict[str, Any] | None = None,
    ) -> LLMCompletionResponse:
        return await self.complete(messages, model, response_format)


class FixedGateway:
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


def _build_runtime(
    *,
    result: ExecutionResult | None = None,
    first_arguments: dict[str, Any] | None = None,
) -> tuple[RuntimeImpl, SessionMemory, MemoryAwareLLMProvider, RecordingTracePort]:
    memory = SessionMemory()
    llm_provider = MemoryAwareLLMProvider(first_arguments)
    trace_port = RecordingTracePort()
    runtime = RuntimeImpl(
        task_store=RecordingTaskStore(),
        session_store=InMemorySessionStore(),
        capability_registry=StaticCapabilityRegistry("oa.get_workflow_status"),
        gateway=FixedGateway(
            result or ExecutionResult(status="completed", trace_id="gateway-trace")
        ),
        trace_port=trace_port,
        llm_provider=llm_provider,
        structured_output=JsonStructuredOutput(),
        intent_model="test-intent-model",
        response_builder=ResponseEnvelopeBuilder(),
        session_memory=memory,
    )
    return runtime, memory, llm_provider, trace_port


async def _handle(
    runtime: RuntimeImpl,
    *,
    ai_user_id: str = "user-1",
    session_id: str = "session-1",
    message: str = "first request",
) -> Any:
    return await runtime.handle_user_message(
        channel="mock",
        ai_user_id=ai_user_id,
        session_id=session_id,
        message=message,
        client_capabilities={},
    )


def test_same_session_and_user_followup_can_use_prior_success_summary() -> None:
    async def exercise() -> tuple[Any, list[LLMMessage]]:
        runtime, _memory, llm_provider, _trace = _build_runtime()
        first = await _handle(runtime)
        assert first.status == "completed"
        followup = await _handle(runtime, message="repeat that")
        return followup, llm_provider.calls[-1]

    followup, followup_messages = asyncio.run(exercise())

    assert followup.status == "completed"
    assert [message.role for message in followup_messages] == [
        "system",
        "system",
        "system",
        "user",
    ]
    knowledge_prompt = followup_messages[1].content
    memory_prompt = followup_messages[2].content
    assert "semantic_system_knowledge" in knowledge_prompt
    assert "session_memory" not in knowledge_prompt
    assert '"capability_id":"oa.get_workflow_status"' in memory_prompt
    assert '"terminal_status":"completed"' in memory_prompt
    assert "first request" not in memory_prompt


@pytest.mark.parametrize(
    ("ai_user_id", "session_id"),
    [
        ("user-1", "session-2"),
        ("user-2", "session-1"),
    ],
    ids=["different-session", "different-ai-user"],
)
def test_followup_cannot_read_another_session_or_users_memory(
    ai_user_id: str,
    session_id: str,
) -> None:
    async def exercise() -> tuple[Any, list[LLMMessage]]:
        runtime, _memory, llm_provider, _trace = _build_runtime()
        first = await _handle(runtime)
        assert first.status == "completed"
        followup = await _handle(
            runtime,
            ai_user_id=ai_user_id,
            session_id=session_id,
            message="repeat that",
        )
        return followup, llm_provider.calls[-1]

    followup, followup_messages = asyncio.run(exercise())

    assert followup.status == "no_capability_found"
    assert [message.role for message in followup_messages] == [
        "system",
        "system",
        "user",
    ]
    assert "semantic_system_knowledge" in followup_messages[1].content
    assert "session_memory" not in followup_messages[1].content


@pytest.mark.parametrize(
    "status",
    ["denied", "failed", "waiting_user", "no_capability_found"],
)
def test_non_successful_turns_leave_no_success_memory(status: str) -> None:
    runtime, memory, _llm, _trace = _build_runtime(
        result=ExecutionResult(status=status, trace_id="gateway-trace")
    )

    asyncio.run(_handle(runtime))

    assert memory.recall(
        SessionMemoryKey("default", "session-1", "user-1")
    ) == ()
    assert memory.entry_count == 0


def test_sensitive_business_payload_never_enters_memory_trace_or_next_context() -> None:
    sensitive_key = "access_" + "token"
    sensitive_value = "synthetic-" + "credential-marker"
    argument_marker = "synthetic-argument-marker"
    payload_marker = "synthetic-business-payload-marker"
    runtime, memory, llm_provider, trace = _build_runtime(
        result=ExecutionResult(
            status="completed",
            data={
                "business_note": payload_marker,
                sensitive_key: sensitive_value,
            },
            trace_id="gateway-trace",
        ),
        first_arguments={"request_note": argument_marker},
    )

    first_envelope = asyncio.run(_handle(runtime))
    asyncio.run(_handle(runtime, message="repeat that"))

    key = SessionMemoryKey("default", "session-1", "user-1")
    serialized_state = repr(memory.recall(key))
    serialized_trace = repr(trace.events)
    serialized_context = repr(llm_provider.calls[-1])
    assert sensitive_value not in serialized_state
    assert sensitive_value not in serialized_trace
    assert sensitive_value not in serialized_context
    assert sensitive_value not in first_envelope.model_dump_json()
    for non_memory_value in (argument_marker, payload_marker):
        assert non_memory_value not in serialized_state
        assert non_memory_value not in serialized_trace
        assert non_memory_value not in serialized_context
