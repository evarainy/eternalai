"""Authenticated Work Object API and online OA synchronization."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Literal, NoReturn, TypeAlias
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from app.api.v1.auth import PrincipalDependency
from app.ports.auth import Principal
from app.ports.capability_gateway import CapabilityGatewayPort, ErrorCode
from app.ports.capability_registry import CapabilityRegistryPort, CapabilitySpec
from app.ports.credential_binding import (
    BackgroundWorkObjectSyncError,
    CredentialCountedFailureCode,
)
from app.ports.request_context import RequestOrgContext
from app.ports.work_object import (
    WORK_OBJECT_LIST_FETCH_LIMIT,
    WORK_OBJECT_LIST_LIMIT,
    OAPendingWorkSnapshot,
    OAPendingWorkSnapshotCollection,
    WorkObjectHandlingMark,
    WorkObjectRecord,
    WorkObjectStorePort,
)
from app.ports.work_object_handling import (
    WorkObjectHandlingAction,
    project_handling_action,
)

_LOGGER = logging.getLogger(__name__)

OA_PENDING_WORKFLOWS_CAPABILITY_ID = "oa.list_pending_workflows"
_REAUTHENTICATION_ERRORS: frozenset[ErrorCode] = frozenset(
    {"identity_unbound", "identity_expired", "identity_revoked"}
)
_BINDING_SCOPE_ERRORS: frozenset[ErrorCode] = frozenset({"needs_binding_scope"})


class _WorkObjectViewBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    work_object_id: str
    assignee_display_name: str
    due_at: datetime | None
    handling_mark: WorkObjectHandlingMark | None
    handling_marked_at: datetime | None
    task_record_id: str | None
    handling_action: WorkObjectHandlingAction
    handling_capability_id: str | None

    @model_validator(mode="after")
    def validate_handling_capability_id(self) -> _WorkObjectViewBase:
        capability_actions = {"ai_draft", "self_serve"}
        if self.handling_action in capability_actions:
            if self.handling_capability_id is None:
                raise ValueError(
                    "handling_capability_id is required for capability handling actions"
                )
        elif self.handling_capability_id is not None:
            raise ValueError(
                "handling_capability_id must be null for non-capability actions"
            )
        return self


class OAWorkObjectView(_WorkObjectViewBase):
    state_authority: Literal["external_snapshot"]
    source_system: Literal["oa"]
    source_kind: Literal["pending_workflow"]
    source_ref: str
    source_title: str
    source_status: str
    source_received_at: str
    source_created_at: str
    source_workflow_type_id: str
    source_fetched_at: datetime


class InternalWorkObjectView(_WorkObjectViewBase):
    state_authority: Literal["internal"]
    source_system: str
    source_kind: str
    source_ref: None
    source_title: None
    source_status: None
    source_received_at: None
    source_created_at: None
    source_workflow_type_id: None
    source_fetched_at: None


WorkObjectView: TypeAlias = Annotated[
    OAWorkObjectView | InternalWorkObjectView,
    Field(discriminator="state_authority"),
]
_WORK_OBJECT_VIEW_ADAPTER: TypeAdapter[WorkObjectView] = TypeAdapter(WorkObjectView)


class WorkObjectListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[WorkObjectView]
    limit: int
    limit_exceeded: bool


class SetHandlingMarkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    mark: WorkObjectHandlingMark


class WorkObjectService:
    """Application service that keeps transport and persistence behind Ports."""

    def __init__(
        self,
        *,
        store: WorkObjectStorePort,
        gateway: CapabilityGatewayPort,
        capability_registry: CapabilityRegistryPort,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._store = store
        self._gateway = gateway
        self._capability_registry = capability_registry
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or (lambda: uuid4().hex)

    async def list_for_principal(
        self,
        principal: Principal,
        *,
        search_term: str | None = None,
    ) -> WorkObjectListResponse:
        records = await self._store.list_for_assignee(
            principal.ai_user_id,
            search_term=search_term,
            limit=WORK_OBJECT_LIST_FETCH_LIMIT,
        )
        capabilities = await self._capability_registry.list(status="active")
        return _list_response(records, capabilities)

    async def get_for_principal(
        self,
        work_object_id: str,
        principal: Principal,
    ) -> WorkObjectView | None:
        record = await self._store.get_for_assignee(
            work_object_id,
            principal.ai_user_id,
        )
        if record is None:
            return None
        capabilities = await self._capability_registry.list(status="active")
        return _view_from_record(record, capabilities)

    async def sync_for_principal(self, principal: Principal) -> WorkObjectListResponse:
        return await self._sync_for_principal(principal, background=False)

    async def _sync_for_principal(
        self,
        principal: Principal,
        *,
        background: bool,
    ) -> WorkObjectListResponse:
        operation_id = self._id_factory()
        result = await self._gateway.execute_capability(
            task_id=f"work-object-sync:{operation_id}",
            session_id=f"work-object:{principal.ai_user_id}",
            ai_user_id=principal.ai_user_id,
            capability_id=OA_PENDING_WORKFLOWS_CAPABILITY_ID,
            arguments={},
            request_context=RequestOrgContext(
                request_id=operation_id,
                tenant_id=principal.org_ctx.tenant_id,
                org_id=principal.org_ctx.org_id,
                department_id=principal.org_ctx.department_id,
                roles=list(principal.roles),
                channel="web",
            ),
        )
        if result.status != "completed" or result.data is None:
            if background:
                _raise_background_sync_failure(result.error_code)
            _raise_sync_failure(result.error_code)
        try:
            payload = OAPendingWorkSnapshotCollection.model_validate(
                {
                    **result.data,
                    "workflows": [
                        {
                            "source_ref": item.get("todo_id"),
                            "title": item.get("title"),
                            "status": item.get("status"),
                            "received_at": item.get("received_at"),
                            "created_at": item.get("created_at"),
                            "workflow_type_id": item.get("workflow_type_id"),
                        }
                        if isinstance(item, dict)
                        else item
                        for item in result.data.get("workflows", [])
                    ],
                },
                strict=True,
            )
        except (AttributeError, TypeError, ValidationError):
            if background:
                raise BackgroundWorkObjectSyncError(
                    authentication_denied=False,
                    failure_code="invalid_response",
                ) from None
            _raise_invalid_sync_payload()
        fetched_at = self._clock()
        await self._store.upsert_oa_pending_workflows(
            assignee_ai_user_id=principal.ai_user_id,
            assignee_display_name=principal.display_name,
            snapshots=[
                OAPendingWorkSnapshot.model_validate(snapshot.model_dump(), strict=True)
                for snapshot in payload.workflows
            ],
            fetched_at=fetched_at,
        )
        return await self.list_for_principal(principal)

    async def sync_for_background(self, principal: Principal) -> WorkObjectListResponse:
        """Run the same Gateway path with retry-safe failure classification."""

        return await self._sync_for_principal(principal, background=True)

    async def set_handling_mark_for_principal(
        self,
        work_object_id: str,
        principal: Principal,
        mark: WorkObjectHandlingMark,
    ) -> WorkObjectView | None:
        record = await self._store.set_handling_mark_for_assignee(
            work_object_id,
            principal.ai_user_id,
            mark,
            marked_at=self._clock(),
        )
        if record is None:
            return None
        capabilities = await self._capability_registry.list(status="active")
        return _view_from_record(record, capabilities)


def make_router(
    service: WorkObjectService | None,
    require_principal: PrincipalDependency,
) -> APIRouter:
    router = APIRouter()

    def configured() -> WorkObjectService:
        if service is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "work_object_unavailable",
                    "message": "Work Object provider is not configured.",
                },
            )
        return service

    @router.get("", response_model=WorkObjectListResponse)
    async def list_work_objects(
        q: Annotated[str | None, Query()] = None,
        principal: Principal = Depends(require_principal),
    ) -> WorkObjectListResponse:
        search_term = q.strip() if q is not None else None
        return await configured().list_for_principal(
            principal,
            search_term=search_term or None,
        )

    @router.post("/sync", response_model=WorkObjectListResponse)
    async def sync_work_objects(
        principal: Principal = Depends(require_principal),
    ) -> WorkObjectListResponse:
        return await configured().sync_for_principal(principal)

    @router.get("/{work_object_id}", response_model=WorkObjectView)
    async def get_work_object(
        work_object_id: str,
        principal: Principal = Depends(require_principal),
    ) -> WorkObjectView:
        view = await configured().get_for_principal(work_object_id, principal)
        if view is None:
            _raise_not_found()
        return view

    @router.patch("/{work_object_id}/handling-mark", response_model=WorkObjectView)
    async def set_work_object_handling_mark(
        work_object_id: str,
        body: SetHandlingMarkRequest,
        principal: Principal = Depends(require_principal),
    ) -> WorkObjectView:
        view = await configured().set_handling_mark_for_principal(
            work_object_id,
            principal,
            body.mark,
        )
        if view is None:
            _raise_not_found()
        return view

    return router


def _list_response(
    records: list[WorkObjectRecord],
    capabilities: list[CapabilitySpec],
) -> WorkObjectListResponse:
    limit_exceeded = len(records) > WORK_OBJECT_LIST_LIMIT
    return WorkObjectListResponse(
        items=[
            _view_from_record(record, capabilities)
            for record in records[:WORK_OBJECT_LIST_LIMIT]
        ],
        limit=WORK_OBJECT_LIST_LIMIT,
        limit_exceeded=limit_exceeded,
    )


def _view_from_record(
    record: WorkObjectRecord,
    capabilities: list[CapabilitySpec],
) -> WorkObjectView:
    handling_capability = _resolve_handling_capability(
        record=record,
        capabilities=capabilities,
    )
    handling_action = project_handling_action(
        state_authority=record.state_authority,
        source_system=record.source_system,
        handling_mark=record.handling_mark,
        capability=handling_capability,
    )
    handling_capability_id = (
        handling_capability.capability_id
        if handling_capability is not None
        and handling_action in {"ai_draft", "self_serve"}
        else None
    )
    view_payload = record.model_dump(
        exclude={
            "assignee_ai_user_id",
            "handling_marked_by_ai_user_id",
            "created_at",
            "updated_at",
        }
    )
    view_payload.update(
        handling_action=handling_action,
        handling_capability_id=handling_capability_id,
    )
    return _WORK_OBJECT_VIEW_ADAPTER.validate_python(
        view_payload,
        strict=True,
    )


def _resolve_handling_capability(
    *,
    record: WorkObjectRecord,
    capabilities: list[CapabilitySpec],
) -> CapabilitySpec | None:
    matches = [
        capability
        for capability in capabilities
        if capability.status == "active"
        and any(
            selector.source_system == record.source_system
            and selector.source_kind == record.source_kind
            and selector.source_workflow_type_id
            == record.source_workflow_type_id
            for selector in capability.handles_work_objects
        )
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) >= 2:
        _LOGGER.warning(
            "Ambiguous Work Object handling mapping; capability_ids=%s",
            sorted(capability.capability_id for capability in matches),
        )
    return None


def _raise_sync_failure(error_code: ErrorCode | None) -> NoReturn:
    if error_code in _REAUTHENTICATION_ERRORS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "oa_reauthentication_required",
                "message": "OA authentication is no longer usable; authenticate again.",
                "next_action": "reauthenticate",
            },
        )
    if error_code in _BINDING_SCOPE_ERRORS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "oa_binding_scope_required",
                "message": "OA binding scope must be clarified before synchronization.",
                "next_action": "clarify_binding_scope",
            },
        )
    if error_code in {"policy_denied", "upstream_permission_denied"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "work_object_sync_forbidden",
                "message": "Work Object synchronization is not permitted.",
            },
        )
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": "work_object_sync_failed",
            "message": "Work Object synchronization failed; stored data is unchanged.",
        },
    )


def _raise_background_sync_failure(error_code: ErrorCode | None) -> NoReturn:
    if error_code in _REAUTHENTICATION_ERRORS:
        raise BackgroundWorkObjectSyncError(authentication_denied=True)
    countable_errors: dict[ErrorCode, CredentialCountedFailureCode] = {
        "adapter_timeout": "timeout",
        "adapter_http_500": "upstream_5xx",
        "adapter_payload_invalid": "invalid_response",
        "adapter_missing_required_field": "invalid_response",
        "adapter_empty_response": "invalid_response",
    }
    failure_code = (
        countable_errors.get(error_code) if error_code is not None else None
    )
    raise BackgroundWorkObjectSyncError(
        authentication_denied=False,
        failure_code=failure_code,
    )


def _raise_invalid_sync_payload() -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail={
            "code": "work_object_sync_invalid",
            "message": "OA returned an invalid Work Object synchronization payload.",
        },
    )


def _raise_not_found() -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "code": "work_object_not_found",
            "message": "Work Object was not found.",
        },
    )


__all__ = (
    "InternalWorkObjectView",
    "OAWorkObjectView",
    "SetHandlingMarkRequest",
    "WorkObjectListResponse",
    "WorkObjectService",
    "WorkObjectView",
    "make_router",
)
