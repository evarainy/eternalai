"""PostgreSQL history with atomic pairs and per-conversation write serialization."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from hashlib import sha256
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import RowMapping, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infra.sdui.response_envelope_builder import sanitize_response_data
from app.ports.conversation_history import (
    ConversationHistoryError,
    ConversationListItem,
    ConversationMessage,
    ConversationMessages,
    ConversationSummary,
    HistoryCard,
    HistoryCursor,
    HistoryEnvelope,
    MessageRole,
    continuable_client_session_id,
)
from app.ports.response_envelope import ResponseEnvelope
from app.ports.user_skills import UserSkillOwner

_LOG = logging.getLogger("uvicorn.error.chat_history")
_HORIZONTAL_SPACE = r"[ \t\u00a0\u1680\u2000-\u200a\u202f\u205f\u3000]*"
_CREDENTIAL_ASSIGNMENT = re.compile(
    rf"((?:密码|口令|验证码|(?<![A-Za-z0-9_])PIN){_HORIZONTAL_SPACE}"
    rf"(?>[是为]{_HORIZONTAL_SPACE}(?:[:：]{_HORIZONTAL_SPACE})?|[:：]{_HORIZONTAL_SPACE}))"
    r"""(?:"[^"\r\n]*"|'[^'\r\n]*'|“[^”\r\n]*”|‘[^’\r\n]*’|[^\s，。；、！？]+)""",
    re.IGNORECASE,
)
_OWNER = "c.tenant_id = :tenant_id AND c.ai_user_id = :ai_user_id"
_SESSION = _OWNER + " AND c.session_id = :session_id"
_LIVE = "c.deleted_at IS NULL"
_LOCK_CONVERSATION = text(
    "SELECT id, deleted_at FROM chat_conversations c WHERE " + _SESSION + " FOR UPDATE"
)


def _session_params(owner: UserSkillOwner, session_id: str) -> dict[str, str]:
    return {**owner.model_dump(), "session_id": session_id}


def _summary(row: RowMapping) -> ConversationSummary:
    return ConversationSummary(
        session_id=row["session_id"],
        client_session_id=continuable_client_session_id(row["client_session_id"]),
        title=row["title"],
        updated_at=row["updated_at"],
    )


def _sanitize_history_text(value: str) -> str:
    return cast(str, sanitize_response_data(_CREDENTIAL_ASSIGNMENT.sub(r"\g<1>***", value)))


def _display_data(data: dict[str, Any] | None) -> dict[str, Any] | None:
    if data is not None:
        # The collection reference carries task_id and version for resuming the form.
        data.pop("d_form_collection", None)
    return data


def _display_envelope(envelope: ResponseEnvelope) -> dict[str, Any]:
    fields: dict[str, Any] = sanitize_response_data(
        envelope.model_dump(
            mode="json",
            include={
                "status": True,
                "message": True,
                "ui": {"component_type", "title"},
                "data": True,
            },
        )
    )
    fields["data"] = _display_data(fields["data"])
    # A reply, a query input or a result cell may repeat a credential written in Chinese.
    return cast(dict[str, Any], _mask_assignments(fields))


