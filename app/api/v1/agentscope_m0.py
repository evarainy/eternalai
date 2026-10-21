"""Internal M0 AgentScope counting route; registered only when explicitly enabled.

Authentication, revocation and CSRF come from the shared csrf-protected
principal dependency; the turn runner is injected by composition, so this
module never imports infra. The response is symbolic only.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.auth import PrincipalDependency
from app.api.v1.runtime import _bind_session, _PrivateHandleRoute
from app.ports.auth import AuthenticatedSessionContext, Principal, authenticated_session

M0TurnRunner = Callable[
    [AuthenticatedSessionContext, str, str, Sequence[tuple[str, dict[str, Any]]]],
    Awaitable[dict[str, Any]],
]
_PRIVATE_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache"}


class ExpectedToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str = Field(min_length=1, max_length=64)
    arguments: dict[str, Any] = Field(default_factory=dict)


class M0TurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=2000)
    expected_calls: list[ExpectedToolCall] = Field(default_factory=list, max_length=4)


def make_router(
    turn_runner: M0TurnRunner,
    require_principal: PrincipalDependency,
    session_binder: Callable[[Principal, str], str] | None,
) -> APIRouter:
    # Validation failures answer with a code only, never echoing the message.
    router = APIRouter(route_class=_PrivateHandleRoute)

    @router.post("/turn", include_in_schema=False)
    async def turn(
        body: M0TurnRequest,
        principal: Principal = Depends(require_principal),
    ) -> JSONResponse:
        session = authenticated_session.get()
        if session is None or session.principal != principal:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "authentication_required"},
                headers=_PRIVATE_HEADERS,
            )
        session_id = _bind_session(
            principal=principal,
            requested_session_id=body.session_id,
            session_binder=session_binder,
        )
        result = await turn_runner(
            session,
            session_id,
            body.message,
            [(call.tool, call.arguments) for call in body.expected_calls],
        )
        return JSONResponse(result, headers=_PRIVATE_HEADERS)

    return router


__all__ = ("M0TurnRequest", "M0TurnRunner", "make_router")
