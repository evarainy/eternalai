"""Admin Lite API router for role-guarded Registry management."""

from __future__ import annotations

from typing import Annotated, NoReturn
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict

from app.admin.registry import (
    AdminCapabilityCreate,
    AdminCapabilityNotFoundError,
    AdminCapabilityView,
    AdminInvalidStatusTransitionError,
    AdminRegistryService,
    AdminRequestContext,
    AdminRoleNotAllowedError,
)


class AdminCapabilityListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AdminCapabilityView]


def _request_context(
    role_claims: Annotated[
        str | None,
        Header(alias="X-EternalAI-Roles"),
    ] = None,
) -> AdminRequestContext:
    roles = tuple(role.strip() for role in (role_claims or "").split(",") if role.strip())
    trace_id = uuid4().hex
    return AdminRequestContext(
        trace_id=trace_id,
        session_id="admin-lite",
        ai_user_id="unverified-admin-request",
        roles=roles,
    )


AdminContext = Annotated[AdminRequestContext, Depends(_request_context)]


def _configured(service: AdminRegistryService | None) -> AdminRegistryService:
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "admin_registry_unavailable",
                "message": "Admin Registry provider is not configured.",
            },
        )
    return service


def _raise_http(error: RuntimeError) -> NoReturn:
    if isinstance(error, AdminRoleNotAllowedError):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "role_not_allowed",
                "message": "Management role is required.",
            },
        ) from None
    if isinstance(error, AdminCapabilityNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "capability_not_found",
                "message": "Capability was not found.",
            },
        ) from None
    if isinstance(error, AdminInvalidStatusTransitionError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "invalid_status_transition",
                "message": "Capability status transition is not allowed.",
            },
        ) from None
    raise error


def make_router(service: AdminRegistryService | None) -> APIRouter:
    router = APIRouter()

    @router.get("/registry", response_model=AdminCapabilityListResponse)
    async def list_capabilities(context: AdminContext) -> AdminCapabilityListResponse:
        try:
            capabilities = await _configured(service).list_capabilities(context)
        except RuntimeError as error:
            _raise_http(error)
        return AdminCapabilityListResponse(
            items=[AdminCapabilityView.from_spec(item) for item in capabilities]
        )

    @router.get("/registry/{capability_id}", response_model=AdminCapabilityView)
    async def get_capability(
        capability_id: str,
        context: AdminContext,
    ) -> AdminCapabilityView:
        try:
            capability = await _configured(service).get_capability(
                capability_id,
                context,
            )
        except RuntimeError as error:
            _raise_http(error)
        return AdminCapabilityView.from_spec(capability)

    @router.post(
        "/registry",
        response_model=AdminCapabilityView,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_capability(
        body: AdminCapabilityCreate,
        context: AdminContext,
    ) -> AdminCapabilityView:
        try:
            capability = await _configured(service).create_capability(body, context)
        except RuntimeError as error:
            _raise_http(error)
        return AdminCapabilityView.from_spec(capability)

    @router.post(
        "/registry/{capability_id}/enable",
        response_model=AdminCapabilityView,
    )
    async def enable_capability(
        capability_id: str,
        context: AdminContext,
    ) -> AdminCapabilityView:
        try:
            capability = await _configured(service).enable_capability(
                capability_id,
                context,
            )
        except RuntimeError as error:
            _raise_http(error)
        return AdminCapabilityView.from_spec(capability)

    @router.post(
        "/registry/{capability_id}/disable",
        response_model=AdminCapabilityView,
    )
    async def disable_capability(
        capability_id: str,
        context: AdminContext,
    ) -> AdminCapabilityView:
        try:
            capability = await _configured(service).disable_capability(
                capability_id,
                context,
            )
        except RuntimeError as error:
            _raise_http(error)
        return AdminCapabilityView.from_spec(capability)

    return router
