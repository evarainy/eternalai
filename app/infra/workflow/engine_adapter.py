"""Production `WorkflowEnginePort` implementation.

Wraps the existing `app.workflow.engine.WorkflowEngine` — the first (and, per
the 2026-08-19 decision, deliberately the *only* for now) implementation of
the seam. It delegates every method 1:1 except `version_bindings`, whose
result is converted from the engine's private
`_WorkflowVersionBindingsSnapshot` into the port's public
`WorkflowVersionBindings`.
"""

from __future__ import annotations

from typing import Any, Mapping

from app.ports.capability_gateway import RequestOrgContext
from app.ports.capability_registry import CapabilitySpec
from app.ports.human_gate import VersionBinding
from app.ports.workflow_engine import WorkflowVersionBindings
from app.workflow.engine import WorkflowEngine
from app.workflow.models import WorkflowRunResult


class WorkflowEngineAdapter:
    """Adapts the concrete `WorkflowEngine` to `WorkflowEnginePort`."""

    def __init__(self, engine: WorkflowEngine) -> None:
        self._engine = engine

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
    ) -> WorkflowRunResult:
        return await self._engine.execute(
            workflow_id=workflow_id,
            expected_version=expected_version,
            workflow_capability=workflow_capability,
            task_id=task_id,
            session_id=session_id,
            ai_user_id=ai_user_id,
            initial_input=initial_input,
            request_context=request_context,
        )

    async def resume(
        self,
        *,
        task_id: str,
        confirmed: bool,
        expected_action_digest: str | None = None,
    ) -> WorkflowRunResult:
        return await self._engine.resume(
            task_id=task_id,
            confirmed=confirmed,
            expected_action_digest=expected_action_digest,
        )

    async def version_bindings(
        self,
        *,
        workflow_capability: CapabilitySpec,
    ) -> WorkflowVersionBindings:
        snapshot = await self._engine.version_bindings(
            workflow_capability=workflow_capability,
        )
        return WorkflowVersionBindings(
            bindings=snapshot.bindings,
            workflow_binding=snapshot.workflow_binding,
        )

    async def resume_version_bindings(
        self,
        *,
        task_id: str,
    ) -> tuple[VersionBinding, ...]:
        return await self._engine.resume_version_bindings(task_id=task_id)

    def discard_checkpoint(self, task_id: str) -> None:
        self._engine.discard_checkpoint(task_id)

    def pending_confirmation_action_digest(self, task_id: str) -> str:
        return self._engine.pending_confirmation_action_digest(task_id)


__all__ = ("WorkflowEngineAdapter",)
