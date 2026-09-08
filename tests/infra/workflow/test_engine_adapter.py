"""Forwarding guard for `WorkflowEngineAdapter` (P2-PORT-SEAM-001 monitor finding).

An earlier candidate wired the seam — `WorkflowEnginePort` + adapter +
`RuntimeImpl` depending on the Port — but nothing asserted that each
delegating method actually forwards its arguments and return value: turning
`discard_checkpoint` into a no-op left the full suite (2877 tests) fully
green. These tests exercise every one of `WorkflowEngineAdapter`'s six
methods directly against a spy `WorkflowEngine` stand-in, so a delegate that
drops arguments, swallows a call, or discards/mutates a return value is
caught here rather than nowhere.

Each method's forwarding is proven with a real mutation of
`app/infra/workflow/engine_adapter.py` (turning the delegate call into a
no-op / wrong-value stub), re-run in isolation against this file only — see
the PR body for the six exit-code/failing-test records. `version_bindings`
additionally asserts the private `_WorkflowVersionBindingsSnapshot` ->
public `WorkflowVersionBindings` conversion carries every field across
unchanged.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.infra.workflow.engine_adapter import WorkflowEngineAdapter
from app.ports.capability_gateway import RequestOrgContext
from app.ports.capability_registry import CapabilitySpec
from app.ports.human_gate import VersionBinding
from app.workflow.engine import _WorkflowVersionBindingsSnapshot
from app.workflow.models import WorkflowRunResult


def _capability(capability_id: str = "wf.example") -> CapabilitySpec:
    return CapabilitySpec(
        capability_id=capability_id,
        name=capability_id,
        type="workflow",
        input_schema_digest=f"input-{capability_id}",
        output_schema_digest=f"output-{capability_id}",
        risk_level="low",
        owner="adapter-test",
        version="1.0.0",
        status="active",
        short_description=capability_id,
        target_system="oa",
        execution_identity="user_delegated",
        binding_required=False,
    )


def _request_context() -> RequestOrgContext:
    return RequestOrgContext(
        request_id="trace-adapter-1",
        channel="api",
        tenant_id="tenant-adapter",
    )


def _binding(resource_id: str, *, digest_char: str = "a") -> VersionBinding:
    return VersionBinding(
        resource_type="workflow",
        resource_id=resource_id,
        version="1.0.0",
        digest=digest_char * 64,
    )


def _run_result(status: str = "completed") -> WorkflowRunResult:
    return WorkflowRunResult(
        workflow_id="wf.example",
        workflow_version="1.0.0",
        trace_id="trace-adapter-1",
        status=status,  # type: ignore[arg-type]
        output={"marker": "adapter-test-output"},
        step_outputs={"step-1": {"marker": "adapter-test-step-output"}},
    )


class _SpyWorkflowEngine:
    """Duck-typed stand-in for `WorkflowEngine`; records every call it sees."""

    def __init__(self) -> None:
        self.execute_calls: list[dict[str, Any]] = []
        self.resume_calls: list[dict[str, Any]] = []
        self.version_bindings_calls: list[dict[str, Any]] = []
        self.resume_version_bindings_calls: list[dict[str, Any]] = []
        self.discard_checkpoint_calls: list[str] = []
        self.pending_confirmation_action_digest_calls: list[str] = []

        self.execute_result = _run_result("completed")
        self.resume_result = _run_result("waiting_confirm")
        self.version_bindings_result = _WorkflowVersionBindingsSnapshot(
            bindings=(
                _binding("wf.example", digest_char="a"),
                _binding("wf.step", digest_char="b"),
            ),
            workflow_binding=_binding("wf.example", digest_char="a"),
        )
        self.resume_version_bindings_result: tuple[VersionBinding, ...] = (
            _binding("wf.example", digest_char="c"),
        )
        self.pending_confirmation_action_digest_result = "d" * 64

    async def execute(self, **kwargs: Any) -> WorkflowRunResult:
        self.execute_calls.append(kwargs)
        return self.execute_result

    async def resume(self, **kwargs: Any) -> WorkflowRunResult:
        self.resume_calls.append(kwargs)
        return self.resume_result

    async def version_bindings(self, **kwargs: Any) -> _WorkflowVersionBindingsSnapshot:
        self.version_bindings_calls.append(kwargs)
        return self.version_bindings_result

    async def resume_version_bindings(self, **kwargs: Any) -> tuple[VersionBinding, ...]:
        self.resume_version_bindings_calls.append(kwargs)
        return self.resume_version_bindings_result

    def discard_checkpoint(self, task_id: str) -> None:
        self.discard_checkpoint_calls.append(task_id)

    def pending_confirmation_action_digest(self, task_id: str) -> str:
        self.pending_confirmation_action_digest_calls.append(task_id)
        return self.pending_confirmation_action_digest_result


def test_execute_forwards_every_argument_and_returns_engine_result_unchanged() -> None:
    async def exercise() -> None:
        spy = _SpyWorkflowEngine()
        adapter = WorkflowEngineAdapter(spy)  # type: ignore[arg-type]
        capability = _capability()
        context = _request_context()

        result = await adapter.execute(
            workflow_id="wf.example",
            expected_version="1.0.0",
            workflow_capability=capability,
            task_id="task-1",
            session_id="session-1",
            ai_user_id="user-1",
            initial_input={"field": "value"},
            request_context=context,
        )

        assert len(spy.execute_calls) == 1
        forwarded = spy.execute_calls[0]
        assert forwarded == {
            "workflow_id": "wf.example",
            "expected_version": "1.0.0",
            "workflow_capability": capability,
            "task_id": "task-1",
            "session_id": "session-1",
            "ai_user_id": "user-1",
            "initial_input": {"field": "value"},
            "request_context": context,
        }
        assert result is spy.execute_result

    asyncio.run(exercise())


def test_resume_forwards_every_argument_and_returns_engine_result_unchanged() -> None:
    async def exercise() -> None:
        spy = _SpyWorkflowEngine()
        adapter = WorkflowEngineAdapter(spy)  # type: ignore[arg-type]

        result = await adapter.resume(
            task_id="task-2",
            confirmed=True,
            expected_action_digest="e" * 64,
        )

        assert len(spy.resume_calls) == 1
        assert spy.resume_calls[0] == {
            "task_id": "task-2",
            "confirmed": True,
            "expected_action_digest": "e" * 64,
        }
        assert result is spy.resume_result

    asyncio.run(exercise())


def test_resume_forwards_default_expected_action_digest_as_none() -> None:
    async def exercise() -> None:
        spy = _SpyWorkflowEngine()
        adapter = WorkflowEngineAdapter(spy)  # type: ignore[arg-type]

        await adapter.resume(task_id="task-3", confirmed=True)

        assert spy.resume_calls[0]["expected_action_digest"] is None

    asyncio.run(exercise())


def test_version_bindings_forwards_argument_and_converts_snapshot_without_dropping_fields() -> None:
    async def exercise() -> None:
        spy = _SpyWorkflowEngine()
        adapter = WorkflowEngineAdapter(spy)  # type: ignore[arg-type]
        capability = _capability()

        result = await adapter.version_bindings(workflow_capability=capability)

        assert len(spy.version_bindings_calls) == 1
        assert spy.version_bindings_calls[0] == {"workflow_capability": capability}

        # The private engine snapshot and the public port result must carry
        # the exact same tuple of bindings and the exact same top-level
        # workflow binding — the conversion must not drop, reorder, or
        # substitute either field.
        assert result.bindings == spy.version_bindings_result.bindings
        assert result.workflow_binding == spy.version_bindings_result.workflow_binding
        assert len(result.bindings) == 2

    asyncio.run(exercise())


def test_resume_version_bindings_forwards_argument_and_returns_engine_result_unchanged() -> None:
    async def exercise() -> None:
        spy = _SpyWorkflowEngine()
        adapter = WorkflowEngineAdapter(spy)  # type: ignore[arg-type]

        result = await adapter.resume_version_bindings(task_id="task-4")

        assert len(spy.resume_version_bindings_calls) == 1
        assert spy.resume_version_bindings_calls[0] == {"task_id": "task-4"}
        assert result == spy.resume_version_bindings_result

    asyncio.run(exercise())


def test_discard_checkpoint_forwards_task_id() -> None:
    spy = _SpyWorkflowEngine()
    adapter = WorkflowEngineAdapter(spy)  # type: ignore[arg-type]

    adapter.discard_checkpoint("task-5")

    assert spy.discard_checkpoint_calls == ["task-5"]


def test_pending_confirmation_action_digest_forwards_task_id_and_returns_engine_result() -> None:
    spy = _SpyWorkflowEngine()
    adapter = WorkflowEngineAdapter(spy)  # type: ignore[arg-type]

    result = adapter.pending_confirmation_action_digest("task-6")

    assert spy.pending_confirmation_action_digest_calls == ["task-6"]
    assert result == spy.pending_confirmation_action_digest_result
