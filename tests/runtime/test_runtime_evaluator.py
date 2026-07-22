"""Runtime terminal Evaluator integration tests."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from app.evaluator import (
    EvaluationConclusion,
    TerminalBusinessStatus,
    TerminalEvaluator,
)
from app.infra.llm.mock_llm.mock_llm_provider import MockLLMProvider
from app.infra.llm.mock_structured_output.mock_structured_output_provider import (
    MockStructuredOutputProvider,
)
from app.infra.sdui.response_envelope_builder import ResponseEnvelopeBuilder
from app.ports.capability_gateway import (
    ErrorCode,
    ExecutionResult,
    ExecutionStatus,
    RequestOrgContext,
)
from app.ports.capability_registry import CapabilitySpec
from app.ports.response_envelope import ResponseEnvelope
from app.ports.task_store import SessionRecord, TaskRecord
from app.runtime.models import CapabilityRef
from app.runtime.runtime import RuntimeImpl
from app.workflow.engine import WorkflowEngine
from app.workflow.models import WorkflowRunResult, WorkflowRunStatus
from tests.runtime.registry_fakes import active_capability

MESSAGE = "evaluate terminal request"


class TaskStore:
    def __init__(self) -> None:
        self.created: list[TaskRecord] = []
        self.status_updates: list[tuple[str, str | None]] = []

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
        self.status_updates.append((status, error_code))
        original = cast(TaskRecord, await self.get_task(task_id))
        return original.model_copy(update={"status": status, "error_code": error_code})

    async def append_event(self, task_id: str, event: Any) -> None:
        return None


class SessionStore:
    async def create_session(self, record: SessionRecord) -> SessionRecord:
        return record

    async def get_session(self, session_id: str) -> SessionRecord | None:
        return SessionRecord(session_id=session_id)


class Registry:
    def __init__(self, capability_type: str = "query") -> None:
        self.capability = active_capability("synthetic.evaluate").model_copy(
            update={"type": capability_type}
        )

    async def get(self, capability_id: str) -> CapabilitySpec | None:
        if capability_id == self.capability.capability_id:
            return self.capability
        return None

    async def list(
        self,
        target_system: str | None = None,
        type: str | None = None,
        status: str | None = None,
    ) -> list[CapabilitySpec]:
        if type is not None and type != self.capability.type:
            return []
        if status is not None and status != self.capability.status:
            return []
        return [self.capability]


class Gateway:
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


class Trace:
    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []
        self.finalizations: list[dict[str, Any]] = []
        self.timeline: list[str] = []

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
                "trace_id": trace_id,
                "task_id": task_id,
                "session_id": session_id,
                "event_type": event_type,
                "status": status,
                "capability_id": capability_id,
                "error_code": error_code,
                "attributes": attributes or {},
            }
        )
        self.timeline.append(event_type)

    async def record_policy_decision(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def record_gateway_call(self, *args: Any, **kwargs: Any) -> None:
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
        self.finalizations.append(
            {
                "status": status,
                "capability_id": capability_id,
                "error_code": error_code,
            }
        )
        self.timeline.append("finalize")


class RaisingEvaluator(TerminalEvaluator):
    def __init__(self, sensitive_message: str) -> None:
        self.sensitive_message = sensitive_message

    def evaluate(
        self,
        business_status: TerminalBusinessStatus,
        error_code: ErrorCode | None,
    ) -> EvaluationConclusion:
        raise RuntimeError(self.sensitive_message)


class RecordingEvaluator(TerminalEvaluator):
    def __init__(self) -> None:
        self.calls: list[tuple[TerminalBusinessStatus, ErrorCode | None]] = []

    def evaluate(
        self,
        business_status: TerminalBusinessStatus,
        error_code: ErrorCode | None,
    ) -> EvaluationConclusion:
        self.calls.append((business_status, error_code))
        return super().evaluate(business_status, error_code)


class WaitingWorkflow:
    def __init__(
        self,
        resume_status: WorkflowRunStatus,
        resume_error_code: ErrorCode | None = None,
    ) -> None:
        self.resume_status = resume_status
        self.resume_error_code = resume_error_code

    async def execute(self, **kwargs: Any) -> WorkflowRunResult:
        return _workflow_result("waiting_confirm", "confirm_required")

    async def resume(self, *, task_id: str, confirmed: bool) -> WorkflowRunResult:
        return _workflow_result(self.resume_status, self.resume_error_code)


def _workflow_result(
    status: WorkflowRunStatus,
    error_code: ErrorCode | None,
) -> WorkflowRunResult:
    return WorkflowRunResult(
        workflow_id="synthetic.evaluate",
        workflow_version="1.0.0",
        trace_id="workflow-trace",
        status=status,
        output={"result": "safe"},
        step_outputs={},
        error_code=error_code,
    )


def _runtime(
    result: ExecutionResult,
    *,
    evaluator: TerminalEvaluator | None = None,
    capability_type: str = "query",
    workflow: WaitingWorkflow | None = None,
    malformed_intent: bool = False,
) -> tuple[RuntimeImpl, TaskStore, Trace, Gateway]:
    task_store = TaskStore()
    trace = Trace()
    gateway = Gateway(result)
    structured_output = MockStructuredOutputProvider()
    if malformed_intent:
        structured_output.register_malformed(MESSAGE, CapabilityRef)
    else:
        structured_output.register(
            MESSAGE,
            CapabilityRef,
            CapabilityRef(
                capability_id="synthetic.evaluate",
                arguments={},
                capability_type=cast(Any, capability_type),
            ),
        )
    runtime = RuntimeImpl(
        task_store=task_store,
        session_store=SessionStore(),
        capability_registry=Registry(capability_type),
        gateway=gateway,
        trace_port=trace,
        llm_provider=MockLLMProvider(),
        structured_output=structured_output,
        intent_model="test-intent-model",
        response_builder=ResponseEnvelopeBuilder(),
        workflow_engine=cast(WorkflowEngine, workflow) if workflow is not None else None,
        evaluator=evaluator,
    )
    return runtime, task_store, trace, gateway


def _handle(runtime: RuntimeImpl, message: str = MESSAGE) -> ResponseEnvelope:
    return asyncio.run(
        runtime.handle_user_message(
            channel="mock",
            ai_user_id="user-evaluator",
            session_id="session-evaluator",
            message=message,
            client_capabilities={},
        )
    )


def _evaluation_events(trace: Trace) -> list[dict[str, Any]]:
    return [step for step in trace.steps if step["event_type"] == "evaluation_recorded"]


@pytest.mark.parametrize(
    (
        "business_status",
        "error_code",
        "expected_envelope_status",
        "expected_task_status",
        "expected_evaluation_result",
    ),
    [
        ("completed", None, "completed", "completed", "passed"),
        ("failed", "adapter_error", "failed", "failed", "failed"),
        ("denied", "policy_denied", "blocked", "failed", "failed"),
        (
            "binding_required",
            "identity_unbound",
            "blocked",
            "failed",
            "failed",
        ),
        ("timeout", "adapter_timeout", "failed", "failed", "failed"),
        (
            "no_capability_found",
            "capability_not_found",
            "no_capability_found",
            "no_capability_found",
            "failed",
        ),
    ],
)
def test_main_chain_records_one_distinct_evaluation_for_every_terminal_status(
    business_status: ExecutionStatus,
    error_code: ErrorCode | None,
    expected_envelope_status: str,
    expected_task_status: str,
    expected_evaluation_result: str,
) -> None:
    runtime, task_store, trace, _gateway = _runtime(
        ExecutionResult(
            status=business_status,
            error_code=error_code,
            trace_id="gateway-trace",
        )
    )

    envelope = _handle(runtime)

    events = _evaluation_events(trace)
    assert envelope.status == expected_envelope_status
    assert task_store.status_updates[-1] == (expected_task_status, error_code)
    assert len(events) == 1
    assert events[0]["status"] == (
        "ok" if expected_evaluation_result == "passed" else "failed"
    )
    assert events[0]["error_code"] == error_code
    assert events[0]["attributes"] == {
        "rule_id": "terminal_status_v1",
        "business_status": business_status,
        "business_error_code": error_code,
        "evaluation_result": expected_evaluation_result,
        "reason": (
            "business_completed"
            if expected_evaluation_result == "passed"
            else "business_not_completed"
        ),
    }
    assert trace.timeline[-2:] == ["evaluation_recorded", "finalize"]


@pytest.mark.parametrize(
    ("business_status", "error_code"),
    [
        ("completed", None),
        ("failed", "adapter_error"),
        ("denied", "policy_denied"),
        ("binding_required", "identity_unbound"),
        ("timeout", "adapter_timeout"),
        ("no_capability_found", "capability_not_found"),
    ],
)
def test_evaluator_exception_does_not_change_business_terminal_state(
    business_status: ExecutionStatus,
    error_code: ErrorCode | None,
) -> None:
    result = ExecutionResult(
        status=business_status,
        error_code=error_code,
        trace_id="gateway-trace",
    )
    baseline_runtime, baseline_store, _baseline_trace, _ = _runtime(result)
    error_runtime, error_store, error_trace, _ = _runtime(
        result,
        evaluator=RaisingEvaluator("password=do-not-record-evaluator-error"),
    )

    baseline_envelope = _handle(baseline_runtime)
    error_envelope = _handle(error_runtime)

    assert (error_envelope.status, error_store.status_updates[-1]) == (
        baseline_envelope.status,
        baseline_store.status_updates[-1],
    )
    events = _evaluation_events(error_trace)
    assert len(events) == 1
    assert events[0]["status"] == "failed"
    assert events[0]["error_code"] == error_code
    assert events[0]["attributes"] == {
        "rule_id": "terminal_status_v1",
        "business_status": business_status,
        "business_error_code": error_code,
        "evaluation_result": "error",
        "reason": "evaluator_error",
    }
    assert "do-not-record-evaluator-error" not in repr(error_trace.steps)


def test_no_capability_terminal_path_records_exactly_one_failed_evaluation() -> None:
    runtime, task_store, trace, gateway = _runtime(
        ExecutionResult(status="completed", trace_id="unused"),
        malformed_intent=True,
    )

    envelope = _handle(runtime)

    events = _evaluation_events(trace)
    assert envelope.status == "no_capability_found"
    assert task_store.status_updates == [
        ("no_capability_found", "capability_not_found")
    ]
    assert gateway.calls == 0
    assert len(events) == 1
    assert events[0]["status"] == "failed"
    assert events[0]["error_code"] == "capability_not_found"
    assert events[0]["attributes"] == {
        "rule_id": "terminal_status_v1",
        "business_status": "no_capability_found",
        "business_error_code": "capability_not_found",
        "evaluation_result": "failed",
        "reason": "business_not_completed",
    }


@pytest.mark.parametrize(
    (
        "resume_status",
        "error_code",
        "expected_envelope_status",
        "expected_task_status",
        "expected_evaluation_result",
    ),
    [
        ("completed", None, "completed", "completed", "passed"),
        ("failed", "adapter_error", "failed", "failed", "failed"),
        ("denied", "policy_denied", "blocked", "failed", "failed"),
        ("timeout", "adapter_timeout", "failed", "failed", "failed"),
    ],
)
def test_workflow_waiting_has_no_evaluation_then_resume_records_exact_terminal(
    resume_status: WorkflowRunStatus,
    error_code: ErrorCode | None,
    expected_envelope_status: str,
    expected_task_status: str,
    expected_evaluation_result: str,
) -> None:
    workflow = WaitingWorkflow(resume_status, error_code)
    runtime, task_store, trace, _gateway = _runtime(
        ExecutionResult(status="failed", trace_id="unused"),
        capability_type="workflow",
        workflow=workflow,
    )

    waiting = _handle(runtime)
    assert waiting.status == "waiting_user"
    assert task_store.status_updates == [("waiting_user", "confirm_required")]
    assert _evaluation_events(trace) == []
    assert trace.finalizations == []

    terminal = _handle(runtime, f"确认 {waiting.task_id}")

    events = _evaluation_events(trace)
    assert terminal.status == expected_envelope_status
    assert terminal.task_id == waiting.task_id
    assert task_store.status_updates[-1] == (expected_task_status, error_code)
    assert len(events) == 1
    assert events[0]["task_id"] == waiting.task_id
    assert events[0]["status"] == (
        "ok" if expected_evaluation_result == "passed" else "failed"
    )
    assert events[0]["error_code"] == error_code
    assert events[0]["attributes"] == {
        "rule_id": "terminal_status_v1",
        "business_status": resume_status,
        "business_error_code": error_code,
        "evaluation_result": expected_evaluation_result,
        "reason": (
            "business_completed"
            if expected_evaluation_result == "passed"
            else "business_not_completed"
        ),
    }
    assert len(trace.finalizations) == 1


def test_workflow_resume_waiting_again_still_has_no_evaluation() -> None:
    runtime, _task_store, trace, _gateway = _runtime(
        ExecutionResult(status="failed", trace_id="unused"),
        capability_type="workflow",
        workflow=WaitingWorkflow("waiting_confirm", "confirm_required"),
    )

    first_wait = _handle(runtime)
    second_wait = _handle(runtime, f"确认 {first_wait.task_id}")

    assert first_wait.status == second_wait.status == "waiting_user"
    assert first_wait.task_id == second_wait.task_id
    assert _evaluation_events(trace) == []
    assert trace.finalizations == []


def test_execution_data_and_evaluator_exception_text_never_enter_evaluation_trace_or_state(
) -> None:
    sensitive = "Bearer evaluator-sensitive-token-123"
    evaluator = RecordingEvaluator()
    runtime, task_store, trace, _gateway = _runtime(
        ExecutionResult(
            status="completed",
            data={"password": sensitive, "access_token": sensitive},
            trace_id="gateway-trace",
        ),
        evaluator=evaluator,
    )

    envelope = _handle(runtime)

    assert envelope.status == "completed"
    assert evaluator.calls == [("completed", None)]
    assert len(_evaluation_events(trace)) == 1
    assert sensitive not in repr(trace.steps)
    assert sensitive not in repr(task_store.__dict__)
