"""Task and session store interface contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Protocol, TypeAlias

from pydantic import BaseModel, field_validator

TaskStatus: TypeAlias = Literal[
    "created",
    "running",
    "waiting_user",
    "completed",
    "failed",
    "no_capability_found",
]

TASK_STORE_QUERY_LIMIT = 100


class TaskRecord(BaseModel):
    task_id: str
    session_id: str
    ai_user_id: str
    tenant_id: str | None
    status: TaskStatus
    trace_id: str | None = None
    capability_id: str | None = None
    error_code: str | None = None

    @field_validator("tenant_id")
    @classmethod
    def _tenant_id_must_be_non_blank_when_known(
        cls,
        value: str | None,
    ) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("tenant_id must not be blank")
        return value


class TaskEventRecord(BaseModel):
    event_id: str
    task_id: str
    event_type: str
    timestamp: datetime
    payload: dict[str, Any]


class SessionRecord(BaseModel):
    session_id: str


class TaskStorePort(Protocol):
    async def create_task(self, record: TaskRecord) -> TaskRecord: ...

    async def get_task(self, task_id: str) -> TaskRecord | None: ...

    async def update_status(
        self,
        task_id: str,
        status: TaskStatus,
        error_code: str | None = None,
    ) -> TaskRecord: ...

    async def append_event(self, task_id: str, event: TaskEventRecord) -> None: ...

    async def list_tasks(
        self,
        *,
        session_id: str | None = None,
        ai_user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> list[TaskRecord]: ...

    async def list_events(self, task_id: str) -> list[TaskEventRecord]: ...


class SessionStorePort(Protocol):
    async def create_session(self, record: SessionRecord) -> SessionRecord: ...

    async def get_session(self, session_id: str) -> SessionRecord | None: ...
