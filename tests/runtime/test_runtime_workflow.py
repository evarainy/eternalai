"""RuntimePort integration for a registered lightweight Workflow."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import pytest

from app.composition import build_runtime
from app.infra.llm.mock_llm.mock_llm_provider import MockLLMProvider
from app.infra.llm.mock_structured_output.mock_structured_output_provider import (
    MockStructuredOutputProvider,
)
from app.ports.capability_gateway import ExecutionResult, RequestOrgContext
from app.ports.capability_registry import CapabilitySpec
from app.ports.task_store import SessionRecord, TaskRecord
from app.runtime.models import CapabilityRef
from app.workflow.engine import WorkflowEngine
from app.workflow.models import WorkflowDefinition, WorkflowInputRef, WorkflowStep


def _capability(capability_id: str, capability_type: str) -> CapabilitySpec:
    return CapabilitySpec(
        capability_id=capability_id,
        name=capability_id,
        type=capability_type,
        input_schema_digest=f"input-{capability_id}",
        output_schema_digest=f"output-{capability_id}",
        risk_level="low",
        owner="runtime-workflow-test",
        version="1.0.0",
        status="active",
        short_description=capability_id,
        target_system="oa",
        execution_identity="user_delegated",
        binding_required=False,
    )


class Registry:
    def __init__(self, *capabilities: CapabilitySpec) -> None:
        self.items = {item.capability_id: item for item in capabilities}

    async def get(self, capability_id: str) -> CapabilitySpec | None:
        return self.items.get(capability_id)

    async def list(
        self,
        target_system: str | None = None,
        type: str | None = None,
        status: str | None = None,
    ) -> list[CapabilitySpec]:
        return list(self.items.values())


class TaskStore:
    def __init__(self) -> None:
        self.created: list[TaskRecord] = []
        self.events: list[Any] = []
        self.statuses: list[str] = []
        self.status_updates: list[tuple[str, str | None]] = []

    async def create_task(self, record: TaskRecord) -> TaskRecord:
        self.created.append(record)
        return record

    async def get_task(self, task_id: str) -> TaskRecord | None:
        return self.created[0] if self.created else None

    async def update_status(
        self, task_id: str, status: str, error_code: str | None = None
    ) -> TaskRecord:
        self.statuses.append(status)
        self.status_updates.append((status, error_code))
        return self.created[0].model_copy(update={"status": status, "error_code": error_code})

    async def append_event(self, task_id: str, event: Any) -> None:
        self.events.append(event)


class SessionStore:
    async def create_session(self, record: SessionRecord) -> SessionRecord:
        return record

    async def get_session(self, session_id: str) -> SessionRecord | None:
        sid = session_id
        return SessionRecord(session_id=sid)


class Trace:
    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []
        self.finalizations: list[dict[str, Any]] = []

    async def start_task_trace(self, *args: Any, **kwargs: Any) -> None:
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
                "error_code": error_code,
                "attributes": attributes or {},
            }
        )

    async def finalize_task_trace(self, *args: Any, **kwargs: Any) -> None:
        self.finalizations.append(dict(kwargs))


class Gateway:
    def __init__(self, results: dict[str, ExecutionResult] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.results = results or {}

    async def execute_capability(
        self,
        task_id: str,
        session_id: str,
        ai_user_id: str,
        capability_id: str,
        arguments: dict[str, Any],
        request_context: RequestOrgContext,
    ) -> ExecutionResult:
        self.calls.append((capability_id, arguments))
        configured = self.results.get(capability_id)
        if configured is not None:
            return configured.model_copy(update={"trace_id": request_context.request_id})
        data = (
            {"document_id": "D-1"}
            if capability_id == "oa.document.lookup"
            else {"status": "approved"}
        )
        return ExecutionResult(
            status="completed",
            data=data,
            trace_id=request_context.request_id,
        )


def test_runtime_executes_registered_workflow_without_calling_workflow_as_adapter() -> None:
    async def exercise() -> tuple[Any, Gateway, TaskStore]:
        definition = WorkflowDefinition(
            workflow_id="oa.workflow.document-status",
            version="1.0.0",
            steps=(
                WorkflowStep(
                    step_id="lookup",
                    capability_id="oa.document.lookup",
                    input_mapping={
                        "document_no": WorkflowInputRef(source="workflow_input", key="document_no")
                    },
                ),
                WorkflowStep(
                    step_id="status",
                    capability_id="oa.document.status",
                    input_mapping={
                        "document_id": WorkflowInputRef(
                            source="step_output",
                            step_id="lookup",
                            key="document_id",
                        )
                    },
                ),
            ),
        )
        registry = Registry(
            _capability(definition.workflow_id, "workflow"),
            _capability("oa.document.lookup", "query"),
            _capability("oa.document.status", "query"),
        )
        task_store = TaskStore()
        trace = Trace()
        gateway = Gateway()
        engine = WorkflowEngine(
            definitions={definition.workflow_id: definition},
            capability_registry=registry,
            gateway=gateway,
            task_store=task_store,
            trace_port=trace,
        )
        structured_output = MockStructuredOutputProvider()
        structured_output.register(
            "check document",
            CapabilityRef,
            CapabilityRef(
                capability_id=definition.workflow_id,
                arguments={"document_no": "DOC-7"},
                capability_type="workflow",
            ),
        )
        runtime = build_runtime(
            task_store=task_store,
            session_store=SessionStore(),
            capability_registry=registry,
            gateway=gateway,
            trace_port=trace,
            llm_provider=MockLLMProvider(),
            structured_output=structured_output,
            intent_model="test-intent-model",
            workflow_engine=engine,
        )

        sid = "session-1"
        envelope = await runtime.handle_user_message(
            channel="mock",
            ai_user_id="user-1",
            session_id=sid,
            message="check document",
            client_capabilities={},
        )
        return envelope, gateway, task_store

    envelope, gateway, task_store = asyncio.run(exercise())

    assert [call[0] for call in gateway.calls] == [
        "oa.document.lookup",
        "oa.document.status",
    ]
    assert gateway.calls[0][1] == {"document_no": "DOC-7"}
    assert gateway.calls[1][1] == {"document_id": "D-1"}
    assert envelope.status == "completed"
    assert envelope.data == {"status": "approved"}
    assert task_store.statuses[-1] == "completed"


@pytest.mark.parametrize(
    (
        "step_result",
        "expected_envelope_status",
        "expected_component",
        "expected_action",
        "expected_task_update",
        "expected_workflow_status",
    ),
    (
        (
            ExecutionResult(
                status="denied",
                error_code="policy_denied",
                trace_id="configured-deny",
            ),
            "blocked",
            "operator_handback_card",
            "none",
            ("failed", "policy_denied"),
            "denied",
        ),
        (
            ExecutionResult(
                status="waiting_user",
                error_code="confirm_required",
                trace_id="configured-confirm",
            ),
            "waiting_user",
            "confirm_card",
            "confirm",
            ("waiting_user", "confirm_required"),
            "waiting_confirm",
        ),
    ),
)
def test_runtime_maps_workflow_policy_terminal_to_existing_envelope(
    step_result: ExecutionResult,
    expected_envelope_status: str,
    expected_component: str,
    expected_action: str,
    expected_task_update: tuple[str, str],
    expected_workflow_status: str,
) -> None:
    async def exercise() -> tuple[Any, Gateway, TaskStore, Trace]:
        confirmed_capability_id = (
            "oa.document.lookup.confirmed" if step_result.status == "waiting_user" else None
        )
        definition = WorkflowDefinition(
            workflow_id="oa.workflow.policy-terminal",
            version="1.0.0",
            steps=(
                WorkflowStep(
                    step_id="guarded",
                    capability_id="oa.document.lookup",
                    confirmed_capability_id=confirmed_capability_id,
                    input_mapping={
                        "document_no": WorkflowInputRef(source="workflow_input", key="document_no")
                    },
                ),
                WorkflowStep(step_id="later", capability_id="oa.document.status"),
            ),
        )
        registry = Registry(
            _capability(definition.workflow_id, "workflow"),
            _capability("oa.document.lookup", "query"),
            _capability("oa.document.status", "query"),
            *(
                ()
                if confirmed_capability_id is None
                else (_capability(confirmed_capability_id, "query"),)
            ),
        )
        task_store = TaskStore()
        trace = Trace()
        gateway = Gateway({"oa.document.lookup": step_result})
        engine = WorkflowEngine(
            definitions={definition.workflow_id: definition},
            capability_registry=registry,
            gateway=gateway,
            task_store=task_store,
            trace_port=trace,
        )
        structured_output = MockStructuredOutputProvider()
        structured_output.register(
            "policy terminal workflow",
            CapabilityRef,
            CapabilityRef(
                capability_id=definition.workflow_id,
                arguments={
                    "document_no": "DOC-7",
                    "secret_token": "private-marker-123",
                },
                capability_type="workflow",
            ),
        )
        runtime = build_runtime(
            task_store=task_store,
            session_store=SessionStore(),
            capability_registry=registry,
            gateway=gateway,
            trace_port=trace,
            llm_provider=MockLLMProvider(),
            structured_output=structured_output,
            intent_model="test-intent-model",
            workflow_engine=engine,
        )

        envelope = await runtime.handle_user_message(
            channel="mock",
            ai_user_id="user-1",
            session_id="session-policy-terminal",
            message="policy terminal workflow",
            client_capabilities={},
        )
        return envelope, gateway, task_store, trace

    envelope, gateway, task_store, trace = asyncio.run(exercise())

    assert gateway.calls == [("oa.document.lookup", {"document_no": "DOC-7"})]
    assert envelope.status == expected_envelope_status
    assert envelope.ui.component_type == expected_component
    assert envelope.ui.action == expected_action
    assert task_store.status_updates[-1] == expected_task_update
    workflow_events = task_store.events[-3:]
    terminal_event = (
        "workflow_waiting_confirm"
        if expected_workflow_status == "waiting_confirm"
        else "workflow_completed"
    )
    assert [event.event_type for event in workflow_events] == [
        "workflow_started",
        "workflow_step_finished",
        terminal_event,
    ]
    assert workflow_events[1].payload["step_status"] == expected_workflow_status
    if expected_workflow_status == "waiting_confirm":
        assert workflow_events[2].payload["waiting_step_id"] == "guarded"
    else:
        assert workflow_events[2].payload["workflow_status"] == expected_workflow_status
    assert "private-marker-123" not in repr(trace.steps)
    assert "private-marker-123" not in repr(task_store.events)


def test_registered_workflow_without_engine_uses_standard_failed_terminal() -> None:
    async def exercise() -> tuple[Any, Gateway, TaskStore, Trace]:
        workflow = _capability("oa.workflow.unconfigured", "workflow")
        registry = Registry(workflow)
        task_store = TaskStore()
        trace = Trace()
        gateway = Gateway()
        structured_output = MockStructuredOutputProvider()
        structured_output.register(
            "unconfigured workflow",
            CapabilityRef,
            CapabilityRef(
                capability_id=workflow.capability_id,
                arguments={},
                capability_type="workflow",
            ),
        )
        runtime = build_runtime(
            task_store=task_store,
            session_store=SessionStore(),
            capability_registry=registry,
            gateway=gateway,
            trace_port=trace,
            llm_provider=MockLLMProvider(),
            structured_output=structured_output,
            intent_model="test-intent-model",
        )

        sid = "session-unconfigured"
        envelope = await runtime.handle_user_message(
            channel="mock",
            ai_user_id="user-1",
            session_id=sid,
            message="unconfigured workflow",
            client_capabilities={},
        )
        return envelope, gateway, task_store, trace

    envelope, gateway, task_store, trace = asyncio.run(exercise())

    assert gateway.calls == []
    assert envelope.status == "failed"
    assert task_store.status_updates[-1] == ("failed", "internal_error")
    assert [step["event_type"] for step in trace.steps[-2:]] == [
        "response_envelope_created",
        "task_failed",
    ]
    assert trace.steps[-1]["error_code"] == "internal_error"
    assert trace.finalizations[-1] == {
        "status": "failed",
        "capability_id": "oa.workflow.unconfigured",
        "error_code": "internal_error",
    }


def test_runtime_confirm_message_resumes_only_for_original_session_and_user() -> None:
    async def exercise() -> tuple[Any, Any, Any, Any, Gateway, TaskStore, Trace]:
        sensitive_key = "secret_" + "token"
        sensitive_value = "private-" + "marker-123"
        definition = WorkflowDefinition(
            workflow_id="oa.workflow.leave-submit",
            version="1.0.0",
            steps=(
                WorkflowStep(
                    step_id="submit",
                    capability_id="oa.leave.submit_confirm",
                    confirmed_capability_id="oa.submit_leave_request.confirmed_mock",
                    input_mapping={
                        "requester": WorkflowInputRef(
                            source="workflow_input",
                            key="requester",
                        )
                    },
                ),
                WorkflowStep(
                    step_id="audit",
                    capability_id="oa.leave.audit",
                    input_mapping={
                        "workflow_id": WorkflowInputRef(
                            source="step_output",
                            step_id="submit",
                            key="workflow_id",
                        )
                    },
                ),
            ),
        )
        definitions = {definition.workflow_id: definition}
        registry = Registry(
            _capability(definition.workflow_id, "workflow"),
            _capability("oa.leave.submit_confirm", "query"),
            _capability("oa.submit_leave_request.confirmed_mock", "query"),
            _capability("oa.leave.audit", "query"),
            _capability("oa.document.lookup", "query"),
        )
        task_store = TaskStore()
        trace = Trace()
        gateway = Gateway(
            {
                "oa.leave.submit_confirm": ExecutionResult(
                    status="waiting_user",
                    error_code="confirm_required",
                    trace_id="configured-confirm",
                ),
                "oa.submit_leave_request.confirmed_mock": ExecutionResult(
                    status="completed",
                    data={"workflow_id": "WF-1"},
                    trace_id="configured-completed",
                ),
                "oa.leave.audit": ExecutionResult(
                    status="completed",
                    data={"status": "recorded"},
                    trace_id="configured-audit",
                ),
            }
        )
        engine = WorkflowEngine(
            definitions=definitions,
            capability_registry=registry,
            gateway=gateway,
            task_store=task_store,
            trace_port=trace,
        )
        structured_output = MockStructuredOutputProvider()
        structured_output.register(
            "submit workflow",
            CapabilityRef,
            CapabilityRef(
                capability_id=definition.workflow_id,
                arguments={
                    "requester": "alice",
                    sensitive_key: sensitive_value,
                },
                capability_type="workflow",
            ),
        )
        runtime = build_runtime(
            task_store=task_store,
            session_store=SessionStore(),
            capability_registry=registry,
            gateway=gateway,
            trace_port=trace,
            llm_provider=MockLLMProvider(),
            structured_output=structured_output,
            intent_model="test-intent-model",
            workflow_engine=engine,
        )

        waiting = await runtime.handle_user_message(
            channel="mock",
            ai_user_id="user-1",
            session_id="session-resume",
            message="submit workflow",
            client_capabilities={},
        )
        assert trace.finalizations == []

        confirm_message = f"确认 {waiting.task_id}"
        structured_output.register(
            confirm_message,
            CapabilityRef,
            CapabilityRef(
                capability_id="oa.document.lookup",
                arguments={"document_no": "DOC-7"},
                capability_type="query",
            ),
        )
        wrong_user = await runtime.handle_user_message(
            channel="mock",
            ai_user_id="user-2",
            session_id="session-resume",
            message=confirm_message,
            client_capabilities={},
        )
        wrong_session = await runtime.handle_user_message(
            channel="mock",
            ai_user_id="user-1",
            session_id="session-other",
            message=confirm_message,
            client_capabilities={},
        )

        definitions[definition.workflow_id] = replace(
            definition,
            version="2.0.0",
            steps=(WorkflowStep(step_id="drifted", capability_id="oa.leave.audit"),),
        )
        resumed = await runtime.handle_user_message(
            channel="mock",
            ai_user_id="user-1",
            session_id="session-resume",
            message=confirm_message,
            client_capabilities={},
        )
        return waiting, wrong_user, wrong_session, resumed, gateway, task_store, trace

    waiting, wrong_user, wrong_session, resumed, gateway, task_store, trace = asyncio.run(
        exercise()
    )

    assert waiting.status == "waiting_user"
    assert waiting.ui.action == "confirm"
    assert wrong_user.status == "completed"
    assert wrong_user.task_id != waiting.task_id
    assert wrong_session.status == "completed"
    assert wrong_session.task_id != waiting.task_id
    assert resumed.status == "completed"
    assert resumed.task_id == waiting.task_id
    assert resumed.trace_id == waiting.trace_id
    assert resumed.data == {"status": "recorded"}
    assert len(task_store.created) == 3
    assert task_store.status_updates == [
        ("waiting_user", "confirm_required"),
        ("completed", None),
        ("completed", None),
        ("completed", None),
    ]
    assert [call[0] for call in gateway.calls] == [
        "oa.leave.submit_confirm",
        "oa.document.lookup",
        "oa.document.lookup",
        "oa.submit_leave_request.confirmed_mock",
        "oa.leave.audit",
    ]
    workflow_events = [
        event for event in task_store.events if event.event_type.startswith("workflow_")
    ]
    assert [event.event_type for event in workflow_events] == [
        "workflow_started",
        "workflow_step_finished",
        "workflow_waiting_confirm",
        "workflow_resumed",
        "workflow_step_finished",
        "workflow_step_finished",
        "workflow_completed",
    ]
    assert {event.payload["workflow_version"] for event in workflow_events} == {"1.0.0"}
    assert len(trace.finalizations) == 3
    assert trace.finalizations[-1]["status"] == "ok"
    assert "private-marker-123" not in repr(task_store.events)
    assert "private-marker-123" not in repr(trace.steps)


@pytest.mark.parametrize(
    ("step_result", "expected_gateway_calls"),
    (
        (
            ExecutionResult(
                status="timeout",
                error_code="adapter_timeout",
                trace_id="configured-timeout",
            ),
            2,
        ),
        (
            ExecutionResult(
                status="failed",
                error_code="adapter_http_500",
                trace_id="configured-failed",
            ),
            1,
        ),
    ),
)
def test_runtime_preserves_workflow_terminal_error_without_reporting_completed(
    step_result: ExecutionResult,
    expected_gateway_calls: int,
) -> None:
    async def exercise() -> tuple[Any, Gateway, TaskStore, Trace]:
        definition = WorkflowDefinition(
            workflow_id="oa.workflow.failure",
            version="1.0.0",
            steps=(
                WorkflowStep(step_id="terminal", capability_id="oa.failure"),
                WorkflowStep(step_id="later", capability_id="oa.later"),
            ),
        )
        registry = Registry(
            _capability(definition.workflow_id, "workflow"),
            _capability("oa.failure", "query"),
            _capability("oa.later", "query"),
        )
        gateway = Gateway({"oa.failure": step_result})
        task_store = TaskStore()
        trace = Trace()
        engine = WorkflowEngine(
            definitions={definition.workflow_id: definition},
            capability_registry=registry,
            gateway=gateway,
            task_store=task_store,
            trace_port=trace,
        )
        structured_output = MockStructuredOutputProvider()
        structured_output.register(
            "run failing workflow",
            CapabilityRef,
            CapabilityRef(
                capability_id=definition.workflow_id,
                arguments={"secret_token": "private-marker-123"},
                capability_type="workflow",
            ),
        )
        runtime = build_runtime(
            task_store=task_store,
            session_store=SessionStore(),
            capability_registry=registry,
            gateway=gateway,
            trace_port=trace,
            llm_provider=MockLLMProvider(),
            structured_output=structured_output,
            intent_model="test-intent-model",
            workflow_engine=engine,
        )
        envelope = await runtime.handle_user_message(
            channel="mock",
            ai_user_id="user-failure",
            session_id="session-failure",
            message="run failing workflow",
            client_capabilities={},
        )
        return envelope, gateway, task_store, trace

    envelope, gateway, task_store, trace = asyncio.run(exercise())

    assert envelope.status == "failed"
    assert [capability_id for capability_id, _ in gateway.calls] == [
        "oa.failure"
    ] * expected_gateway_calls
    assert task_store.status_updates == [("failed", step_result.error_code)]
    assert "task_completed" not in [step["event_type"] for step in trace.steps]
    assert trace.steps[-1]["event_type"] == "task_failed"
    assert trace.steps[-1]["error_code"] == step_result.error_code
    assert trace.finalizations[-1]["status"] == "failed"
    assert trace.finalizations[-1]["error_code"] == step_result.error_code
    workflow_events = [
        event for event in task_store.events if event.event_type.startswith("workflow_")
    ]
    assert [event.event_type for event in workflow_events] == [
        "workflow_started",
        "workflow_step_finished",
        "workflow_failed",
    ]
    assert workflow_events[-1].payload["error_code"] == step_result.error_code
    assert "private-marker-123" not in repr(trace.steps)
    assert "private-marker-123" not in repr(task_store.events)


def test_failed_resume_clears_engine_checkpoint_and_runtime_pending() -> None:
    async def exercise() -> tuple[Any, Any, Any, Gateway, TaskStore, Trace, list[Any]]:
        definition = WorkflowDefinition(
            workflow_id="oa.workflow.resume-failure",
            version="1.0.0",
            steps=(
                WorkflowStep(
                    step_id="submit",
                    capability_id="oa.submit.confirm",
                    confirmed_capability_id="oa.submit.confirmed",
                ),
                WorkflowStep(step_id="later", capability_id="oa.later"),
            ),
        )
        registry = Registry(
            _capability(definition.workflow_id, "workflow"),
            _capability("oa.submit.confirm", "query"),
            _capability("oa.submit.confirmed", "query"),
            _capability("oa.later", "query"),
            _capability("oa.document.lookup", "query"),
        )
        gateway = Gateway(
            {
                "oa.submit.confirm": ExecutionResult(
                    status="waiting_user",
                    error_code="confirm_required",
                    trace_id="configured-waiting",
                ),
                "oa.submit.confirmed": ExecutionResult(
                    status="failed",
                    error_code="adapter_http_500",
                    trace_id="configured-failure",
                ),
            }
        )
        task_store = TaskStore()
        trace = Trace()
        engine = WorkflowEngine(
            definitions={definition.workflow_id: definition},
            capability_registry=registry,
            gateway=gateway,
            task_store=task_store,
            trace_port=trace,
        )
        structured_output = MockStructuredOutputProvider()
        structured_output.register(
            "start resumable failure",
            CapabilityRef,
            CapabilityRef(
                capability_id=definition.workflow_id,
                arguments={"secret_token": "private-marker-123"},
                capability_type="workflow",
            ),
        )
        runtime = build_runtime(
            task_store=task_store,
            session_store=SessionStore(),
            capability_registry=registry,
            gateway=gateway,
            trace_port=trace,
            llm_provider=MockLLMProvider(),
            structured_output=structured_output,
            intent_model="test-intent-model",
            workflow_engine=engine,
        )
        waiting = await runtime.handle_user_message(
            channel="mock",
            ai_user_id="user-resume-failure",
            session_id="session-resume-failure",
            message="start resumable failure",
            client_capabilities={},
        )
        confirm_message = f"确认 {waiting.task_id}"
        structured_output.register(
            confirm_message,
            CapabilityRef,
            CapabilityRef(
                capability_id="oa.document.lookup",
                arguments={"document_no": "DOC-9"},
                capability_type="query",
            ),
        )
        failed = await runtime.handle_user_message(
            channel="mock",
            ai_user_id="user-resume-failure",
            session_id="session-resume-failure",
            message=confirm_message,
            client_capabilities={},
        )
        failed_trace = list(trace.steps)
        with pytest.raises(ValueError, match="no waiting Workflow checkpoint"):
            await engine.resume(task_id=waiting.task_id, confirmed=True)
        repeated = await runtime.handle_user_message(
            channel="mock",
            ai_user_id="user-resume-failure",
            session_id="session-resume-failure",
            message=confirm_message,
            client_capabilities={},
        )
        return waiting, failed, repeated, gateway, task_store, trace, failed_trace

    waiting, failed, repeated, gateway, task_store, trace, failed_trace = asyncio.run(exercise())

    assert waiting.status == "waiting_user"
    assert failed.status == "failed"
    assert failed.task_id == waiting.task_id
    assert failed.trace_id == waiting.trace_id
    assert repeated.status == "completed"
    assert repeated.task_id != waiting.task_id
    assert [capability_id for capability_id, _ in gateway.calls] == [
        "oa.submit.confirm",
        "oa.submit.confirmed",
        "oa.document.lookup",
    ]
    assert task_store.status_updates == [
        ("waiting_user", "confirm_required"),
        ("failed", "adapter_http_500"),
        ("completed", None),
    ]
    assert "task_completed" not in [step["event_type"] for step in failed_trace]
    assert trace.finalizations[0]["status"] == "failed"
    assert trace.finalizations[0]["error_code"] == "adapter_http_500"
    assert "private-marker-123" not in repr(trace.steps)
    assert "private-marker-123" not in repr(task_store.events)
