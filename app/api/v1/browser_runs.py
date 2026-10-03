"""Authenticated no-store browser Run reads and cancellation requests."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.auth import PrincipalDependency
from app.ports.auth import Principal, SessionBindingError
from app.ports.browser_chat import BrowserChatError, BrowserChatPort, BrowserRunResponse

_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache"}
RunIdentifier = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,96}$")]


class _PrivateRunRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                response = await original(request)
                response.headers.update(_HEADERS)
                return response
            except RequestValidationError:
                # Never reflect malformed session IDs or submitted body values.
                raise HTTPException(
                    422, {"code": "browser_request_input_invalid"}, headers=_HEADERS,
                ) from None
            except HTTPException as error:
                error.headers = {**(error.headers or {}), **_HEADERS}
                raise
            except Exception:
                raise HTTPException(
                    503, {"code": "browser_chat_unavailable"}, headers=_HEADERS,
                ) from None

        return handle


class BrowserCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    session_id: str = Field(min_length=1, max_length=192)


def make_router(
    service: BrowserChatPort | None, require_principal: PrincipalDependency,
    session_binder: Callable[[Principal, str], str] | None,
) -> APIRouter:
    """The injected verified Principal dependency must also enforce POST CSRF."""
    router = APIRouter(route_class=_PrivateRunRoute)

    def bind(principal: Principal, requested: str) -> tuple[BrowserChatPort, str]:
        if session_binder is None:
            raise HTTPException(401, {"code": "authentication_required"}, headers=_HEADERS)
        try:
            bound = session_binder(principal, requested)
        except SessionBindingError:
            raise HTTPException(404, {"code": "session_not_found"}, headers=_HEADERS) from None
        if service is None:
            raise HTTPException(503, {"code": "browser_chat_unavailable"}, headers=_HEADERS)
        return service, bound

    @router.get("/browser-runs/{task_id}/{run_id}", response_model=BrowserRunResponse)
    async def get_run(
        task_id: RunIdentifier, run_id: RunIdentifier, response: Response,
        session_id: Annotated[str, Query(min_length=1, max_length=192)],
        principal: Principal = Depends(require_principal),
    ) -> BrowserRunResponse:
        response.headers.update(_HEADERS)
        bound_service, session = bind(principal, session_id)
        try:
            return await bound_service.get(principal, session, task_id, run_id)
        except BrowserChatError as error:
            raise HTTPException(
                error.http_status, {"code": error.code, "message": "Browser Run is unavailable."},
                headers=_HEADERS,
            ) from None

    @router.post(
        "/browser-runs/{task_id}/{run_id}/cancel",
        response_model=BrowserRunResponse, status_code=202,
    )
    async def cancel_run(
        task_id: RunIdentifier, run_id: RunIdentifier, body: BrowserCancelRequest,
        response: Response, principal: Principal = Depends(require_principal),
    ) -> BrowserRunResponse:
        response.headers.update(_HEADERS)
        bound_service, session = bind(principal, body.session_id)
        try:
            return await bound_service.cancel(principal, session, task_id, run_id)
        except BrowserChatError as error:
            raise HTTPException(
                error.http_status,
                {"code": error.code, "message": "Cancellation was not accepted."},
                headers=_HEADERS,
            ) from None

    return router
