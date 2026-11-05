"""Authenticated, CSRF-protected IM login and reference selection."""

from collections.abc import Callable
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.auth import PrincipalDependency
from app.api.v1.runtime import _bind_session, _PrivateHandleRoute
from app.im_context import IMContextService
from app.ports.auth import Principal
from app.ports.communication import IMSelection
from app.ports.im_context import IMContextError


class SourceRequest(IMSelection):
    session_id: str = Field(min_length=1)


class SourceMessage(BaseModel):
    message_id: str
    text: str = Field(repr=False)


class SourceResponse(BaseModel):
    ref_id: str
    expires_at: datetime
    messages: list[SourceMessage]
    deep_link: str


class IMConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    im_instance_id: str
    im_origin: str
    host_origin: str
    helper_url: str


class IMLoginResponse(IMConfiguration):
    realm_id: str
    im_user_id: str
    action_url: str
    fields: dict[str, str] = Field(repr=False)
    expires_at: datetime


def make_router(
    service: IMContextService | None,
    require_principal: PrincipalDependency,
    session_binder: Callable[[Principal, str], str] | None,
) -> APIRouter:
    router = APIRouter(route_class=_PrivateHandleRoute)

    def available() -> IMContextService:
        if service is None:
            raise HTTPException(503, {"code": "im_not_enabled"})
        return service

    @router.get("/configuration", response_model=IMConfiguration | None)
    async def configuration() -> IMConfiguration | None:
        # Logout needs fixed, non-secret configuration even after host authentication expires.
        if service is None:
            return None
        return IMConfiguration(
            provider=service.provider, im_instance_id=service.im_instance_id,
            im_origin=service.im_origin, host_origin=service.host_origin,
            helper_url=service.helper_url,
        )

    @router.post("/login", response_model=IMLoginResponse)
    async def login(principal: Principal = Depends(require_principal)) -> IMLoginResponse:
        current = available()
        try:
            binding, form = await current.issue_login(principal)
        except IMContextError as error:
            raise HTTPException(error.http_status, {"code": error.code}) from None
        return IMLoginResponse(
            provider=binding.provider,
            im_instance_id=binding.im_instance_id,
            realm_id=binding.realm_id,
            im_user_id=binding.im_user_id,
            im_origin=current.im_origin,
            host_origin=current.host_origin,
            helper_url=current.helper_url,
            action_url=form.action_url,
            fields=form.fields,
            expires_at=form.expires_at,
        )

    @router.post("/sources", response_model=SourceResponse)
    async def select(
        body: SourceRequest,
        principal: Principal = Depends(require_principal),
    ) -> SourceResponse:
        current = available()
        bound_session = _bind_session(
            principal=principal, requested_session_id=body.session_id, session_binder=session_binder
        )
        try:
            result = await current.select(
                principal,
                bound_session,
                IMSelection.model_construct(**body.model_dump(exclude={"session_id"})),
            )
        except IMContextError as error:
            raise HTTPException(error.http_status, {"code": error.code}) from None
        return SourceResponse(
            ref_id=result.ref_id,
            expires_at=result.expires_at,
            messages=[SourceMessage(message_id=m.message_id, text=m.text) for m in result.messages],
            deep_link=result.deep_link,
        )

    return router
