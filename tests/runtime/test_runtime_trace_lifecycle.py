from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from app.infra.gateway.capability_gateway import CapabilityGateway
from app.infra.llm.json_structured_output import JSONStructuredOutputProvider
from app.infra.llm.mock_llm.mock_llm_provider import MockLLMProvider
from app.infra.llm.mock_structured_output.mock_structured_output_provider import (
    MockStructuredOutputProvider,
)
from app.infra.observability.noop_trace_writer import NoopTraceWriter
from app.infra.orchestration.agent_adapter import AgentOrchestrationAdapter
from app.infra.policy.minimal_policy_guard import MinimalPolicyGuard
from app.infra.sdui.response_envelope_builder import ResponseEnvelopeBuilder
from app.memory import SessionMemoryKey
from app.ports.adapter import AdapterResult
from app.ports.capability_gateway import ExecutionResult, ExecutionStatus, RequestOrgContext
from app.ports.llm_provider import LLMCompletionResponse
from app.ports.response_envelope import ResponseEnvelope
from app.ports.task_store import SessionRecord, TaskEventRecord, TaskRecord
from app.runtime.models import IntentOutput, MatchedIntent
from app.runtime.runtime import RuntimeImpl
from tests.runtime.principal_fakes import runtime_principal
from tests.runtime.registry_fakes import StaticCapabilityRegistry, active_capability


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
            tenant_id=original.tenant_id,
            status=cast(Any, status),
            trace_id=original.trace_id,
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
        provider.register_malformed(message, IntentOutput)
    else:
        provider.register(
            message,
            IntentOutput,
            MatchedIntent(match="capability", capability_id="oa.workflow_status.get", arguments={}),
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
    orchestration_registry = StaticCapabilityRegistry("oa.workflow_status.get")
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
        trace_port=writer,
        llm_provider=MockLLMProvider(),
        structured_output=_provider(message, malformed=malformed),
        intent_model="test-intent-model",
        response_builder=orchestration_builder,
    )
    envelope = asyncio.run(
        runtime.handle_user_message(
            channel="mock",
            principal=runtime_principal("synthetic-user"),
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
    orchestration_registry = StaticCapabilityRegistry("oa.workflow_status.get")
    orchestration_workflow = None
    orchestration_builder = ResponseEnvelopeBuilder()
    runtime = RuntimeImpl(
        candidate_policy=MinimalPolicyGuard(),
        task_store=task_store,
        session_store=ExistingSessionStore(),
        capability_registry=orchestration_registry,
        orchestration=AgentOrchestrationAdapter(
            capability_registry=orchestration_registry,
            gateway=CapabilityGateway(adapter=SuccessfulAdapter(), trace_port=writer),
            workflow_engine=orchestration_workflow,
            response_builder=orchestration_builder,
        ),
        trace_port=writer,
        llm_provider=MockLLMProvider(),
        structured_output=_provider(message),
        intent_model="test-intent-model",
        response_builder=orchestration_builder,
    )

    envelope = asyncio.run(
        runtime.handle_user_message(
            channel="mock",
            principal=runtime_principal("synthetic-user"),
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
        "evaluation_recorded",
    ]
    assert event_types.count("task_created") == 1
    assert event_types.count("gateway_pre_recorded") == 1
    assert event_types.count("task_completed") == 1
    assert event_types.count("task_failed") == 0
    assert event_types.count("evaluation_recorded") == 1
    assert logger.events[-1]["attributes"]["evaluation_result"] == "passed"
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
def test_real_writer_terminal_matrix_is_followed_by_one_evaluation(
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
        event_type for event_type in event_types if event_type in {"task_completed", "task_failed"}
    ]
    assert terminals == ([] if expected_terminal is None else [expected_terminal])
    assert event_types.count("task_completed") == (expected_terminal == "task_completed")
    assert event_types.count("task_failed") == (expected_terminal == "task_failed")
    if expected_terminal is not None:
        assert event_types[-3:] == [
            "response_envelope_created",
            expected_terminal,
            "evaluation_recorded",
        ]
        assert event_types.count("evaluation_recorded") == 1
    else:
        assert event_types[-1] == "response_envelope_created"
        assert event_types.count("evaluation_recorded") == 0


def test_real_writer_structured_output_parse_failure_has_one_failed_terminal() -> None:
    _, events, task_store = _run_runtime(
        ResultGateway(ExecutionResult(status="completed", trace_id="unused-synthetic-trace")),
        malformed=True,
    )
    event_types = _event_types(events)

    assert event_types == [
        "task_created",
        "intent_parsed",
        "response_envelope_created",
        "task_failed",
        "evaluation_recorded",
    ]
    assert events[-1]["attributes"]["evaluation_result"] == "failed"
    assert task_store.status_updates[-1][1:] == ("failed", "internal_error")


@pytest.mark.parametrize("kind", ["cancel", "reject", "expired"])
def test_confirmation_terminal_persists_original_task_and_trace(
    kind: str, migrated_database_url: str
) -> None:
    from datetime import timedelta

    from app.db.session import make_async_engine, make_async_session_factory
    from app.event_loop import make_event_loop
    from app.infra.human_gate.postgresql import PostgreSQLHumanGate
    from app.infra.observability.postgresql_trace import (
        PostgreSQLTraceReader,
        PostgreSQLTraceWriter,
    )
    from app.infra.persistence.task_store.postgresql import PostgreSQLTaskStore
    from tests.runtime.test_runtime_user_action import (
        _EXECUTE_ID,
        _build_harness,
        _pending,
        _terminal_action,
    )

    async def exercise() -> None:
        engine = make_async_engine(migrated_database_url)
        try:
            factory = make_async_session_factory(engine)
            store = PostgreSQLTaskStore(factory)
            gate = PostgreSQLHumanGate(factory)
            harness = await _build_harness(
                gate=gate, task_store_override=store, trace_override=PostgreSQLTraceWriter(factory)
            )
            pending = _pending(harness)
            if kind == "expired":
                harness.runtime._utc_clock = lambda: pending.expires_at + timedelta(microseconds=1)
            response = await _terminal_action(harness, "confirm" if kind == "expired" else kind)
            expected = "confirmation_invalidated" if kind == "expired" else "cancelled"
            assert response.status == expected
            assert response.data == {"action_outcome": expected, "result": None}
            # Fresh repository instances open independent database sessions for each read.
            record = await PostgreSQLTaskStore(factory).get_task(pending.task_id)
            assert record is not None
            assert (
                record.status,
                record.trace_id,
                record.ai_user_id,
                record.session_id,
                record.tenant_id,
            ) == (
                expected,
                pending.trace_id,
                harness.principal.ai_user_id,
                "session-action",
                "default",
            )
            expected_error = "confirm_required" if kind == "expired" else None
            assert record.error_code == expected_error
            decision = await PostgreSQLHumanGate(factory).get_decision(pending.gate_request_id)
            if kind == "expired":
                assert decision is None
            else:
                assert decision is not None
                assert decision.decision == "rejected"
                assert (
                    decision.task_id,
                    decision.decided_by_ai_user_id,
                    decision.decided_session_id,
                    decision.decided_tenant_id,
                    decision.request_digest,
                    decision.binding_manifest_digest,
                ) == (
                    pending.task_id,
                    harness.principal.ai_user_id,
                    "session-action",
                    "default",
                    pending.request_digest,
                    pending.binding_manifest_digest,
                )
                # PostgreSQL really permits a byte-equivalent decision replay.
                assert await gate.record_decision(decision) == decision
            reader = PostgreSQLTraceReader(factory)
            events = await reader.list_events_by_trace(pending.trace_id, tenant_id="default")
            terminal = [
                e
                for e in events
                if e.event_type in {"task_cancelled", "task_confirmation_invalidated"}
            ]
            evaluations = [e for e in events if e.event_type == "evaluation_recorded"]
            assert len(terminal) == len(evaluations) == 1
            assert terminal[0].event_type == (
                "task_confirmation_invalidated" if kind == "expired" else "task_cancelled"
            )
            assert (
                terminal[0].task_id,
                terminal[0].trace_id,
                terminal[0].tenant_id,
                terminal[0].ai_user_id,
                terminal[0].error_code,
            ) == (
                pending.task_id,
                pending.trace_id,
                "default",
                harness.principal.ai_user_id,
                expected_error,
            )
            assert evaluations[0].attributes["business_status"] == expected
            assert evaluations[0].attributes["business_error_code"] == expected_error
            again = await _terminal_action(harness, "confirm")
            assert again.status == expected
            after = await reader.list_events_by_trace(pending.trace_id, tenant_id="default")
            assert [e.event_id for e in after] == [e.event_id for e in events]
            assert harness.engine.resume_calls == 0
            assert [c[0] for c in harness.gateway.calls].count(_EXECUTE_ID) == 0
        finally:
            await engine.dispose()

    asyncio.run(exercise(), loop_factory=make_event_loop)


class _DriftedRegistry(StaticCapabilityRegistry):
    async def get(self, capability_id: str) -> Any:
        listed = await super().get(capability_id)
        if listed is None:
            return None
        return listed.model_copy(update={"version": "9.9.9"})


class _FailingIntentTrace(NoopTraceWriter):
    async def record_step(self, *args: Any, **kwargs: Any) -> None:
        if kwargs.get("event_type") == "intent_parsed":
            raise RuntimeError("synthetic trace write failure")
        await super().record_step(*args, **kwargs)


class _CountingGateway(ResultGateway):
    def __init__(self) -> None:
        super().__init__(ExecutionResult(status="completed", data={}, trace_id="unused"))
        self.calls = 0

    async def execute_capability(self, *args: Any, **kwargs: Any) -> ExecutionResult:
        self.calls += 1
        return await super().execute_capability(*args, **kwargs)


def _candidate_runtime(
    registry: StaticCapabilityRegistry,
    completion: str,
    *,
    message: str,
    writer: NoopTraceWriter,
    gateway: _CountingGateway,
) -> tuple[RuntimeImpl, MemoryTaskStore, MockLLMProvider]:
    task_store = MemoryTaskStore()
    llm_provider = MockLLMProvider()
    llm_provider.register(message, LLMCompletionResponse(content=completion))
    builder = ResponseEnvelopeBuilder()
    runtime = RuntimeImpl(
        candidate_policy=MinimalPolicyGuard(),
        task_store=task_store,
        session_store=ExistingSessionStore(),
        capability_registry=registry,
        orchestration=AgentOrchestrationAdapter(
            capability_registry=registry,
            gateway=gateway,
            workflow_engine=None,
            response_builder=builder,
        ),
        trace_port=writer,
        llm_provider=llm_provider,
        structured_output=JSONStructuredOutputProvider(),
        intent_model="test-intent-model",
        response_builder=builder,
    )
    return runtime, task_store, llm_provider


def _handle(runtime: RuntimeImpl, message: str) -> ResponseEnvelope:
    return asyncio.run(
        runtime.handle_user_message(
            channel="mock",
            principal=runtime_principal("synthetic-user"),
            session_id="synthetic-session",
            message=message,
            client_capabilities={},
        )
    )


def test_candidate_failure_has_safe_code_and_complete_terminal_trace() -> None:
    canary = "candidate-trace-canary"
    none = '{"match":"none"}'
    select_target = '{"match":"capability","capability_id":"zz.target","arguments":{}}'
    select_outside = '{"match":"capability","capability_id":"zz.outside","arguments":{}}'
    zero = [active_capability(f"aa.zero-{index}") for index in range(9)]
    tied = [
        active_capability(f"aa.tied-{index}").model_copy(update={"short_description": "alpha"})
        for index in range(9)
    ]
    oversized = active_capability("zz.target").model_copy(
        update={"input_schema": {"type": "object", "properties": {"k" * 5000: {"type": "string"}}}}
    )
    invalid = active_capability("zz.target").model_copy(
        update={"short_description": f"{canary} {{system}}"}
    )
    target = active_capability("zz.target")
    scenarios: list[tuple[str, StaticCapabilityRegistry, str, str, str]] = [
        ("low", StaticCapabilityRegistry(*zero), none, "nothing", "low_confidence"),
        ("ambiguous", StaticCapabilityRegistry(*tied), none, "alpha", "ambiguous"),
        ("budget", StaticCapabilityRegistry(oversized), none, "zz.target", "over_budget"),
        ("invalid", StaticCapabilityRegistry(invalid), none, "zz.target", "catalog_invalid"),
        ("outside", StaticCapabilityRegistry(target), select_outside, "zz.target", "out_of_scope"),
        ("stale", _DriftedRegistry(target), select_target, "zz.target", "stale"),
        ("partial", StaticCapabilityRegistry(target, *zero), none, "zz.target", "low_confidence"),
    ]
    codes = {
        "low_confidence": "capability_candidates_low_confidence",
        "ambiguous": "capability_candidates_ambiguous",
        "over_budget": "capability_candidates_over_budget",
        "catalog_invalid": "capability_catalog_invalid",
        "out_of_scope": "capability_candidate_out_of_scope",
        "stale": "capability_candidate_stale",
    }

    for label, registry, completion, message, short_code in scenarios:
        error_code = codes[short_code]
        logger = CapturingLogger()
        gateway = _CountingGateway()
        runtime, task_store, _llm = _candidate_runtime(
            registry,
            completion,
            message=message,
            writer=NoopTraceWriter(logger=cast(Any, logger)),
            gateway=gateway,
        )
        envelope = _handle(runtime, message)

        assert envelope.status == "failed", label
        assert task_store.status_updates[-1][1:] == ("failed", error_code), label
        assert _event_types(logger.events) == [
            "task_created",
            "intent_parsed",
            "response_envelope_created",
            "task_failed",
            "evaluation_recorded",
        ], label
        intent = logger.events[1]
        assert (intent["status"], intent["error_code"]) == ("failed", error_code), label
        assert logger.events[3]["error_code"] == error_code
        assert set(intent["attributes"]) == {
            "protocol_version",
            "outcome",
            "coverage_complete",
            "visible_count",
            "selected_count",
            "omitted_count",
            "truncated_by",
            "payload_bytes",
            "reason",
        }
        assert logger.events[-1]["attributes"]["evaluation_result"] != "passed"
        assert gateway.calls == 0
        assert canary not in repr((envelope, logger.events))
        assert (
            runtime._session_memory.recall(
                SessionMemoryKey(
                    tenant_id="tenant-test",
                    session_id="synthetic-session",
                    ai_user_id="synthetic-user",
                )
            )
            == ()
        )

    empty_logger = CapturingLogger()
    empty_runtime, _store, empty_llm = _candidate_runtime(
        StaticCapabilityRegistry(),
        none,
        message="anything",
        writer=NoopTraceWriter(logger=cast(Any, empty_logger)),
        gateway=_CountingGateway(),
    )
    empty = _handle(empty_runtime, "anything")
    assert empty.status == "no_capability_found"
    assert empty_llm.calls == []
    assert "no_capability_found" in _event_types(empty_logger.events)

    failing_gateway = _CountingGateway()
    failing_runtime, _store, failing_llm = _candidate_runtime(
        StaticCapabilityRegistry(target),
        select_target,
        message="zz.target",
        writer=_FailingIntentTrace(logger=cast(Any, CapturingLogger())),
        gateway=failing_gateway,
    )
    with pytest.raises(RuntimeError, match="synthetic trace write failure"):
        _handle(failing_runtime, "zz.target")
    assert failing_gateway.calls == 0
    assert len(failing_llm.calls) == 1
