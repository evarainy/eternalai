"""Authenticated Work Object API and online OA synchronization."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, NoReturn, TypeAlias
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_serializer,
    field_validator,
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
from app.ports.organization_directory import (
    OrganizationDepartment,
    OrganizationDirectoryPort,
    OrganizationUserMembership,
)
from app.ports.request_context import RequestOrgContext
from app.ports.trace import TraceEvent, TracePort
from app.ports.work_object import (
    REMINDER_CHOICES,
    WORK_OBJECT_LIST_FETCH_LIMIT,
    WORK_OBJECT_LIST_LIMIT,
    DispatchKind,
    DispatchReceipt,
    InternalWorkObjectRecord,
    OAPendingWorkSnapshot,
    OAPendingWorkSnapshotCollection,
    ReminderChoice,
    WorkObjectHandlingMark,
    WorkObjectRecord,
    WorkObjectStorePort,
)
from app.ports.work_object_handling import (
    WorkObjectHandlingAction,
    project_handling_action,
)
from app.ports.work_object_scope import (
    AuthorizedWorkObjectScope,
    DispatchAuthorizationDecision,
    compute_dispatch_authorization,
    compute_visibility_scope,
)
from app.ports.work_object_search import SEARCH_WHITESPACE, normalize_search_query

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


class InternalWorkObjectView(_WorkObjectViewBase):
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
    status: Literal["assigned", "department_pending"] | None
    reminder_choices: list[ReminderChoice] | None
    reminder_delivery: Literal["not_enabled"] | None
    version: int | None
    created_at: datetime | None
    updated_at: datetime | None

    @field_serializer("due_at", "handling_marked_at", "created_at", "updated_at")
    def _serialize_time(self, value: datetime | None) -> str | None:
        return None if value is None else _utc_text(value)


class UserDispatchTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["user"]
    directory_user_id: str = Field(min_length=1, max_length=128)
    department_id: str = Field(min_length=1, max_length=128)

    @field_validator("directory_user_id", "department_id")
    @classmethod
    def _nonblank_id(cls, value: str) -> str:
        if not value.strip(SEARCH_WHITESPACE):
            raise ValueError("target ID must not be blank")
        return value


class DepartmentDispatchTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["department"]
    department_id: str = Field(min_length=1, max_length=128)

    @field_validator("department_id")
    @classmethod
    def _nonblank_id(cls, value: str) -> str:
        if not value.strip(SEARCH_WHITESPACE):
            raise ValueError("target ID must not be blank")
        return value


DispatchTarget: TypeAlias = Annotated[
    UserDispatchTarget | DepartmentDispatchTarget,
    Field(discriminator="kind"),
]


class DispatchWorkObjectsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: DispatchKind
    title: str = Field(min_length=1, max_length=200)
    requirement: str = Field(max_length=10000)
    receipt_requirement: str = Field(max_length=2000)
    due_at: datetime | None
    reminder_choices: list[ReminderChoice] = Field(max_length=4)
    targets: list[DispatchTarget] = Field(min_length=1, max_length=100)

    @field_validator("title", "requirement", "receipt_requirement", mode="before")
    @classmethod
    def _trim_text(cls, value: object) -> object:
        return value.strip(SEARCH_WHITESPACE) if isinstance(value, str) else value

    @field_validator("due_at", mode="before")
    @classmethod
    def _aware_datetime(cls, value: object) -> datetime | None:
        if value is None:
            return None
        if (
            not isinstance(value, str)
            or re.fullmatch(
                r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})",
                value,
            )
            is None
        ):
            raise ValueError("due_at requires an RFC3339 timezone")
        parsed = datetime.fromisoformat(value.upper())
        return parsed.astimezone(UTC)

    @model_validator(mode="after")
    def _canonical_collections(self) -> DispatchWorkObjectsRequest:
        self.reminder_choices = [
            choice for choice in REMINDER_CHOICES if choice in self.reminder_choices
        ]
        if self.due_at is None and self.reminder_choices:
            raise ValueError("reminders require due_at")
        unique = {_target_key(target): target for target in self.targets}
        self.targets = [unique[key] for key in sorted(unique)]
        return self

    def canonical_bytes(self) -> bytes:
        value = self.model_dump(mode="json")
        value["due_at"] = None if self.due_at is None else _utc_text(self.due_at)
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )


class DispatchWorkObjectsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[InternalWorkObjectView] = Field(min_length=1, max_length=100)
    created_count: int
    replayed: bool

    @model_validator(mode="after")
    def _complete_snapshots(self) -> DispatchWorkObjectsResponse:
        if self.created_count != len(self.items):
            raise ValueError("dispatch count must match the snapshot")
        required = (
            "title",
            "requirement",
            "receipt_requirement",
            "owner_department_id",
            "initiator_ai_user_id",
            "kind",
            "target_kind",
            "status",
            "reminder_choices",
            "reminder_delivery",
            "version",
            "created_at",
            "updated_at",
        )
        for item in self.items:
            if any(getattr(item, name) is None for name in required):
                raise ValueError("dispatch snapshots must be complete")
            if (
                item.source_system != "eternalai"
                or item.source_kind != "manual_dispatch"
                or item.version != 1
                or item.created_at != item.updated_at
                or item.handling_action != "view_only"
                or item.handling_capability_id is not None
                or item.handling_mark is not None
                or item.handling_marked_at is not None
                or item.task_record_id is not None
            ):
                raise ValueError("dispatch snapshot must preserve the published initial state")
            if item.target_kind == "user":
                if item.status != "assigned" or item.assignee_display_name is not None:
                    raise ValueError("user dispatch snapshot is inconsistent")
            elif item.status != "department_pending" or item.assignee_display_name is None:
                raise ValueError("department dispatch snapshot is inconsistent")
        return self


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _target_key(target: UserDispatchTarget | DepartmentDispatchTarget) -> tuple[str, str, str]:
    return (
        target.kind,
        target.department_id,
        (target.directory_user_id if isinstance(target, UserDispatchTarget) else ""),
    )


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
        organization_directory: OrganizationDirectoryPort | None = None,
        trace_port: TracePort | None = None,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
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
    ) -> WorkObjectListResponse:
        scope = await self._visibility_scope(principal)
        records = await self._store.list_for_scope(
            scope,
            search_term=search_term,
            limit=WORK_OBJECT_LIST_FETCH_LIMIT,
        )
        capabilities = await self._projection_capabilities(records)
        return _list_response(records, capabilities)

    async def get_for_principal(
        self,
        work_object_id: str,
        principal: Principal,
    ) -> WorkObjectView | None:
        scope = await self._visibility_scope(principal)
        record = await self._store.get_for_scope(
            work_object_id,
            scope,
        )
        if record is None:
            return None
        capabilities = await self._projection_capabilities([record])
        return _view_from_record(record, capabilities)

    async def _visibility_scope(self, principal: Principal) -> AuthorizedWorkObjectScope:
        department_id = None
        join_key = principal.org_ctx.directory_user_id
        if self._organization_directory is not None and join_key:
            try:
                memberships = await self._organization_directory.list_user_memberships(join_key)
                # No inferred primary department for zero or multiple memberships.
                if len(memberships) == 1 and memberships[0].user_id == join_key:
                    department = await self._organization_directory.get_department(
                        memberships[0].department_id
                    )
                    if (
                        department is not None
                        and department.department_id == memberships[0].department_id
                    ):
                        department_id = department.department_id
            except Exception:
                # Driver exception text may include query parameters (the join key).
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "code": "organization_directory_unavailable",
                        "message": "Organization directory is unavailable.",
                    },
                ) from None
        return compute_visibility_scope(
            principal_tenant_id=principal.org_ctx.tenant_id,
            principal_ai_user_id=principal.ai_user_id,
            principal_department_id=department_id,
        )

    async def sync_for_principal(self, principal: Principal) -> WorkObjectListResponse:
        await self._sync_snapshots_for_principal(principal, background=False)
        return await self.list_for_principal(principal)

    async def _sync_snapshots_for_principal(
        self,
        principal: Principal,
        *,
        background: bool,
    ) -> None:
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
        try:
            await self._store.upsert_oa_pending_workflows(
                assignee_ai_user_id=principal.ai_user_id,
                assignee_display_name=principal.display_name,
                snapshots=[
                    OAPendingWorkSnapshot.model_validate(snapshot.model_dump(), strict=True)
                    for snapshot in payload.workflows
                ],
                fetched_at=fetched_at,
            )
        except Exception:
            if background:
                raise BackgroundWorkObjectSyncError(
                    authentication_denied=False,
                    failure_code=None,
                ) from None
            _raise_dispatch_error(503, "work_object_sync_failed")

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
        fingerprint = hashlib.sha256(body.canonical_bytes()).hexdigest()
        try:
            receipt = await self._store.get_dispatch_receipt(
                tenant_id=principal.org_ctx.tenant_id,
                initiator_ai_user_id=principal.ai_user_id,
                idempotency_key=key,
            )
        except Exception:
            _raise_dispatch_error(503, "work_object_dispatch_failed")
        try:
            return await self._dispatch_resolved(principal, body, key, fingerprint, receipt)
        except _DispatchAuditUnavailable:
            # Another request may have committed after our initial receipt lookup.
            try:
                winner = await self._store.get_dispatch_receipt(
                    tenant_id=principal.org_ctx.tenant_id,
                    initiator_ai_user_id=principal.ai_user_id,
                    idempotency_key=key,
                )
            except Exception:
                winner = None
            if winner is not None:
                return await self._dispatch_resolved(
                    principal,
                    body,
                    key,
                    fingerprint,
                    winner,
                    audit_already_failed=True,
                )
            _raise_dispatch_error(503, "work_object_audit_unavailable")
        except HTTPException:
            raise
        except Exception:
            _raise_dispatch_error(503, "work_object_dispatch_failed")

    async def _dispatch_resolved(
        self,
        principal: Principal,
        body: DispatchWorkObjectsRequest,
        key: UUID,
        fingerprint: str,
        receipt: DispatchReceipt | None,
        *,
        audit_already_failed: bool = False,
    ) -> DispatchWorkObjectsResponse:
        directory = self._organization_directory
        if directory is None:
            _raise_dispatch_error(503, "organization_directory_unavailable")
        join_key = principal.org_ctx.directory_user_id
        try:
            memberships = await directory.list_user_memberships(join_key) if join_key else []
            membership = memberships[0] if len(memberships) == 1 else None
            if membership is not None and (
                membership.user_id != join_key
                or not membership.department_id.strip(SEARCH_WHITESPACE)
            ):
                membership = None
            department = (
                await directory.get_department(membership.department_id) if membership else None
            )
        except Exception:
            _raise_dispatch_error(503, "organization_directory_unavailable")
        decision = self._decision(
            membership, department, membership.department_id if membership else ""
        )
        if len(memberships) >= 2:
            await self._deny_dispatch(
                principal,
                decision,
                "directory_membership_ambiguous",
                receipt,
                audit_already_failed=audit_already_failed,
            )
        if decision.decision == "deny":
            await self._deny_dispatch(
                principal,
                decision,
                decision.reason_code or "not_department_head",
                receipt,
                audit_already_failed=audit_already_failed,
            )
        if receipt is not None:
            if receipt.request_fingerprint != fingerprint:
                _raise_dispatch_error(409, "idempotency_key_reused")
            response = DispatchWorkObjectsResponse.model_validate(receipt.result)
            scope = await self._visibility_scope(principal)
            for item in response.items:
                target_decision = self._decision(
                    membership, department, item.owner_department_id or ""
                )
                if target_decision.decision != "allow" or (
                    item.owner_department_id != scope.principal_department_id
                    and item.initiator_ai_user_id != scope.principal_ai_user_id
                ):
                    await self._deny_dispatch(
                        principal,
                        target_decision,
                        "cross_department_dispatch_denied",
                        receipt,
                        audit_already_failed=audit_already_failed,
                    )
            if not audit_already_failed:
                await self._audit_dispatch(principal, decision, None, required=False)
            return response.model_copy(update={"replayed": True})

        resolved: list[tuple[UserDispatchTarget | DepartmentDispatchTarget, str, str | None]] = []
        missing = False
        ambiguous = False
        try:
            for target in body.targets:
                if isinstance(target, UserDispatchTarget):
                    target_memberships = await directory.list_user_memberships(
                        target.directory_user_id
                    )
                    matching = [
                        item
                        for item in target_memberships
                        if item.user_id == target.directory_user_id
                        and item.department_id == target.department_id
                    ]
                    if len(matching) != 1:
                        missing = True
                    else:
                        resolved.append((target, matching[0].department_id, None))
                    ambiguous = ambiguous or len(target_memberships) >= 2
                else:
                    target_department = await directory.get_department(target.department_id)
                    if (
                        target_department is None
                        or target_department.department_id != target.department_id
                    ):
                        missing = True
                    else:
                        resolved.append(
                            (
                                target,
                                target_department.department_id,
                                target_department.display_name,
                            )
                        )
        except Exception:
            _raise_dispatch_error(503, "organization_directory_unavailable")
        if missing:
            _raise_dispatch_error(404, "dispatch_target_not_found")
        if ambiguous:
            await self._deny_dispatch(
                principal, decision, "dispatch_target_membership_ambiguous", None
            )
        for _, owner_department_id, _ in resolved:
            target_decision = self._decision(membership, department, owner_department_id)
            if target_decision.decision != "allow":
                await self._deny_dispatch(
                    principal, target_decision, "cross_department_dispatch_denied", None
                )
        await self._audit_dispatch(principal, decision, None, required=True)
        now = self._clock().astimezone(UTC)
        records = [
            InternalWorkObjectRecord(
                work_object_id=uuid4().hex,
                state_authority="internal",
                source_system="eternalai",
                source_kind="manual_dispatch",
                source_ref=None,
                source_title=None,
                source_status=None,
                source_received_at=None,
                source_created_at=None,
                source_workflow_type_id=None,
                source_fetched_at=None,
                assignee_ai_user_id=None,
                assignee_display_name=display_name,
                due_at=body.due_at,
                handling_mark=None,
                handling_marked_by_ai_user_id=None,
                handling_marked_at=None,
                task_record_id=None,
                created_at=now,
                updated_at=now,
                tenant_id=principal.org_ctx.tenant_id,
                owner_department_id=owner,
                initiator_ai_user_id=principal.ai_user_id,
                target_kind=target.kind,
                assignee_directory_user_id=(
                    target.directory_user_id if isinstance(target, UserDispatchTarget) else None
                ),
                title=body.title,
                kind=body.kind,
                requirement=body.requirement,
                receipt_requirement=body.receipt_requirement,
                reminder_choices=body.reminder_choices,
                status="assigned" if target.kind == "user" else "department_pending",
                version=1,
            )
            for target, owner, display_name in resolved
        ]
        response = DispatchWorkObjectsResponse.model_validate(
            {
                "items": [_view_from_record(record, []) for record in records],
                "created_count": len(records),
                "replayed": False,
            }
        )
        candidate = DispatchReceipt(
            tenant_id=principal.org_ctx.tenant_id,
            initiator_ai_user_id=principal.ai_user_id,
            idempotency_key=key,
            request_fingerprint=fingerprint,
            result=response.model_dump(mode="json"),
            authorization_summary=_dispatch_attributes(decision, None),
            created_at=now,
        )
        winner, created = await self._store.create_internal_dispatch(
            records=records, receipt=candidate
        )
        if created:
            return response
        return await self._dispatch_resolved(principal, body, key, fingerprint, winner)

    def _decision(
        self,
        membership: OrganizationUserMembership | None,
        department: OrganizationDepartment | None,
        target_department_id: str,
    ) -> DispatchAuthorizationDecision:
        value = compute_dispatch_authorization(
            dispatcher_membership=membership,
            dispatcher_department=department,
            target_department_id=target_department_id,
        )
        # Revalidate even a broken provider returning model_construct output.
        return DispatchAuthorizationDecision.model_validate(value.model_dump())

    async def _deny_dispatch(
        self,
        principal: Principal,
        decision: DispatchAuthorizationDecision,
        reason: str,
        receipt: DispatchReceipt | None,
        *,
        audit_already_failed: bool = False,
    ) -> NoReturn:
        if reason in {
            "directory_membership_missing",
            "directory_membership_ambiguous",
            "dispatch_target_membership_ambiguous",
        }:
            _LOGGER.warning(reason)
        if not audit_already_failed:
            await self._audit_dispatch(principal, decision, reason, required=receipt is None)
        _raise_dispatch_error(403, reason)

    async def _audit_dispatch(
        self,
        principal: Principal,
        decision: DispatchAuthorizationDecision,
        reason: str | None,
        *,
        required: bool,
    ) -> None:
        try:
            if self._trace_port is None:
                raise _DispatchAuditUnavailable()
            operation_id = self._id_factory()
            await self._trace_port.record_event(
                TraceEvent(
                    trace_id=operation_id,
                    task_id="dispatch:" + operation_id,
                    session_id="dispatch:" + operation_id,
                    tenant_id=principal.org_ctx.tenant_id,
                    ai_user_id=principal.ai_user_id,
                    event_type="user_action",
                    status="ok" if reason is None else "blocked",
                    error_code=None,
                    capability_id=None,
                    attributes=_dispatch_attributes(decision, reason),
                )
            )
        except Exception:
            _LOGGER.warning("work_object_audit_unavailable")
            if required:
                raise _DispatchAuditUnavailable() from None


def make_router(
    service: WorkObjectService | None,
    require_principal: PrincipalDependency,
) -> APIRouter:
    class DispatchValidationRoute(APIRoute):
        def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
            original = super().get_route_handler()

            async def handle(request: Request) -> Response:
                try:
                    return await original(request)
                except RequestValidationError:
                    if request.method == "POST" and request.url.path.endswith(
                        "/work-objects/dispatch"
                    ):
                        _raise_dispatch_error(422, "dispatch_request_invalid")
                    raise

            return handle

    router = APIRouter(route_class=DispatchValidationRoute)

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

    @router.get(
        "", response_model=WorkObjectListResponse, responses={503: {"model": WorkObjectError}}
    )
    async def list_work_objects(
        q: Annotated[str | None, Query()] = None,
        principal: Principal = Depends(require_principal),
    ) -> WorkObjectListResponse:
        search_term = normalize_search_query(q)
        return await configured().list_for_principal(
            principal,
            search_term=search_term,
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
            200: {"model": DispatchWorkObjectsResponse},
            **{code: {"model": WorkObjectError} for code in (401, 403, 404, 409, 422, 503)},
        },
    )
    async def dispatch_work_objects(
        body: DispatchWorkObjectsRequest,
        request: Request,
        response: Response,
        principal: Principal = Depends(require_principal),
    ) -> DispatchWorkObjectsResponse:
        values = request.headers.getlist("idempotency-key")
        if len(values) != 1:
            _raise_dispatch_error(422, "idempotency_key_invalid")
        try:
            key = UUID(values[0])
        except ValueError:
            _raise_dispatch_error(422, "idempotency_key_invalid")
        result = await configured().dispatch_for_principal(principal, body, key)
        response.status_code = 200 if result.replayed else 201
        return result

    @router.get(
        "/{work_object_id}",
        response_model=WorkObjectView,
        responses={503: {"model": WorkObjectError}},
    )
    async def get_work_object(
        work_object_id: str,
        response: Response,
        principal: Principal = Depends(require_principal),
    ) -> WorkObjectView:
        view = await configured().get_for_principal(work_object_id, principal)
        if view is None:
            _raise_not_found()
        if isinstance(view, InternalWorkObjectView) and view.version is not None:
            response.headers["ETag"] = f'"wo:{view.work_object_id}:{view.version}"'
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
            _view_from_record(record, capabilities) for record in records[:WORK_OBJECT_LIST_LIMIT]
        ],
        limit=WORK_OBJECT_LIST_LIMIT,
        limit_exceeded=limit_exceeded,
    )


def _view_from_record(
    record: WorkObjectRecord,
    capabilities: list[CapabilitySpec],
) -> WorkObjectView:
    manual = record.state_authority == "internal" and record.source_kind == "manual_dispatch"
    handling_capability = (
        None
        if manual
        else _resolve_handling_capability(
            record=record,
            capabilities=capabilities,
        )
    )
    handling_action = (
        "view_only"
        if manual
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


class _DispatchAuditUnavailable(RuntimeError):
    pass


class WorkObjectErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    message: str


class WorkObjectError(BaseModel):
    model_config = ConfigDict(extra="forbid")
    detail: WorkObjectErrorDetail


def _raise_dispatch_error(http_status: int, code: str) -> NoReturn:
    messages = {
        "organization_directory_unavailable": "Organization directory is unavailable.",
        "work_object_dispatch_failed": "Dispatch could not be completed; retry with the same key.",
        "work_object_audit_unavailable": "Dispatch audit is unavailable; retry with the same key.",
        "dispatch_target_not_found": "Dispatch target was not found.",
        "idempotency_key_reused": "Idempotency key belongs to a different request.",
        "idempotency_key_invalid": "One UUID idempotency key is required.",
        "dispatch_request_invalid": "Dispatch request is invalid.",
    }
    raise HTTPException(
        status_code=http_status,
        detail={
            "code": code,
            "message": messages.get(code, "Work Object operation is not permitted."),
        },
    )


def _dispatch_attributes(
    decision: DispatchAuthorizationDecision,
    reason: str | None,
) -> dict[str, str | None]:
    if reason not in {
        None,
        "directory_membership_missing",
        "directory_membership_ambiguous",
        "not_department_head",
        "cross_department_dispatch_denied",
        "dispatch_target_membership_ambiguous",
    }:
        raise ValueError("Unknown dispatch authorization reason")
    return {
        "operation": "dispatch",
        "phase": "authorization_decided",
        **decision.trace_attributes,
        "reason_code": reason,
    }


__all__ = (
    "InternalWorkObjectView",
    "OAWorkObjectView",
    "SetHandlingMarkRequest",
    "WorkObjectListResponse",
    "WorkObjectService",
    "WorkObjectView",
    "make_router",
)
