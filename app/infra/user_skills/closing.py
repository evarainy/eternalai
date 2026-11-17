"""Task close for runs of personal flows, in the same order as the chat close.

Task status, terminal trace event, evaluation record, trace finalize.
"""

from __future__ import annotations

from app.evaluator import TerminalEvaluator
from app.evaluator.trace import terminal_evaluation_attributes
from app.ports.error_codes import ErrorCode
from app.ports.task_store import TaskStorePort
from app.ports.trace import TracePort


class UserSkillTaskCloser:
    def __init__(
        self,
        task_store: TaskStorePort,
        trace_port: TracePort,
        evaluator: TerminalEvaluator | None = None,
    ) -> None:
        self._tasks = task_store
        self._trace = trace_port
        self._evaluator = evaluator or TerminalEvaluator()

    async def __call__(
        self,
        *,
        task_id: str,
        trace_id: str,
        session_id: str,
        tenant_id: str,
        ai_user_id: str,
        capability_id: str,
        completed: bool,
        error_code: ErrorCode | None,
    ) -> None:
        ids = (trace_id, task_id, session_id)
        await self._tasks.update_status(task_id, "completed" if completed else "failed", error_code)
        await self._trace.record_step(
            *ids,
            tenant_id=tenant_id,
            ai_user_id=ai_user_id,
            event_type="task_completed" if completed else "task_failed",
            status="ok" if completed else "failed",
            capability_id=capability_id,
            error_code=error_code,
        )
        evaluation_status, attributes = terminal_evaluation_attributes(
            self._evaluator,
            business_status="completed" if completed else "failed",
            error_code=error_code,
            capability_id=capability_id,
            raw_execution=None,
            business_verification=None,
        )
        await self._trace.record_step(
            *ids,
            tenant_id=tenant_id,
            ai_user_id=ai_user_id,
            event_type="evaluation_recorded",
            status=evaluation_status,
            capability_id=capability_id,
            error_code=error_code,
            attributes=attributes,
        )
        await self._trace.finalize_task_trace(
            *ids,
            tenant_id=tenant_id,
            ai_user_id=ai_user_id,
            status="ok" if completed else "failed",
            capability_id=capability_id,
            error_code=error_code,
        )


__all__ = ("UserSkillTaskCloser",)
