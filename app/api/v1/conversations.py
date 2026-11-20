"""Read and manage the authenticated user's conversation history."""

from __future__ import annotations

import base64
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.api.v1.csrf import PrincipalDependency
from app.api.v1.runtime import _bind_session, _history_unavailable, _session_not_found
from app.ports.auth import Principal
from app.ports.conversation_history import (
    ConversationHistoryError,
    ConversationHistoryPort,
    ConversationMessages,
    ConversationSummary,
    HistoryCursor,
)
from app.ports.user_skills import UserSkillOwner

_LOG = logging.getLogger("uvicorn.error.chat_history")
_NO_STORE = {"Cache-Control": "no-store"}


class ConversationListResponse(BaseModel):
    items: list[ConversationSummary]
    next_cursor: str | None


class RenameConversationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, StringConstraints(strip_whitespace=True)] = Field(
        min_length=1, max_length=60
    )


def _encode_cursor(updated_at: datetime, row_id: UUID) -> str:
    value = f"{updated_at.isoformat()}|{row_id}"
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> HistoryCursor:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        decoded = base64.b64decode(padded, altchars=b"-_", validate=True).decode()
        timestamp, row_id = decoded.split("|")
        updated_at = datetime.fromisoformat(timestamp)
        if updated_at.tzinfo is None:
            raise ValueError
        return updated_at, UUID(row_id)
    except ValueError:
        raise HTTPException(
            422, {"code": "conversation_cursor_invalid"}, headers=_NO_STORE
        ) from None


def _owner(principal: Principal) -> UserSkillOwner:
    return UserSkillOwner(tenant_id=principal.org_ctx.tenant_id, ai_user_id=principal.ai_user_id)


def make_router(
    history: ConversationHistoryPort | None,
    require_principal: PrincipalDependency,
    session_binder: Callable[[Principal, str], str] | None,
) -> APIRouter:
    router = APIRouter(responses={503: {"description": "Conversation history unavailable"}})
    not_found: dict[int | str, dict[str, Any]] = {404: {"description": "Session not found"}}

    def configured() -> ConversationHistoryPort:
        if history is None:
            raise _history_unavailable()
        return history

    def verify(principal: Principal, session_id: str) -> str:
        if not session_id.startswith("sid_v1."):
            raise _session_not_found()
        return _bind_session(
            principal=principal, requested_session_id=session_id, session_binder=session_binder
        )

    async def guarded[T](call: Awaitable[T], *, op: Literal["list", "delete"] | None = None) -> T:
        try:
            return await call
        except ConversationHistoryError as error:
            if op is not None:
                _LOG.warning("chat_history.%s result=error code=%s", op, error)
            raise _history_unavailable() from None

    @router.get("")
    async def list_conversations(
        response: Response,
        principal: Principal = Depends(require_principal),
        cursor: str | None = Query(default=None, min_length=1, max_length=128),
        limit: int = Query(default=20, ge=1, le=50),
    ) -> ConversationListResponse:
        before = None if cursor is None else _decode_cursor(cursor)
        rows = await guarded(configured().list(_owner(principal), before, limit + 1), op="list")
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = _encode_cursor(last.summary.updated_at, last.id)
        items = [row.summary for row in rows[:limit]]
        _LOG.info("chat_history.list result=ok count=%d", len(items))
        response.headers.update(_NO_STORE)
        return ConversationListResponse(items=items, next_cursor=next_cursor)

    @router.get("/{session_id}/messages", response_model=ConversationMessages, responses=not_found)
    async def conversation_messages(
        session_id: str,
        response: Response,
        principal: Principal = Depends(require_principal),
    ) -> ConversationMessages:
        bound = verify(principal, session_id)
        result = await guarded(configured().messages(_owner(principal), bound))
        if result is None:
            raise _session_not_found()
        response.headers.update(_NO_STORE)
        return result

    @router.patch("/{session_id}", response_model=ConversationSummary, responses=not_found)
    async def rename_conversation(
        session_id: str,
        body: RenameConversationRequest,
        response: Response,
        principal: Principal = Depends(require_principal),
    ) -> ConversationSummary:
        bound = verify(principal, session_id)
        result = await guarded(configured().rename(_owner(principal), bound, body.title))
        if result is None:
            raise _session_not_found()
        response.headers.update(_NO_STORE)
        return result

    @router.delete("/{session_id}", status_code=204, response_class=Response, responses=not_found)
    async def delete_conversation(
        session_id: str,
        principal: Principal = Depends(require_principal),
    ) -> Response:
        bound = verify(principal, session_id)
        if not await guarded(configured().delete(_owner(principal), bound), op="delete"):
            raise _session_not_found()
        return Response(status_code=204, headers=_NO_STORE)

    return router
