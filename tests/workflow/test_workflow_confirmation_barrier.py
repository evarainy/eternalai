"""Confirmation barrier: Policy allow never stands in for a human confirmation."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from app.infra.gateway.capability_gateway import CapabilityGateway
from app.infra.policy.minimal_policy_guard import MinimalPolicyGuard
from app.ports.capability_gateway import RequestOrgContext
from app.workflow.engine import WorkflowEngine
from app.workflow.models import WorkflowDefinition, WorkflowRunResult, WorkflowStep
from tests.workflow.test_engine import (
    RecordingRegistry,
    RecordingTaskStore,
    RecordingTrace,
    RoutingAdapter,
    _capability,
)

_CONTEXT = RequestOrgContext(request_id="trace-barrier", tenant_id="tenant-barrier", channel="mock")
_READ = _capability("oa.leave.read")
_WRITE = _capability("oa.leave.write", capability_type="action")
_APPROVE_CONFIRM = _capability("oa.leave.approve_confirm", capability_type="action")
_APPROVE = _capability("oa.leave.approve", capability_type="action")
_GOVERNED_LEAF = _capability(
    "business.plan.submit", risk_level="high", capability_type="action"
).model_copy(update={"target_system": "business_platform"})


def _engine(
    definition: WorkflowDefinition,
) -> tuple[WorkflowEngine, RoutingAdapter, RecordingTrace]:
    registry = RecordingRegistry(_READ, _WRITE, _APPROVE_CONFIRM, _APPROVE, _GOVERNED_LEAF)
    adapter = RoutingAdapter({item: {"ok": True} for item in registry.items})
    trace = RecordingTrace()
    gateway = CapabilityGateway(
        adapters={"oa": adapter},
        capability_registry=registry,
        policy_guard=MinimalPolicyGuard(),
        trace_port=trace,
        tenant_id="tenant-barrier",
    )
    engine = WorkflowEngine(
        definitions={definition.workflow_id: definition},
        capability_registry=registry,
        gateway=gateway,
        task_store=RecordingTaskStore(),
        trace_port=trace,
    )
    return engine, adapter, trace


async def _execute(
    engine: WorkflowEngine, definition: WorkflowDefinition, *, barrier: bool
) -> WorkflowRunResult:
    return await engine.execute(
        workflow_id=definition.workflow_id,
        expected_version=definition.version,
        workflow_capability=_capability(definition.workflow_id, capability_type="workflow"),
        task_id="task-barrier",
        session_id="session-barrier",
        ai_user_id="user-barrier",
        initial_input={},
        request_context=_CONTEXT,
        confirmation_barrier=barrier,
    )


def _blocked(trace: RecordingTrace, capability_id: str) -> list[tuple[str, str, str | None]]:
    return [
        (step["event_type"], step["status"], step["error_code"])
        for step in trace.steps
        if step["capability_id"] == capability_id
        and step["event_type"] in {"policy_checked", "blocked_by_policy"}
    ]


_READ_THEN_WRITE = WorkflowDefinition(
    workflow_id="workflow.barrier.read-write",
    version="1.0.0",
    steps=(
        WorkflowStep(step_id="read", capability_id=_READ.capability_id),
        WorkflowStep(step_id="write", capability_id=_WRITE.capability_id),
    ),
)
_GOVERNED = WorkflowDefinition(
    workflow_id="workflow.barrier.governed",
    version="1.0.0",
    steps=(WorkflowStep(step_id="submit", capability_id=_GOVERNED_LEAF.capability_id),),
)
_CONFIRM_THEN_WRITE = WorkflowDefinition(
    workflow_id="workflow.barrier.confirm-write",
    version="1.0.0",
    steps=(
        WorkflowStep(
            step_id="approve",
            capability_id=_APPROVE_CONFIRM.capability_id,
            confirmed_capability_id=_APPROVE.capability_id,
        ),
        WorkflowStep(step_id="write", capability_id=_WRITE.capability_id),
    ),
)


def test_allow_write_leaf_is_rejected_before_any_confirmation() -> None:
    async def exercise() -> None:
        engine, adapter, trace = _engine(_READ_THEN_WRITE)
        result = await _execute(engine, _READ_THEN_WRITE, barrier=True)
        assert (result.status, result.error_code) == ("denied", "policy_denied")
        assert adapter.calls == [(_READ.capability_id, {})]
        assert _blocked(trace, _WRITE.capability_id) == [
            ("policy_checked", "blocked", "policy_denied"),
            ("blocked_by_policy", "blocked", "policy_denied"),
        ]

    asyncio.run(exercise())


def test_barrier_off_keeps_existing_allow_behavior() -> None:
    async def exercise() -> None:
        engine, adapter, trace = _engine(_READ_THEN_WRITE)
        result = await _execute(engine, _READ_THEN_WRITE, barrier=False)
        assert result.status == "completed"
        assert adapter.calls == [(_READ.capability_id, {}), (_WRITE.capability_id, {})]
        assert _blocked(trace, _WRITE.capability_id) == [("policy_checked", "ok", None)]

    asyncio.run(exercise())


def test_only_the_confirmed_step_sends_and_later_allow_writes_stay_blocked() -> None:
    async def exercise() -> None:
        engine, adapter, trace = _engine(_CONFIRM_THEN_WRITE)
        waiting = await _execute(engine, _CONFIRM_THEN_WRITE, barrier=True)
        assert (waiting.status, waiting.error_code) == ("waiting_confirm", "confirm_required")
        assert adapter.calls == []
        resumed = await engine.resume(task_id="task-barrier", confirmed=True)
        assert (resumed.status, resumed.error_code) == ("denied", "policy_denied")
        assert adapter.calls == [(_APPROVE.capability_id, {})]
        assert _blocked(trace, _WRITE.capability_id) == [
            ("policy_checked", "blocked", "policy_denied"),
            ("blocked_by_policy", "blocked", "policy_denied"),
        ]

    asyncio.run(exercise())


def test_governed_mcp_start_never_receives_the_barrier() -> None:
    async def exercise() -> None:
        engine, adapter, _ = _engine(_GOVERNED)
        started: list[dict[str, Any]] = []
        sentinel = WorkflowRunResult(
            workflow_id=_GOVERNED.workflow_id,
            workflow_version=_GOVERNED.version,
            trace_id=_CONTEXT.request_id,
            status="waiting_confirm",
            output={},
            step_outputs={},
        )

        async def start(definition: WorkflowDefinition, **kwargs: Any) -> WorkflowRunResult:
            started.append(kwargs)
            return sentinel

        engine._governed = SimpleNamespace(  # type: ignore[assignment]
            admitted=lambda definition: True, start=start
        )
        assert await _execute(engine, _GOVERNED, barrier=True) is sentinel
        assert len(started) == 1 and "confirmation_barrier" not in started[0]
        assert adapter.calls == []

    asyncio.run(exercise())
