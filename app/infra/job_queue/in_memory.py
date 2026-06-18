"""InMemoryJobQueue — deterministic Phase 0 baseline adapter."""

from __future__ import annotations

import inspect
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from app.ports.job_queue import JobStatus


@dataclass
class _JobRecord:
    status: JobStatus = "queued"
    result: Any | None = None


class InMemoryJobQueue:
    """Deterministic in-memory job queue.

    Handlers execute inline inside ``enqueue()`` — no background tasks,
    workers, polling, or scheduler behavior.
    """

    def __init__(
        self,
        handlers: dict[str, Callable[[dict[str, Any]], Any]],
    ) -> None:
        self._handlers = handlers
        self._jobs: dict[str, _JobRecord] = {}

    async def enqueue(
        self,
        task_type: str,
        payload: dict[str, Any],
        task_id: str | None = None,
    ) -> str:
        job_id = task_id if task_id is not None else uuid.uuid4().hex

        if job_id in self._jobs:
            raise ValueError(f"Duplicate job id: {job_id}")

        record = _JobRecord()
        self._jobs[job_id] = record

        handler = self._handlers.get(task_type)
        if handler is None:
            record.status = "failed"
            return job_id

        record.status = "in_progress"
        try:
            if inspect.iscoroutinefunction(handler):
                record.result = await handler(payload)
            else:
                record.result = handler(payload)
            record.status = "complete"
        except Exception:
            record.status = "failed"

        return job_id

    async def get_status(self, job_id: str) -> JobStatus:
        record = self._jobs.get(job_id)
        if record is None:
            return "not_found"
        return record.status

    async def get_result(self, job_id: str) -> Any | None:
        record = self._jobs.get(job_id)
        if record is None:
            return None
        return record.result
