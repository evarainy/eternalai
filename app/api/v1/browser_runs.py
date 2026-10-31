"""Authenticated no-store browser submission, reads and cancellation requests."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Coroutine
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from app.api.v1.auth import PrincipalDependency
from app.browser_skill.models import OpaqueId
from app.ports.auth import Principal, SessionBindingError
from app.ports.browser_chat import (
    BrowserChatError,
    BrowserRunAccessPort,
    BrowserRunResponse,
    BrowserSubmissionPort,
)
from app.ports.response_envelope import ResponseEnvelope

_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache"}
_MAX_ARGUMENT_BYTES = 16_384
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
            except Exception as error:
                try:
                    exception_type = type(error).__name__
                    logging.getLogger(__name__).warning(
                        "browser_fallback_diagnostic stage=private_run_route exception_type=%s",
                        exception_type if exception_type.isascii() and exception_type.isidentifier()
                        and len(exception_type) <= 64 else "UnknownException",
                    )
                except Exception:
                    pass  # Diagnostic failure must preserve the original failure path.
                raise HTTPException(
                    503, {"code": "browser_chat_unavailable"}, headers=_HEADERS,
                ) from None

        return handle


class BrowserCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    session_id: str = Field(min_length=1, max_length=192)


class BrowserDecisionRequest(BrowserCancelRequest):
    decision: Literal["confirmed", "rejected"]


class BrowserErrorDetail(BaseModel):
    code: str
    message: str | None = None


class BrowserError(BaseModel):
    """Error body shape: ``{"detail": {"code": ...}}`` (sanitised, never reflects input)."""

    detail: BrowserErrorDetail


_VERIFY_RESULT_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": BrowserError, "description": "authentication_required"},
    403: {"model": BrowserError, "description": "csrf_validation_failed"},
    404: {"model": BrowserError, "description": "session_not_found | browser_run_not_found"},
    409: {
        "model": BrowserError,
        "description": "browser_handoff_not_waiting | browser_handoff_expired",
    },
    422: {"model": BrowserError, "description": "browser_request_input_invalid"},
    503: {"model": BrowserError, "description": "browser_chat_unavailable"},
}


class BrowserSubmitRequest(BrowserCancelRequest):
    skill_id: OpaqueId
    skill_version: OpaqueId
    arguments: dict[str, JsonValue] = Field(repr=False)
    client_request_id: OpaqueId

    @field_validator("arguments")
    @classmethod
    def bounded_arguments(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        # Match the unified submission service's canonical JSON byte budget.
        try:
            encoded = json.dumps(
                value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"),
            ).encode("ascii")
        except (TypeError, ValueError, RecursionError):
            raise ValueError("browser_request_input_invalid") from None
        if len(encoded) > _MAX_ARGUMENT_BYTES:
            raise ValueError("browser_request_input_invalid")
        return value


def make_router(
    service: BrowserRunAccessPort | None, require_principal: PrincipalDependency,
    session_binder: Callable[[Principal, str], str] | None,
    *, submission: BrowserSubmissionPort | None = None,
) -> APIRouter:
    """The injected verified Principal dependency must also enforce POST CSRF."""
    router = APIRouter(route_class=_PrivateRunRoute)

    def bind_session(principal: Principal, requested: str) -> str:
        if session_binder is None:
            raise HTTPException(401, {"code": "authentication_required"}, headers=_HEADERS)
        try:
            return session_binder(principal, requested)
        except SessionBindingError:
            raise HTTPException(404, {"code": "session_not_found"}, headers=_HEADERS) from None

    def bind(principal: Principal, requested: str) -> tuple[BrowserRunAccessPort, str]:
        bound = bind_session(principal, requested)
        if service is None:
            raise HTTPException(503, {"code": "browser_chat_unavailable"}, headers=_HEADERS)
        return service, bound

    @router.post("/browser-runs", response_model=ResponseEnvelope)
    async def submit_run(
        body: BrowserSubmitRequest, response: Response,
        principal: Principal = Depends(require_principal),
    ) -> ResponseEnvelope:
        response.headers.update(_HEADERS)
        session = bind_session(principal, body.session_id)
        if submission is None:
            raise HTTPException(503, {"code": "browser_chat_unavailable"}, headers=_HEADERS)
        try:
            return await submission.submit(
                principal=principal, bound_session=session, skill_id=body.skill_id,
                skill_version=body.skill_version, arguments=body.arguments,
                client_request_id=body.client_request_id,
            )
        except BrowserChatError as error:
            raise HTTPException(
                error.http_status,
                {"code": error.code, "message": "Browser request was not accepted."},
                headers=_HEADERS,
            ) from None

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

    @router.post(
        "/browser-runs/{task_id}/{run_id}/verify-result",
        response_model=BrowserRunResponse, status_code=202,
        responses=_VERIFY_RESULT_ERRORS,
    )
    async def verify_result(
        task_id: RunIdentifier, run_id: RunIdentifier, body: BrowserCancelRequest,
        response: Response, principal: Principal = Depends(require_principal),
    ) -> BrowserRunResponse:
        response.headers.update(_HEADERS)
        bound_service, session = bind(principal, body.session_id)
        try:
            return await bound_service.check_handoff(principal, session, task_id, run_id)
        except BrowserChatError as error:
            # The approved contract has one unavailable code; store, lane and
            # projection failures keep 503 without exposing their internal codes.
            code = "browser_chat_unavailable" if error.http_status == 503 else error.code
            raise HTTPException(
                error.http_status,
                {"code": code, "message": "Result check was not accepted."},
                headers=_HEADERS,
            ) from None

    @router.post(
        "/browser-runs/{task_id}/{run_id}/decision",
        response_model=BrowserRunResponse,
    )
    async def decide_write(
        task_id: RunIdentifier, run_id: RunIdentifier, body: BrowserDecisionRequest,
        response: Response, principal: Principal = Depends(require_principal),
    ) -> BrowserRunResponse:
        response.headers.update(_HEADERS)
        bound_service, session = bind(principal, body.session_id)
        try:
            return await bound_service.decide_write(
                principal, session, task_id, run_id, body.decision,
            )
        except BrowserChatError as error:
            raise HTTPException(
                error.http_status,
                {"code": error.code, "message": "Browser decision was not accepted."},
                headers=_HEADERS,
            ) from None

    @router.get("/browser-runs/{task_id}/{run_id}/artifacts/{opaque_ref}")
    async def read_artifact(
        task_id: RunIdentifier, run_id: RunIdentifier, opaque_ref: RunIdentifier,
        session_id: Annotated[str, Query(min_length=1, max_length=192)],
        principal: Principal = Depends(require_principal),
    ) -> Response:
        bound_service, session = bind(principal, session_id)
        try:
            media_type, payload = await bound_service.read_artifact(
                principal, session, task_id, run_id, opaque_ref,
            )
        except BrowserChatError as error:
            raise HTTPException(
                error.http_status,
                {"code": error.code, "message": "Browser artifact is unavailable."},
                headers=_HEADERS,
            ) from None
        if media_type not in {
            "application/json", "application/pdf", "text/plain", "text/csv", "image/png",
        } or type(payload) is not bytes:
            raise HTTPException(
                503, {"code": "browser_file_unavailable"}, headers=_HEADERS,
            )
        return Response(
            content=payload, media_type=media_type,
            headers={**_HEADERS, "X-Content-Type-Options": "nosniff",
                     "Content-Disposition": 'attachment; filename="browser-artifact"'},
        )

    return router
