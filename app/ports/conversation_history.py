"""Owner-scoped access to a conversation's visible history."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from app.ports.response_envelope import ResponseEnvelope, ResponseEnvelopeStatus
from app.ports.user_skills import UserSkillOwner

MessageRole = Literal["user", "assistant"]
HistoryCursor = tuple[datetime, UUID]


# The web client's local session id (``crypto.randomUUID`` shape, versions 1-8, RFC variant);
# only such an id can resume a saved conversation. Same rule as web/src/features/browser/api.ts.
_CLIENT_SESSION_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", re.IGNORECASE
)


def continuable_client_session_id(value: str | None) -> str | None:
    """``value`` unchanged when the web client can resume under it, else None (view-only)."""
    return value if value is not None and _CLIENT_SESSION_ID.fullmatch(value) else None


class ConversationHistoryError(Exception):
    """Storage operation failed."""


@dataclass(frozen=True, slots=True)
class ConversationSummary:
    session_id: str
    client_session_id: str | None
    title: str
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ConversationListItem:
    id: UUID
    summary: ConversationSummary


@dataclass(frozen=True, slots=True)
class HistoryCard:
    component_type: str
    title: str | None = None


@dataclass(frozen=True, slots=True)
class HistoryEnvelope:
    status: ResponseEnvelopeStatus
    message: str
    ui: HistoryCard
    data: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class ConversationMessage:
    role: MessageRole
    text: str
    envelope: HistoryEnvelope | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ConversationMessages:
    session_id: str
    title: str
    messages: list[ConversationMessage]


class ConversationHistoryPort(Protocol):
    async def is_deleted(self, owner: UserSkillOwner, session_id: str) -> bool: ...

    async def append_turn(
        self,
        owner: UserSkillOwner,
        session_id: str,
        request_id: str | None,
        user_text: str,
        envelope: ResponseEnvelope,
        *,
        client_session_id: str | None,
    ) -> None: ...

    async def recent_turns(
        self, owner: UserSkillOwner, session_id: str, limit: int
    ) -> list[tuple[MessageRole, str]]: ...

    async def list(
        self, owner: UserSkillOwner, before: HistoryCursor | None, limit: int
    ) -> list[ConversationListItem]: ...

    async def messages(
        self, owner: UserSkillOwner, session_id: str
    ) -> ConversationMessages | None: ...

    async def rename(
        self, owner: UserSkillOwner, session_id: str, title: str
    ) -> ConversationSummary | None: ...

    async def delete(self, owner: UserSkillOwner, session_id: str) -> bool: ...
