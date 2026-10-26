"""Authenticated Work Object API and online OA synchronization."""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, NoReturn, TypeAlias
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_serializer,
    model_validator,
)

from app.api.v1.auth import PrincipalDependency
from app.organization_directory_access import (
    DEFAULT_MAX_AGE_S,
)
from app.ports.auth import Principal
from app.ports.capability_gateway import CapabilityGatewayPort, ErrorCode
from app.ports.capability_registry import CapabilityRegistryPort, CapabilitySpec
from app.ports.credential_binding import (
    BackgroundWorkObjectSyncError,
    CredentialCountedFailureCode,
)
from app.ports.dispatch_leader_roster import LeaderRosterReadPort
from app.ports.organization_directory import (
    OrganizationDirectoryPort,
)
from app.ports.request_context import RequestOrgContext
from app.ports.trace import TracePort
from app.ports.work_object import (
    WORK_OBJECT_LIST_FETCH_LIMIT,
    WORK_OBJECT_LIST_LIMIT,
    DispatchKind,
    InternalWorkObjectRecord,
    OAObservation,
    OAPendingWorkSnapshotCollection,
    OASyncClockInvalid,
    OASyncFailureCode,
    OASyncOutcomeUnknown,
    OASyncStatusView,
    OASyncSubject,
    OASyncTicket,
    OAView,
    ReminderChoice,
    WorkObjectHandlingMark,
    WorkObjectRecord,
    WorkObjectStorePort,
)
from app.ports.work_object_attachment import (
    MAX_ATTACHMENT_BYTES,
    AttachmentLinkRequest,
    AttachmentMutationResult,
    AttachmentPurpose,
    AttachmentView,
)
from app.ports.work_object_dispatch import DispatchRequest, HandlingRelationView, WorkDispatchError
from app.ports.work_object_handling import (
    WorkObjectHandlingAction,
    project_handling_action,
)
from app.ports.work_object_lifecycle import (
    CompletionFilter,
    LifecycleCommand,
    LifecycleEventRecord,
    LifecycleOperation,
    LifecycleStatus,
    LifecycleView,
    RemarkHistoryPage,
    RemarkView,
)
from app.ports.work_object_scope import (
    AuthorizedWorkObjectScope,
    compute_visibility_scope,
)
from app.ports.work_object_search import normalize_search_query
from app.work_dispatch.attachments import WorkObjectAttachmentService
from app.work_dispatch.recurrence import WorkObjectSeriesService
from app.work_dispatch.service import WorkDispatchService

_LOGGER = logging.getLogger(__name__)

OA_PENDING_WORKFLOWS_CAPABILITY_ID = "oa.list_pending_workflows"
_REAUTHENTICATION_ERRORS: frozenset[ErrorCode] = frozenset(
    {"identity_unbound", "identity_expired", "identity_revoked"}
)
_BINDING_SCOPE_ERRORS: frozenset[ErrorCode] = frozenset({"needs_binding_scope"})


class _WorkObjectViewBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    work_object_id: str
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
            raise ValueError("handling_capability_id must be null for non-capability actions")
        return self


class OAWorkObjectView(_WorkObjectViewBase):
    assignee_display_name: str
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
    oa_observation: OAObservation


class _InternalWorkObjectViewBase(_WorkObjectViewBase):
    assignee_display_name: str | None
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
    title: str | None
    requirement: str | None
    receipt_requirement: str | None
    owner_department_id: str | None
    initiator_ai_user_id: str | None
    kind: DispatchKind | None
    target_kind: Literal["user", "department"] | None
    reminder_choices: list[ReminderChoice] | None
    reminder_delivery: Literal["not_enabled"] | None
    version: int | None
    created_at: datetime | None
    updated_at: datetime | None

    @field_serializer("due_at", "handling_marked_at", "created_at", "updated_at")
    def _serialize_time(self, value: datetime | None) -> str | None:
        return None if value is None else _utc_text(value)


class DispatchInitialWorkObjectView(_InternalWorkObjectViewBase):
    status: Literal["assigned", "department_pending"] | None


class InternalWorkObjectView(_InternalWorkObjectViewBase):
    status: LifecycleStatus | None
    accepted_at: datetime | None = None
    completed_at: datetime | None = None
    dispatch_model_version: int | None = None
    dispatch_mode: Literal["independent", "collaborative"] | None = None
    completion_mode: Literal["submit_completes", "confirm_required"] | None = None
    withdrawn_at: datetime | None = None
    series_id: UUID | None = None
    relations: list[HandlingRelationView] = Field(default_factory=list)
    frozen: bool = False

    @field_serializer("accepted_at", "completed_at")
    def _serialize_lifecycle_time(self, value: datetime | None) -> str | None:
        return None if value is None else _utc_text(value)


class DispatchWorkObjectsRequest(DispatchRequest):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=False)


class DispatchWorkObjectsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[InternalWorkObjectView]
    created_count: int
    replayed: bool


class LifecycleCommandResponse(BaseModel):
    event: LifecycleEventView
    replayed: bool


class LifecycleEventView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: UUID
    work_object_id: str
    actor_ai_user_id: str
    operation: LifecycleOperation
    from_status: LifecycleStatus
    to_status: LifecycleStatus
    result_version: int
    occurred_at: datetime
    text: str | None
    handling_relation_id: UUID | None = None
    relation_from_status: str | None = None
    relation_to_status: str | None = None
    payload: dict[str, Any] | None = None


LifecycleCommandResponse.model_rebuild()


