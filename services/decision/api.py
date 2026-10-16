"""Factory for the frozen POST /select boundary, never the main Runtime API."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from contextlib import suppress

from fastapi import FastAPI, Request, Response
from starlette.requests import ClientDisconnect

from services.decision.service import DecisionService, ServingFailure

HTTP_STATUS = {
    "unauthorized": 401,
    "input_unsupported": 422,
    "model_mismatch": 412,
    "overloaded": 429,
    "timeout": 504,
    "cancelled": 499,
    "invalid_response": 502,
    "unavailable": 503,
    "WAITING_ENV": 503,
}


def create_app(
    service: DecisionService, *, authorize: Callable[[Request], bool] | None = None
) -> FastAPI:
    """Operator supplies current HTTP auth, service input auth and approved policy.

    Missing HTTP authorization denies every request. No credentials are copied
    to model input, results or logs. Caller owns backend startup/close lifecycle.
    """
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)

    @app.post("/select")
    async def select(request: Request) -> Response:
        deadline = time.monotonic() + service.limits.timeout_seconds
        cancellation = asyncio.Event()
        watcher: asyncio.Task[None] | None = None

        async def body() -> bytes:
            data = bytearray()
            async for chunk in request.stream():
                if len(data) + len(chunk) > service.limits.maximum_request_bytes:
                    raise ServingFailure("input_unsupported")
                data.extend(chunk)
            return bytes(data)

        async def disconnect() -> None:
            while True:
                message = await request.receive()
                if message["type"] == "http.disconnect":
                    cancellation.set()
                    return

        try:
            try:
                permitted = authorize is not None and authorize(request) is True
            except Exception:
                permitted = False
            if not permitted:
                raise ServingFailure("unauthorized")
            if (
                request.headers.get("content-type", "").split(";", 1)[0].strip()
                != "application/json"
            ):
                raise ServingFailure("input_unsupported")
            raw = await asyncio.wait_for(body(), timeout=max(0.0, deadline - time.monotonic()))
            watcher = asyncio.create_task(disconnect())
            response = await service.select(
                raw, cancellation=cancellation, deadline_monotonic=deadline
            )
            return Response(
                content=response,
                media_type="application/json",
                headers={
                    "X-Decision-Execution-Kind": service.execution_kind,
                },
            )
        except (TimeoutError, ClientDisconnect) as error:
            code = "timeout" if isinstance(error, TimeoutError) else "cancelled"
            return Response(
                content=f'{{"error":"{code}"}}',
                status_code=HTTP_STATUS[code],
                media_type="application/json",
            )
        except ServingFailure as error:
            return Response(
                content=f'{{"error":"{error.code}"}}',
                status_code=HTTP_STATUS[error.code],
                media_type="application/json",
            )
        finally:
            cancellation.set()
            if watcher is not None:
                watcher.cancel()
                with suppress(asyncio.CancelledError):
                    await watcher

    return app
