"""JobQueuePort — Phase 0 baseline port interface."""

from __future__ import annotations

from typing import Any, Literal, Protocol

JobStatus = Literal["queued", "in_progress", "complete", "failed", "not_found"]


class JobQueuePort(Protocol):
    async def enqueue(
        self,
        task_type: str,
        payload: dict[str, Any],
        task_id: str | None = None,
    ) -> str: ...

    async def get_status(self, job_id: str) -> JobStatus: ...

    async def get_result(self, job_id: str) -> Any | None: ...
