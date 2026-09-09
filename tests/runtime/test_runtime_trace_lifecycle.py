from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from app.infra.gateway.capability_gateway import CapabilityGateway
from app.infra.llm.mock_llm.mock_llm_provider import MockLLMProvider
from app.infra.llm.mock_structured_output.mock_structured_output_provider import (
    MockStructuredOutputProvider,
)
from app.infra.observability.noop_trace_writer import NoopTraceWriter
from app.infra.orchestration.agent_adapter import AgentOrchestrationAdapter
from app.infra.sdui.response_envelope_builder import ResponseEnvelopeBuilder
from app.ports.adapter import AdapterResult
from app.ports.capability_gateway import ExecutionResult, ExecutionStatus, RequestOrgContext
from app.ports.response_envelope import ResponseEnvelope
from app.ports.task_store import SessionRecord, TaskEventRecord, TaskRecord
from app.runtime.models import IntentOutput, MatchedIntent
from app.runtime.runtime import RuntimeImpl
from tests.runtime.principal_fakes import runtime_principal
from tests.runtime.registry_fakes import StaticCapabilityRegistry


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
