"""Internal M0 AgentScope counting route; registered only when explicitly enabled.

Authentication, revocation and CSRF come from the shared csrf-protected
principal dependency; the turn runner is injected by composition, so this
module never imports infra. The response is symbolic only. A client that
disconnects cancels its turn instead of leaving it running to the turn budget.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.auth import PrincipalDependency
from app.api.v1.runtime import _bind_session, _PrivateHandleRoute
from app.ports.auth import AuthenticatedSessionContext, Principal, authenticated_session

# (session, bound session id, sample id, message, expected calls)
M0TurnRunner = Callable[
    [AuthenticatedSessionContext, str, str, str, Sequence[tuple[str, dict[str, Any]]]],
    Awaitable[dict[str, Any]],
]
_PRIVATE_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache"}
_DISCONNECT_POLL_SECONDS = 0.5
_logger = logging.getLogger(__name__)


class ExpectedToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str = Field(min_length=1, max_length=64)
    arguments: dict[str, Any] = Field(default_factory=dict)


class M0TurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1, max_length=128)
    sample_id: str = Field(pattern=r"^(S(0[1-9]|1[0-2])|S10R)$")
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
        request: Request,
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
        running = asyncio.ensure_future(
            turn_runner(
                session,
                session_id,
                body.sample_id,
                body.message,
                [(call.tool, call.arguments) for call in body.expected_calls],
            )
        )
        watcher = asyncio.ensure_future(_wait_for_disconnect(request))
        try:
            await asyncio.wait({running, watcher}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            # Disconnect or cancellation of this handler both cancel the turn;
            # the cancellation propagates through the bridge's task closes.
            watcher.cancel()
            # Collect the watcher so no task outcome is left unretrieved.
            await asyncio.gather(watcher, return_exceptions=True)
            if not running.done():
                running.cancel()
                with suppress(asyncio.CancelledError):
                    await running
        if running.cancelled():
            _logger.info("agentscope_m0_turn result=client_disconnected")
            return JSONResponse(
                {"outcome": "client_disconnected"}, status_code=499, headers=_PRIVATE_HEADERS
            )
        return JSONResponse(running.result(), headers=_PRIVATE_HEADERS)

    return router


async def _wait_for_disconnect(request: Request) -> None:
    try:
        while not await request.is_disconnected():
            await asyncio.sleep(_DISCONNECT_POLL_SECONDS)
    except Exception as error:
        # A failed check must not cancel the turn: stop watching, log the
        # class name only, and let the turn finish within its own budget.
        _logger.info(
            "agentscope_m0_turn disconnect_check=failed error_class=%s", type(error).__name__
        )
        await asyncio.Event().wait()


__all__ = ("M0TurnRequest", "M0TurnRunner", "make_router")
