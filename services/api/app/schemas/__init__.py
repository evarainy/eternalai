"""Typed public API schemas for the API service."""

from services.api.app.schemas.common import BadRequestErrorResponse, NotFoundErrorResponse
from services.api.app.schemas.knowledge_source import (
    GetKnowledgeSourceResponse,
    RegisterKnowledgeSourceRequest,
    RegisterKnowledgeSourceResponse,
)
from services.api.app.schemas.message import SubmitMessageRequest, SubmitMessageResponse
from services.api.app.schemas.session import ConversationTurn, CreateSessionRequest, CreateSessionResponse, GetSessionResponse
from services.api.app.schemas.task import GetTaskResponse, TaskError

__all__ = [
    "BadRequestErrorResponse",
    "ConversationTurn",
    "CreateSessionRequest",
    "CreateSessionResponse",
    "GetKnowledgeSourceResponse",
    "GetSessionResponse",
    "GetTaskResponse",
    "NotFoundErrorResponse",
    "RegisterKnowledgeSourceRequest",
    "RegisterKnowledgeSourceResponse",
    "SubmitMessageRequest",
    "SubmitMessageResponse",
    "TaskError",
]
