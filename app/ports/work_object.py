"""Work Object aggregate and persistence contracts."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Any, Literal, Protocol, TypeAlias
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.ports.work_object_lifecycle import (
    CompletionFilter,
    LifecycleActor,
    LifecycleCommand,
    LifecycleEventRecord,
    LifecycleMutationResult,
    LifecycleStatus,
)
from app.ports.work_object_scope import AuthorizedWorkObjectScope
from app.ports.work_object_search import SEARCH_WHITESPACE

WORK_OBJECT_LIST_LIMIT = 200
WORK_OBJECT_LIST_FETCH_LIMIT = WORK_OBJECT_LIST_LIMIT + 1

DispatchKind: TypeAlias = Literal["通知", "督办令", "工作任务", "提醒"]
ReminderChoice: TypeAlias = Literal["提前 7 天", "提前 3 天", "提前 1 天", "逾期当天"]
REMINDER_CHOICES: tuple[ReminderChoice, ...] = (
    "提前 7 天",
    "提前 3 天",
    "提前 1 天",
    "逾期当天",
)
DISPATCH_RECORD_FIELDS: tuple[str, ...] = (
    "tenant_id",
    "owner_department_id",
    "initiator_ai_user_id",
    "target_kind",
    "assignee_directory_user_id",
    "title",
    "kind",
    "requirement",
    "receipt_requirement",
    "reminder_choices",
    "status",
    "version",
    "accepted_by_ai_user_id",
    "accepted_at",
    "completed_by_ai_user_id",
    "completed_at",
)

WorkObjectHandlingMark: TypeAlias = Literal[
    "pending_sync_confirmation",
    "handled_elsewhere",
]

OASyncStream: TypeAlias = Literal["pending", "completed"]
OAView: TypeAlias = Literal["active", "unconfirmed", "all"]
OASyncFailureCode: TypeAlias = Literal[
    "reauthentication_required", "binding_scope_required", "forbidden",
    "invalid_response", "upstream_unavailable", "storage_unavailable", "clock_invalid",
]


class _OAStrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    @field_validator("*", mode="after")
    @classmethod
    def _utc_datetimes(cls, value: Any) -> Any:
        if isinstance(value, datetime) and value.utcoffset() != timedelta(0):
            raise ValueError("OA observation times require UTC")
        return value


class OASyncSubject(_OAStrictModel):
    tenant_id: Literal["default"]
    ai_user_id: str = Field(min_length=1)

    @field_validator("ai_user_id")
    @classmethod
    def _nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("OA subject must not be blank")
        return value


class OASyncTicket(_OAStrictModel):
    subject: OASyncSubject
    stream: OASyncStream
    generation: int = Field(gt=0)
    started_at: datetime


class OAObservation(_OAStrictModel):
    pending_state: Literal["legacy_unverified", "current", "unconfirmed"]
    revision: int = Field(ge=0)
    last_seen_at: datetime
    last_checked_at: datetime | None

    @model_validator(mode="after")
    def _consistent(self) -> OAObservation:
        if self.pending_state == "legacy_unverified":
            if self.revision != 0 or self.last_checked_at is not None:
                raise ValueError("legacy observations have no reconciliation")
        elif (
            self.revision == 0 or self.last_checked_at is None
            or self.last_checked_at < self.last_seen_at
            or (self.pending_state == "current" and self.last_checked_at != self.last_seen_at)
        ):
            raise ValueError("inconsistent reconciled observation")
        return self


class OASyncStatusView(_OAStrictModel):
    status: Literal["never", "running", "succeeded", "failed", "unsupported_scope"]
    revision: int = Field(ge=0)
    attempt_revision: int = Field(ge=0)
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    failure_code: OASyncFailureCode | None

    @model_validator(mode="after")
    def _consistent(self) -> OASyncStatusView:
        if self.attempt_revision < self.revision:
            raise ValueError("attempt revision precedes publication")
        if (self.revision == 0) != (self.last_success_at is None):
            raise ValueError("success time must match publication")
        if self.status in {"never", "unsupported_scope"}:
            if self.attempt_revision or self.last_attempt_at is not None or self.failure_code:
                raise ValueError("unattempted sync must be empty")
        else:
            if self.last_attempt_at is None or self.attempt_revision == 0:
                raise ValueError("attempt metadata required")
            if self.status == "succeeded":
                if self.revision != self.attempt_revision or self.revision == 0:
                    raise ValueError("success must publish the issued generation")
                if self.last_success_at is None or self.last_success_at < self.last_attempt_at:
                    raise ValueError("success precedes attempt")
            elif self.attempt_revision <= self.revision:
                raise ValueError("unfinished publication requires a newer attempt")
        if (self.status == "failed") != (self.failure_code is not None):
            raise ValueError("only failed sync has a diagnostic code")
        return self


class OASyncStatus(_OAStrictModel):
    subject: OASyncSubject
    stream: OASyncStream
    issued_generation: int = Field(ge=0)
    applied_generation: int = Field(ge=0)
    last_attempt_status: Literal["never", "running", "succeeded", "failed"]
    last_attempt_started_at: datetime | None
    last_attempt_finished_at: datetime | None
    last_success_at: datetime | None
    last_error_code: OASyncFailureCode | None

    def to_view(self) -> OASyncStatusView:
        return OASyncStatusView(
            status=self.last_attempt_status, revision=self.applied_generation,
            attempt_revision=self.issued_generation, last_attempt_at=self.last_attempt_started_at,
            last_success_at=self.last_success_at, failure_code=self.last_error_code,
        )

    @model_validator(mode="after")
    def _consistent(self) -> OASyncStatus:
        self.to_view()
        finished = self.last_attempt_finished_at
        if self.last_attempt_status in {"never", "running"}:
            if finished is not None:
                raise ValueError("unfinished attempt has a finish time")
        elif finished is None:
            raise ValueError("terminal attempt requires a finish time")
        elif self.last_attempt_status == "succeeded" and finished != self.last_success_at:
            raise ValueError("successful finish must match publication time")
        elif (
            self.last_attempt_started_at is not None
            and finished < self.last_attempt_started_at and self.last_error_code != "clock_invalid"
        ):
            raise ValueError("finish precedes start without clock diagnostic")
        return self


class OASyncOutcomeUnknown(RuntimeError):
    """Commit acknowledgement was lost; callers must read, never repeat apply."""


class OASyncClockInvalid(ValueError):
    """A snapshot cannot replace a later observed fact."""


class OAPendingWorkSnapshot(BaseModel):
    """One allowlisted OA pending-workflow snapshot, without raw payload data."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    source_ref: str
    title: str
    status: str
    received_at: str
    created_at: str
    workflow_type_id: str

    @field_validator(
        "source_ref",
        "title",
        "status",
        "received_at",
        "created_at",
        "workflow_type_id",
    )
    @classmethod
    def _require_non_empty_html_free_string(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("pending-workflow text fields must not be empty")
        if "<" in value or ">" in value:
            raise ValueError("pending-workflow text fields must not contain HTML")
        return value


class OAPendingWorkSnapshotCollection(BaseModel):
    """Complete Gateway payload accepted by the Work Object sync boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    workflows: list[OAPendingWorkSnapshot]
    returned_count: int
    authoritative_count: int
    is_complete: Literal[True]

    @field_validator("is_complete", mode="before")
    @classmethod
    def _require_true_boolean(cls, value: Any) -> Any:
        if value is not True:
            raise ValueError("OA collection completeness requires boolean true")
        return value

    @model_validator(mode="after")
    def _validate_complete_collection(self) -> OAPendingWorkSnapshotCollection:
        count = len(self.workflows)
        if self.returned_count != count or self.authoritative_count != count:
            raise ValueError("OA pending-workflow counts must match the collection")
        if len({workflow.source_ref for workflow in self.workflows}) != count:
            raise ValueError("OA pending-workflow source references must be unique")
        return self


class _WorkObjectRecordBase(BaseModel):
    """Fields shared by both Work Object state-authority arms."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    work_object_id: str
    due_at: datetime | None
    handling_mark: WorkObjectHandlingMark | None
    handling_marked_by_ai_user_id: str | None
    handling_marked_at: datetime | None
    task_record_id: str | None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def _validate_handling_record(self) -> _WorkObjectRecordBase:
        metadata = (
            self.handling_marked_by_ai_user_id,
            self.handling_marked_at,
        )
        if self.handling_mark is None and any(value is not None for value in metadata):
            raise ValueError("handling metadata requires a handling mark")
        if self.handling_mark is not None and any(value is None for value in metadata):
            raise ValueError("handling mark requires actor and timestamp")
        return self


class OAWorkObjectRecord(_WorkObjectRecordBase):
    """Work Object whose business state remains authoritative in OA."""

    assignee_ai_user_id: str
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


class InternalWorkObjectRecord(_WorkObjectRecordBase):
    """Internal records distinguish complete dispatches from private legacy rows."""

    assignee_ai_user_id: str | None
    assignee_display_name: str | None
    tenant_id: str | None = None
    owner_department_id: str | None = None
    initiator_ai_user_id: str | None = None
    target_kind: Literal["user", "department"] | None = None
    assignee_directory_user_id: str | None = None
    title: str | None = None
    kind: DispatchKind | None = None
    requirement: str | None = None
    receipt_requirement: str | None = None
    reminder_choices: list[ReminderChoice] | None = None
    status: LifecycleStatus | None = None
    version: int | None = None
    accepted_by_ai_user_id: str | None = None
    accepted_at: datetime | None = None
    completed_by_ai_user_id: str | None = None
    completed_at: datetime | None = None

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

    @model_validator(mode="after")
    def _validate_dispatch(self) -> InternalWorkObjectRecord:
        if self.source_kind != "manual_dispatch":
            if self.assignee_ai_user_id is None or self.assignee_display_name is None:
                raise ValueError("legacy internal records require their original assignee")
            if any(getattr(self, name) is not None for name in DISPATCH_RECORD_FIELDS):
                raise ValueError("legacy internal records cannot carry dispatch fields")
            return self
        if self.source_system != "eternalai" or self.assignee_ai_user_id is not None:
            raise ValueError("manual dispatch requires the internal target identity")
        if not self.tenant_id or not self.initiator_ai_user_id:
            raise ValueError("manual dispatch requires trusted tenant and initiator")
        if not self.owner_department_id or not 1 <= len(self.owner_department_id) <= 128:
            raise ValueError("manual dispatch requires a bounded department")
        if self.version is None or self.version < 1:
            raise ValueError("manual dispatch requires a positive version")
        for value, minimum, maximum in (
            (self.title, 1, 200),
            (self.requirement, 0, 10000),
            (self.receipt_requirement, 0, 2000),
        ):
            if value is None or value != value.strip(SEARCH_WHITESPACE):
                raise ValueError("manual dispatch text must be normalized")
            if not minimum <= len(value) <= maximum:
                raise ValueError("manual dispatch text length is invalid")
        if self.kind is None or self.target_kind is None or self.status is None:
            raise ValueError("manual dispatch requires business discriminators")
        if self.target_kind == "user":
            if (
                not self.assignee_directory_user_id
                or not 1 <= len(self.assignee_directory_user_id) <= 128
                or self.assignee_display_name is not None
                or self.status == "department_pending"
            ):
                raise ValueError("manual user target is inconsistent")
        elif (
            self.assignee_directory_user_id is not None
            or self.assignee_display_name is None
            or self.status == "assigned"
        ):
            raise ValueError("manual department target is inconsistent")
        if self.status in {"assigned", "department_pending"}:
            if any(
                value is not None
                for value in (
                    self.accepted_by_ai_user_id,
                    self.accepted_at,
                    self.completed_by_ai_user_id,
                    self.completed_at,
                )
            ):
                raise ValueError("initial state cannot carry lifecycle fields")
        else:
            if (
                not self.accepted_by_ai_user_id
                or self.accepted_at is None
                or self.version < 2
                or self.accepted_at < self.created_at
                or self.updated_at < self.accepted_at
            ):
                raise ValueError("accepted state requires consistent evidence")
            if self.status == "in_progress":
                if self.completed_by_ai_user_id is not None or self.completed_at is not None:
                    raise ValueError("in-progress state cannot carry completion")
            elif (
                self.version < 3
                or self.completed_at is None
                or self.completed_by_ai_user_id != self.accepted_by_ai_user_id
                or self.completed_at < self.accepted_at
                or self.updated_at != self.completed_at
            ):
                raise ValueError("completed state requires consistent evidence")
        if self.reminder_choices is None or self.reminder_choices != [
            value for value in REMINDER_CHOICES if value in self.reminder_choices
        ]:
            raise ValueError("manual reminders must be canonical")
        if self.due_at is None and self.reminder_choices:
            raise ValueError("reminders require a due date")
        if self.due_at is not None and self.due_at.utcoffset() is None:
            raise ValueError("manual due date requires a timezone")
        if any(
            value is not None
            for value in (
                self.handling_mark,
                self.handling_marked_by_ai_user_id,
                self.handling_marked_at,
                self.task_record_id,
            )
        ):
            raise ValueError("manual dispatch cannot carry OA handling or task references")
        return self


class DispatchReceipt(BaseModel):
    """Persistent safe response snapshot; recipient join keys never belong here."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    tenant_id: str
    initiator_ai_user_id: str
    operation: Literal["dispatch"] = "dispatch"
    idempotency_key: UUID
    request_fingerprint: str
    result: dict[str, Any]
    authorization_summary: dict[str, str | None]
    created_at: datetime


WorkObjectRecord: TypeAlias = Annotated[
    OAWorkObjectRecord | InternalWorkObjectRecord,
    Field(discriminator="state_authority"),
]


class WorkObjectReadBatch(_OAStrictModel):
    records: list[WorkObjectRecord]
    oa_sync: OASyncStatusView


class WorkObjectStorePort(Protocol):
    async def begin_oa_sync(
        self, subject: OASyncSubject, stream: OASyncStream, started_at: datetime,
    ) -> OASyncTicket: ...

    async def apply_oa_pending_snapshot(
        self, ticket: OASyncTicket, *, assignee_display_name: str,
        collection: OAPendingWorkSnapshotCollection, fetched_at: datetime,
    ) -> Literal["applied", "superseded"]: ...

    async def finish_oa_sync_failure(
        self, ticket: OASyncTicket, *, failure_code: OASyncFailureCode, finished_at: datetime,
    ) -> None: ...

    async def get_oa_sync_status(
        self, subject: OASyncSubject, stream: OASyncStream,
    ) -> OASyncStatus: ...

    async def list_with_oa_sync_for_scope(
        self,
        scope: AuthorizedWorkObjectScope,
        *,
        search_term: str | None = None,
        oa_view: OAView = "active",
        limit: int = WORK_OBJECT_LIST_FETCH_LIMIT,
        completion: CompletionFilter | None = None,
    ) -> WorkObjectReadBatch: ...

    async def upsert_oa_pending_workflows(
        self,
        *,
        assignee_ai_user_id: str,
        assignee_display_name: str,
        snapshots: list[OAPendingWorkSnapshot],
        fetched_at: datetime,
    ) -> None: ...

    async def list_for_scope(
        self,
        scope: AuthorizedWorkObjectScope,
        *,
        search_term: str | None = None,
        limit: int = WORK_OBJECT_LIST_FETCH_LIMIT,
        completion: CompletionFilter | None = None,
    ) -> list[WorkObjectRecord]: ...

    async def get_for_scope(
        self,
        work_object_id: str,
        scope: AuthorizedWorkObjectScope,
    ) -> WorkObjectRecord | None: ...

    async def set_handling_mark_for_scope(
        self,
        work_object_id: str,
        scope: AuthorizedWorkObjectScope,
        mark: WorkObjectHandlingMark,
        *,
        marked_at: datetime,
    ) -> WorkObjectRecord | None: ...

    async def get_lifecycle_event_for_scope(
        self,
        work_object_id: str,
        scope: AuthorizedWorkObjectScope,
        *,
        actor_ai_user_id: str,
        idempotency_key: UUID,
    ) -> LifecycleEventRecord | None: ...

    async def list_lifecycle_events_for_scope(
        self,
        work_object_id: str,
        scope: AuthorizedWorkObjectScope,
        *,
        after_version: int,
        limit: int,
    ) -> list[LifecycleEventRecord] | None: ...

    async def apply_lifecycle_command_for_scope(
        self,
        work_object_id: str,
        scope: AuthorizedWorkObjectScope,
        *,
        actor: LifecycleActor,
        command: LifecycleCommand,
        idempotency_key: UUID,
        request_fingerprint: str,
        expected_etag: str,
        event_id: UUID,
    ) -> LifecycleMutationResult: ...

    async def get_dispatch_receipt(
        self,
        *,
        tenant_id: str,
        initiator_ai_user_id: str,
        idempotency_key: UUID,
    ) -> DispatchReceipt | None: ...

    async def create_internal_dispatch(
        self,
        *,
        records: list[InternalWorkObjectRecord],
        receipt: DispatchReceipt,
    ) -> tuple[DispatchReceipt, bool]:
        """Insert receipt first, then all records atomically; return the conflict winner."""
        ...


__all__ = (
    "InternalWorkObjectRecord",
    "OAObservation",
    "OAPendingWorkSnapshot",
    "OAPendingWorkSnapshotCollection",
    "OASyncClockInvalid",
    "OASyncFailureCode",
    "OASyncOutcomeUnknown",
    "OASyncStatus",
    "OASyncStatusView",
    "OASyncStream",
    "OASyncSubject",
    "OASyncTicket",
    "OAView",
    "OAWorkObjectRecord",
    "WORK_OBJECT_LIST_FETCH_LIMIT",
    "WORK_OBJECT_LIST_LIMIT",
    "WorkObjectHandlingMark",
    "WorkObjectReadBatch",
    "WorkObjectRecord",
    "WorkObjectStorePort",
)
