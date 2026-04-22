from datetime import UTC, datetime
from uuid import uuid4

from fastapi import Request

from services.api.app.errors import ResourceNotFoundError
from services.api.app.repositories.base import SessionTaskRepository
from services.api.app.schemas.message import SubmitMessageRequest, SubmitMessageResponse
from services.api.app.schemas.session import (
    ConversationTurn,
    CreateSessionRequest,
    CreateSessionResponse,
    GetSessionResponse,
)
from services.api.app.schemas.task import GetTaskResponse


def _prefixed_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _now() -> datetime:
    return datetime.now(UTC)


class SessionService:
    def __init__(self, repository: SessionTaskRepository) -> None:
        self._repository = repository

    def create_session(self, payload: CreateSessionRequest) -> CreateSessionResponse:
        now = _now()
        session = GetSessionResponse(
            session_id=_prefixed_id("sess"),
            channel=payload.channel,
            status="active",
            created_at=now,
            updated_at=now,
            title=payload.title,
            metadata=payload.metadata,
            turns=[],
        )
        self._repository.create_session(session)
        return CreateSessionResponse(**session.model_dump(exclude={"turns"}))

    def get_session(self, session_id: str) -> GetSessionResponse:
        session = self._repository.get_session(session_id)
        if session is None:
            raise ResourceNotFoundError(resource="session", resource_id=session_id, message="Session not found")
        return session

    def submit_message(self, session_id: str, payload: SubmitMessageRequest) -> SubmitMessageResponse:
        now = _now()
        task_type = "answer" if payload.input_type == "text" else "transcription"
        task = GetTaskResponse(
            task_id=_prefixed_id("task"),
            task_type=task_type,
            status="queued",
            created_at=now,
            updated_at=now,
            session_id=session_id,
        )
        turn = ConversationTurn(
            turn_id=_prefixed_id("turn"),
            session_id=session_id,
            user_input_type=payload.input_type,
            user_input_text=payload.text or payload.audio_ref or "[audio pending]",
            task_id=task.task_id,
            status="accepted",
            created_at=now,
            transcript_text=None,
        )
        persisted = self._repository.submit_message(
            session_id=session_id,
            turn=turn,
            task=task,
            session_updated_at=now,
        )
        if not persisted:
            raise ResourceNotFoundError(resource="session", resource_id=session_id, message="Session not found")
        return SubmitMessageResponse(task_id=task.task_id, accepted_turn_id=turn.turn_id)

    def get_task(self, task_id: str) -> GetTaskResponse:
        task = self._repository.get_task(task_id)
        if task is None:
            raise ResourceNotFoundError(resource="task", resource_id=task_id, message="Task not found")
        return task


def get_session_service(request: Request) -> SessionService:
    return SessionService(request.app.state.session_task_repository)