class LifecycleEventsResponse(BaseModel):
    items: list[LifecycleEventView]
    next_after_version: int | None
    has_more: bool


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


WorkObjectView: TypeAlias = Annotated[
    OAWorkObjectView | InternalWorkObjectView,
    Field(discriminator="state_authority"),
]
_WORK_OBJECT_VIEW_ADAPTER: TypeAdapter[WorkObjectView] = TypeAdapter(WorkObjectView)


class WorkObjectListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[WorkObjectView]
    oa_sync: OASyncStatusView
    limit: int
    limit_exceeded: bool
    next_cursor: str | None = None


class SetHandlingMarkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    mark: WorkObjectHandlingMark


class DepartmentDispatchOption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["department"]
    department_id: str
    department_display_name: str


class UserDispatchOption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["user"]
    directory_user_id: str
    display_name: str = Field(repr=False)
    department_id: str
    department_display_name: str


class DispatchOptionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["department", "user"]
    can_dispatch_departments: bool
    can_dispatch_others: bool
    can_self_dispatch: bool
    items: list[DepartmentDispatchOption] | list[UserDispatchOption] = Field(repr=False)
    next_cursor: str | None
    has_more: bool
    snapshot_version: int = Field(ge=1)
    unselectable_count: int = Field(ge=0)

    @model_validator(mode="after")
    def _consistent(self) -> DispatchOptionsResponse:
        if any(item.kind != self.kind for item in self.items):
            raise ValueError("inconsistent dispatch options")
        if self.has_more != (self.next_cursor is not None):
            raise ValueError("inconsistent dispatch pagination")
        return self


def _without_head_authority(scope: AuthorizedWorkObjectScope) -> AuthorizedWorkObjectScope:
    return scope.model_copy(
        update={
            "responsible_department_ids": frozenset(),
            "directory_snapshot_version": None,
            "valid_until": None,
        }
    )


