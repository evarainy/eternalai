"""Shared API schema primitives and reusable error payloads."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
SessionId = Annotated[str, StringConstraints(pattern=r"^sess_[A-Za-z0-9-]+$")]
TurnId = Annotated[str, StringConstraints(pattern=r"^turn_[A-Za-z0-9-]+$")]
TaskId = Annotated[str, StringConstraints(pattern=r"^task_[A-Za-z0-9-]+$")]
KnowledgeSourceId = Annotated[str, StringConstraints(pattern=r"^ks_[A-Za-z0-9-]+$")]

SessionChannel = Literal["web", "miniapp", "api"]
SessionStatus = Literal["active", "archived"]
MessageInputType = Literal["text", "audio"]
TurnStatus = Literal["accepted", "processing", "completed", "failed"]
TaskStatus = Literal["queued", "running", "succeeded", "failed", "canceled"]
KnowledgeSourceType = Literal["url", "file", "wiki"]
KnowledgeSourceStatus = Literal["draft", "queued", "ingesting", "ready", "failed", "disabled"]


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Citation(APIModel):
    source_id: NonEmptyString
    snippet: NonEmptyString
    locator: str | None = None


class BadRequestError(APIModel):
    code: Literal["invalid_request"] = "invalid_request"
    message: NonEmptyString
    details: dict[str, Any] | None = None


class BadRequestErrorResponse(APIModel):
    error: BadRequestError


class NotFoundError(APIModel):
    code: Literal["not_found"] = "not_found"
    message: NonEmptyString
    resource: NonEmptyString
    resource_id: NonEmptyString | None = None


class NotFoundErrorResponse(APIModel):
    error: NotFoundError
