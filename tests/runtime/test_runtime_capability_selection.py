"""Registry-backed Runtime capability-selection behavior."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from app.infra.llm.mock_llm.mock_llm_provider import MockLLMProvider
from app.infra.llm.mock_structured_output.mock_structured_output_provider import (
    MockStructuredOutputProvider,
)
from app.infra.sdui.response_envelope_builder import ResponseEnvelopeBuilder
from app.ports.capability_gateway import ExecutionResult, RequestOrgContext
from app.ports.capability_registry import (
    CapabilitySpec,
    CapabilityStatus,
    CapabilityTargetSystem,
    CapabilityType,
)
from app.ports.llm_provider import LLMCompletionResponse
from app.ports.response_envelope import ResponseEnvelope
from app.ports.task_store import SessionRecord, TaskEventRecord, TaskRecord
from app.runtime.models import CapabilityRef
from app.runtime.runtime import RuntimeImpl


class RecordingTaskStore:
    def __init__(self) -> None:
        self.created: list[TaskRecord] = []
        self.status_updates: list[tuple[str, str, str | None]] = []
        self.events: list[tuple[str, TaskEventRecord]] = []

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
        return self.created[0].model_copy(
            update={"status": status, "error_code": error_code}
        )

    async def append_event(self, task_id: str, event: TaskEventRecord) -> None:
        self.events.append((task_id, event))


class ExistingSessionStore:
    async def create_session(self, record: SessionRecord) -> SessionRecord:
        return record

    async def get_session(self, session_id: str) -> SessionRecord | None:
        return SessionRecord.model_validate({"session_id": session_id})


class RecordingTracePort:
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
                "event_type": event_type,
                "status": status,
                "capability_id": capability_id,
                "error_code": error_code,
                "attributes": attributes or {},
            }
        )

    async def record_policy_decision(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def record_gateway_call(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def finalize_task_trace(self, *args: Any, **kwargs: Any) -> None:
        return None


class RecordingGateway:
    def __init__(self) -> None:
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
                "capability_id": capability_id,
                "arguments": arguments,
                "channel": request_context.channel,
            }
        )
        return ExecutionResult(
            status="completed",
            data={"selected": capability_id},
            trace_id=request_context.request_id,
        )


class StaticRegistry:
    def __init__(self, capabilities: list[CapabilitySpec]) -> None:
        self.capabilities = list(capabilities)
        self.get_calls: list[str] = []
        self.list_calls: list[dict[str, str | None]] = []

    async def get(self, capability_id: str) -> CapabilitySpec | None:
        self.get_calls.append(capability_id)
        return next(
            (
                capability
                for capability in self.capabilities
                if capability.capability_id == capability_id
            ),
            None,
        )

    async def list(
        self,
        target_system: str | None = None,
        type: str | None = None,
        status: str | None = None,
    ) -> list[CapabilitySpec]:
        self.list_calls.append(
            {"target_system": target_system, "type": type, "status": status}
        )
        result = list(self.capabilities)
        if target_system is not None:
            result = [item for item in result if item.target_system == target_system]
        if type is not None:
            result = [item for item in result if item.type == type]
        if status is not None:
            result = [item for item in result if item.status == status]
        return result


def _capability(
    capability_id: str,
    *,
    status: CapabilityStatus = "active",
    intent_tags: list[str] | None = None,
    capability_type: CapabilityType = "query",
) -> CapabilitySpec:
    return CapabilitySpec(
        capability_id=capability_id,
        name=capability_id,
        type=capability_type,
        intent_tags=intent_tags or [],
        input_schema_digest=f"input-{capability_id}",
        output_schema_digest=f"output-{capability_id}",
        risk_level="low",
        owner="runtime-selection-test",
        version="1.0.0",
        status=status,
        short_description=capability_id,
        target_system="oa" if capability_id.startswith("oa.") else None,
        execution_identity="user_delegated",
        binding_required=False,
    )


def _run_runtime(
    selector: str,
    capabilities: list[CapabilitySpec],
    *,
    channel: str = "web",
    target_system: CapabilityTargetSystem | None = None,
    capability_type: CapabilityType | None = None,
    llm_completion: LLMCompletionResponse | None = None,
    malformed_intent: bool = False,
) -> tuple[
    ResponseEnvelope,
    RecordingTaskStore,
    RecordingTracePort,
    RecordingGateway,
    StaticRegistry,
]:
    task_store = RecordingTaskStore()
    trace_port = RecordingTracePort()
    gateway = RecordingGateway()
    registry = StaticRegistry(capabilities)
    structured_output = MockStructuredOutputProvider()
    message = f"select {selector}"
    if malformed_intent:
        structured_output.register_malformed(message, CapabilityRef)
    else:
        structured_output.register(
            message,
            CapabilityRef,
            CapabilityRef(
                capability_id=selector,
                arguments={"request": "value"},
                target_system=target_system,
                capability_type=capability_type,
            ),
        )
    llm_provider = MockLLMProvider()
    if llm_completion is not None:
        llm_provider.register(message, llm_completion)
    runtime = RuntimeImpl(
        task_store=task_store,
        session_store=ExistingSessionStore(),
        capability_registry=registry,
        gateway=gateway,
        trace_port=trace_port,
        llm_provider=llm_provider,
        structured_output=structured_output,
        intent_model="test-intent-model",
        response_builder=ResponseEnvelopeBuilder(),
    )

    async def exercise() -> ResponseEnvelope:
        return await runtime.handle_user_message(
            channel=cast(Any, channel),
            ai_user_id="ai-user-1",
            session_id=f"session-{channel}",
            message=message,
            client_capabilities={},
        )

    envelope = asyncio.run(exercise())
    return envelope, task_store, trace_port, gateway, registry


@pytest.mark.parametrize("channel", ["web", "cli"])
def test_exact_active_capability_is_selected_for_web_and_cli(channel: str) -> None:
    capability = _capability("oa.list_pending_workflows")

    envelope, _task_store, trace, gateway, registry = _run_runtime(
        capability.capability_id,
        [capability],
        channel=channel,
    )

    assert envelope.status == "completed"
    assert registry.get_calls == [capability.capability_id]
    assert registry.list_calls == [
        {"target_system": None, "type": None, "status": None}
    ]
    assert gateway.calls == [
        {
            "capability_id": capability.capability_id,
            "arguments": {"request": "value"},
            "channel": channel,
        }
    ]
    selected = next(step for step in trace.steps if step["event_type"] == "capability_selected")
    assert selected["capability_id"] == capability.capability_id


def test_unique_intent_tag_fallback_selects_canonical_capability_id() -> None:
    capability = _capability(
        "oa.list_pending_workflows",
        intent_tags=[" Pending-Workflows "],
    )

    envelope, _task_store, trace, gateway, registry = _run_runtime(
        "pending-workflows",
        [capability],
    )

    assert envelope.status == "completed"
    assert registry.get_calls == ["pending-workflows"]
    assert registry.list_calls == [
        {"target_system": None, "type": None, "status": None},
        {"target_system": None, "type": None, "status": "active"}
    ]
    assert gateway.calls[0]["capability_id"] == capability.capability_id
    selected = next(step for step in trace.steps if step["event_type"] == "capability_selected")
    assert selected["capability_id"] == capability.capability_id


@pytest.mark.parametrize("status", ["draft", "disabled", "deprecated"])
def test_exact_inactive_capability_fails_closed_without_tag_fallback(
    status: CapabilityStatus,
) -> None:
    capability = _capability(
        "oa.list_pending_workflows",
        status=status,
        intent_tags=["pending-workflows"],
    )

    envelope, task_store, trace, gateway, registry = _run_runtime(
        capability.capability_id,
        [capability],
    )

    assert envelope.status == "no_capability_found"
    assert task_store.status_updates[-1][1] == "no_capability_found"
    assert registry.get_calls == [capability.capability_id]
    assert registry.list_calls == [
        {"target_system": None, "type": None, "status": None},
        {"target_system": None, "type": None, "status": "active"},
    ]
    assert gateway.calls == []
    assert "Admin Lite > Registry" in envelope.message
    assert capability.capability_id not in envelope.message
    assert [step["event_type"] for step in trace.steps] == [
        "task_created",
        "intent_parsed",
        "no_capability_found",
        "response_envelope_created",
        "task_failed",
    ]


def test_unregistered_selector_returns_standard_envelope_without_gateway_call() -> None:
    envelope, task_store, trace, gateway, registry = _run_runtime(
        "unknown.capability",
        [],
    )

    assert isinstance(envelope, ResponseEnvelope)
    assert envelope.status == "no_capability_found"
    assert envelope.ui.component_type == "operator_handback_card"
    assert envelope.ui.action == "none"
    assert task_store.status_updates[-1][1] == "no_capability_found"
    assert registry.get_calls == ["unknown.capability"]
    assert registry.list_calls == [
        {"target_system": None, "type": None, "status": None},
        {"target_system": None, "type": None, "status": "active"},
        {"target_system": None, "type": None, "status": "active"}
    ]
    assert gateway.calls == []
    assert "capability_selected" not in {
        step["event_type"] for step in trace.steps
    }


def test_ambiguous_intent_tag_is_order_independent_and_fails_closed() -> None:
    first = _capability("oa.first", intent_tags=["shared-intent"])
    second = _capability("oa.second", intent_tags=["SHARED-INTENT"])

    outcomes = []
    for capabilities in ([first, second], [second, first]):
        envelope, task_store, trace, gateway, registry = _run_runtime(
            "shared-intent",
            list(capabilities),
        )
        outcomes.append(
            (
                envelope.status,
                task_store.status_updates[-1][1],
                [step["event_type"] for step in trace.steps],
                len(gateway.calls),
                registry.list_calls,
            )
        )

    assert outcomes[0] == outcomes[1]
    assert outcomes[0][0:2] == ("no_capability_found", "no_capability_found")
    assert outcomes[0][3] == 0


def test_exact_id_wins_over_other_capability_tag_regardless_of_registry_order() -> None:
    exact = _capability("oa.exact")
    tag_decoy = _capability("oa.decoy", intent_tags=["oa.exact"])

    selected_ids = []
    for capabilities in ([exact, tag_decoy], [tag_decoy, exact]):
        envelope, _task_store, _trace, gateway, registry = _run_runtime(
            "oa.exact",
            list(capabilities),
        )
        assert envelope.status == "completed"
        assert registry.list_calls == [
            {"target_system": None, "type": None, "status": None}
        ]
        selected_ids.append(gateway.calls[0]["capability_id"])

    assert selected_ids == ["oa.exact", "oa.exact"]


def test_exact_active_capability_must_match_intent_constraints() -> None:
    capability = _capability("oa.list_pending_workflows")

    envelope, task_store, trace, gateway, registry = _run_runtime(
        capability.capability_id,
        [capability],
        target_system="u8",
    )

    assert envelope.status == "no_capability_found"
    assert task_store.status_updates[-1][1:] == (
        "no_capability_found",
        "capability_not_found",
    )
    assert registry.get_calls == [capability.capability_id]
    assert registry.list_calls == [
        {"target_system": None, "type": None, "status": None},
        {"target_system": None, "type": None, "status": "active"},
    ]
    assert gateway.calls == []
    no_capability = next(
        step for step in trace.steps if step["event_type"] == "no_capability_found"
    )
    assert no_capability["error_code"] == "capability_not_found"
    assert no_capability["attributes"] == {"reason": "no_unique_active_candidate"}


def test_tag_selection_filters_by_target_system_and_capability_type() -> None:
    query = _capability("oa.query", intent_tags=["shared-intent"])
    action = _capability(
        "oa.action",
        intent_tags=["shared-intent"],
        capability_type="action",
    )
    other_system = _capability("u8.query", intent_tags=["shared-intent"])

    envelope, _task_store, trace, gateway, registry = _run_runtime(
        "shared-intent",
        [query, action, other_system],
        target_system="oa",
        capability_type="action",
    )

    assert envelope.status == "completed"
    assert registry.list_calls == [
        {"target_system": None, "type": None, "status": None},
        {"target_system": "oa", "type": "action", "status": "active"}
    ]
    assert gateway.calls[0]["capability_id"] == "oa.action"
    intent_event = next(
        step for step in trace.steps if step["event_type"] == "intent_parsed"
    )
    assert intent_event["attributes"] == {
        "result": "valid",
        "intent_fingerprint": (
            "5e7b0ce7c4c1dc054d4e768a2c0287032f9104902dc75071c5e4edf164cdc1d6"
        ),
        "target_system": "oa",
        "capability_type": "action",
    }
    selected = next(
        step for step in trace.steps if step["event_type"] == "capability_selected"
    )
    assert selected["attributes"] == {
        "intent_fingerprint": (
            "5e7b0ce7c4c1dc054d4e768a2c0287032f9104902dc75071c5e4edf164cdc1d6"
        ),
        "selection_rule": "unique_intent_tag",
    }
    assert len(_task_store.events) == 1
    persisted_task_id, persisted_event = _task_store.events[0]
    assert persisted_task_id == _task_store.created[0].task_id
    assert persisted_event.event_type == "capability_selected"
    assert persisted_event.payload == {
        "capability_id": "oa.action",
        "selection_rule": "unique_intent_tag",
    }


def test_model_generated_intent_is_fingerprinted_before_trace() -> None:
    sensitive_intent = "access_token=synthetic-secret-value"
    capability = _capability("oa.safe", intent_tags=[sensitive_intent])

    envelope, _task_store, trace, gateway, _registry = _run_runtime(
        sensitive_intent,
        [capability],
    )

    assert envelope.status == "completed"
    assert gateway.calls[0]["capability_id"] == "oa.safe"
    serialized_trace = repr(trace.steps)
    assert sensitive_intent not in serialized_trace
    intent_event = next(
        step for step in trace.steps if step["event_type"] == "intent_parsed"
    )
    selected_event = next(
        step for step in trace.steps if step["event_type"] == "capability_selected"
    )
    assert intent_event["attributes"]["intent_fingerprint"]
    assert (
        selected_event["attributes"]["intent_fingerprint"]
        == intent_event["attributes"]["intent_fingerprint"]
    )


def test_provider_failure_and_invalid_intent_have_distinct_safe_trace_reasons() -> None:
    capability = _capability("oa.safe")
    cases: list[tuple[LLMCompletionResponse | None, bool, str]] = [
        (LLMCompletionResponse(error_code="timeout"), False, "provider_error"),
        (None, True, "structured_output_error"),
    ]
    observed_reasons: list[str] = []

    for llm_completion, malformed_intent, expected_reason in cases:
        envelope, task_store, trace, gateway, registry = _run_runtime(
            capability.capability_id,
            [capability],
            llm_completion=llm_completion,
            malformed_intent=malformed_intent,
        )

        assert envelope.status == "no_capability_found"
        assert task_store.status_updates[-1][1:] == (
            "no_capability_found",
            "capability_not_found",
        )
        assert registry.get_calls == []
        assert registry.list_calls == [
            {"target_system": None, "type": None, "status": None},
            {"target_system": None, "type": None, "status": "active"},
        ]
        assert gateway.calls == []
        intent_event = next(
            step for step in trace.steps if step["event_type"] == "intent_parsed"
        )
        no_capability_event = next(
            step for step in trace.steps if step["event_type"] == "no_capability_found"
        )
        assert intent_event["attributes"] == {
            "result": "invalid",
            "reason": expected_reason,
        }
        assert no_capability_event["attributes"] == {"reason": expected_reason}
        observed_reasons.append(expected_reason)

    assert observed_reasons == ["provider_error", "structured_output_error"]
