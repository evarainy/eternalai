"""WorkflowEnginePort — the abstraction boundary `RuntimeImpl` executes Workflow
capabilities through, instead of depending on the concrete `WorkflowEngine`
class directly.

Extracted 2026-08-19 decision ("两个执行内核接缝从现有实现提取，不等第三方框架",
`docs/phase2/DECISIONS.md`): the port covers the complete public surface that
`RuntimeImpl` actually calls on `WorkflowEngine` — `execute`, `resume`,
`version_bindings`, `resume_version_bindings`, `discard_checkpoint` and
`pending_confirmation_action_digest` — so no call site is left bypassing the
seam. The signature intentionally carries no candidate-framework types
(LangGraph/Temporal/OpenAI Agents SDK/Microsoft Agent Framework checkpointer,
`RunState`, thread ID, task token); those stay inside adapters.

`WorkflowVersionBindings` is this port's own public return type for
`version_bindings`. It intentionally does not reuse
`app.workflow.engine._WorkflowVersionBindingsSnapshot` — that dataclass is a
private infra implementation detail, and lifting it into the port would make
the port depend on infra shape rather than the other way around.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from app.ports.capability_gateway import RequestOrgContext
from app.ports.capability_registry import CapabilitySpec
from app.ports.human_gate import VersionBinding
from app.workflow.models import WorkflowRunResult


@dataclass(frozen=True)
class WorkflowVersionBindings:
    """Public projection of a Workflow definition's version-binding tuple.

    `bindings` is the complete immutable tuple resolved from the definition;
    `workflow_binding` is the single top-level binding that identifies the
    Workflow definition itself within that tuple.
    """

    bindings: tuple[VersionBinding, ...]
    workflow_binding: VersionBinding


class WorkflowEnginePort(Protocol):
    """Executes a version-locked Workflow definition to completion or a
    waiting-confirmation checkpoint, and resumes a waiting checkpoint after
    confirmation."""

    async def execute(
        self,
        *,
        workflow_id: str,
        expected_version: str,
        workflow_capability: CapabilitySpec,
        task_id: str,
        session_id: str,
        ai_user_id: str,
        initial_input: Mapping[str, Any],
        request_context: RequestOrgContext,
    ) -> WorkflowRunResult: ...

    async def resume(
        self,
        *,
        task_id: str,
        confirmed: bool,
        expected_action_digest: str | None = None,
    ) -> WorkflowRunResult: ...

    async def version_bindings(
        self,
        *,
        workflow_capability: CapabilitySpec,
    ) -> WorkflowVersionBindings: ...

    async def resume_version_bindings(
        self,
        *,
        task_id: str,
    ) -> tuple[VersionBinding, ...]: ...

    def discard_checkpoint(self, task_id: str) -> None: ...

    def pending_confirmation_action_digest(self, task_id: str) -> str: ...


__all__ = ("WorkflowEnginePort", "WorkflowVersionBindings")
