"""Runtime API router factory."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, RootModel, ValidateAs, ValidationError

from app.api.v1.auth import PrincipalDependency
from app.contracts.sdui.models import UserAction
from app.ports.auth import Principal, SessionBindingError
from app.ports.browser_chat import BrowserChatError, BrowserChatPort
from app.ports.response_envelope import ResponseEnvelope, UIComponent
from app.ports.runtime import RuntimePort, UserActionOutcome

_PRIVATE_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache"}


class _PrivateHandleRoute(APIRoute):
    """Protect submitted conversation data before body validation or auth succeeds."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                response = await original(request)
            except RequestValidationError:
                raise HTTPException(
                    422, {"code": "runtime_request_input_invalid"}, headers=_PRIVATE_HEADERS,
                ) from None
            except HTTPException as error:
                error.headers = {**(error.headers or {}), **_PRIVATE_HEADERS}
                raise
            response.headers.update(_PRIVATE_HEADERS)
            return response

        return handle


class HandleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: Literal["web", "cli", "api", "mock"]
    session_id: str
    message: str
    client_capabilities: dict[str, Any] = Field(default_factory=dict)
    client_request_id: str | None = Field(default=None, min_length=1, max_length=96)


class ActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: Literal["web", "cli", "api", "mock"]
    session_id: str
    action: UserAction


class ProjectedActionResult(RootModel[dict[str, Any]]):
    """Runtime result after CapabilitySpec.output_schema projection."""

    model_config = ConfigDict(
        json_schema_extra={
            "additionalProperties": {},
            "description": (
                "Dynamic business result after app.runtime.response_projection."
                "project_response_data applies CapabilitySpec.output_schema; this "
                "OpenAPI shape is not an exposure allowlist."
            ),
        }
    )


class ActionResponseData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_outcome: UserActionOutcome
    result: ProjectedActionResult | None


class ActionResponseEnvelope(ResponseEnvelope):
    """ResponseEnvelope specialization for structured user actions."""

    data: Annotated[
        Any,
        ValidateAs(ActionResponseData, lambda value: value),
    ]


def _failed_action_response(envelope: ResponseEnvelope) -> ActionResponseEnvelope:
    fields = envelope.model_dump()
    fields.update(
        {
            "status": "failed",
            "message": (
                "操作响应未通过安全校验，无法确认本次操作结果。"
                "请先核对业务状态，避免重复提交。"
            ),
            "fallback_text": (
                "Action response validation failed; verify the business state and "
                "do not submit again until the result is known."
            ),
            "ui": UIComponent(component_type="none", action="none"),
            "data": {
                "action_outcome": "action_gate_unavailable",
                "result": None,
            },
            "trace_summary": None,
        }
    )
    return ActionResponseEnvelope.model_validate(fields)


def _bind_session(
    *,
    principal: Principal,
    requested_session_id: str,
    session_binder: Callable[[Principal, str], str] | None,
) -> str:
    if session_binder is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "authentication_required",
                "message": "Valid authentication is required.",
            },
            headers={"WWW-Authenticate": "Session"},
        )
    try:
        session_id = session_binder(principal, requested_session_id)
    except SessionBindingError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "session_not_found",
                "message": "Session was not found.",
            },
        ) from None
    return session_id


def _bind_runtime_request(
    *,
    runtime: RuntimePort | None,
    principal: Principal,
    requested_session_id: str,
    session_binder: Callable[[Principal, str], str] | None,
) -> tuple[RuntimePort, str]:
    session_id = _bind_session(
        principal=principal,
        requested_session_id=requested_session_id,
        session_binder=session_binder,
    )
    if runtime is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "runtime_unavailable",
                "message": "Runtime provider is not configured.",
            },
        )
    return runtime, session_id


def make_router(
    runtime: RuntimePort | None,
    require_principal: PrincipalDependency,
    session_binder: Callable[[Principal, str], str] | None,
    *,
    browser_chat: BrowserChatPort | None = None,
) -> APIRouter:
    router = APIRouter()
    handle_router = APIRouter(route_class=_PrivateHandleRoute)

    @handle_router.post("/handle", response_model=ResponseEnvelope)
    async def handle(
        body: HandleRequest,
        response: Response,
        principal: Principal = Depends(require_principal),
    ) -> ResponseEnvelope:
        if body.client_capabilities.get("browser_async_v1") is True:
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
            session_id = _bind_session(
                principal=principal,
                requested_session_id=body.session_id,
                session_binder=session_binder,
            )
            if browser_chat is None:
                raise HTTPException(
                    status_code=503,
                    detail={"code": "browser_runtime_unavailable"},
                    headers={"Cache-Control": "no-store"},
                )
            if body.client_capabilities.get("browser_skill_id") != browser_chat.skill_id:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "browser_skill_not_configured"},
                    headers={"Cache-Control": "no-store"},
                )
            if body.client_request_id is None:
                raise BrowserChatError("browser_request_id_required", http_status=422)
            return await browser_chat.start(
                channel=body.channel,
                principal=principal,
                bound_session=session_id,
                message=body.message,
                client_capabilities=body.client_capabilities,
                client_request_id=body.client_request_id,
                skill_id=browser_chat.skill_id,
            )
        bound_runtime, session_id = _bind_runtime_request(
            runtime=runtime,
            principal=principal,
            requested_session_id=body.session_id,
            session_binder=session_binder,
        )
        envelope: ResponseEnvelope = await bound_runtime.handle_user_message(
            channel=body.channel,
            principal=principal,
            session_id=session_id,
            message=body.message,
            client_capabilities=body.client_capabilities,
        )
        return envelope

    @router.post("/action", response_model=ActionResponseEnvelope)
    async def handle_action(
        body: ActionRequest,
        principal: Principal = Depends(require_principal),
    ) -> ActionResponseEnvelope:
        bound_runtime, session_id = _bind_runtime_request(
            runtime=runtime,
            principal=principal,
            requested_session_id=body.session_id,
            session_binder=session_binder,
        )
        envelope = await bound_runtime.handle_user_action(
            channel=body.channel,
            principal=principal,
            session_id=session_id,
            action=body.action,
        )
        try:
            return ActionResponseEnvelope.model_validate(envelope.model_dump())
        except ValidationError:
            return _failed_action_response(envelope)

    router.include_router(handle_router)
    return router
