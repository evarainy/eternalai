"""Typed schemas for knowledge-source registration and lookup."""

from datetime import datetime
from typing import Literal

from services.api.app.schemas.common import (
    APIModel,
    KnowledgeSourceId,
    KnowledgeSourceStatus,
    KnowledgeSourceType,
    NonEmptyString,
    TaskId,
)


class RegisterKnowledgeSourceRequest(APIModel):
    source_type: KnowledgeSourceType
    source_locator: NonEmptyString
    display_name: str | None = None


class RegisterKnowledgeSourceResponse(APIModel):
    knowledge_source_id: KnowledgeSourceId
    task_id: TaskId
    status: Literal["queued"] = "queued"
    source_type: KnowledgeSourceType
    source_locator: NonEmptyString
    created_at: datetime
    display_name: str | None = None


class GetKnowledgeSourceResponse(APIModel):
    knowledge_source_id: KnowledgeSourceId
    source_type: KnowledgeSourceType
    source_locator: NonEmptyString
    status: KnowledgeSourceStatus
    created_at: datetime
    display_name: str | None = None
    last_ingested_at: datetime | None = None
    failure_reason: str | None = None
