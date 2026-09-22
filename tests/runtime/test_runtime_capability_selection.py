"""Registry-backed Runtime capability-selection behavior."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from app.infra.adapters.oa.adapter import OAReadAdapter
from app.infra.gateway.capability_gateway import CapabilityGateway
from app.infra.identity.mock_identity_mapping import MockIdentityMapping
from app.infra.llm.json_structured_output import JSONStructuredOutputProvider
from app.infra.llm.mock_llm.mock_llm_provider import MockLLMProvider
from app.infra.llm.mock_structured_output.mock_structured_output_provider import (
    MockStructuredOutputProvider,
)
from app.infra.orchestration.agent_adapter import AgentOrchestrationAdapter
from app.infra.policy.minimal_policy_guard import MinimalPolicyGuard
from app.infra.sdui.response_envelope_builder import ResponseEnvelopeBuilder
from app.ports.adapter import AdapterResult
from app.ports.capability_gateway import ExecutionResult, RequestOrgContext
from app.ports.capability_registry import (
    CapabilitySpec,
    CapabilityStatus,
    CapabilityTargetSystem,
    CapabilityType,
)
from app.ports.human_gate import HumanGateConflictError
from app.ports.identity_mapping import IdentityCheckResult
from app.ports.llm_provider import LLMCompletionResponse
from app.ports.policy_guard import PolicyDecision
from app.ports.response_envelope import ResponseEnvelope
from app.ports.task_store import SessionRecord, TaskEventRecord, TaskRecord
from app.runtime.models import IntentOutput, MatchedIntent
from app.runtime.runtime import RuntimeImpl
from tests.runtime.principal_fakes import runtime_principal
from tests.runtime.registry_fakes import runtime_output_schema, schema_digest


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
        return self.created[0].model_copy(update={"status": status, "error_code": error_code})

    async def append_event(self, task_id: str, event: TaskEventRecord) -> None:
        self.events.append((task_id, event))

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


class ExistingSessionStore:
    async def create_session(self, record: SessionRecord) -> SessionRecord:
        return record

    async def get_session(self, session_id: str, *, tenant_id: str) -> SessionRecord | None:
        return SessionRecord.model_validate({"tenant_id": tenant_id, "session_id": session_id})


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
        **_owner: Any,
    ) -> None:
        self.steps.append(
            {
                "event_type": "gateway_pre_recorded",
                "status": status,
                "capability_id": capability_id,
                "error_code": error_code,
                "attributes": attributes or {},
            }
        )

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


class RecordingPolicyGuard:
    def __init__(self) -> None:
        self.call_count = 0

    async def decide(self, **kwargs: Any) -> PolicyDecision:
        self.call_count += 1
        return PolicyDecision(decision="allow")


class RecordingAdapter:
    def __init__(self, delegate: OAReadAdapter) -> None:
        self._delegate = delegate
        self.call_count = 0

    async def execute(
        self,
        capability_id: str,
        arguments: dict[str, Any],
        execution_context: dict[str, Any],
    ) -> AdapterResult:
        self.call_count += 1
        return await self._delegate.execute(capability_id, arguments, execution_context)


class SentinelOAProvider:
    requires_credential = False

    def __init__(self) -> None:
        self.call_count = 0

    async def list_pending_workflows(self, credential: Any = None) -> Any:
        self.call_count += 1
        raise AssertionError("OA provider must not be called for invalid arguments")

    async def list_system_messages(self, credential: Any = None) -> Any:
        self.call_count += 1
        raise AssertionError("OA provider must not be called for invalid arguments")


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
        self.list_calls.append({"target_system": target_system, "type": type, "status": status})
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
    input_schema: dict[str, Any] | None = None,
) -> CapabilitySpec:
    output_schema = runtime_output_schema("test_runtime_capability_selection.default")
    return CapabilitySpec(
        capability_id=capability_id,
        name=capability_id,
        type=capability_type,
        intent_tags=intent_tags or [],
        input_schema=input_schema or {},
        input_schema_digest=f"input-{capability_id}",
        output_schema=output_schema,
        output_schema_digest=schema_digest(output_schema),
        risk_level="low",
        owner="runtime-selection-test",
        version="1.0.0",
        status=status,
        short_description=capability_id,
        target_system="oa" if capability_id.startswith("oa.") else None,
        execution_identity="user_delegated",
        binding_required=False,
    )


def _ready_single_selection() -> dict[str, Any]:
    from app.knowledge.capability_selection import select_capability_candidates

    selection = select_capability_candidates("select oa.safe", (_capability("oa.safe"),))
    assert selection.outcome == "ready"
    return selection.trace_attributes()


_READY_SINGLE_SELECTION = _ready_single_selection()


def _run_runtime(
    selector: str,
    capabilities: list[CapabilitySpec],
    *,
    channel: str = "web",
    target_system: CapabilityTargetSystem | None = None,
    capability_type: CapabilityType | None = None,
    llm_completion: LLMCompletionResponse | None = None,
    malformed_intent: bool = False,
    structured_output_override: Any | None = None,
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
    structured_output = structured_output_override or MockStructuredOutputProvider()
    message = f"select {selector}"
    if isinstance(structured_output, MockStructuredOutputProvider):
        if malformed_intent:
            structured_output.register_malformed(message, IntentOutput)
        else:
            structured_output.register(
                message,
                IntentOutput,
                MatchedIntent(
                    match="capability",
                    capability_id=selector,
                    arguments={"request": "value"},
                    target_system=target_system,
                    capability_type=capability_type,
                ),
            )
    llm_provider = MockLLMProvider()
    if llm_completion is not None:
        llm_provider.register(message, llm_completion)
    orchestration_registry = registry
    orchestration_workflow = None
    orchestration_builder = ResponseEnvelopeBuilder()
    runtime = RuntimeImpl(
        candidate_policy=MinimalPolicyGuard(),
        task_store=task_store,
        session_store=ExistingSessionStore(),
        capability_registry=orchestration_registry,
        orchestration=AgentOrchestrationAdapter(
            capability_registry=orchestration_registry,
            gateway=gateway,
            workflow_engine=orchestration_workflow,
            response_builder=orchestration_builder,
        ),
        trace_port=trace_port,
        llm_provider=llm_provider,
        structured_output=structured_output,
        intent_model="test-intent-model",
        response_builder=orchestration_builder,
    )

    async def exercise() -> ResponseEnvelope:
        return await runtime.handle_user_message(
            channel=cast(Any, channel),
            principal=runtime_principal("ai-user-1"),
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
    assert registry.list_calls == [{"target_system": None, "type": None, "status": "active"}]
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
    # The tag is resolved inside this request's admitted candidates, then the
    # canonical exact ID is re-read once; no full-Registry tag search remains.
    assert registry.get_calls == [capability.capability_id]
    assert registry.list_calls == [{"target_system": None, "type": None, "status": "active"}]
    assert gateway.calls[0]["capability_id"] == capability.capability_id
    selected = next(step for step in trace.steps if step["event_type"] == "capability_selected")
    assert selected["capability_id"] == capability.capability_id
    assert selected["attributes"]["selection_rule"] == "unique_intent_tag"


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
    # An inactive definition is never visible, so no model call or re-read happens.
    assert registry.get_calls == []
    assert registry.list_calls == [{"target_system": None, "type": None, "status": "active"}]
    assert gateway.calls == []
    assert "Admin Lite > Registry" not in envelope.message
    assert capability.capability_id not in envelope.message
    assert [step["event_type"] for step in trace.steps] == [
        "task_created",
        "intent_parsed",
        "no_capability_found",
        "response_envelope_created",
        "task_failed",
        "evaluation_recorded",
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
    assert registry.get_calls == []
    assert registry.list_calls == [{"target_system": None, "type": None, "status": "active"}]
    assert gateway.calls == []
    assert "capability_selected" not in {step["event_type"] for step in trace.steps}


def test_runtime_rejects_outside_candidate_and_preserves_tag_compatibility() -> None:
    ninth = _capability("oa.ninth")
    candidate = _capability("oa.candidate", intent_tags=["candidate-tag"])
    exact_decoy = _capability("oa.decoy", intent_tags=["oa.candidate"])

    outside, outside_store, outside_trace, outside_gateway, outside_registry = _run_runtime(
        "oa.unlisted",
        [ninth, candidate],
    )
    tagged, _tag_store, tag_trace, tag_gateway, _tag_registry = _run_runtime(
        "candidate-tag",
        [ninth, candidate],
    )
    exact, _exact_store, _exact_trace, exact_gateway, _exact_registry = _run_runtime(
        "oa.candidate",
        [exact_decoy, candidate],
    )
    conflicting, conflict_store, _conflict_trace, conflict_gateway, conflict_registry = (
        _run_runtime("oa.candidate", [candidate], capability_type="action")
    )

    assert outside.status == "failed"
    assert outside_store.status_updates[-1][1:] == (
        "failed",
        "capability_candidate_out_of_scope",
    )
    assert outside.message == "本次能力选择无效，请重新描述请求。"
    assert outside_registry.get_calls == []
    assert outside_gateway.calls == []
    outside_intent = next(
        step for step in outside_trace.steps if step["event_type"] == "intent_parsed"
    )
    assert outside_intent["status"] == "failed"
    assert outside_intent["error_code"] == "capability_candidate_out_of_scope"
    assert outside_intent["attributes"]["reason"] == "candidate_out_of_scope"
    assert "oa.unlisted" not in repr(outside_trace.steps)
    assert tagged.status == "completed"
    assert tag_gateway.calls[0]["capability_id"] == "oa.candidate"
    tag_selected = next(
        step for step in tag_trace.steps if step["event_type"] == "capability_selected"
    )
    assert tag_selected["attributes"]["selection_rule"] == "unique_intent_tag"
    assert exact.status == "completed"
    assert exact_gateway.calls[0]["capability_id"] == "oa.candidate"
    assert conflicting.status == "failed"
    assert conflict_store.status_updates[-1][2] == "capability_candidate_out_of_scope"
    assert conflict_registry.get_calls == []
    assert conflict_gateway.calls == []


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
    assert task_store.status_updates[-1][2] == "capability_not_found"
    no_match = next(step for step in trace.steps if step["event_type"] == "no_capability_found")
    assert no_match["attributes"]["reason"] == "no_unique_active_candidate"
    assert "no_capability_found" in outcomes[0][2]
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
        assert registry.list_calls == [{"target_system": None, "type": None, "status": "active"}]
        selected_ids.append(gateway.calls[0]["capability_id"])

    assert selected_ids == ["oa.exact", "oa.exact"]


def test_exact_active_capability_must_match_intent_constraints() -> None:
    capability = _capability("oa.list_pending_workflows")

    envelope, task_store, trace, gateway, registry = _run_runtime(
        capability.capability_id,
        [capability],
        target_system="u8",
    )

    assert envelope.status == "failed"
    assert task_store.status_updates[-1][1:] == (
        "failed",
        "capability_candidate_out_of_scope",
    )
    assert registry.get_calls == []
    assert registry.list_calls == [{"target_system": None, "type": None, "status": "active"}]
    assert gateway.calls == []
    assert all(step["event_type"] != "no_capability_found" for step in trace.steps)
    intent_event = next(step for step in trace.steps if step["event_type"] == "intent_parsed")
    assert intent_event["error_code"] == "capability_candidate_out_of_scope"
    assert intent_event["attributes"]["reason"] == "candidate_out_of_scope"


def test_tag_selection_filters_by_target_system_and_capability_type() -> None:
    query = _capability("oa.query", intent_tags=["shared-intent"])
    action = _capability(
        "oa.action",
        intent_tags=["shared-intent"],
        capability_type="action",
    )
    other_system = _capability("u8.query", intent_tags=["shared-intent"])
    colliding, _collision_store, _collision_trace, collision_gateway, _registry = _run_runtime(
        "shared-intent",
        [query, action, other_system],
        target_system="oa",
        capability_type="action",
    )
    unique_query = _capability("oa.query")
    unique_other = _capability("u8.query")

    envelope, _task_store, trace, gateway, registry = _run_runtime(
        "shared-intent",
        [unique_query, action, unique_other],
        target_system="oa",
        capability_type="action",
    )

    # A tag shared by several admitted candidates is not disambiguated by type/target.
    assert colliding.status == "no_capability_found"
    assert _collision_store.status_updates[-1][2] == "capability_not_found"
    assert collision_gateway.calls == []
    assert envelope.status == "completed"
    assert registry.list_calls == [{"target_system": None, "type": None, "status": "active"}]
    assert registry.get_calls == ["oa.action"]
    assert gateway.calls[0]["capability_id"] == "oa.action"
    intent_event = next(step for step in trace.steps if step["event_type"] == "intent_parsed")
    selection_summary = {
        key: intent_event["attributes"][key]
        for key in (
            "protocol_version",
            "outcome",
            "coverage_complete",
            "visible_count",
            "selected_count",
            "omitted_count",
            "truncated_by",
            "payload_bytes",
        )
    }
    assert intent_event["attributes"] == {
        "result": "valid",
        "intent_fingerprint": ("5e7b0ce7c4c1dc054d4e768a2c0287032f9104902dc75071c5e4edf164cdc1d6"),
        "target_system": "oa",
        "capability_type": "action",
        **selection_summary,
    }
    assert selection_summary["outcome"] == "ready"
    assert selection_summary["coverage_complete"] is True
    assert selection_summary["visible_count"] == selection_summary["selected_count"] == 3
    selected = next(step for step in trace.steps if step["event_type"] == "capability_selected")
    assert selected["attributes"] == {
        "intent_fingerprint": ("5e7b0ce7c4c1dc054d4e768a2c0287032f9104902dc75071c5e4edf164cdc1d6"),
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
    legacy_capability = _capability("oa.safe").model_copy(
        update={"intent_tags": [sensitive_intent]}
    )
    safe_capability = _capability("oa.safe", intent_tags=["safe-tag"])

    rejected, rejected_store, rejected_trace, rejected_gateway, _ = _run_runtime(
        sensitive_intent,
        [legacy_capability],
    )
    outside, _outside_store, outside_trace, outside_gateway, _ = _run_runtime(
        sensitive_intent,
        [safe_capability],
    )
    envelope, _task_store, trace, gateway, _registry = _run_runtime(
        "safe-tag",
        [safe_capability],
    )

    # A bypassed invalid tag now fails the whole visible catalog before the model.
    assert rejected.status == "failed"
    assert rejected_store.status_updates[-1][2] == "capability_catalog_invalid"
    assert rejected_gateway.calls == []
    assert outside.status == "failed"
    assert outside_gateway.calls == []
    for steps in (rejected_trace.steps, outside_trace.steps):
        assert sensitive_intent not in repr(steps)
    assert envelope.status == "completed"
    assert gateway.calls[0]["capability_id"] == "oa.safe"
    intent_event = next(step for step in trace.steps if step["event_type"] == "intent_parsed")
    selected_event = next(
        step for step in trace.steps if step["event_type"] == "capability_selected"
    )
    assert intent_event["attributes"]["intent_fingerprint"]
    assert (
        selected_event["attributes"]["intent_fingerprint"]
        == intent_event["attributes"]["intent_fingerprint"]
    )


@pytest.mark.parametrize(
    (
        "llm_completion",
        "malformed_intent",
        "expected_reason",
        "expected_subcode",
        "message_fragment",
    ),
    [
        (
            LLMCompletionResponse(
                error_code="provider_error",
                error_message="sensitive-model-failure-detail",
            ),
            False,
            "provider_error",
            None,
            "模型服务暂时无法连接或响应",
        ),
        (
            LLMCompletionResponse(content=None),
            False,
            "empty_response",
            None,
            "模型返回的查询结果暂时无法识别",
        ),
        (
            None,
            True,
            "structured_output_error",
            "parse_error",
            "模型返回的查询结果暂时无法识别",
        ),
    ],
)
def test_intent_boundary_failures_return_safe_failed_envelopes_without_registry_advice(
    llm_completion: LLMCompletionResponse | None,
    malformed_intent: bool,
    expected_reason: str,
    expected_subcode: str | None,
    message_fragment: str,
) -> None:
    capability = _capability("oa.safe")
    canary = "sensitive-model-failure-detail"

    envelope, task_store, trace, gateway, registry = _run_runtime(
        capability.capability_id,
        [capability],
        llm_completion=llm_completion,
        malformed_intent=malformed_intent,
    )

    assert envelope.status == "failed"
    assert task_store.status_updates[-1][1:] == ("failed", "internal_error")
    assert registry.get_calls == []
    assert registry.list_calls == [{"target_system": None, "type": None, "status": "active"}]
    assert gateway.calls == []
    assert message_fragment in envelope.message
    assert "暂未接入该能力" not in envelope.message
    assert "Admin Lite" not in envelope.message
    assert all(step["event_type"] != "no_capability_found" for step in trace.steps)
    intent_event = next(step for step in trace.steps if step["event_type"] == "intent_parsed")
    expected_attributes: dict[str, Any] = {"result": "invalid", "reason": expected_reason}
    if expected_subcode is not None:
        expected_attributes["structured_output_error_code"] = expected_subcode
    assert intent_event["attributes"] == {**_READY_SINGLE_SELECTION, **expected_attributes}
    serialized = repr((envelope, task_store.status_updates, trace.steps, gateway.calls))
    assert canary not in serialized


def test_intent_validation_trace_has_only_safe_diagnostics_and_no_rejected_value(
    caplog: pytest.LogCaptureFixture,
) -> None:
    canary = "must-not-enter-trace-log-or-response"
    raw_intent = (
        '{"match":"capability","capability_id":"oa.safe","arguments":{"user":"'
        + canary
        + '"},"target_system":"oa","capability_type":"query","'
        + canary
        + '":"rejected-extra-value"}'
    )
    capability = _capability("oa.safe")

    envelope, task_store, trace, gateway, registry = _run_runtime(
        capability.capability_id,
        [capability],
        llm_completion=LLMCompletionResponse(content=raw_intent),
        structured_output_override=JSONStructuredOutputProvider(),
    )

    assert envelope.status == "failed"
    assert task_store.status_updates[-1][1:] == ("failed", "internal_error")
    assert registry.get_calls == []
    assert gateway.calls == []
    intent_event = next(step for step in trace.steps if step["event_type"] == "intent_parsed")
    assert intent_event["attributes"] == {
        **_READY_SINGLE_SELECTION,
        "result": "invalid",
        "reason": "schema_invalid",
        "structured_output_error_code": "validation_error",
        "error_path": "$",
        "error_type": "extra_forbidden",
        "argument_keys": ["user"],
    }
    serialized = repr((envelope, task_store.status_updates, trace.steps, gateway.calls))
    assert canary not in serialized
    assert canary not in caplog.text
    assert "Admin Lite" not in envelope.message


def test_runtime_real_gateway_rejects_schema_invalid_arguments_before_policy_adapter_and_oa(
    caplog: pytest.LogCaptureFixture,
) -> None:
    canary = "must-not-enter-runtime-response-trace-or-log"
    capability = _capability(
        "oa.list_pending_workflows",
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    )
    registry = StaticRegistry([capability])
    task_store = RecordingTaskStore()
    trace = RecordingTracePort()
    policy = RecordingPolicyGuard()
    oa_provider = SentinelOAProvider()
    adapter = RecordingAdapter(OAReadAdapter(oa_provider))
    gateway = CapabilityGateway(
        adapter=adapter,
        capability_registry=registry,
        policy_guard=policy,
        trace_port=trace,
        tenant_id="tenant-test",
    )
    message = "query pending workflows"
    llm_provider = MockLLMProvider()
    llm_provider.register(
        message,
        LLMCompletionResponse(
            content=(
                '{"match":"capability","capability_id":"oa.list_pending_workflows",'
                f'"arguments":{{"user":"{canary}"}},'
                '"target_system":"oa","capability_type":"query"}'
            )
        ),
    )
    orchestration_registry = registry
    orchestration_workflow = None
    orchestration_builder = ResponseEnvelopeBuilder()
    runtime = RuntimeImpl(
        candidate_policy=MinimalPolicyGuard(),
        task_store=task_store,
        session_store=ExistingSessionStore(),
        capability_registry=orchestration_registry,
        orchestration=AgentOrchestrationAdapter(
            capability_registry=orchestration_registry,
            gateway=gateway,
            workflow_engine=orchestration_workflow,
            response_builder=orchestration_builder,
        ),
        trace_port=trace,
        llm_provider=llm_provider,
        structured_output=JSONStructuredOutputProvider(),
        intent_model="test-intent-model",
        response_builder=orchestration_builder,
    )

    envelope = asyncio.run(
        runtime.handle_user_message(
            channel="web",
            principal=runtime_principal("ai-user-1"),
            session_id="session-web",
            message=message,
            client_capabilities={},
        )
    )

    assert envelope.status == "failed"
    assert task_store.status_updates[-1][1:] == ("failed", "adapter_error")
    assert policy.call_count == 0
    assert adapter.call_count == 0
    assert oa_provider.call_count == 0
    gateway_event = next(
        step for step in trace.steps if step["event_type"] == "gateway_pre_recorded"
    )
    assert gateway_event["attributes"] == {
        "error_path": "$.arguments",
        "error_type": "additionalProperties",
        "argument_keys": ["user"],
    }
    serialized = repr((envelope, task_store.status_updates, trace.steps))
    assert canary not in serialized
    assert canary not in caplog.text


class DriftingRegistry(StaticRegistry):
    """Serve the listed snapshot, then a changed exact definition after the model."""

    def __init__(
        self,
        capabilities: list[CapabilitySpec],
        *,
        drifted: CapabilitySpec | None,
    ) -> None:
        super().__init__(capabilities)
        self.drifted = drifted

    async def get(self, capability_id: str) -> CapabilitySpec | None:
        self.get_calls.append(capability_id)
        if self.drifted is not None and self.drifted.capability_id != capability_id:
            return None
        return self.drifted


class ContextGateway:
    def __init__(self) -> None:
        self.request_contexts: list[RequestOrgContext] = []
        self.ai_user_ids: list[str] = []

    async def execute_capability(
        self,
        task_id: str,
        session_id: str,
        ai_user_id: str,
        capability_id: str,
        arguments: dict[str, Any],
        request_context: RequestOrgContext,
    ) -> ExecutionResult:
        self.request_contexts.append(request_context)
        self.ai_user_ids.append(ai_user_id)
        return ExecutionResult(status="completed", data={}, trace_id=request_context.request_id)


class RecordingCandidatePolicy:
    def __init__(self, result: Any = "defer", *, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[str, str, RequestOrgContext]] = []

    async def preview_capability(
        self,
        *,
        ai_user_id: str,
        capability_id: str,
        request_context: RequestOrgContext,
    ) -> Any:
        self.calls.append((ai_user_id, capability_id, request_context))
        if self.error is not None:
            raise self.error
        return self.result


class DecisionPolicyGuard:
    def __init__(self, decision: PolicyDecision) -> None:
        self.decision = decision
        self.call_count = 0

    async def decide(self, **kwargs: Any) -> PolicyDecision:
        self.call_count += 1
        return self.decision


class StatusIdentityMapping:
    def __init__(self, bind_status: str) -> None:
        self.bind_status = bind_status
        self.call_count = 0

    async def resolve_execution_identity(
        self,
        ai_user_id: str,
        target_system: Any,
        execution_identity: Any,
        request_context: RequestOrgContext,
    ) -> IdentityCheckResult:
        self.call_count += 1
        return IdentityCheckResult(
            bind_status=cast(Any, self.bind_status),
            target_system=target_system,
            execution_identity=execution_identity,
        )


class CountingSuccessAdapter:
    def __init__(self) -> None:
        self.call_count = 0

    async def execute(
        self,
        capability_id: str,
        arguments: dict[str, Any],
        execution_context: dict[str, Any],
    ) -> AdapterResult:
        self.call_count += 1
        return AdapterResult(status="success", data={})


def _handle_with(
    *,
    registry: StaticRegistry,
    gateway: Any,
    candidate_policy: Any,
    completion: str,
    message: str = "select oa.target",
    principal_tenant: str = "tenant-test",
    human_gate: Any = None,
) -> tuple[ResponseEnvelope, RecordingTaskStore, RecordingTracePort, MockLLMProvider]:
    task_store = RecordingTaskStore()
    trace_port = RecordingTracePort()
    llm_provider = MockLLMProvider()
    llm_provider.register(message, LLMCompletionResponse(content=completion))
    builder = ResponseEnvelopeBuilder()
    runtime = RuntimeImpl(
        human_gate_port=human_gate,
        candidate_policy=candidate_policy,
        task_store=task_store,
        session_store=ExistingSessionStore(),
        capability_registry=registry,
        orchestration=AgentOrchestrationAdapter(
            capability_registry=registry,
            gateway=gateway,
            workflow_engine=None,
            response_builder=builder,
        ),
        trace_port=trace_port,
        llm_provider=llm_provider,
        structured_output=JSONStructuredOutputProvider(),
        intent_model="test-intent-model",
        response_builder=builder,
    )
    envelope = asyncio.run(
        runtime.handle_user_message(
            channel="web",
            principal=runtime_principal("ai-user-topk", tenant_id=principal_tenant),
            session_id="session-web",
            message=message,
            client_capabilities={},
        )
    )
    return envelope, task_store, trace_port, llm_provider


_SELECT_TARGET = '{"match":"capability","capability_id":"oa.target","arguments":{}}'


def test_selected_definition_change_fails_before_gateway() -> None:
    listed = _capability("oa.target")
    drifts = {
        "disabled": listed.model_copy(update={"status": "disabled"}),
        "deleted": None,
        "schema_changed": listed.model_copy(
            update={"input_schema": {"type": "object", "properties": {"x": {}}}}
        ),
        "owner_changed": listed.model_copy(update={"owner": "another-owner"}),
        "version_upgraded": listed.model_copy(update={"version": "2.0.0"}),
    }

    for label, drifted in drifts.items():
        gateway = RecordingGateway()
        registry = DriftingRegistry([listed], drifted=drifted)
        envelope, task_store, trace, llm = _handle_with(
            registry=registry,
            gateway=gateway,
            candidate_policy=MinimalPolicyGuard(),
            completion=_SELECT_TARGET,
        )
        assert envelope.status == "failed", label
        assert task_store.status_updates[-1][1:] == ("failed", "capability_candidate_stale")
        assert envelope.message == "能力配置已变化，请重新发起请求。"
        assert gateway.calls == []
        assert len(llm.calls) == 1
        intent_events = [step for step in trace.steps if step["event_type"] == "intent_parsed"]
        assert len(intent_events) == 1
        assert intent_events[0]["status"] == "failed"
        assert intent_events[0]["attributes"]["reason"] == "candidate_stale"
        assert "capability_selected" not in {step["event_type"] for step in trace.steps}

    control_gateway = RecordingGateway()
    control, control_store, _trace, _llm = _handle_with(
        registry=DriftingRegistry([listed], drifted=listed.model_copy(deep=True)),
        gateway=control_gateway,
        candidate_policy=MinimalPolicyGuard(),
        completion=_SELECT_TARGET,
    )
    assert control.status == "completed"
    assert control_store.status_updates[-1][1] == "completed"
    assert [call["capability_id"] for call in control_gateway.calls] == ["oa.target"]


def test_preview_never_replaces_gateway_authorization() -> None:
    capability = _capability("oa.target")
    cases = [
        (PolicyDecision(decision="deny", reason_code="policy_denied"), "active", "policy_denied"),
        (
            PolicyDecision(decision="confirm", required_action="confirm"),
            "active",
            "confirm_required",
        ),
        (PolicyDecision(decision="allow"), "unbound", "identity_unbound"),
        (PolicyDecision(decision="allow"), "needs_binding_scope", "needs_binding_scope"),
        (PolicyDecision(decision="allow"), "active", None),
    ]

    for decision, bind_status, expected_error in cases:
        registry = StaticRegistry([capability])
        adapter = CountingSuccessAdapter()
        policy = DecisionPolicyGuard(decision)
        identity = StatusIdentityMapping(bind_status)
        candidate_policy = RecordingCandidatePolicy("defer")
        gateway = CapabilityGateway(
            adapter=adapter,
            capability_registry=registry,
            identity_mapping=cast(Any, identity),
            policy_guard=policy,
            trace_port=RecordingTracePort(),
            tenant_id="tenant-test",
        )

        _envelope, task_store, _trace, _llm = _handle_with(
            registry=registry,
            gateway=gateway,
            candidate_policy=candidate_policy,
            completion=_SELECT_TARGET,
        )

        assert [call[1] for call in candidate_policy.calls] == ["oa.target"]
        assert identity.call_count == 1
        assert task_store.status_updates[-1][2] == expected_error
        if expected_error is None:
            assert policy.call_count == 1
            assert adapter.call_count == 1
        else:
            assert adapter.call_count == 0


def test_preview_failure_and_execution_context_are_preserved() -> None:
    target = _capability("oa.target")
    admin = _capability("admin_registry_list")
    failures = {
        "raises": RecordingCandidatePolicy(error=RuntimeError("preview-canary")),
        "invalid": RecordingCandidatePolicy("allow"),
    }
    for label, candidate_policy in failures.items():
        gateway = RecordingGateway()
        envelope, task_store, trace, llm = _handle_with(
            registry=StaticRegistry([target]),
            gateway=gateway,
            candidate_policy=candidate_policy,
            completion=_SELECT_TARGET,
        )
        assert envelope.status == "failed", label
        assert task_store.status_updates[-1][1:] == ("failed", "capability_catalog_invalid")
        assert envelope.message == "能力配置暂不可用，请联系管理员核对。"
        assert llm.calls == []
        assert gateway.calls == []
        assert "preview-canary" not in repr((envelope, trace.steps))

    candidate_policy = RecordingCandidatePolicy("defer")
    context_gateway = ContextGateway()
    scoped = (
        '{"match":"capability","capability_id":"oa.target","arguments":'
        '{"account_set_id":"set-1","resource_scope":"scope-1","device_domain_id":"domain-1"}}'
    )
    envelope, _store, _trace, _llm = _handle_with(
        registry=StaticRegistry([target]),
        gateway=context_gateway,
        candidate_policy=candidate_policy,
        completion=scoped,
        principal_tenant="tenant-topk",
    )

    assert envelope.status == "completed"
    ((preview_user, _, preview_context),) = candidate_policy.calls
    (execute_context,) = context_gateway.request_contexts
    assert preview_user == context_gateway.ai_user_ids[0] == "ai-user-topk"
    assert preview_context.request_id == execute_context.request_id == envelope.trace_id
    assert preview_context.channel == execute_context.channel == "web"
    assert preview_context.tenant_id == execute_context.tenant_id == "tenant-topk"
    assert (
        preview_context.account_set_id,
        preview_context.resource_scope,
        preview_context.device_domain_id,
    ) == (None, None, None)
    assert (
        execute_context.account_set_id,
        execute_context.resource_scope,
        execute_context.device_domain_id,
    ) == ("set-1", "scope-1", "domain-1")
    for context in (preview_context, execute_context):
        assert (context.org_id, context.department_id, context.roles) == (None, None, [])

    excluded_gateway = RecordingGateway()
    excluded, excluded_store, excluded_trace, excluded_llm = _handle_with(
        registry=StaticRegistry([admin, target]),
        gateway=excluded_gateway,
        candidate_policy=MinimalPolicyGuard(),
        completion='{"match":"capability","capability_id":"admin_registry_list","arguments":{}}',
    )
    assert excluded.status == "failed"
    assert excluded_store.status_updates[-1][2] == "capability_candidate_out_of_scope"
    assert excluded_gateway.calls == []
    sent = "\n".join(message.content for message in excluded_llm.calls[0]["messages"])
    assert "admin_registry_list" not in sent
    intent = next(step for step in excluded_trace.steps if step["event_type"] == "intent_parsed")
    assert intent["attributes"]["visible_count"] == 1


class _InvalidRowRegistry(StaticRegistry):
    async def list(self, *args: Any, **kwargs: Any) -> list[CapabilitySpec]:
        await super().list(*args, **kwargs)
        CapabilitySpec.model_validate(
            {**_capability("oa.target").model_dump(), "short_description": "row-canary {x}"}
        )
        raise AssertionError("unreachable")


def test_registry_row_validation_error_is_catalog_invalid_without_raw_text() -> None:
    gateway = RecordingGateway()
    envelope, task_store, trace, llm = _handle_with(
        registry=_InvalidRowRegistry([]),
        gateway=gateway,
        candidate_policy=MinimalPolicyGuard(),
        completion=_SELECT_TARGET,
    )

    assert envelope.status == "failed"
    assert task_store.status_updates[-1][1:] == ("failed", "capability_catalog_invalid")
    assert llm.calls == []
    assert gateway.calls == []
    assert "row-canary" not in repr((envelope, trace.steps))


@pytest.mark.parametrize("selector, expected", [("claß", "failed"), ("ｃｌａｓｓ", "completed")])
def test_tag_reference_reuses_registry_character_validation(selector: str, expected: str) -> None:
    envelope, store, _trace, gateway, registry = _run_runtime(
        selector, [_capability("oa.target", intent_tags=["class"])]
    )
    assert envelope.status == expected
    if expected == "failed":
        assert store.status_updates[-1][2] == "capability_candidate_out_of_scope"
        assert registry.get_calls == gateway.calls == []
    else:
        assert registry.get_calls == ["oa.target"]
        assert [call["capability_id"] for call in gateway.calls] == ["oa.target"]


class _InvalidRereadRegistry(StaticRegistry):
    async def get(self, capability_id: str) -> CapabilitySpec | None:
        await super().get(capability_id)
        return CapabilitySpec.model_validate(
            {**_capability(capability_id).model_dump(), "owner": "reread-canary {x}"}
        )


def test_selected_row_validation_error_finishes_stale_without_raw_text() -> None:
    registry = _InvalidRereadRegistry([_capability("oa.target")])
    gateway = RecordingGateway()
    envelope, store, trace, llm = _handle_with(
        registry=registry,
        gateway=gateway,
        candidate_policy=MinimalPolicyGuard(),
        completion=_SELECT_TARGET,
    )
    assert registry.get_calls == ["oa.target"]
    assert len(llm.calls) == 1
    assert envelope.status == "failed"
    assert store.status_updates[-1][1:] == ("failed", "capability_candidate_stale")
    assert gateway.calls == []
    assert [step["event_type"] for step in trace.steps] == [
        "task_created",
        "intent_parsed",
        "response_envelope_created",
        "task_failed",
        "evaluation_recorded",
    ]
    intent = trace.steps[1]
    assert intent["status"] == "failed"
    assert intent["error_code"] == "capability_candidate_stale"
    assert "reread-canary" not in repr((envelope, store.events, trace.steps))


class _ConflictingHumanGate:
    async def bind_task(self, manifest: Any) -> None:
        raise HumanGateConflictError("synthetic version conflict")


@pytest.mark.parametrize("partial", [False, True])
def test_partial_candidate_notice_survives_version_binding_failure(partial: bool) -> None:
    capabilities = [_capability("oa.target")]
    if partial:
        capabilities.extend(_capability(f"oa.filler-{index}") for index in range(8))
    gateway = RecordingGateway()
    envelope, store, trace, _llm = _handle_with(
        registry=StaticRegistry(capabilities),
        gateway=gateway,
        candidate_policy=MinimalPolicyGuard(),
        completion=_SELECT_TARGET,
        human_gate=_ConflictingHumanGate(),
    )
    assert envelope.status == "failed"
    assert store.status_updates[-1][1:] == ("failed", "internal_error")
    assert gateway.calls == []
    assert envelope.message.startswith("任务绑定的执行版本已不可用，本次未执行。")
    assert envelope.message.count("本次仅在相关性最高的部分能力中选择") == int(partial)
    assert envelope.fallback_text.count(
        "This selection considered only the most relevant capabilities"
    ) == int(partial)
    assert [step["event_type"] for step in trace.steps][-3:] == [
        "response_envelope_created",
        "task_failed",
        "evaluation_recorded",
    ]


@pytest.mark.parametrize(
    "scopes, requested_scope, error",
    [
        ([], None, "identity_unbound"),
        (["east", "west"], None, "needs_binding_scope"),
        (["east", "west"], "foreign", "identity_unbound"),
        (["east", "west"], "east", None),
    ],
)
def test_topk_gateway_resolves_binding_rows_before_execution(
    scopes: list[str], requested_scope: str | None, error: str | None
) -> None:
    import json

    # Use the shipped resolver with actual synthetic rows, not precomputed statuses.
    identity = MockIdentityMapping(
        rows=[
            {
                "ai_user_id": "ai-user-topk",
                "target_system": "oa",
                "execution_identity": "user_delegated",
                "bind_status": "active",
                "binding_id": f"binding-{scope}",
                "binding_scope": scope,
            }
            for scope in scopes
        ]
        + [
            {
                "ai_user_id": "another-user",
                "target_system": "oa",
                "execution_identity": "user_delegated",
                "bind_status": "active",
                "binding_id": "foreign-binding",
                "binding_scope": "foreign",
            }
        ],
        tenant_id="tenant-test",
    )
    registry = StaticRegistry([_capability("oa.target")])
    adapter = CountingSuccessAdapter()
    gateway_trace = RecordingTracePort()
    gateway = CapabilityGateway(
        adapter=adapter,
        capability_registry=registry,
        identity_mapping=identity,
        policy_guard=MinimalPolicyGuard(),
        trace_port=gateway_trace,
        tenant_id="tenant-test",
    )
    arguments = {} if requested_scope is None else {"resource_scope": requested_scope}
    envelope, store, _trace, llm = _handle_with(
        registry=registry,
        gateway=gateway,
        candidate_policy=MinimalPolicyGuard(),
        completion=json.dumps(
            {"match": "capability", "capability_id": "oa.target", "arguments": arguments}
        ),
    )
    assert len(llm.calls) == 1
    assert store.status_updates[-1][2] == error
    assert adapter.call_count == int(error is None)
    check = next(step for step in gateway_trace.steps if step["event_type"] == "identity_check")
    if error is None:
        assert envelope.status == "completed"
        assert check["status"] == "ok"
    else:
        assert check["status"] == "blocked"
        assert check["error_code"] == error
