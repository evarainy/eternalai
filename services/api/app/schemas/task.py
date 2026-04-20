"""Typed schemas for async task payloads."""

from datetime import datetime
from typing import Any

from services.api.app.schemas.common import APIModel, KnowledgeSourceId, NonEmptyString, SessionId, TaskId, TaskStatus


class TaskError(APIModel):
    code: NonEmptyString
    message: NonEmptyString


class GetTaskResponse(APIModel):
    task_id: TaskId
    task_type: NonEmptyString
    status: TaskStatus
    created_at: datetime
    updated_at: datetime
    session_id: SessionId | None = None
    knowledge_source_id: KnowledgeSourceId | None = None
    result_summary: str | None = None
    result: dict[str, Any] | None = None
    error: TaskError | None = None
