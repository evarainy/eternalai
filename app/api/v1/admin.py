"""Admin Lite API router for role-guarded Registry management."""

from __future__ import annotations

from typing import Annotated, NoReturn
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict

from app.admin.d_form import (
    AdminDFormManagementService,
    DFormManagementCommand,
    DFormManagementError,
    DFormManagementState,
)
from app.admin.evidence import (
    AdminBindingListResponse,
    AdminTaskEventListResponse,
    AdminTaskListResponse,
    AdminTraceListResponse,
)
from app.admin.leader_roster import (
    AdminLeaderRosterError,
    AdminLeaderRosterService,
    LeaderRosterAddRequest,
    LeaderRosterCandidatesResponse,
    LeaderRosterEntryView,
    LeaderRosterListResponse,
    LeaderRosterRevokeResponse,
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
        org_ctx=principal.org_ctx,
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


_LEADER_ROSTER_HTTP = {
    "directory_scope_denied": status.HTTP_403_FORBIDDEN,
    "leader_roster_entry_not_found": status.HTTP_404_NOT_FOUND,
    "leader_roster_entry_exists": status.HTTP_409_CONFLICT,
    "leader_roster_person_ambiguous": status.HTTP_409_CONFLICT,
    "leader_roster_person_not_found": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "organization_directory_missing": status.HTTP_503_SERVICE_UNAVAILABLE,
    "organization_directory_stale": status.HTTP_503_SERVICE_UNAVAILABLE,
    "organization_directory_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
    "leader_roster_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
}
_NO_STORE = {"Cache-Control": "no-store"}


def _configured_leader_roster(
    service: AdminLeaderRosterService | None,
) -> AdminLeaderRosterService:
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "leader_roster_unavailable",
                "message": "Leader roster management is not configured.",
            },
            headers=_NO_STORE,
        )
    return service


def _raise_leader_roster_http(error: RuntimeError) -> NoReturn:
    if isinstance(error, AdminLeaderRosterError):
        raise HTTPException(
            status_code=_LEADER_ROSTER_HTTP.get(
                error.code, status.HTTP_503_SERVICE_UNAVAILABLE
            ),
            detail={"code": error.code, "message": "Leader roster request was refused."},
            headers=_NO_STORE,
        ) from None
    _raise_http(error)


