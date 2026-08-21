"""Observable behavior tests for the lightweight linear Workflow engine."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any, Callable

import pytest

from app.infra.gateway.capability_gateway import CapabilityGateway
from app.infra.policy.minimal_policy_guard import MinimalPolicyGuard
from app.ports.adapter import AdapterResult
from app.ports.capability_gateway import ExecutionResult, RequestOrgContext
from app.ports.capability_registry import CapabilitySpec
from app.ports.policy_guard import PolicyDecision, PolicyDecisionValue
from app.ports.task_store import TaskEventRecord, TaskRecord
from app.workflow.engine import MAX_STEP_RETRIES, RETRYABLE_ERROR_CODES, WorkflowEngine
from app.workflow.models import (
    WorkflowCondition,
    WorkflowDefinition,
    WorkflowInputRef,
    WorkflowRunStatus,
    WorkflowStep,
)


def _capability(
    capability_id: str,
    *,
    status: str = "active",
    risk_level: str = "low",
    capability_type: str = "query",
) -> CapabilitySpec:
    return CapabilitySpec(
        capability_id=capability_id,
        name=capability_id,
        type=capability_type,
        input_schema_digest=f"input-{capability_id}",
        output_schema_digest=f"output-{capability_id}",
        risk_level=risk_level,
        owner="workflow-test",
        version="1.0.0",
        status=status,
        short_description=capability_id,
        target_system="oa",
        execution_identity="user_delegated",
        binding_required=False,
    )


class RecordingRegistry:
    def __init__(self, *capabilities: CapabilitySpec) -> None:
        self.items = {item.capability_id: item for item in capabilities}

    async def get(self, capability_id: str) -> CapabilitySpec | None:
        return self.items.get(capability_id)


class RecordingTaskStore:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def append_event(self, task_id: str, event: Any) -> None:
        assert event.task_id == task_id
        self.events.append(event)

    async def list_tasks(
        self,
        *,
        session_id: str | None = None,
        ai_user_id: str | None = None,
    ) -> list[TaskRecord]:
        return []

    async def list_events(self, task_id: str) -> list[TaskEventRecord]:
        return []


class RecordingPolicyGuard:
    def __init__(self, decisions: dict[str, PolicyDecisionValue]) -> None:
        self.decisions = decisions
        self.calls: list[str] = []

    async def decide(
        self,
        ai_user_id: str,
        capability_id: str,
        arguments: dict[str, Any],
        request_context: RequestOrgContext,
    ) -> PolicyDecision:
        self.calls.append(capability_id)
        return PolicyDecision(decision=self.decisions[capability_id])


class RecordingTrace:
    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []

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
        sid = session_id
        self.steps.append(
            {
                "trace_id": trace_id,
                "task_id": task_id,
                "session_id": sid,
                "event_type": event_type,
                "status": status,
                "capability_id": capability_id,
                "error_code": error_code,
                "attributes": attributes or {},
            }
        )

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
        await self.record_step(
            trace_id,
            task_id,
            session_id,
            "gateway_pre_recorded",
            status,
            capability_id,
            error_code,
            attributes,
        )


class SequencedGateway:
    def __init__(self, results: dict[str, tuple[ExecutionResult, ...]]) -> None:
        self.results = {capability_id: list(items) for capability_id, items in results.items()}
        self.calls: list[tuple[str, dict[str, Any]]] = []

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
        return (
            self.results[capability_id]
            .pop(0)
            .model_copy(update={"trace_id": request_context.request_id})
        )


class RoutingAdapter:
    def __init__(
        self,
        outputs: dict[str, dict[str, Any]],
        on_first_call: Callable[[], None] | None = None,
    ) -> None:
        self.outputs = outputs
        self.on_first_call = on_first_call
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(
        self,
        capability_id: str,
        arguments: dict[str, Any],
        execution_context: dict[str, Any],
    ) -> AdapterResult:
        assert execution_context == {}
        self.calls.append((capability_id, arguments))
        if len(self.calls) == 1 and self.on_first_call is not None:
            self.on_first_call()
        return AdapterResult(
            status="success",
            data=self.outputs[capability_id],
            error_code=None,
        )


class SequencedAdapter:
    def __init__(self, results: dict[str, tuple[AdapterResult, ...]]) -> None:
        self.results = {capability_id: list(items) for capability_id, items in results.items()}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(
        self,
        capability_id: str,
        arguments: dict[str, Any],
        execution_context: dict[str, Any],
    ) -> AdapterResult:
        assert execution_context == {}
        self.calls.append((capability_id, arguments))
        return self.results[capability_id].pop(0)


def _run_engine(
    definition: WorkflowDefinition,
    registry: RecordingRegistry,
    adapter: RoutingAdapter | SequencedAdapter,
    definitions: dict[str, WorkflowDefinition] | None = None,
    policy_guard: RecordingPolicyGuard | None = None,
) -> tuple[Any, RecordingTrace, RecordingTaskStore]:
    async def exercise() -> tuple[Any, RecordingTrace, RecordingTaskStore]:
        sensitive_key = "secret_" + "token"
        sensitive_value = "private-" + "marker-123"
        trace = RecordingTrace()
        task_store = RecordingTaskStore()
        gateway = CapabilityGateway(
            adapters={"oa": adapter},
            capability_registry=registry,
            policy_guard=policy_guard,
            trace_port=trace,
        )
        engine = WorkflowEngine(
            definitions=definitions or {definition.workflow_id: definition},
            capability_registry=registry,
            gateway=gateway,
            task_store=task_store,
            trace_port=trace,
        )
        sid = "session-1"
        result = await engine.execute(
            workflow_id=definition.workflow_id,
            expected_version=definition.version,
            task_id="task-1",
            session_id=sid,
            ai_user_id="user-1",
            initial_input={
                "requester": "alice",
                sensitive_key: sensitive_value,
            },
            request_context=RequestOrgContext(request_id="trace-1", channel="mock"),
        )
        return result, trace, task_store

    return asyncio.run(exercise())


def _run_engine_with_gateway(
    definition: WorkflowDefinition,
    registry: RecordingRegistry,
    gateway: SequencedGateway,
) -> tuple[Any, RecordingTrace, RecordingTaskStore, WorkflowEngine]:
    async def exercise() -> tuple[Any, RecordingTrace, RecordingTaskStore, WorkflowEngine]:
        trace = RecordingTrace()
        task_store = RecordingTaskStore()
        engine = WorkflowEngine(
            definitions={definition.workflow_id: definition},
            capability_registry=registry,
            gateway=gateway,
            task_store=task_store,
            trace_port=trace,
        )
        result = await engine.execute(
            workflow_id=definition.workflow_id,
            expected_version=definition.version,
            task_id="task-retry",
            session_id="session-retry",
            ai_user_id="user-retry",
            initial_input={"secret_token": "private-marker-123"},
            request_context=RequestOrgContext(request_id="trace-retry", channel="mock"),
        )
        return result, trace, task_store, engine

    return asyncio.run(exercise())


def test_linear_steps_branch_and_io_mapping_run_through_gateway_in_order() -> None:
    sensitive_key = "secret_" + "token"
    sensitive_value = "private-" + "marker-123"
    definition = WorkflowDefinition(
        workflow_id="workflow.employee-summary",
        version="1.0.0",
        steps=(
            WorkflowStep(
                step_id="lookup",
                capability_id="oa.employee.lookup",
                input_mapping={
                    "requested_by": WorkflowInputRef(source="workflow_input", key="requester")
                },
            ),
            WorkflowStep(
                step_id="summary",
                capability_id="oa.employee.summary",
                input_mapping={
                    "employee_id": WorkflowInputRef(
                        source="step_output", step_id="lookup", key="employee_id"
                    )
                },
                when=WorkflowCondition(
                    value=WorkflowInputRef(source="step_output", step_id="lookup", key="approved"),
                    equals=True,
                ),
            ),
        ),
    )
    registry = RecordingRegistry(
        _capability("oa.employee.lookup"),
        _capability("oa.employee.summary"),
    )
    adapter = RoutingAdapter(
        {
            "oa.employee.lookup": {
                "employee_id": "E-1",
                "approved": True,
                sensitive_key: sensitive_value,
            },
            "oa.employee.summary": {"summary": "ready"},
        }
    )

    result, trace, task_store = _run_engine(definition, registry, adapter)

    assert result.workflow_id == definition.workflow_id
    assert result.workflow_version == "1.0.0"
    assert result.status == "completed"
    assert result.step_outputs["summary"] == {"summary": "ready"}
    assert adapter.calls == [
        ("oa.employee.lookup", {"requested_by": "alice"}),
        ("oa.employee.summary", {"employee_id": "E-1"}),
    ]
    assert [step["event_type"] for step in trace.steps] == [
        "capability_selected",
        "gateway_pre_recorded",
        "adapter_called",
        "gateway_post_recorded",
        "capability_selected",
        "gateway_pre_recorded",
        "adapter_called",
        "gateway_post_recorded",
    ]
    workflow_trace = [step for step in trace.steps if "workflow_id" in step["attributes"]]
    assert [step["attributes"]["step_id"] for step in workflow_trace] == [
        "lookup",
        "summary",
    ]
    assert [step["attributes"]["step_status"] for step in workflow_trace] == [
        "running",
        "running",
    ]
    gateway_terminals = [
        step for step in trace.steps if step["event_type"] == "gateway_post_recorded"
    ]
    assert [step["capability_id"] for step in gateway_terminals] == [
        "oa.employee.lookup",
        "oa.employee.summary",
    ]
    assert [step["status"] for step in gateway_terminals] == ["ok", "ok"]
    assert [event.event_type for event in task_store.events] == [
        "workflow_started",
        "workflow_step_finished",
        "workflow_step_finished",
        "workflow_completed",
    ]
    assert sensitive_value not in repr(trace.steps)
    assert sensitive_value not in repr(task_store.events)


@pytest.mark.parametrize(
    ("decision", "expected_workflow_status", "expected_policy_event"),
    (
        ("deny", "denied", "blocked_by_policy"),
        ("confirm", "waiting_confirm", "confirm_required"),
    ),
)
def test_policy_terminal_short_circuits_workflow_before_later_gateway_call(
    decision: PolicyDecisionValue,
    expected_workflow_status: WorkflowRunStatus,
    expected_policy_event: str,
) -> None:
    definition = WorkflowDefinition(
        workflow_id="workflow.policy-short-circuit",
        version="1.0.0",
        steps=(
            WorkflowStep(
                step_id="guarded",
                capability_id="oa.guarded",
                confirmed_capability_id="oa.guarded.confirmed" if decision == "confirm" else None,
            ),
            WorkflowStep(step_id="later", capability_id="oa.later"),
        ),
    )
    registry = RecordingRegistry(
        _capability("oa.guarded"),
        _capability("oa.later"),
        *((_capability("oa.guarded.confirmed"),) if decision == "confirm" else ()),
    )
    policy_guard = RecordingPolicyGuard({"oa.guarded": decision, "oa.later": "allow"})
    adapter = RoutingAdapter(
        {
            "oa.guarded": {"must_not": "run"},
            "oa.later": {"must_not": "run"},
        }
    )

    result, trace, task_store = _run_engine(
        definition,
        registry,
        adapter,
        policy_guard=policy_guard,
    )

    assert result.status == expected_workflow_status
    assert result.output == {}
    assert result.step_outputs == {}
    assert policy_guard.calls == ["oa.guarded"]
    assert adapter.calls == []
    assert [step["event_type"] for step in trace.steps] == [
        "capability_selected",
        "policy_checked",
        expected_policy_event,
    ]
    terminal_event = "workflow_waiting_confirm" if decision == "confirm" else "workflow_completed"
    assert [event.event_type for event in task_store.events] == [
        "workflow_started",
        "workflow_step_finished",
        terminal_event,
    ]
    assert task_store.events[1].payload["step_status"] == expected_workflow_status
    if decision == "deny":
        assert task_store.events[2].payload["workflow_status"] == expected_workflow_status
    else:
        assert task_store.events[2].payload["waiting_step_id"] == "guarded"
    assert "private-marker-123" not in repr(trace.steps)
    assert "private-marker-123" not in repr(task_store.events)


def test_policy_allow_continues_through_all_workflow_steps() -> None:
    definition = WorkflowDefinition(
        workflow_id="workflow.policy-allow",
        version="1.0.0",
        steps=(
            WorkflowStep(step_id="first", capability_id="oa.first"),
            WorkflowStep(step_id="second", capability_id="oa.second"),
        ),
    )
    registry = RecordingRegistry(_capability("oa.first"), _capability("oa.second"))
    policy_guard = RecordingPolicyGuard({"oa.first": "allow", "oa.second": "allow"})
    adapter = RoutingAdapter({"oa.first": {"first": 1}, "oa.second": {"second": 2}})

    result, _, task_store = _run_engine(
        definition,
        registry,
        adapter,
        policy_guard=policy_guard,
    )

    assert result.status == "completed"
    assert result.output == {"second": 2}
    assert policy_guard.calls == ["oa.first", "oa.second"]
    assert [call[0] for call in adapter.calls] == ["oa.first", "oa.second"]
    assert task_store.events[-1].payload["workflow_status"] == "completed"


def test_false_condition_skips_only_that_step_then_rejoins_linear_sequence() -> None:
    definition = WorkflowDefinition(
        workflow_id="workflow.branch",
        version="1.0.0",
        steps=(
            WorkflowStep(step_id="check", capability_id="oa.check"),
            WorkflowStep(
                step_id="conditional",
                capability_id="oa.conditional",
                when=WorkflowCondition(
                    value=WorkflowInputRef(source="step_output", step_id="check", key="approved"),
                    equals=True,
                ),
            ),
            WorkflowStep(
                step_id="finish",
                capability_id="oa.finish",
                input_mapping={
                    "check_id": WorkflowInputRef(
                        source="step_output", step_id="check", key="check_id"
                    )
                },
            ),
        ),
    )
    registry = RecordingRegistry(
        _capability("oa.check"),
        _capability("oa.conditional"),
        _capability("oa.finish"),
    )
    adapter = RoutingAdapter(
        {
            "oa.check": {"approved": False, "check_id": "C-1"},
            "oa.conditional": {"unexpected": True},
            "oa.finish": {"done": True},
        }
    )

    result, trace, task_store = _run_engine(definition, registry, adapter)

    assert [call[0] for call in adapter.calls] == ["oa.check", "oa.finish"]
    assert adapter.calls[-1][1] == {"check_id": "C-1"}
    assert "conditional" not in result.step_outputs
    skipped = [step for step in trace.steps if step["attributes"].get("step_status") == "skipped"]
    assert len(skipped) == 1
    assert skipped[0]["event_type"] == "capability_selected"
    assert skipped[0]["attributes"]["step_id"] == "conditional"
    assert not [
        step
        for step in trace.steps
        if step["capability_id"] == "oa.conditional"
        and step["event_type"]
        in {"gateway_pre_recorded", "adapter_called", "gateway_post_recorded"}
    ]
    assert [event.payload["step_status"] for event in task_store.events[1:-1]] == [
        "completed",
        "skipped",
        "completed",
    ]


def test_definition_is_snapshotted_so_version_cannot_drift_mid_run() -> None:
    definition = WorkflowDefinition(
        workflow_id="workflow.locked",
        version="1.0.0",
        steps=(
            WorkflowStep(step_id="one", capability_id="oa.one"),
            WorkflowStep(step_id="two", capability_id="oa.two"),
        ),
    )
    definitions = {definition.workflow_id: definition}
    registry = RecordingRegistry(_capability("oa.one"), _capability("oa.two"))
    adapter = RoutingAdapter(
        {"oa.one": {"one": 1}, "oa.two": {"two": 2}},
        on_first_call=lambda: definitions.__setitem__(
            definition.workflow_id,
            replace(definition, version="2.0.0"),
        ),
    )

    result, trace, task_store = _run_engine(
        definition,
        registry,
        adapter,
        definitions,
    )

    assert definitions[definition.workflow_id].version == "2.0.0"
    assert result.workflow_version == "1.0.0"
    assert {
        step["attributes"]["workflow_version"]
        for step in trace.steps
        if "workflow_version" in step["attributes"]
    } == {"1.0.0"}
    assert {event.payload["workflow_version"] for event in task_store.events} == {"1.0.0"}


@pytest.mark.parametrize(
    "capability",
    (
        None,
        _capability("oa.unsafe", status="disabled"),
        _capability("oa.unsafe", risk_level="high"),
        _capability("oa.unsafe", capability_type="workflow"),
    ),
)
def test_every_step_requires_registered_active_low_risk_non_workflow_capability(
    capability: CapabilitySpec | None,
) -> None:
    definition = WorkflowDefinition(
        workflow_id="workflow.invalid-step",
        version="1.0.0",
        steps=(WorkflowStep(step_id="unsafe", capability_id="oa.unsafe"),),
    )
    registry = RecordingRegistry(*(() if capability is None else (capability,)))
    adapter = RoutingAdapter({"oa.unsafe": {"unexpected": True}})

    with pytest.raises(ValueError, match="active low-risk registered capability"):
        _run_engine(definition, registry, adapter)

    assert adapter.calls == []


@pytest.mark.parametrize("reference_site", ("input_mapping", "when"))
@pytest.mark.parametrize("target_step_id", ("first", "second", "missing"))
def test_step_output_reference_must_target_an_existing_strictly_earlier_step(
    reference_site: str,
    target_step_id: str,
) -> None:
    value_ref = WorkflowInputRef(
        source="step_output",
        step_id=target_step_id,
        key="value",
    )
    first_step = WorkflowStep(
        step_id="first",
        capability_id="oa.first",
        input_mapping={"value": value_ref} if reference_site == "input_mapping" else {},
        when=(
            WorkflowCondition(value=value_ref, equals=True) if reference_site == "when" else None
        ),
    )
    definition = WorkflowDefinition(
        workflow_id="workflow.invalid-reference",
        version="1.0.0",
        steps=(
            first_step,
            WorkflowStep(step_id="second", capability_id="oa.second"),
        ),
    )
    registry = RecordingRegistry(
        _capability("oa.first"),
        _capability("oa.second"),
    )
    adapter = RoutingAdapter(
        {
            "oa.first": {"value": 1},
            "oa.second": {"value": 2},
        }
    )

    with pytest.raises(ValueError, match="strictly earlier"):
        _run_engine(definition, registry, adapter)

    assert adapter.calls == []


def test_ungated_execute_preserves_invalid_input_mapping_error_identity() -> None:
    definition = WorkflowDefinition(
        workflow_id="workflow.invalid-runtime-input",
        version="1.0.0",
        steps=(
            WorkflowStep(
                step_id="lookup",
                capability_id="oa.lookup",
                input_mapping={
                    "required_value": WorkflowInputRef(
                        source="workflow_input",
                        key="missing_value",
                    )
                },
            ),
        ),
    )
    registry = RecordingRegistry(_capability("oa.lookup"))
    adapter = RoutingAdapter({"oa.lookup": {"must_not": "run"}})

    with pytest.raises(ValueError, match="^invalid Workflow input mapping$"):
        _run_engine(definition, registry, adapter)

    assert adapter.calls == []


class RecordingMinimalPolicyGuard(MinimalPolicyGuard):
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def decide(
        self,
        ai_user_id: str,
        capability_id: str,
        arguments: dict[str, Any],
        request_context: RequestOrgContext,
    ) -> PolicyDecision:
        self.calls.append(capability_id)
        return await super().decide(
            ai_user_id,
            capability_id,
            arguments,
            request_context,
        )


def test_confirm_checkpoint_resumes_with_confirmed_variant_and_locked_definition() -> None:
    async def exercise() -> tuple[
        Any,
        Any,
        RecordingMinimalPolicyGuard,
        RoutingAdapter,
        RecordingTrace,
        RecordingTaskStore,
    ]:
        sensitive_key = "secret_" + "token"
        sensitive_value = "private-" + "marker-123"
        definition = WorkflowDefinition(
            workflow_id="workflow.leave-submit",
            version="1.0.0",
            steps=(
                WorkflowStep(step_id="prepare", capability_id="oa.leave.prepare"),
                WorkflowStep(
                    step_id="submit",
                    capability_id="oa.leave.submit_confirm",
                    confirmed_capability_id="oa.submit_leave_request.confirmed_mock",
                    input_mapping={
                        "request_id": WorkflowInputRef(
                            source="step_output",
                            step_id="prepare",
                            key="request_id",
                        ),
                        "requester": WorkflowInputRef(
                            source="workflow_input",
                            key="requester",
                        ),
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
        registry = RecordingRegistry(
            _capability("oa.leave.prepare"),
            _capability("oa.leave.submit_confirm"),
            _capability("oa.submit_leave_request.confirmed_mock"),
            _capability("oa.leave.audit"),
        )
        adapter = RoutingAdapter(
            {
                "oa.leave.prepare": {
                    "request_id": "REQ-1",
                    sensitive_key: sensitive_value,
                },
                "oa.leave.submit_confirm": {"must_not": "run"},
                "oa.submit_leave_request.confirmed_mock": {"workflow_id": "WF-1"},
                "oa.leave.audit": {"status": "recorded"},
            }
        )
        policy_guard = RecordingMinimalPolicyGuard()
        trace = RecordingTrace()
        task_store = RecordingTaskStore()
        gateway = CapabilityGateway(
            adapters={"oa": adapter},
            capability_registry=registry,
            policy_guard=policy_guard,
            trace_port=trace,
        )
        engine = WorkflowEngine(
            definitions=definitions,
            capability_registry=registry,
            gateway=gateway,
            task_store=task_store,
            trace_port=trace,
        )

        waiting = await engine.execute(
            workflow_id=definition.workflow_id,
            expected_version=definition.version,
            task_id="task-confirm",
            session_id="session-confirm",
            ai_user_id="user-confirm",
            initial_input={
                "requester": "alice",
                sensitive_key: sensitive_value,
            },
            request_context=RequestOrgContext(
                request_id="trace-confirm",
                channel="mock",
            ),
        )

        registry.items["oa.submit_leave_request.confirmed_mock"] = _capability(
            "oa.submit_leave_request.confirmed_mock",
            status="disabled",
        )
        with pytest.raises(ValueError, match="confirmed Workflow capability"):
            await engine.resume(task_id="task-confirm", confirmed=True)
        assert [call[0] for call in adapter.calls] == ["oa.leave.prepare"]
        assert "workflow_resumed" not in [event.event_type for event in task_store.events]
        registry.items["oa.submit_leave_request.confirmed_mock"] = _capability(
            "oa.submit_leave_request.confirmed_mock"
        )

        definitions[definition.workflow_id] = replace(
            definition,
            version="2.0.0",
            steps=(WorkflowStep(step_id="drifted", capability_id="oa.leave.audit"),),
        )
        resumed = await engine.resume(task_id="task-confirm", confirmed=True)
        with pytest.raises(ValueError, match="no waiting Workflow checkpoint"):
            await engine.resume(task_id="task-confirm", confirmed=True)
        return waiting, resumed, policy_guard, adapter, trace, task_store

    waiting, resumed, policy_guard, adapter, trace, task_store = asyncio.run(exercise())

    assert waiting.status == "waiting_confirm"
    assert waiting.output == {}
    assert waiting.step_outputs == {
        "prepare": {
            "request_id": "REQ-1",
            "secret_token": "private-marker-123",
        }
    }
    assert [call[0] for call in adapter.calls[:1]] == ["oa.leave.prepare"]
    waiting_event = next(
        event for event in task_store.events if event.event_type == "workflow_waiting_confirm"
    )
    assert waiting_event.event_type == "workflow_waiting_confirm"
    assert waiting_event.payload == {
        "workflow_id": "workflow.leave-submit",
        "workflow_version": "1.0.0",
        "waiting_step_id": "submit",
        "waiting_step_index": 1,
        "confirmed_capability_id": "oa.submit_leave_request.confirmed_mock",
        "completed_step_ids": ["prepare"],
        "step_output_keys": {"prepare": ["request_id"]},
        "recovery_input_keys": ["requester"],
    }
    assert "workflow_completed" not in [event.event_type for event in task_store.events[:3]]

    assert resumed.status == "completed"
    assert resumed.workflow_version == "1.0.0"
    assert resumed.output == {"status": "recorded"}
    assert [call[0] for call in adapter.calls] == [
        "oa.leave.prepare",
        "oa.submit_leave_request.confirmed_mock",
        "oa.leave.audit",
    ]
    assert policy_guard.calls == [
        "oa.leave.prepare",
        "oa.leave.submit_confirm",
        "oa.submit_leave_request.confirmed_mock",
        "oa.leave.audit",
    ]
    assert [event.event_type for event in task_store.events] == [
        "workflow_started",
        "workflow_step_finished",
        "workflow_step_finished",
        "workflow_waiting_confirm",
        "workflow_resumed",
        "workflow_step_finished",
        "workflow_step_finished",
        "workflow_completed",
    ]
    assert {event.payload["workflow_version"] for event in task_store.events} == {"1.0.0"}
    selected_capabilities = [
        step["capability_id"] for step in trace.steps if step["event_type"] == "capability_selected"
    ]
    assert selected_capabilities == [
        "oa.leave.prepare",
        "oa.leave.submit_confirm",
        "oa.submit_leave_request.confirmed_mock",
        "oa.leave.audit",
    ]
    assert "private-marker-123" not in repr(task_store.events)
    assert "private-marker-123" not in repr(trace.steps)


@pytest.mark.parametrize(
    "confirmed_capability",
    (
        None,
        _capability("oa.confirmed", status="disabled"),
        _capability("oa.confirmed", risk_level="high"),
        _capability("oa.confirmed", capability_type="workflow"),
    ),
)
def test_confirmed_variant_requires_registered_active_low_risk_capability(
    confirmed_capability: CapabilitySpec | None,
) -> None:
    definition = WorkflowDefinition(
        workflow_id="workflow.invalid-confirmed-variant",
        version="1.0.0",
        steps=(
            WorkflowStep(
                step_id="guarded",
                capability_id="oa.guarded_confirm",
                confirmed_capability_id="oa.confirmed",
            ),
        ),
    )
    registry = RecordingRegistry(
        _capability("oa.guarded_confirm"),
        *(() if confirmed_capability is None else (confirmed_capability,)),
    )
    adapter = RoutingAdapter(
        {
            "oa.guarded_confirm": {"must_not": "run"},
            "oa.confirmed": {"must_not": "run"},
        }
    )

    with pytest.raises(ValueError, match="confirmed Workflow capability"):
        _run_engine(definition, registry, adapter)

    assert adapter.calls == []


def test_retry_policy_is_fixed_to_one_adapter_timeout_retry() -> None:
    assert RETRYABLE_ERROR_CODES == frozenset({"adapter_timeout"})
    assert MAX_STEP_RETRIES == 1


def test_retryable_timeout_exhaustion_stops_before_later_gateway_call() -> None:
    definition = WorkflowDefinition(
        workflow_id="workflow.retry-exhausted",
        version="1.0.0",
        steps=(
            WorkflowStep(step_id="unstable", capability_id="oa.unstable"),
            WorkflowStep(step_id="later", capability_id="oa.later"),
        ),
    )
    registry = RecordingRegistry(_capability("oa.unstable"), _capability("oa.later"))
    adapter = SequencedAdapter(
        {
            "oa.unstable": (
                AdapterResult(status="timeout", error_code="adapter_timeout"),
                AdapterResult(status="timeout", error_code="adapter_timeout"),
            ),
            "oa.later": (AdapterResult(status="success", data={"late": True}),),
        }
    )

    result, trace, task_store = _run_engine(definition, registry, adapter)

    assert result.status == "timeout"
    assert result.error_code == "adapter_timeout"
    assert result.output == {}
    assert result.step_outputs == {}
    assert [capability_id for capability_id, _ in adapter.calls] == [
        "oa.unstable",
        "oa.unstable",
    ]
    selected = [step for step in trace.steps if step["event_type"] == "capability_selected"]
    assert [step["attributes"]["step_status"] for step in selected] == [
        "running",
        "retrying",
    ]
    assert [step["attributes"]["attempt"] for step in selected] == [1, 2]
    assert [step["attributes"]["retry_number"] for step in selected] == [0, 1]
    assert all(step["attributes"]["max_attempts"] == 2 for step in selected)
    mapped = [step for step in trace.steps if step["event_type"] == "adapter_error_mapped"]
    assert len(mapped) == 2
    assert all(step["error_code"] == "adapter_timeout" for step in mapped)
    assert [event.event_type for event in task_store.events] == [
        "workflow_started",
        "workflow_step_finished",
        "workflow_failed",
    ]
    assert task_store.events[1].payload == {
        "workflow_id": definition.workflow_id,
        "workflow_version": definition.version,
        "step_id": "unstable",
        "step_index": 0,
        "step_status": "timeout",
        "attempt": 2,
        "retry_number": 1,
        "max_attempts": 2,
        "error_code": "adapter_timeout",
    }
    assert task_store.events[2].payload["workflow_status"] == "timeout"
    assert task_store.events[2].payload["error_code"] == "adapter_timeout"
    assert "private-marker-123" not in repr(trace.steps)
    assert "private-marker-123" not in repr(task_store.events)


def test_retryable_timeout_can_succeed_on_the_only_retry() -> None:
    definition = WorkflowDefinition(
        workflow_id="workflow.retry-success",
        version="1.0.0",
        steps=(
            WorkflowStep(step_id="unstable", capability_id="oa.unstable"),
            WorkflowStep(step_id="later", capability_id="oa.later"),
        ),
    )
    registry = RecordingRegistry(_capability("oa.unstable"), _capability("oa.later"))
    gateway = SequencedGateway(
        {
            "oa.unstable": (
                ExecutionResult(
                    status="timeout",
                    error_code="adapter_timeout",
                    trace_id="timeout",
                ),
                ExecutionResult(status="completed", data={"ready": True}, trace_id="ready"),
            ),
            "oa.later": (
                ExecutionResult(status="completed", data={"done": True}, trace_id="done"),
            ),
        }
    )

    result, _, task_store, _ = _run_engine_with_gateway(definition, registry, gateway)

    assert result.status == "completed"
    assert result.output == {"done": True}
    assert [capability_id for capability_id, _ in gateway.calls] == [
        "oa.unstable",
        "oa.unstable",
        "oa.later",
    ]
    first_step = task_store.events[1]
    assert first_step.payload["step_status"] == "completed"
    assert first_step.payload["attempt"] == 2
    assert first_step.payload["retry_number"] == 1


@pytest.mark.parametrize(
    ("status", "error_code", "expected_status"),
    (
        ("denied", "policy_denied", "denied"),
        ("waiting_user", "confirm_required", "waiting_confirm"),
        ("no_capability_found", "capability_not_found", "failed"),
        ("binding_required", "identity_unbound", "failed"),
        ("binding_required", "identity_expired", "failed"),
        ("binding_required", "identity_revoked", "failed"),
        ("failed", "adapter_payload_invalid", "failed"),
        ("failed", "adapter_missing_required_field", "failed"),
    ),
)
def test_deterministic_step_errors_never_retry_or_call_later_step(
    status: str,
    error_code: str,
    expected_status: WorkflowRunStatus,
) -> None:
    definition = WorkflowDefinition(
        workflow_id="workflow.no-retry",
        version="1.0.0",
        steps=(
            WorkflowStep(
                step_id="terminal",
                capability_id="oa.terminal",
                confirmed_capability_id="oa.terminal.confirmed",
            ),
            WorkflowStep(step_id="later", capability_id="oa.later"),
        ),
    )
    registry = RecordingRegistry(
        _capability("oa.terminal"),
        _capability("oa.terminal.confirmed"),
        _capability("oa.later"),
    )
    gateway = SequencedGateway(
        {
            "oa.terminal": (
                ExecutionResult(
                    status=status,
                    error_code=error_code,
                    trace_id="terminal",
                ),
            ),
            "oa.later": (
                ExecutionResult(status="completed", data={"late": True}, trace_id="late"),
            ),
        }
    )

    result, trace, _, _ = _run_engine_with_gateway(definition, registry, gateway)

    assert result.status == expected_status
    assert result.error_code == error_code
    assert [capability_id for capability_id, _ in gateway.calls] == ["oa.terminal"]
    selected = [step for step in trace.steps if step["event_type"] == "capability_selected"]
    assert len(selected) == 1
    assert selected[0]["attributes"]["attempt"] == 1
    assert selected[0]["attributes"]["retry_number"] == 0