def _mask_assignments(value: Any) -> Any:
    if isinstance(value, str):
        return _CREDENTIAL_ASSIGNMENT.sub(r"\g<1>***", value)
    if isinstance(value, dict):
        return {key: _mask_assignments(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_mask_assignments(item) for item in value]
    return value


def _message(row: RowMapping) -> ConversationMessage:
    payload = row["envelope"]
    envelope = (
        None
        if payload is None
        else HistoryEnvelope(
            status=payload["status"],
            message=payload["message"],
            ui=HistoryCard(**payload["ui"]),
            data=_display_data(payload["data"]),
        )
    )
    return ConversationMessage(row["role"], row["text"], envelope, row["created_at"])


class PostgreSQLConversationHistory:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[AsyncSession]:
        try:
            async with self._sessions.begin() as session:
                yield session
        except (OSError, SQLAlchemyError) as error:
            raise ConversationHistoryError(type(error).__name__) from None

    async def is_deleted(self, owner: UserSkillOwner, session_id: str) -> bool:
        async with self._transaction() as session:
            result = await session.execute(
                text("SELECT deleted_at IS NOT NULL FROM chat_conversations c WHERE " + _SESSION),
                _session_params(owner, session_id),
            )
            return result.scalar_one_or_none() is True

    async def append_turn(
        self,
        owner: UserSkillOwner,
        session_id: str,
        request_id: str | None,
        user_text: str,
        envelope: ResponseEnvelope,
        *,
        client_session_id: str | None,
    ) -> None:
        async with self._transaction() as session:
            result = await self._append(
                session,
                owner,
                session_id,
                request_id,
                user_text,
                envelope,
                client_session_id=client_session_id,
            )
        _LOG.info("chat_history.append result=%s", result)

    async def _append(
        self,
        session: AsyncSession,
        owner: UserSkillOwner,
        session_id: str,
        request_id: str | None,
        user_text: str,
        envelope: ResponseEnvelope,
        *,
        client_session_id: str | None,
    ) -> str:
        params = _session_params(owner, session_id)
        clean_text = _sanitize_history_text(user_text)
        await session.execute(
            text(
                "INSERT INTO chat_conversations (id, tenant_id, ai_user_id, session_id, "
                "title) VALUES (:id, :tenant_id, :ai_user_id, :session_id, :title) "
                "ON CONFLICT (tenant_id, ai_user_id, session_id) DO NOTHING"
            ),
            {**params, "id": uuid4(), "title": clean_text.strip()[:30]},
        )
        locked = await session.execute(_LOCK_CONVERSATION, params)
        head = locked.mappings().one()
        if head["deleted_at"] is not None:
            return "skip_deleted"
        conversation_id = head["id"]
        request_digest = sha256(request_id.encode()).hexdigest() if request_id else None
        turn_params = {"conversation_id": conversation_id, "request_id": request_digest}
        if request_digest is not None:
            duplicate = await session.execute(
                text(
                    "SELECT count(*) = 2 FROM chat_messages "
                    "WHERE conversation_id = :conversation_id AND request_id = :request_id"
                ),
                turn_params,
            )
            if duplicate.scalar_one():
                return "duplicate"
        position = await session.execute(
            text(
                "SELECT COALESCE(max(seq), 0) + 1 FROM chat_messages "
                "WHERE conversation_id = :conversation_id"
            ),
            turn_params,
        )
        seq = position.scalar_one()
        display = _display_envelope(envelope)
        await session.execute(
            text(
                "INSERT INTO chat_messages (conversation_id, seq, role, text, envelope, "
                "request_id) VALUES "
                "(:conversation_id, :seq, 'user', :user_text, NULL, :request_id), "
                "(:conversation_id, :next_seq, 'assistant', :answer, CAST(:envelope AS JSONB), "
                ":request_id)"
            ),
            {
                **turn_params,
                "seq": seq,
                "next_seq": seq + 1,
                "user_text": clean_text,
                "answer": display["message"],
                "envelope": json.dumps(display, ensure_ascii=False),
            },
        )
        await session.execute(
            text(
                "UPDATE chat_conversations SET updated_at = clock_timestamp(), "
                "client_session_id = COALESCE(client_session_id, :client_session_id) "
                "WHERE id = :id"
            ),
            {"id": conversation_id, "client_session_id": client_session_id},
        )
        return "ok"

    async def recent_turns(
        self, owner: UserSkillOwner, session_id: str, limit: int
    ) -> list[tuple[MessageRole, str]]:
        # Every committed append adds exactly one adjacent pair under the row lock.
        async with self._transaction() as session:
            rows = await session.execute(
                text(
                    "SELECT role, text FROM chat_messages m "
                    "JOIN chat_conversations c ON c.id = m.conversation_id "
                    "WHERE " + _SESSION + " AND " + _LIVE + " ORDER BY m.seq DESC LIMIT :limit"
                ),
                {**_session_params(owner, session_id), "limit": limit * 2},
            )
            return [(row.role, row.text) for row in reversed(rows.all())]

    async def list(
        self, owner: UserSkillOwner, before: HistoryCursor | None, limit: int
    ) -> list[ConversationListItem]:
        params = {**owner.model_dump(), "limit": limit}
        where_cursor = ""
        if before is not None:
            params.update(before_at=before[0], before_id=before[1])
            where_cursor = " AND (c.updated_at, c.id) < (:before_at, :before_id)"
        async with self._transaction() as session:
            rows = await session.execute(
                text(
                    "SELECT c.id, c.session_id, c.client_session_id, c.title, c.updated_at "
                    "FROM chat_conversations c WHERE "
                    + _OWNER
                    + " AND "
                    + _LIVE
                    + where_cursor
                    + " ORDER BY c.updated_at DESC, c.id DESC LIMIT :limit"
                ),
                params,
            )
            items = [ConversationListItem(row["id"], _summary(row)) for row in rows.mappings()]
        return items

    async def messages(self, owner: UserSkillOwner, session_id: str) -> ConversationMessages | None:
        async with self._transaction() as session:
            result = await session.execute(
                text(
                    "SELECT c.title, m.role, m.text, m.envelope, m.created_at "
                    "FROM chat_conversations c JOIN chat_messages m "
                    "ON m.conversation_id = c.id WHERE "
                    + _SESSION
                    + " AND "
                    + _LIVE
                    + " ORDER BY m.seq"
                ),
                _session_params(owner, session_id),
            )
            rows = result.mappings().all()
            if not rows:
                return None
            return ConversationMessages(
                session_id, rows[0]["title"], [_message(row) for row in rows]
            )

    async def rename(
        self, owner: UserSkillOwner, session_id: str, title: str
    ) -> ConversationSummary | None:
        async with self._transaction() as session:
            result = await session.execute(
                text(
                    "UPDATE chat_conversations c SET title = :title, "
                    "updated_at = clock_timestamp() WHERE "
                    + _SESSION
                    + " AND "
                    + _LIVE
                    + " RETURNING session_id, client_session_id, title, updated_at"
                ),
                {**_session_params(owner, session_id), "title": _sanitize_history_text(title)},
            )
            row = result.mappings().one_or_none()
            return None if row is None else _summary(row)

    async def delete(self, owner: UserSkillOwner, session_id: str) -> bool:
        async with self._transaction() as session:
            locked = await session.execute(_LOCK_CONVERSATION, _session_params(owner, session_id))
            head = locked.mappings().one_or_none()
            conversation_id = (
                head["id"] if head is not None and head["deleted_at"] is None else None
            )
            if conversation_id is not None:
                await session.execute(
                    text(
                        "UPDATE chat_conversations SET deleted_at = clock_timestamp() "
                        "WHERE id = :id"
                    ),
                    {"id": conversation_id},
                )
        deleted = conversation_id is not None
        _LOG.info("chat_history.delete result=%s", "ok" if deleted else "not_found")
        return deleted
