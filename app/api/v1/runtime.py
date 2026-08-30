"""Runtime API router factory."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, RootModel, ValidateAs

from app.api.v1.auth import PrincipalDependency
from app.contracts.sdui.models import UserAction
from app.ports.auth import Principal, SessionBindingError
from app.ports.response_envelope import ResponseEnvelope
from app.ports.runtime import RuntimePort, UserActionOutcome


class HandleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: Literal["web", "cli", "api", "mock"]
    session_id: str
    message: str
    client_capabilities: dict[str, Any] = Field(default_factory=dict)


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


def _bind_runtime_request(
    *,
    runtime: RuntimePort | None,
    principal: Principal,
    requested_session_id: str,
    session_binder: Callable[[Principal, str], str] | None,
) -> tuple[RuntimePort, str]:
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
) -> APIRouter:
    router = APIRouter()

    @router.post("/handle", response_model=ResponseEnvelope)
    async def handle(
        body: HandleRequest,
        principal: Principal = Depends(require_principal),
    ) -> ResponseEnvelope:
        bound_runtime, session_id = _bind_runtime_request(
            runtime=runtime,
            principal=principal,
            requested_session_id=body.session_id,
            session_binder=session_binder,
        )
        envelope: ResponseEnvelope = await bound_runtime.handle_user_message(
            channel=body.channel,
            ai_user_id=principal.ai_user_id,
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
        return ActionResponseEnvelope.model_validate(envelope.model_dump())

    return router
