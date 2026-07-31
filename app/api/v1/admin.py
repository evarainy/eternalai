"""Admin Lite API router for role-guarded Registry management."""

from __future__ import annotations

from typing import Annotated, NoReturn
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict

from app.admin.evidence import (
    AdminBindingListResponse,
    AdminTaskEventListResponse,
    AdminTaskListResponse,
    AdminTraceListResponse,
)
from app.admin.registry import (
    AdminBindingMutationResponse,
    AdminBindingMutationUnavailableError,
    AdminBindingNotFoundError,
    AdminBindingQueryInvalidError,
    AdminCapabilityCreate,
    AdminCapabilityNotFoundError,
    AdminCapabilityView,
    AdminInvalidStatusTransitionError,
    AdminRegistryService,
    AdminRegistryServiceWithBindingMutations,
    AdminRequestContext,
    AdminRoleNotAllowedError,
    AdminTaskFilterRequiredError,
    AdminTaskNotFoundError,
    AdminTraceFilterRequiredError,
)
from app.api.v1.auth import PrincipalDependency
from app.ports.auth import Principal


class AdminCapabilityListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AdminCapabilityView]


def _request_context(principal: Principal) -> AdminRequestContext:
    trace_id = uuid4().hex
    return AdminRequestContext(
        trace_id=trace_id,
        session_id=f"admin-v1:{principal.ai_user_id}",
        ai_user_id=principal.ai_user_id,
        roles=principal.roles,
        principal_authenticated=True,
    )


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


def _configured_binding_mutations(
    service: AdminRegistryService | None,
) -> AdminRegistryServiceWithBindingMutations:
    if not isinstance(service, AdminRegistryServiceWithBindingMutations):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "admin_binding_mutation_unavailable",
                "message": "Admin binding mutation provider is not configured.",
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
    if isinstance(error, AdminTaskNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "task_not_found",
                "message": "Task was not found.",
            },
        ) from None
    if isinstance(error, AdminTaskFilterRequiredError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "task_filter_required",
                "message": "session_id or ai_user_id is required.",
            },
        ) from None
    if isinstance(error, AdminBindingQueryInvalidError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "binding_query_invalid",
                "message": "Binding query parameters are invalid.",
            },
        ) from None
    if isinstance(error, AdminBindingNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "binding_not_found",
                "message": "Binding was not found.",
            },
        ) from None
    if isinstance(error, AdminBindingMutationUnavailableError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "binding_mutation_unavailable",
                "message": "Binding mutation provider is unavailable.",
            },
        ) from None
    if isinstance(error, AdminTraceFilterRequiredError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "trace_filter_required",
                "message": "trace_id, task_id, or session_id is required.",
            },
        ) from None
    raise error


def make_router(
    service: AdminRegistryService | None,
    require_principal: PrincipalDependency,
) -> APIRouter:
    router = APIRouter()

    async def authenticated_context(
        principal: Principal = Depends(require_principal),
    ) -> AdminRequestContext:
        return _request_context(principal)

    @router.get("/registry", response_model=AdminCapabilityListResponse)
    async def list_capabilities(
        context: AdminRequestContext = Depends(authenticated_context),
    ) -> AdminCapabilityListResponse:
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
        context: AdminRequestContext = Depends(authenticated_context),
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
        context: AdminRequestContext = Depends(authenticated_context),
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
        context: AdminRequestContext = Depends(authenticated_context),
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
        context: AdminRequestContext = Depends(authenticated_context),
    ) -> AdminCapabilityView:
        try:
            capability = await _configured(service).disable_capability(
                capability_id,
                context,
            )
        except RuntimeError as error:
            _raise_http(error)
        return AdminCapabilityView.from_spec(capability)

    @router.get("/tasks", response_model=AdminTaskListResponse)
    async def list_tasks(
        context: AdminRequestContext = Depends(authenticated_context),
        session_id: Annotated[str | None, Query()] = None,
        ai_user_id: Annotated[str | None, Query()] = None,
    ) -> AdminTaskListResponse:
        try:
            tasks = await _configured(service).list_tasks(
                context,
                session_id=session_id,
                ai_user_id=ai_user_id,
            )
        except RuntimeError as error:
            _raise_http(error)
        return AdminTaskListResponse(items=tasks)

    @router.get(
        "/tasks/{task_id}/events",
        response_model=AdminTaskEventListResponse,
        response_model_exclude_none=True,
    )
    async def list_task_events(
        task_id: str,
        context: AdminRequestContext = Depends(authenticated_context),
    ) -> AdminTaskEventListResponse:
        try:
            events = await _configured(service).list_task_events(task_id, context)
        except RuntimeError as error:
            _raise_http(error)
        return AdminTaskEventListResponse(items=events)

    @router.get("/bindings", response_model=AdminBindingListResponse)
    async def list_bindings(
        context: AdminRequestContext = Depends(authenticated_context),
        ai_user_id: Annotated[str | None, Query()] = None,
        target_system: Annotated[str | None, Query()] = None,
        binding_scope: Annotated[str | None, Query()] = None,
        account_set_id: Annotated[str | None, Query()] = None,
        device_domain_id: Annotated[str | None, Query()] = None,
    ) -> AdminBindingListResponse:
        try:
            bindings = await _configured(service).list_bindings(
                ai_user_id,
                context,
                target_system=target_system,
                binding_scope=binding_scope,
                account_set_id=account_set_id,
                device_domain_id=device_domain_id,
            )
        except RuntimeError as error:
            _raise_http(error)
        return bindings

    @router.post(
        "/bindings/{binding_id}/revoke",
        response_model=AdminBindingMutationResponse,
    )
    async def revoke_binding(
        binding_id: str,
        context: AdminRequestContext = Depends(authenticated_context),
    ) -> AdminBindingMutationResponse:
        try:
            result = await _configured_binding_mutations(service).revoke_binding(
                binding_id,
                context,
            )
        except RuntimeError as error:
            _raise_http(error)
        return result

    @router.post(
        "/bindings/{binding_id}/reset",
        response_model=AdminBindingMutationResponse,
    )
    async def reset_binding(
        binding_id: str,
        context: AdminRequestContext = Depends(authenticated_context),
    ) -> AdminBindingMutationResponse:
        try:
            result = await _configured_binding_mutations(service).reset_binding(
                binding_id,
                context,
            )
        except RuntimeError as error:
            _raise_http(error)
        return result

    @router.get(
        "/traces",
        response_model=AdminTraceListResponse,
        response_model_exclude_none=True,
    )
    async def list_traces(
        context: AdminRequestContext = Depends(authenticated_context),
        trace_id: Annotated[str | None, Query()] = None,
        task_id: Annotated[str | None, Query()] = None,
        session_id: Annotated[str | None, Query()] = None,
    ) -> AdminTraceListResponse:
        try:
            traces = await _configured(service).list_traces(
                context,
                trace_id=trace_id,
                task_id=task_id,
                session_id=session_id,
            )
        except RuntimeError as error:
            _raise_http(error)
        return AdminTraceListResponse(items=traces)

    return router
