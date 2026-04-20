"""Typed schemas for session resources and turn projections."""

from datetime import datetime
from typing import Any

from pydantic import Field

from services.api.app.schemas.common import (
    APIModel,
    Citation,
    MessageInputType,
    NonEmptyString,
    SessionChannel,
    SessionId,
    SessionStatus,
    TaskId,
    TurnId,
    TurnStatus,
)


class ConversationTurn(APIModel):
    turn_id: TurnId
    session_id: SessionId
    user_input_type: MessageInputType
    user_input_text: NonEmptyString
    task_id: TaskId
    status: TurnStatus
    created_at: datetime
    assistant_output_text: str | None = None
    citations: list[Citation] | None = None
    transcript_text: str | None = None
    failure_reason: str | None = None


class CreateSessionRequest(APIModel):
    channel: SessionChannel
    title: str | None = None
    metadata: dict[str, Any] | None = None


class CreateSessionResponse(APIModel):
    session_id: SessionId
    channel: SessionChannel
    status: SessionStatus = "active"
    created_at: datetime
    updated_at: datetime
    title: str | None = None
    metadata: dict[str, Any] | None = None


class GetSessionResponse(CreateSessionResponse):
    turns: list[ConversationTurn] = Field(default_factory=list)
