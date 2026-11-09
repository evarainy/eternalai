"""Runtime API router factory."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, RootModel, ValidateAs, ValidationError

from app.api.v1.auth import PrincipalDependency
from app.contracts.sdui.models import UserAction
from app.file_artifacts import FileArtifactService
from app.ports.auth import Principal, SessionBindingError, authenticated_session
from app.ports.browser_chat import BrowserChatError, BrowserChatPort
from app.ports.browser_gateway import BrowserRequestScope, browser_request_scope
from app.ports.file_artifacts import ChatAttachmentDescriptor, ChatAttachmentError
from app.ports.im_context import IMContextError, IMContextPort
from app.ports.response_envelope import ResponseEnvelope, UIComponent
from app.ports.runtime import (
    DFormCollectionReference,
    RuntimePort,
    RuntimeSessionBusyError,
    UserActionOutcome,
    chat_attachment_context,
    chat_client_request_id,
    d_form_collection_reference,
    im_source_context,
)
from app.ports.task_store import DFormCollectionError

_PRIVATE_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache"}
_ATTACHMENT_MESSAGES = {
    "chat_attachment_invalid": "附件已失效或不属于当前会话，请移除后重新上传。",
    "chat_attachment_unsupported": "当前模式不支持附件，请移除附件后再发送。",
}


class _PrivateHandleRoute(APIRoute):
    """Protect submitted conversation data before body validation or auth succeeds."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                response = await original(request)
            except RequestValidationError:
                raise HTTPException(
                    422,
                    {"code": "runtime_request_input_invalid"},
                    headers=_PRIVATE_HEADERS,
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
    d_form_collection: DFormCollectionReference | None = None
    source_refs: list[str] = Field(default_factory=list, max_length=1)
    attachment_ids: list[UUID] = Field(default_factory=list, max_length=5)


class RuntimeRequestErrorDetail(BaseModel):
    code: str
    message: str | None = None


class RuntimeRequestErrorResponse(BaseModel):
    """/handle converts framework validation failures too, so 422 always has this shape."""

    detail: RuntimeRequestErrorDetail


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
                "操作响应未通过安全校验，无法确认本次操作结果。请先核对业务状态，避免重复提交。"
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


def _attachment_error(code: str) -> HTTPException:
    return HTTPException(
        422,
        {"code": code, "message": _ATTACHMENT_MESSAGES[code]},
        headers=_PRIVATE_HEADERS,
    )


def _session_busy() -> HTTPException:
    return HTTPException(409, {"code": "runtime_session_busy"}, headers=_PRIVATE_HEADERS)


def make_router(
    runtime: RuntimePort | None,
    require_principal: PrincipalDependency,
    session_binder: Callable[[Principal, str], str] | None,
    *,
    browser_chat: BrowserChatPort | None = None,
    im_context: IMContextPort | None = None,
    chat_attachments: FileArtifactService | None = None,
) -> APIRouter:
    router = APIRouter()
    handle_router = APIRouter(route_class=_PrivateHandleRoute)

    @handle_router.post(
        "/handle",
        response_model=ResponseEnvelope,
        responses={422: {"model": RuntimeRequestErrorResponse}},
    )
    async def handle(
        body: HandleRequest,
        response: Response,
        principal: Principal = Depends(require_principal),
    ) -> ResponseEnvelope:
        if body.source_refs and (
            body.channel != "web" or body.d_form_collection is not None
            or body.client_capabilities
        ):
            raise HTTPException(422, {"code": "im_source_combination_invalid"})
        if body.attachment_ids and (
            body.source_refs
            or body.d_form_collection is not None
            or body.client_capabilities.get("browser_async_v1") is True
        ):
            raise _attachment_error("chat_attachment_unsupported")
        if body.client_capabilities.get("browser_async_v1") is True:
            if body.d_form_collection is not None:
                raise HTTPException(422, {"code": "collection_not_found"})
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
            selected_skill = body.client_capabilities.get(
                "browser_skill_id",
                browser_chat.skill_id,
            )
            selector = getattr(browser_chat, "can_select_skill", None)
            if type(selected_skill) is not str or (
                selected_skill != browser_chat.skill_id
                and (not callable(selector) or selector(selected_skill) is not True)
            ):
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
                skill_id=selected_skill,
            )
        bound_runtime, session_id = _bind_runtime_request(
            runtime=runtime,
            principal=principal,
            requested_session_id=body.session_id,
            session_binder=session_binder,
        )
        attachments: tuple[ChatAttachmentDescriptor, ...] = ()
        if body.attachment_ids:
            if chat_attachments is None:
                raise _attachment_error("chat_attachment_unsupported")
            try:
                # Row 1 headers (read by the isolated child) let the model map columns.
                attachments = await chat_attachments.resolve_chat_attachments(
                    principal, session_id, body.attachment_ids, with_headers=True
                )
            except ChatAttachmentError as error:
                raise _attachment_error(error.code) from None
        scope = None
        if browser_chat is not None and body.client_request_id is not None and not body.source_refs:
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
            captured = authenticated_session.get()
            if captured is None or captured.principal != principal:
                raise HTTPException(status_code=403, detail={"code": "policy_denied"})
            try:
                scope = BrowserRequestScope(captured, session_id, body.client_request_id)
            except ValueError:
                raise HTTPException(
                    status_code=422, detail={"code": "adapter_payload_invalid"}
                ) from None
        token = browser_request_scope.set(scope)
        collection_token = d_form_collection_reference.set(body.d_form_collection)
        # Read only by the AgentScope runtime's confirmation receipts.
        request_id_token = chat_client_request_id.set(body.client_request_id)
        source_token = im_source_context.set(None)
        attachment_token = chat_attachment_context.set(attachments)
        try:
            if body.source_refs:
                if im_context is None:
                    raise IMContextError("im_not_enabled", 503)
                source = await im_context.revalidate(principal, session_id, tuple(body.source_refs))
                im_source_context.set(source)
            result = await bound_runtime.handle_user_message(
                channel=body.channel,
                principal=principal,
                session_id=session_id,
                message=body.message,
                client_capabilities=body.client_capabilities,
            )
            if body.source_refs:
                assert im_context is not None
                await im_context.revalidate(principal, session_id, tuple(body.source_refs))
            return result
        except IMContextError as error:
            raise HTTPException(error.http_status, {"code": error.code},
                                headers=_PRIVATE_HEADERS) from None
        except DFormCollectionError as error:
            raise HTTPException(
                404 if error.code == "collection_not_found" else 409,
                {"code": error.code},
                headers=_PRIVATE_HEADERS,
            ) from None
        except ChatAttachmentError as error:
            raise _attachment_error(error.code) from None
        except RuntimeSessionBusyError:
            raise _session_busy() from None
        finally:
            chat_attachment_context.reset(attachment_token)
            im_source_context.reset(source_token)
            chat_client_request_id.reset(request_id_token)
            d_form_collection_reference.reset(collection_token)
            browser_request_scope.reset(token)

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
        try:
            envelope = await bound_runtime.handle_user_action(
                channel=body.channel,
                principal=principal,
                session_id=session_id,
                action=body.action,
            )
        except RuntimeSessionBusyError:
            raise _session_busy() from None
        try:
            return ActionResponseEnvelope.model_validate(envelope.model_dump())
        except ValidationError:
            return _failed_action_response(envelope)

    router.include_router(handle_router)
    return router
