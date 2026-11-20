"""Runtime API router factory."""

from __future__ import annotations

import logging
import re
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
from app.ports.conversation_history import ConversationHistoryError, ConversationHistoryPort
from app.ports.file_artifacts import ChatAttachmentDescriptor, ChatAttachmentError
from app.ports.im_context import IMContextError, IMContextPort
from app.ports.response_envelope import ResponseEnvelope, UIComponent
from app.ports.runtime import (
    DFormCollectionReference,
    ExplicitUserSkill,
    RuntimePort,
    RuntimeSessionBusyError,
    UserActionOutcome,
    chat_attachment_context,
    chat_client_request_id,
    d_form_collection_reference,
    im_source_context,
    user_skill_ref_reference,
)
from app.ports.task_store import DFormCollectionError
from app.ports.user_skills import UserSkillOwner
from app.user_skills.definition import NAME_MAX

_HISTORY_LOG = logging.getLogger("uvicorn.error.chat_history")
_UUID_SESSION = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
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


class UserSkillRef(BaseModel):
    """The user's own saved flow, explicitly named in the chat input (``$``)."""

    model_config = ConfigDict(extra="forbid")

    skill_id: UUID
    # The name shown on the tag: a flow renamed since is refused, never run.
    name: str = Field(min_length=1, max_length=NAME_MAX)


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
    user_skill_ref: UserSkillRef | None = None


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


def _session_not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "session_not_found", "message": "Session was not found."},
        headers=_PRIVATE_HEADERS,
    )


def _history_unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": "chat_history_unavailable",
            "message": "Conversation history is unavailable.",
        },
        headers=_PRIVATE_HEADERS,
    )


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
        raise _session_not_found() from None
    return session_id


def _require_runtime(runtime: RuntimePort | None) -> RuntimePort:
    if runtime is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "runtime_unavailable",
                "message": "Runtime provider is not configured.",
            },
        )
    return runtime


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
    chat_history: ConversationHistoryPort | None = None,
) -> APIRouter:
    router = APIRouter()
    handle_router = APIRouter(route_class=_PrivateHandleRoute)

    async def require_active_history(
        owner: UserSkillOwner, session_id: str
    ) -> ConversationHistoryPort:
        if chat_history is None:
            raise _history_unavailable()
        try:
            deleted = await chat_history.is_deleted(owner, session_id)
        except ConversationHistoryError:
            raise _history_unavailable() from None
        if deleted:
            raise _session_not_found()
        return chat_history

    @handle_router.post(
        "/handle",
        response_model=ResponseEnvelope,
        responses={
            404: {"model": RuntimeRequestErrorResponse},
            422: {"model": RuntimeRequestErrorResponse},
            503: {"model": RuntimeRequestErrorResponse},
        },
    )
    async def handle(
        body: HandleRequest,
        response: Response,
        principal: Principal = Depends(require_principal),
    ) -> ResponseEnvelope:
        if body.user_skill_ref is not None and (
            body.channel != "web" or body.source_refs or body.attachment_ids
            or body.d_form_collection is not None
            or body.client_capabilities.get("browser_async_v1") is True
        ):
            # Before every other route: an explicit flow never mixes with these.
            raise HTTPException(
                422,
                {"code": "user_skill_ref_not_allowed",
                 "message": "指定功能时不能同时使用附件、表单续填或浏览器技能。"},
                headers=_PRIVATE_HEADERS,
            )
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
        browser_async = body.client_capabilities.get("browser_async_v1") is True
        if browser_async and body.d_form_collection is not None:
            raise HTTPException(422, {"code": "collection_not_found"})
        session_id = _bind_session(
            principal=principal,
            requested_session_id=body.session_id,
            session_binder=session_binder,
        )
        owner = UserSkillOwner(
            tenant_id=principal.org_ctx.tenant_id, ai_user_id=principal.ai_user_id
        )
        history = await require_active_history(owner, session_id)
        if browser_async:
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
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
        bound_runtime = _require_runtime(runtime)
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
        skill_ref_token = user_skill_ref_reference.set(
            None if body.user_skill_ref is None else ExplicitUserSkill(
                str(body.user_skill_ref.skill_id), body.user_skill_ref.name,
            )
        )
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
            elif body.channel == "web" and result.status != "running":
                try:
                    await history.append_turn(
                        owner,
                        session_id,
                        body.client_request_id,
                        body.message,
                        result,
                        # Only the web client's UUID can resume the conversation later.
                        client_session_id=(
                            body.session_id
                            if _UUID_SESSION.fullmatch(body.session_id) else None
                        ),
                    )
                except ConversationHistoryError as error:
                    _HISTORY_LOG.warning("chat_history.append result=error code=%s", error)
            return result
        except IMContextError as error:
            detail = {"code": error.code}
            if error.code == "user_skill_ref_not_allowed":
                detail["message"] = "当前运行模式不支持指定功能，请不写 $ 直接描述要办的事。"
            raise HTTPException(error.http_status, detail, headers=_PRIVATE_HEADERS) from None
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
            user_skill_ref_reference.reset(skill_ref_token)
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
        session_id = _bind_session(
            principal=principal,
            requested_session_id=body.session_id,
            session_binder=session_binder,
        )
        owner = UserSkillOwner(
            tenant_id=principal.org_ctx.tenant_id, ai_user_id=principal.ai_user_id
        )
        await require_active_history(owner, session_id)
        bound_runtime = _require_runtime(runtime)
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
