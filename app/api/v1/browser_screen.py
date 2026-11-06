"""Owner-only WebSocket for a Run's remote OA window: frames out, allowlisted input in.

Handshake: one exact configured Origin (replaces the CSRF header) plus the session
cookie; nothing travels in the query string, which uvicorn writes to its access
log. The bound chat session arrives in the first message. Before a viewer is
open no frame is sent and no input is read.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import struct
from collections.abc import Awaitable, Callable, Collection
from contextlib import suppress
from typing import Any, Literal, cast

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.requests import HTTPConnection

from app.ports.auth import AuthenticatedSessionContext, Principal, SessionBindingError
from app.ports.browser_screen import (
    SCREEN_KEYS,
    SCREEN_PROMPT_MAX,
    SCREEN_TEXT_MAX,
    BrowserScreenError,
    BrowserScreenPort,
    BrowserScreenSession,
    ScreenFrame,
    ScreenKey,
    ScreenMouse,
    ScreenText,
)

_logger = logging.getLogger("uvicorn.error.remote_screen")
_ID = re.compile(r"[A-Za-z0-9_-]{1,96}")
_DIALOG_ID = re.compile(r"[0-9a-f]{32}")
_INIT_SECONDS = 5.0
_RECHECK_SECONDS = 15.0
_MESSAGE_LIMIT = 16384
_CLOSE_DENIED = 4403
_MOUSE_ACTIONS = frozenset({"move", "down", "up", "wheel"})
MouseAction = Literal["move", "down", "up", "wheel"]

SessionDependency = Callable[[HTTPConnection], Awaitable[AuthenticatedSessionContext]]


def _log(code: str) -> None:
    """Fixed handshake refusal reason; never headers, cookies, IDs or message bodies."""
    _logger.info("remote_screen stage=handshake status=failed code=%s viewers=0", code)


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


async def _dispatch(session: BrowserScreenSession, data: dict[str, Any]) -> None:
    """Malformed or unknown messages are dropped; only typed inputs reach the session."""
    kind = data.get("type")
    if kind == "take":
        session.request_control()
    elif kind == "release":
        await session.release_control()
    elif kind == "mouse":
        action = data.get("action")
        x, y = _number(data.get("x")), _number(data.get("y"))
        if action not in _MOUSE_ACTIONS or x is None or y is None:
            return
        dx = _number(data.get("dx", 0)) or 0.0
        dy = _number(data.get("dy", 0)) or 0.0
        await session.send(ScreenMouse(cast(MouseAction, action), x, y, dx, dy))
    elif kind == "key":
        key = data.get("key")
        if type(key) is str and key in SCREEN_KEYS:
            await session.send(ScreenKey(key))
    elif kind == "text":
        text = data.get("text")
        if type(text) is str and 0 < len(text) <= SCREEN_TEXT_MAX and "\x00" not in text:
            await session.send(ScreenText(text))
    elif kind == "dialog":
        dialog_id, accept, prompt = data.get("id"), data.get("accept"), data.get("text")
        if (
            type(dialog_id) is str
            and _DIALOG_ID.fullmatch(dialog_id)
            and type(accept) is bool
            and (prompt is None or type(prompt) is str and len(prompt) <= SCREEN_PROMPT_MAX)
        ):
            await session.answer_dialog(dialog_id, accept, prompt)


def make_router(
    screen: BrowserScreenPort | None,
    require_session: SessionDependency,
    session_binder: Callable[[Principal, str], str] | None,
    allowed_origins: Collection[str],
) -> APIRouter:
    router = APIRouter()
    origins = frozenset(allowed_origins)

    @router.websocket("/browser-runs/{task_id}/{run_id}/screen")
    async def browser_screen(websocket: WebSocket, task_id: str, run_id: str) -> None:
        offered = websocket.headers.getlist("origin")
        if (
            screen is None
            or session_binder is None
            or not _ID.fullmatch(task_id)
            or not _ID.fullmatch(run_id)
            or len(offered) != 1
            or offered[0] not in origins
        ):
            _log("origin" if screen is not None else "unavailable")
            await websocket.close()  # Before accept: the client sees HTTP 403.
            return
        try:
            context = await require_session(websocket)
        except HTTPException:
            _log("session")
            await websocket.close()
            return
        await websocket.accept()
        session: BrowserScreenSession | None = None
        try:
            raw = await asyncio.wait_for(websocket.receive_text(), _INIT_SECONDS)
            data = json.loads(raw) if len(raw) <= 1024 else None
            requested = data.get("session_id") if type(data) is dict else None
            if data is None or data.get("type") != "init" or type(requested) is not str:
                raise ValueError
            if not 0 < len(requested) <= 192:
                raise ValueError
            bound = session_binder(context.principal, requested)
            session = await screen.open(context.principal, bound, task_id, run_id)
        except (WebSocketDisconnect, asyncio.CancelledError):
            raise
        except (TimeoutError, ValueError, KeyError, SessionBindingError, BrowserScreenError):
            _log("init")
            with suppress(Exception):
                await websocket.close(_CLOSE_DENIED)
            return
        except Exception:
            _log("error")
            with suppress(Exception):
                await websocket.close(_CLOSE_DENIED)
            return

        async def outgoing() -> None:
            assert session is not None
            while True:
                event = await session.next_event()
                if isinstance(event, ScreenFrame):
                    # Each frame carries its own CSS viewport; clients map clicks with it.
                    header = struct.pack(">II", event.width, event.height)
                    await websocket.send_bytes(header + event.data)
                else:
                    await websocket.send_text(json.dumps(dict(event.payload), ensure_ascii=False))

        async def incoming() -> None:
            assert session is not None
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return
                text = message.get("text")
                if type(text) is not str or len(text) > _MESSAGE_LIMIT:
                    continue
                try:
                    data = json.loads(text)
                except ValueError:
                    continue
                if type(data) is dict:
                    await _dispatch(session, data)

        async def recheck() -> None:
            assert session is not None and session_binder is not None
            while True:
                await asyncio.sleep(_RECHECK_SECONDS)
                current = await require_session(websocket)  # Revoked or expired: raises.
                if current.principal != context.principal:
                    raise BrowserScreenError("denied")
                session_binder(current.principal, requested)
                await session.recheck()

        tasks = [asyncio.create_task(work()) for work in (outgoing, incoming, recheck)]
        code = 1000
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                error = task.exception()
                if isinstance(error, BrowserScreenError) and error.code == "closed":
                    code = 1000
                elif isinstance(error, (HTTPException, SessionBindingError, BrowserScreenError)):
                    code = _CLOSE_DENIED
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await session.close()
            with suppress(Exception):
                await websocket.close(code)

    return router