class WorkObjectService:
    """Application service that keeps transport and persistence behind Ports."""

    def __init__(
        self,
        *,
        store: WorkObjectStorePort,
        gateway: CapabilityGatewayPort,
        capability_registry: CapabilityRegistryPort,
        organization_directory: OrganizationDirectoryPort | None = None,
        leader_roster: LeaderRosterReadPort | None = None,
        trace_port: TracePort | None = None,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
        directory_max_age_s: int = DEFAULT_MAX_AGE_S,
        monotonic: Callable[[], float] = time.monotonic,
        dispatch_service: WorkDispatchService | None = None,
        attachment_service: WorkObjectAttachmentService | None = None,
        series_service: WorkObjectSeriesService | None = None,
    ) -> None:
        self.dispatch = dispatch_service or WorkDispatchService(
            store=store,
            organization_directory=organization_directory,
            leader_roster=leader_roster,
            trace_port=trace_port,
            clock=clock,
            id_factory=id_factory,
            directory_max_age_s=directory_max_age_s,
            monotonic=monotonic,
        )
        self.attachments = attachment_service
        self.series = series_service
        self._directory_max_age_s = directory_max_age_s
        self._monotonic = monotonic
        self._store = store
        self._gateway = gateway
        self._capability_registry = capability_registry
        self._organization_directory = organization_directory
        self._trace_port = trace_port
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or (lambda: uuid4().hex)

    async def list_for_principal(
        self,
        principal: Principal,
        *,
        search_term: str | None = None,
        oa_view: OAView = "active",
        completion: CompletionFilter | None = None,
        queue: str | None = None,
        history_from: datetime | None = None,
        history_to: datetime | None = None,
        cursor: str | None = None,
    ) -> WorkObjectListResponse:
        scope = await self.dispatch.build_scope(principal)
        while True:
            try:
                batch = await self._store.list_with_oa_sync_for_scope(
                    scope,
                    search_term=search_term,
                    oa_view=oa_view,
                    limit=WORK_OBJECT_LIST_FETCH_LIMIT,
                    completion=completion,
                    queue=queue,
                    history_from=history_from,
                    history_to=history_to,
                    cursor=cursor,
                )
                capabilities = await self._projection_capabilities(batch.records)
            except WorkDispatchError as exc:
                if exc.code != "organization_directory_stale" or scope.valid_until is None:
                    raise
                scope = _without_head_authority(scope)
                continue
            if scope.valid_until is not None and self._monotonic() > scope.valid_until:
                scope = _without_head_authority(scope)
                continue
            break
        result = _list_response(batch.records, capabilities, batch.oa_sync)
        if result.limit_exceeded and completion in {"completed", "withdrawn"}:
            last = batch.records[WORK_OBJECT_LIST_LIMIT - 1]
            if isinstance(last, InternalWorkObjectRecord):
                occurred = last.completed_at if completion == "completed" else last.withdrawn_at
                if occurred is not None:
                    result.next_cursor = base64.urlsafe_b64encode(
                        (occurred.isoformat() + "|" + last.work_object_id).encode()
                    ).decode()
        return result

    async def get_for_principal(
        self, work_object_id: str, principal: Principal
    ) -> WorkObjectView | None:
        scope = await self.dispatch.build_scope(principal)
        while True:
            try:
                record = await self._store.get_for_scope(work_object_id, scope)
                if record is None:
                    return None
                view = _view_from_record(record, await self._projection_capabilities([record]))
                if isinstance(view, InternalWorkObjectView) and self.series is not None:
                    view.series_id = await self.series.series_id_for_task(principal, work_object_id)
            except WorkDispatchError as exc:
                if exc.code != "organization_directory_stale" or scope.valid_until is None:
                    raise
                scope = _without_head_authority(scope)
                continue
            if scope.valid_until is not None and self._monotonic() > scope.valid_until:
                scope = _without_head_authority(scope)
                continue
            return view

    async def sync_for_principal(self, principal: Principal) -> WorkObjectListResponse:
        await self._sync_snapshots_for_principal(principal, background=False)
        return await self.list_for_principal(principal, completion=None)

    async def _sync_snapshots_for_principal(
        self,
        principal: Principal,
        *,
        background: bool,
    ) -> None:
        ticket: OASyncTicket | None = None
        if principal.org_ctx.tenant_id == "default":
            try:
                ticket = await self._store.begin_oa_sync(
                    OASyncSubject(tenant_id="default", ai_user_id=principal.ai_user_id),
                    "pending",
                    self._clock(),
                )
            except OASyncOutcomeUnknown:
                _raise_local_sync_error("oa_sync_outcome_unknown", background)
            except Exception:
                _raise_local_sync_error("work_object_sync_failed", background)
        operation_id = self._id_factory()
        try:
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
        except Exception:
            await self._record_sync_failure(ticket, "upstream_unavailable", background)
            _raise_local_sync_error("work_object_sync_failed", background)
        if result.status != "completed" or result.data is None:
            await self._record_sync_failure(ticket, _sync_diagnostic(result.error_code), background)
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
            await self._record_sync_failure(ticket, "invalid_response", background)
            if background:
                raise BackgroundWorkObjectSyncError(
                    authentication_denied=False,
                    failure_code="invalid_response",
                ) from None
            _raise_invalid_sync_payload()
        fetched_at = self._clock()
        try:
            if ticket is None:
                await self._store.upsert_oa_pending_workflows(
                    assignee_ai_user_id=principal.ai_user_id,
                    assignee_display_name=principal.display_name,
                    snapshots=payload.workflows,
                    fetched_at=fetched_at,
                )
                return
            outcome = await self._store.apply_oa_pending_snapshot(
                ticket,
                assignee_display_name=principal.display_name,
                collection=payload,
                fetched_at=fetched_at,
            )
        except OASyncOutcomeUnknown:
            _raise_local_sync_error("oa_sync_outcome_unknown", background)
        except OASyncClockInvalid:
            await self._record_sync_failure(ticket, "clock_invalid", background)
            _raise_local_sync_error("work_object_sync_failed", background)
        except Exception:
            await self._record_sync_failure(ticket, "storage_unavailable", background)
            _raise_local_sync_error("work_object_sync_failed", background)
        if outcome == "superseded":
            _raise_local_sync_error("oa_sync_superseded", background)

    async def _record_sync_failure(
        self,
        ticket: OASyncTicket | None,
        code: OASyncFailureCode,
        background: bool,
    ) -> None:
        if ticket is None:
            return
        try:
            await self._store.finish_oa_sync_failure(
                ticket,
                failure_code=code,
                finished_at=self._clock(),
            )
        except Exception:
            # Callers still raise the original background failure classification.
            # A diagnostic write must not replace authentication/countable errors.
            if not background:
                _raise_local_sync_error("work_object_sync_failed", background=False)

    async def sync_for_background(self, principal: Principal) -> None:
        """Run the same Gateway path with retry-safe failure classification."""

        await self._sync_snapshots_for_principal(principal, background=True)

    async def set_handling_mark_for_principal(
        self,
        work_object_id: str,
        principal: Principal,
        mark: WorkObjectHandlingMark,
    ) -> WorkObjectView | None:
        scope = self._handling_scope(principal)
        record = await self._store.set_handling_mark_for_scope(
            work_object_id,
            scope,
            mark,
            marked_at=self._clock(),
        )
        if record is None:
            return None
        capabilities = await self._capability_registry.list(status="active")
        return _view_from_record(record, capabilities)

    def _handling_scope(self, principal: Principal) -> AuthorizedWorkObjectScope:
        return compute_visibility_scope(
            principal_tenant_id=principal.org_ctx.tenant_id,
            principal_ai_user_id=principal.ai_user_id,
            principal_department_id=None,
        )

    async def _projection_capabilities(
        self, records: list[WorkObjectRecord]
    ) -> list[CapabilitySpec]:
        if all(
            record.state_authority == "internal" and record.source_kind == "manual_dispatch"
            for record in records
        ):
            return []
        return await self._capability_registry.list(status="active")

    async def dispatch_for_principal(
        self,
        principal: Principal,
        body: DispatchWorkObjectsRequest,
        key: UUID,
    ) -> DispatchWorkObjectsResponse:
        result = await self.dispatch.dispatch_for_principal(
            principal,
            request=body,
            idempotency_key=key,
        )
        items = [
            InternalWorkObjectView.model_validate_json(json.dumps(item)) for item in result.items
        ]
        return DispatchWorkObjectsResponse(
            items=items, created_count=result.created_count, replayed=result.replayed
        )


class ProtectedWorkRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                result = await original(request)
                result.headers["Cache-Control"] = "no-store"
                return result
            except WorkDispatchError as exc:
                _LOGGER.warning("work_dispatch stage=http code=%s", exc.code)
                if self.name in {"dispatch_work_objects", "list_dispatch_options"}:
                    _raise_dispatch_error(_DISPATCH_HTTP.get(exc.code, 503), exc.code)
                _lifecycle_error(exc.code)
            except RequestValidationError:
                if self.name == "set_work_object_handling_mark":
                    raise
                if self.name == "list_work_objects":
                    completion_values = request.query_params.getlist("completion")
                    if not completion_values or (
                        len(completion_values) == 1
                        and completion_values[0] in {"active", "completed", "withdrawn"}
                    ):
                        raise
                code = {
                    "dispatch_work_objects": "dispatch_request_invalid",
                    "list_dispatch_options": "dispatch_options_request_invalid",
                    "upload_attachment": "work_object_attachment_invalid",
                }.get(self.name, "work_object_lifecycle_request_invalid")
                if "series" in self.name:
                    code = "work_object_series_request_invalid"
                if self.name in {"dispatch_work_objects", "list_dispatch_options"}:
                    _raise_dispatch_error(422, code)
                _lifecycle_error(code)
            except HTTPException as exc:
                exc.headers = {**(exc.headers or {}), "Cache-Control": "no-store"}
                raise

        return handle


def request_key(request: Request) -> UUID:
    values = request.headers.getlist("idempotency-key")
    try:
        if len(values) != 1:
            raise ValueError
        return UUID(values[0])
    except ValueError:
        _lifecycle_error("idempotency_key_invalid")


def request_etag(request: Request) -> str:
    values = request.headers.getlist("if-match")
    if not values:
        _lifecycle_error("work_object_precondition_required")
    if len(values) != 1 or re.fullmatch(r'"(?:wolc|worm|wosr)-[0-9a-f]{64}"', values[0]) is None:
        _lifecycle_error("work_object_lifecycle_request_invalid")
    return values[0]


_COMMAND_ADAPTER: TypeAdapter[LifecycleCommand] = TypeAdapter(LifecycleCommand)


def _command_body(value: Any) -> Any:
    return _COMMAND_ADAPTER.validate_json(json.dumps(value))


def _attachment_body(value: Any) -> Any:
    return AttachmentLinkRequest.model_validate_json(json.dumps(value))


LifecycleRequest: TypeAlias = Annotated[LifecycleCommand, BeforeValidator(_command_body)]
AttachmentRequest: TypeAlias = Annotated[AttachmentLinkRequest, BeforeValidator(_attachment_body)]


class EditRemarkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(max_length=2000)


class RemarkResponse(BaseModel):
    remark: RemarkView
    replayed: bool


_IDEMPOTENCY_PARAMETER = {
    "name": "Idempotency-Key",
    "in": "header",
    "required": True,
    "schema": {"type": "string", "format": "uuid"},
}
_VERSION_PARAMETER = {
    "name": "If-Match",
    "in": "header",
    "required": True,
    "schema": {"type": "string", "pattern": '"wolc-[0-9a-f]{64}"'},
}


def mutation_schema(*, versioned: bool = True, prefix: str = "wolc") -> dict[str, Any]:
    version = {
        **_VERSION_PARAMETER,
        "schema": {"type": "string", "pattern": f'"{prefix}-[0-9a-f]{{64}}"'},
    }
    return {"parameters": [_IDEMPOTENCY_PARAMETER, *([version] if versioned else [])]}


