"""Same-origin current-user API and file streams, with host auth and CSRF."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from contextlib import AsyncExitStack
from typing import Any

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.routing import APIRoute
from starlette.requests import ClientDisconnect
from starlette.routing import Match
from starlette.types import Receive, Scope, Send

from app.api.v1.csrf import PrincipalDependency
from app.im_client import IMClientService
from app.ports.auth import Principal
from app.ports.im_client import IMClientError, IMClientRequest, IMClientResponse

_LOG = logging.getLogger("uvicorn.error.im_client")
_NO_STORE = {"Cache-Control": "private, no-store"}
_METHODS = ["GET", "POST", "PATCH", "DELETE", "PUT", "HEAD", "OPTIONS"]


def _error_response(error: IMClientError) -> JSONResponse:
    headers = {**error.headers, **_NO_STORE}
    if error.http_status == 401:
        headers["WWW-Authenticate"] = "Session"
    return JSONResponse(
        {"detail": {"code": error.code, "message": error.message}},
        status_code=error.http_status, headers=headers,
    )


def _denied() -> IMClientError:
    return IMClientError("im_endpoint_not_allowed", 404, "IM endpoint is not allowed.")


class IMClientRoute(APIRoute):
    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        match, child_scope = super().matches(scope)
        return (Match.FULL if match == Match.PARTIAL else match), child_scope

    async def handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Keep the original method and FastAPI dependency solving for all methods.
        await self.app(scope, receive, send)

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                response = await original(request)
            except IMClientError as error:
                response = _error_response(error)
            except HTTPException as error:
                response = JSONResponse(
                    {"detail": error.detail}, status_code=error.status_code,
                    headers=error.headers,
                )
            response.headers.update(_NO_STORE)
            return response

        return handler


class IMClientStreamingResponse(StreamingResponse):
    def __init__(
        self, response: IMClientResponse, stack: AsyncExitStack, *,
        revalidate: Callable[[], Awaitable[None]],
    ) -> None:
        super().__init__(response.body, status_code=response.status, headers=response.headers)
        self.headers.update(_NO_STORE)
        self._stack, self._revalidate = stack, revalidate
        self._sent = 0
        self._result = "cancelled"

    async def stream_response(self, send: Send) -> None:
        # This runs before http.response.start, not in an iterator's first yield.
        try:
            await self._revalidate()
        except IMClientError as error:
            self._result = "denied"
            response = _error_response(error)
            await send({"type": "http.response.start", "status": response.status_code,
                        "headers": response.raw_headers})
            await send({"type": "http.response.body", "body": response.body})
            return
        await send({"type": "http.response.start", "status": self.status_code,
                    "headers": self.raw_headers})
        try:
            async for chunk in self.body_iterator:
                await self._revalidate()
                if isinstance(chunk, str):
                    chunk = chunk.encode(self.charset)
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
                self._sent += len(chunk)
        except IMClientError as error:
            self._result = "denied" if error.http_status in (401, 409, 503) else "upstream_failed"
            # No final body: the ASGI server closes an incomplete response. Headers
            # already sent cannot be replaced, and JSON must not enter a file stream.
            return
        await send({"type": "http.response.body", "body": b"", "more_body": False})
        self._result = "complete"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        except ClientDisconnect:
            self._result = "cancelled"
        finally:
            with anyio.CancelScope(shield=True):
                await self._stack.aclose()
            _LOG.info(
                "im_client stage=stream result=%s bytes_sent=%s range_result=%s",
                self._result, self._sent, self.status_code,
            )


def make_router(
    service: IMClientService | None, require_principal: PrincipalDependency,
) -> APIRouter:
    router = APIRouter(route_class=IMClientRoute)

    def available() -> IMClientService:
        if service is None:
            raise IMClientError("im_disabled", 503, "IM is not configured.")
        return service

    @router.api_route("/identity", methods=_METHODS, response_model=None)
    async def identity(
        request: Request, principal: Principal = Depends(require_principal),
    ) -> Response:
        if (request.method != "GET" or request.scope.get("raw_path")
                != b"/api/v1/im-client/identity"):
            raise _denied()
        return JSONResponse(await available().identity(principal))

    async def exchange(
        request: Request, principal: Principal, kind: str, body_done: anyio.Event,
    ) -> Response:
        current = available()
        lease = await current.acquire(principal)
        prefix = b"/api/v1/im-client/" + (b"zulip" if kind == "api" else b"files")
        raw_path = request.scope.get("raw_path", b"")
        if not raw_path.startswith(prefix + b"/"):
            raise _denied()
        try:
            path = raw_path[len(prefix):].decode("ascii")
        except UnicodeDecodeError:
            raise _denied() from None
        headers = {name: request.headers[name]
                   for name in ("content-type", "range", "accept") if name in request.headers}
        lengths = request.headers.getlist("content-length")
        if len(lengths) == 1 and lengths[0].isascii() and lengths[0].isdecimal():
            headers["content-length"] = lengths[0]
        async def request_body() -> AsyncIterator[bytes]:
            async for chunk in request.stream():
                yield chunk
            body_done.set()

        upstream = IMClientRequest(
            kind="api" if kind == "api" else "file", method=request.method, path=path,
            query=request.scope["query_string"], headers=headers,
            body=request_body(), session_scope=lease.scope,
        )
        stack = AsyncExitStack()
        try:
            response = await stack.enter_async_context(current.request(lease, upstream))
            if kind == "file":
                async def revalidate() -> None:
                    await current.revalidate(lease)

                return IMClientStreamingResponse(response, stack, revalidate=revalidate)
            body = b"".join([chunk async for chunk in response.body])
            await current.revalidate(lease)
            return Response(body, status_code=response.status, headers=response.headers)
        except BaseException:
            with anyio.CancelScope(shield=True):
                await stack.aclose()
            raise
        finally:
            if kind != "file":
                with anyio.CancelScope(shield=True):
                    await stack.aclose()

    async def proxy(request: Request, principal: Principal, kind: str) -> Response:
        # API/upload bodies have one reader; the listener waits for it to finish.
        # Files forward no body, so listen while identity and upstream headers wait.
        body_done = anyio.Event()
        if kind == "file":
            body_done.set()
        response: Response | None = None
        async with anyio.create_task_group() as tasks:
            async def disconnect() -> None:
                await body_done.wait()
                while True:
                    if (await request.receive())["type"] == "http.disconnect":
                        tasks.cancel_scope.cancel()
                        return

            tasks.start_soon(disconnect)
            try:
                response = await exchange(request, principal, kind, body_done)
            except IMClientError as error:
                response = _error_response(error)
            finally:
                tasks.cancel_scope.cancel()
        return response if response is not None else Response(status_code=499, headers=_NO_STORE)

    @router.api_route("/zulip/{path:path}", methods=_METHODS, response_model=None)
    async def api(
        request: Request, path: str, principal: Principal = Depends(require_principal),
    ) -> Response:
        return await proxy(request, principal, "api")

    @router.api_route("/files/{path:path}", methods=_METHODS, response_model=None)
    async def files(
        request: Request, path: str, principal: Principal = Depends(require_principal),
    ) -> Response:
        return await proxy(request, principal, "file")

    @router.api_route("", methods=_METHODS, response_model=None)
    @router.api_route("/{path:path}", methods=_METHODS, response_model=None)
    async def unknown(
        request: Request, principal: Principal = Depends(require_principal), path: str = "",
    ) -> Any:
        raise _denied()

    return router