def make_router(
    service: AdminRegistryService | None,
    require_principal: PrincipalDependency,
    *, d_form_management: AdminDFormManagementService | None = None,
    leader_roster: AdminLeaderRosterService | None = None,
) -> APIRouter:
    router = APIRouter()

    async def authenticated_context(
        principal: Principal = Depends(require_principal),
    ) -> AdminRequestContext:
        return _request_context(principal)

    async def d_form_access(principal: Principal) -> AdminDFormManagementService:
        if d_form_management is None:
            raise HTTPException(503, {"code": "d_form_management_unavailable",
                                      "message": "D form management is unavailable."})
        # The installed fixed D owner has a narrower, live session/binding grant.
        # Every other management route retains its existing Policy check.
        try:
            if await d_form_management.check_fixed_access(principal) is True:
                return d_form_management
        except Exception:
            raise HTTPException(
                503, {"code": "d_form_management_unavailable",
                      "message": "D form access is unavailable."},
            ) from None
        try:
            await _configured(service).list_bindings(
                principal.ai_user_id, _request_context(principal),
            )
        except RuntimeError as error:
            _raise_http(error)
        return d_form_management

    @router.get("/d-form", response_model=DFormManagementState)
    async def d_form_state(
        response: Response, principal: Principal = Depends(require_principal),
    ) -> DFormManagementState:
        response.headers.update({"Cache-Control": "no-store", "Pragma": "no-cache"})
        management = await d_form_access(principal)
        try:
            return await management.state(principal)
        except DFormManagementError as error:
            raise HTTPException(
                403, {"code": error.code, "message": "D form state is unavailable."},
                                headers={"Cache-Control": "no-store"}) from None
        except Exception:
            raise HTTPException(503, {"code": "d_form_management_unavailable",
                                      "message": "D form state is unavailable."},
                                headers={"Cache-Control": "no-store"}) from None

    @router.post(
        "/d-form/actions", response_model=DFormManagementState,
        openapi_extra={"requestBody": {"required": True, "content": {
            "application/json": {"schema": DFormManagementCommand.model_json_schema()},
        }}},
    )
    async def d_form_action(
        request: Request, response: Response,
        principal: Principal = Depends(require_principal),
    ) -> DFormManagementState:
        response.headers.update({"Cache-Control": "no-store", "Pragma": "no-cache"})
        management = await d_form_access(principal)
        try:
            encoded = await request.body()
            if len(encoded) > 16384:
                raise ValueError
            body = DFormManagementCommand.model_validate_json(encoded)
        except Exception:
            # Never echo conversation IDs or parameter values through validation errors.
            raise HTTPException(422, {"code": "d_form_command_invalid",
                                      "message": "D form command is invalid."},
                                headers={"Cache-Control": "no-store"}) from None
        try:
            return await management.perform(principal, body)
        except DFormManagementError as error:
            message = {
                "browser_publication_version_conflict": (
                    "发布版本冲突：已有同版本或另一待激活版本。"
                ),
                "browser_publication_not_found": "当前版本尚未准备，请先准备当前版本。",
                "browser_publication_already_prepared": "当前版本已发布或停用，不能重复准备。",
                "browser_publication_head_changed": "当前激活版本已变化，请刷新准备状态。",
                "browser_publication_revision_conflict": "发布状态已变化，请刷新准备状态。",
            }.get(error.code, "D form action was refused.")
            raise HTTPException(
                409, {"code": error.code, "message": message},
                                headers={"Cache-Control": "no-store"}) from None

    @router.get("/leader-roster", response_model=LeaderRosterListResponse)
    async def list_leader_roster(
        response: Response,
        context: AdminRequestContext = Depends(authenticated_context),
    ) -> LeaderRosterListResponse:
        response.headers.update(_NO_STORE)
        try:
            return await _configured_leader_roster(leader_roster).list_entries(context)
        except RuntimeError as error:
            _raise_leader_roster_http(error)

    @router.get("/leader-roster/candidates", response_model=LeaderRosterCandidatesResponse)
    async def list_leader_roster_candidates(
        response: Response,
        context: AdminRequestContext = Depends(authenticated_context),
        query: Annotated[str, Query(max_length=64)] = "",
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> LeaderRosterCandidatesResponse:
        response.headers.update(_NO_STORE)
        try:
            return await _configured_leader_roster(leader_roster).list_candidates(
                context, query=query, limit=limit
            )
        except RuntimeError as error:
            _raise_leader_roster_http(error)

    @router.post(
        "/leader-roster",
        response_model=LeaderRosterEntryView,
        status_code=status.HTTP_201_CREATED,
    )
    async def add_leader_roster_entry(
        body: LeaderRosterAddRequest,
        response: Response,
        context: AdminRequestContext = Depends(authenticated_context),
    ) -> LeaderRosterEntryView:
        response.headers.update(_NO_STORE)
        try:
            return await _configured_leader_roster(leader_roster).add_entry(body, context)
        except RuntimeError as error:
            _raise_leader_roster_http(error)

    @router.post(
        "/leader-roster/{entry_id}/revoke",
        response_model=LeaderRosterRevokeResponse,
    )
    async def revoke_leader_roster_entry(
        entry_id: UUID,
        response: Response,
        context: AdminRequestContext = Depends(authenticated_context),
    ) -> LeaderRosterRevokeResponse:
        response.headers.update(_NO_STORE)
        try:
            return await _configured_leader_roster(leader_roster).revoke_entry(
                entry_id, context
            )
        except RuntimeError as error:
            _raise_leader_roster_http(error)

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