def make_router(
    service: WorkObjectService | None, require_principal: PrincipalDependency
) -> APIRouter:
    router = APIRouter(route_class=ProtectedWorkRoute)

    def configured() -> WorkObjectService:
        if service is None:
            _lifecycle_error("work_object_unavailable")
        return service

    def attachments() -> WorkObjectAttachmentService:
        result = configured().attachments
        if result is None:
            _lifecycle_error("work_object_unavailable")
        return result

    errors: dict[int | str, dict[str, Any]] = {
        **{
            code: {
                "model": WorkObjectError,
                "headers": {"Cache-Control": {"schema": {"type": "string", "const": "no-store"}}},
            }
            for code in (401, 403, 404, 409, 412, 413, 422, 428, 503)
        },
    }

    @router.get(
        "",
        response_model=WorkObjectListResponse,
        responses={
            **errors,
            200: {
                "headers": {"Cache-Control": {"schema": {"type": "string", "const": "no-store"}}}
            },
            422: {
                "description": (
                    "Invalid completion uses WorkObjectError without input echo and with "
                    "Cache-Control: no-store. Other query validation retains HTTPValidationError."
                ),
                "content": {
                    "application/json": {
                        "schema": {
                            "oneOf": [
                                {"$ref": "#/components/schemas/WorkObjectError"},
                                {"$ref": "#/components/schemas/HTTPValidationError"},
                            ]
                        }
                    }
                },
                "headers": {
                    "Cache-Control": {
                        "description": "Present on fixed completion validation failures.",
                        "schema": {"type": "string", "const": "no-store"},
                    }
                },
            },
        },
    )
    async def list_work_objects(
        request: Request,
        q: str | None = None,
        oa_view: OAView = "active",
        completion: CompletionFilter | None = None,
        queue: Literal[
            "mine_sent", "department_unassigned", "department_review", "awaiting_acceptance"
        ]
        | None = None,
        history_from: datetime | None = None,
        history_to: datetime | None = None,
        cursor: Annotated[str | None, Query(max_length=2048)] = None,
        principal: Principal = Depends(require_principal),
    ) -> WorkObjectListResponse:
        if len(request.query_params.getlist("completion")) > 1:
            _lifecycle_error("work_object_lifecycle_request_invalid")
        if any(t is not None and t.utcoffset() is None for t in (history_from, history_to)):
            _lifecycle_error("work_object_lifecycle_request_invalid")
        if history_from is not None and history_to is not None and history_from >= history_to:
            _lifecycle_error("work_object_lifecycle_request_invalid")
        return await configured().list_for_principal(
            principal,
            search_term=normalize_search_query(q),
            oa_view=oa_view,
            completion=completion,
            queue=queue,
            history_from=history_from,
            history_to=history_to,
            cursor=cursor,
        )

    @router.post("/sync", response_model=WorkObjectListResponse)
    async def sync_work_objects(
        principal: Principal = Depends(require_principal),
    ) -> WorkObjectListResponse:
        return await configured().sync_for_principal(principal)

    @router.post(
        "/dispatch",
        response_model=DispatchWorkObjectsResponse,
        status_code=201,
        responses={
            **errors,
            200: {
                "model": DispatchWorkObjectsResponse,
                "headers": {"Cache-Control": {"schema": {"const": "no-store", "type": "string"}}},
            },
        },
        openapi_extra=mutation_schema(versioned=False),
    )
    async def dispatch_work_objects(
        body: DispatchWorkObjectsRequest,
        request: Request,
        response: Response,
        principal: Principal = Depends(require_principal),
    ) -> DispatchWorkObjectsResponse:
        result = await configured().dispatch_for_principal(principal, body, request_key(request))
        response.status_code = 200 if result.replayed else 201
        return result

    @router.get(
        "/dispatch-options",
        response_model=DispatchOptionsResponse,
        responses={
            **errors,
            200: {
                "headers": {"Cache-Control": {"schema": {"const": "no-store", "type": "string"}}}
            },
        },
    )
    async def list_dispatch_options(
        request: Request,
        kind: Literal["user", "department"],
        query: Annotated[str, Query(max_length=200)] = "",
        cursor: Annotated[str | None, Query(max_length=2048)] = None,
        department_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        principal: Principal = Depends(require_principal),
        configured_service: WorkObjectService = Depends(configured),
    ) -> DispatchOptionsResponse:
        active = configured_service.dispatch
        params = request.query_params
        if (
            any(
                k not in {"kind", "query", "cursor", "department_id", "limit"}
                or len(params.getlist(k)) != 1
                for k in params
            )
            or (kind == "department" and department_id is not None)
            or ("cursor" in params and not cursor)
            or ("limit" in params and params["limit"] != str(limit))
        ):
            _raise_dispatch_error(422, "dispatch_options_request_invalid")
        result = await active.list_dispatch_options(
            principal,
            kind=kind,
            query=query,
            cursor=cursor,
            department_id=department_id,
            limit=limit,
        )
        return DispatchOptionsResponse.model_validate(result.model_dump())

    @router.post(
        "/attachments",
        response_model=AttachmentView,
        status_code=201,
        responses={
            **errors,
            201: {
                "headers": {"Cache-Control": {"schema": {"const": "no-store", "type": "string"}}}
            },
        },
        openapi_extra={
            **mutation_schema(versioned=False),
            "requestBody": {
                "required": True,
                "content": {
                    "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
                },
            },
        },
    )
    async def upload_attachment(
        request: Request,
        filename: Annotated[str, Query(min_length=1, max_length=255)],
        media_type: Annotated[str, Query(min_length=1, max_length=128)],
        purpose: AttachmentPurpose,
        principal: Principal = Depends(require_principal),
    ) -> AttachmentView:
        key = request_key(request)
        payload = bytearray()
        async for chunk in request.stream():
            if len(payload) + len(chunk) > MAX_ATTACHMENT_BYTES:
                _lifecycle_error("work_object_attachment_too_large")
            payload.extend(chunk)
        return await attachments().upload(
            principal,
            filename=filename,
            media_type=media_type,
            purpose=purpose,
            payload=bytes(payload),
            idempotency_key=key,
        )

    @router.get(
        "/{work_object_id}/lifecycle",
        response_model=LifecycleView,
        responses={
            **errors,
            200: {
                "model": LifecycleView,
                "headers": {
                    "Cache-Control": {"schema": {"type": "string", "const": "no-store"}},
                    "ETag": {"schema": {"type": "string", "pattern": '"wolc-[0-9a-f]{64}"'}},
                },
            },
        },
    )
    async def get_work_object_lifecycle(
        work_object_id: str, response: Response, principal: Principal = Depends(require_principal)
    ) -> LifecycleView:
        result = await configured().dispatch.get_lifecycle_for_principal(work_object_id, principal)
        response.headers["ETag"] = result.etag
        return result

    @router.post(
        "/{work_object_id}/lifecycle/commands",
        response_model=LifecycleCommandResponse,
        responses={
            **errors,
            200: {
                "model": LifecycleCommandResponse,
                "headers": {
                    "Cache-Control": {"schema": {"type": "string", "const": "no-store"}},
                    "ETag": {"schema": {"type": "string", "pattern": '"wolc-[0-9a-f]{64}"'}},
                },
            },
        },
        openapi_extra=mutation_schema(),
    )
    async def command_work_object_lifecycle(
        work_object_id: str,
        body: LifecycleRequest,
        request: Request,
        response: Response,
        principal: Principal = Depends(require_principal),
    ) -> LifecycleCommandResponse:
        active = configured().dispatch
        try:
            command_key = request_key(request)
        except HTTPException:
            _lifecycle_error("work_object_lifecycle_request_invalid")
        result = await active.command_lifecycle_for_principal(
            work_object_id,
            principal,
            command=body,
            if_match=request_etag(request),
            idempotency_key=command_key,
        )
        response.headers["ETag"] = (
            await active.get_lifecycle_for_principal(work_object_id, principal)
        ).etag
        return LifecycleCommandResponse(
            event=_lifecycle_event_view(result.event), replayed=result.replayed
        )

    @router.get(
        "/{work_object_id}/lifecycle/events",
        response_model=LifecycleEventsResponse,
        responses={
            **errors,
            200: {
                "headers": {"Cache-Control": {"schema": {"const": "no-store", "type": "string"}}}
            },
        },
    )
    async def list_work_object_lifecycle_events(
        work_object_id: str,
        request: Request,
        after_version: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        principal: Principal = Depends(require_principal),
    ) -> LifecycleEventsResponse:
        params = request.query_params
        if any(k not in {"after_version", "limit"} or len(params.getlist(k)) != 1 for k in params):
            _lifecycle_error("work_object_lifecycle_request_invalid")
        if any(
            params[k] != str(v)
            for k, v in (("after_version", after_version), ("limit", limit))
            if k in params
        ):
            _lifecycle_error("work_object_lifecycle_request_invalid")
        result = await configured().dispatch.list_lifecycle_events_for_principal(
            work_object_id, principal, after_version=after_version, limit=limit
        )
        return LifecycleEventsResponse(
            items=[_lifecycle_event_view(e) for e in result.items],
            next_after_version=result.next_after_version,
            has_more=result.next_after_version is not None,
        )

    @router.get(
        "/{work_object_id}/remarks",
        response_model=RemarkView,
        responses={
            **errors,
            200: {
                "model": RemarkView,
                "headers": {
                    "Cache-Control": {"schema": {"type": "string", "const": "no-store"}},
                    "ETag": {"schema": {"type": "string", "pattern": '"worm-[0-9a-f]{64}"'}},
                },
            },
        },
    )
    async def get_remark(
        work_object_id: str, response: Response, principal: Principal = Depends(require_principal)
    ) -> RemarkView:
        result = await configured().dispatch.get_remark(work_object_id, principal)
        response.headers["ETag"] = result.etag
        return result

    @router.patch(
        "/{work_object_id}/remarks",
        response_model=RemarkResponse,
        responses={
            **errors,
            200: {
                "model": RemarkResponse,
                "headers": {
                    "Cache-Control": {"schema": {"type": "string", "const": "no-store"}},
                    "ETag": {"schema": {"type": "string", "pattern": '"worm-[0-9a-f]{64}"'}},
                },
            },
        },
        openapi_extra=mutation_schema(prefix="worm"),
    )
    async def edit_remark(
        work_object_id: str,
        body: EditRemarkRequest,
        request: Request,
        response: Response,
        principal: Principal = Depends(require_principal),
    ) -> RemarkResponse:
        result = await configured().dispatch.edit_remark(
            work_object_id,
            principal,
            text=body.text,
            if_match=request_etag(request),
            idempotency_key=request_key(request),
        )
        response.headers["ETag"] = result.remark.etag
        return RemarkResponse(remark=result.remark, replayed=result.replayed)

    @router.get(
        "/{work_object_id}/remarks/history",
        response_model=RemarkHistoryPage,
        responses={
            **errors,
            200: {
                "headers": {"Cache-Control": {"schema": {"const": "no-store", "type": "string"}}}
            },
        },
    )
    async def list_remark_history(
        work_object_id: str,
        cursor: Annotated[str | None, Query(max_length=2048)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        principal: Principal = Depends(require_principal),
    ) -> RemarkHistoryPage:
        return await configured().dispatch.list_remark_history(
            work_object_id, principal, cursor=cursor, limit=limit
        )

    @router.get(
        "/{work_object_id}/attachments",
        response_model=list[AttachmentView],
        responses={
            **errors,
            200: {
                "headers": {"Cache-Control": {"schema": {"const": "no-store", "type": "string"}}}
            },
        },
    )
    async def list_attachments(
        work_object_id: str, principal: Principal = Depends(require_principal)
    ) -> list[AttachmentView]:
        return await attachments().list_for_task(principal, work_object_id)

    @router.post(
        "/{work_object_id}/attachments",
        response_model=AttachmentMutationResult,
        responses={
            **errors,
            200: {
                "model": AttachmentMutationResult,
                "headers": {
                    "Cache-Control": {"schema": {"type": "string", "const": "no-store"}},
                    "ETag": {"schema": {"type": "string", "pattern": '"wolc-[0-9a-f]{64}"'}},
                },
            },
        },
        openapi_extra=mutation_schema(),
    )
    async def link_attachments(
        work_object_id: str,
        body: AttachmentRequest,
        request: Request,
        response: Response,
        principal: Principal = Depends(require_principal),
    ) -> AttachmentMutationResult:
        result = await attachments().link(
            principal,
            work_object_id,
            request=body,
            if_match=request_etag(request),
            idempotency_key=request_key(request),
        )
        response.headers["ETag"] = result.etag
        return result

    @router.delete(
        "/{work_object_id}/attachments/{attachment_id}",
        response_model=AttachmentMutationResult,
        responses={
            **errors,
            200: {
                "model": AttachmentMutationResult,
                "headers": {
                    "Cache-Control": {"schema": {"type": "string", "const": "no-store"}},
                    "ETag": {"schema": {"type": "string", "pattern": '"wolc-[0-9a-f]{64}"'}},
                },
            },
        },
        openapi_extra=mutation_schema(),
    )
    async def detach_attachment(
        work_object_id: str,
        attachment_id: UUID,
        request: Request,
        response: Response,
        principal: Principal = Depends(require_principal),
    ) -> AttachmentMutationResult:
        result = await attachments().detach(
            principal,
            work_object_id,
            attachment_id,
            if_match=request_etag(request),
            idempotency_key=request_key(request),
        )
        response.headers["ETag"] = result.etag
        return result

    @router.get(
        "/{work_object_id}/attachments/{attachment_id}/content",
        response_class=Response,
        responses={
            **errors,
            200: {
                "content": {
                    "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
                }
            },
        },
    )
    async def download_attachment(
        work_object_id: str, attachment_id: UUID, principal: Principal = Depends(require_principal)
    ) -> Response:
        from urllib.parse import quote

        result = await attachments().download(principal, work_object_id, attachment_id)
        return Response(
            result.payload,
            media_type=result.attachment.media_type,
            headers={
                "Content-Disposition": "attachment; filename*=UTF-8''"
                + quote(result.attachment.filename, safe=""),
                "Cache-Control": "no-store",
            },
        )

    @router.get(
        "/{work_object_id}",
        response_model=WorkObjectView,
        responses={
            **errors,
            200: {
                "headers": {"Cache-Control": {"schema": {"const": "no-store", "type": "string"}}}
            },
        },
    )
    async def get_work_object(
        work_object_id: str, response: Response, principal: Principal = Depends(require_principal)
    ) -> WorkObjectView:
        result = await configured().get_for_principal(work_object_id, principal)
        if result is None:
            _raise_not_found()
        if isinstance(result, InternalWorkObjectView) and result.version is not None:
            response.headers["ETag"] = f'"wo:{result.work_object_id}:{result.version}"'
        return result

    @router.patch("/{work_object_id}/handling-mark", response_model=WorkObjectView)
    async def set_work_object_handling_mark(
        work_object_id: str,
        body: SetHandlingMarkRequest,
        principal: Principal = Depends(require_principal),
    ) -> WorkObjectView:
        result = await configured().set_handling_mark_for_principal(
            work_object_id, principal, body.mark
        )
        if result is None:
            _raise_not_found()
        return result

    return router


def _lifecycle_event_view(event: LifecycleEventRecord) -> LifecycleEventView:
    return LifecycleEventView.model_validate(
        event.model_dump(
            exclude={
                "tenant_id",
                "idempotency_key",
                "request_fingerprint",
                "event_model_version",
                "visibility",
            }
        )
    )


def _list_response(
    records: list[WorkObjectRecord],
    capabilities: list[CapabilitySpec],
    oa_sync: OASyncStatusView,
) -> WorkObjectListResponse:
    limit_exceeded = len(records) > WORK_OBJECT_LIST_LIMIT
    return WorkObjectListResponse(
        items=[
            _view_from_record(record, capabilities) for record in records[:WORK_OBJECT_LIST_LIMIT]
        ],
        limit=WORK_OBJECT_LIST_LIMIT,
        limit_exceeded=limit_exceeded,
        oa_sync=oa_sync,
    )


def _view_from_record(
    record: WorkObjectRecord,
    capabilities: list[CapabilitySpec],
) -> WorkObjectView:
    manual = record.state_authority == "internal" and record.source_kind == "manual_dispatch"
    view_only = manual or (
        record.state_authority == "external_snapshot"
        and record.oa_observation.pending_state == "unconfirmed"
    )
    handling_capability = (
        None
        if view_only
        else _resolve_handling_capability(
            record=record,
            capabilities=capabilities,
        )
    )
    handling_action = (
        "view_only"
        if view_only
        else project_handling_action(
            state_authority=record.state_authority,
            source_system=record.source_system,
            handling_mark=record.handling_mark,
            capability=handling_capability,
        )
    )
    handling_capability_id = (
        handling_capability.capability_id
        if handling_capability is not None and handling_action in {"ai_draft", "self_serve"}
        else None
    )
    view_payload = record.model_dump(
        exclude={
            "assignee_ai_user_id",
            "assignee_directory_user_id",
            "accepted_by_ai_user_id",
            "completed_by_ai_user_id",
            "withdrawn_by_ai_user_id",
            "tenant_id",
            "handling_marked_by_ai_user_id",
            "created_at",
            "updated_at",
        }
    )
    if isinstance(record, InternalWorkObjectRecord):
        view_payload.update(
            created_at=record.created_at,
            updated_at=record.updated_at,
            reminder_delivery="not_enabled" if manual else None,
            frozen=record.status in {"completed", "withdrawn"},
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
            and selector.source_workflow_type_id == record.source_workflow_type_id
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


def _sync_diagnostic(code: ErrorCode | None) -> OASyncFailureCode:
    if code in _REAUTHENTICATION_ERRORS:
        return "reauthentication_required"
    if code in _BINDING_SCOPE_ERRORS:
        return "binding_scope_required"
    if code in {"policy_denied", "upstream_permission_denied"}:
        return "forbidden"
    if code in {
        "adapter_payload_invalid",
        "adapter_missing_required_field",
        "adapter_empty_response",
    }:
        return "invalid_response"
    return "upstream_unavailable"


def _raise_local_sync_error(code: str, background: bool) -> NoReturn:
    if background:
        raise BackgroundWorkObjectSyncError(
            authentication_denied=False, failure_code=None
        ) from None
    messages = {
        "oa_sync_superseded": "A newer OA synchronization has started; refresh the stored result.",
        "oa_sync_outcome_unknown": (
            "OA synchronization outcome is not confirmed; refresh the stored result."
        ),
        "work_object_sync_failed": "Work Object synchronization failed; stored data is unchanged.",
    }
    raise HTTPException(
        status_code=409 if code == "oa_sync_superseded" else 503,
        detail={"code": code, "message": messages[code]},
    ) from None


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
    failure_code = countable_errors.get(error_code) if error_code is not None else None
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


class WorkObjectErrorDetail(BaseModel):
    code: str
    message: str


class WorkObjectError(BaseModel):
    detail: WorkObjectErrorDetail


_DISPATCH_HTTP = {
    "work_object_not_found": 404,
    "work_object_series_not_found": 404,
    "dispatch_target_not_found": 404,
    "directory_scope_denied": 403,
    "directory_membership_missing": 403,
    "directory_membership_ambiguous": 403,
    "cross_department_dispatch_denied": 403,
    "not_department_head": 403,
    "dispatch_target_department_missing": 422,
    "dispatch_target_membership_missing": 422,
    "dispatch_options_cursor_invalid": 422,
    "dispatch_target_membership_ambiguous": 403,
    "dispatch_target_forbidden": 403,
    "work_object_action_forbidden": 403,
    "dispatch_department_head_missing": 409,
    "dispatch_department_head_ambiguous": 409,
    "work_object_transition_invalid": 409,
    "work_object_lifecycle_unsupported": 409,
    "work_object_legacy_conversion_blocked": 409,
    "idempotency_key_reused": 409,
    "organization_directory_snapshot_changed": 409,
    "work_object_version_conflict": 412,
    "work_object_precondition_required": 428,
    "work_object_attachment_too_large": 413,
    "organization_directory_missing": 503,
    "organization_directory_stale": 503,
    "organization_directory_unavailable": 503,
    "leader_roster_unavailable": 503,
    "work_object_unavailable": 503,
    "work_object_store_unavailable": 503,
    "work_object_audit_unavailable": 503,
    "work_object_lifecycle_failed": 503,
    "work_object_dispatch_failed": 503,
    "work_object_attachment_invalid": 422,
    "work_object_attachment_limit_exceeded": 422,
    "work_object_series_request_invalid": 422,
    "work_object_series_local_time_invalid": 422,
    "work_object_lifecycle_request_invalid": 422,
    "dispatch_options_request_invalid": 422,
    "dispatch_request_invalid": 422,
    "idempotency_key_invalid": 422,
}


_LIFECYCLE_MESSAGES = {
    "work_object_lifecycle_request_invalid": "Invalid work object lifecycle request.",
    "work_object_precondition_required": "A current work object lifecycle ETag is required.",
    "work_object_not_found": "Work Object was not found.",
    "work_object_lifecycle_unsupported": "Work object lifecycle is not supported for this object.",
    "directory_membership_missing": "Organization directory membership is missing.",
    "directory_membership_ambiguous": "Organization directory membership is ambiguous.",
    "work_object_action_forbidden": "Work object action is not permitted.",
    "work_object_transition_invalid": "Work object state does not permit this action.",
    "idempotency_key_reused": "Idempotency key was used for a different request.",
    "work_object_version_conflict": "Work object lifecycle has changed.",
    "organization_directory_missing": "Organization directory has no trusted snapshot.",
    "organization_directory_stale": "Organization directory snapshot is stale.",
    "organization_directory_unavailable": "Organization directory is unavailable.",
    "leader_roster_unavailable": "Leader roster is unavailable.",
    "work_object_unavailable": "Work Object provider is not configured.",
    "work_object_audit_unavailable": "Work object audit is unavailable.",
    "work_object_lifecycle_failed": "Work object lifecycle operation failed.",
}


def _lifecycle_error(code: str) -> NoReturn:
    code = {
        "precondition_required": "work_object_precondition_required",
        "version_conflict": "work_object_version_conflict",
        "transition_invalid": "work_object_transition_invalid",
    }.get(code, code)
    raise HTTPException(
        _DISPATCH_HTTP.get(code, 503),
        detail={
            "code": code,
            "message": _LIFECYCLE_MESSAGES.get(code, "Work Object request could not be completed."),
        },
        headers={"Cache-Control": "no-store"},
    )


def _raise_dispatch_error(http_status: int, code: str) -> NoReturn:
    code = {
        "dispatch_target_department_missing": "dispatch_target_not_found",
        "dispatch_target_membership_missing": "dispatch_target_not_found",
    }.get(code, code)
    if code == "dispatch_target_not_found":
        http_status = 404
    messages = {
        "dispatch_options_request_invalid": "Dispatch options request is invalid.",
        "organization_directory_snapshot_changed": (
            "Organization directory has changed; reload the selection."
        ),
        "organization_directory_missing": (
            "Organization directory has not completed its first successful synchronization."
        ),
        "organization_directory_stale": (
            "Organization directory is stale; directory-dependent authorization is unavailable."
        ),
        "organization_directory_unavailable": "Organization directory is unavailable.",
        "leader_roster_unavailable": "Leader roster is unavailable.",
        "directory_scope_denied": "Directory access is not permitted for this identity.",
        "work_object_unavailable": "Work Object provider is not configured.",
        "work_object_dispatch_failed": "Dispatch could not be completed; retry with the same key.",
        "work_object_audit_unavailable": "Dispatch audit is unavailable; retry with the same key.",
        "dispatch_target_not_found": "Dispatch target was not found.",
        "idempotency_key_reused": "Idempotency key belongs to a different request.",
        "idempotency_key_invalid": "One UUID idempotency key is required.",
        "dispatch_request_invalid": "Dispatch request is invalid.",
    }
    raise HTTPException(
        http_status,
        detail={
            "code": code,
            "message": messages.get(code, "Work Object operation is not permitted."),
        },
        headers={"Cache-Control": "no-store"},
    )
