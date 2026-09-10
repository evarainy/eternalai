"""Work Object aggregate and persistence contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, Protocol, TypeAlias
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
)

WorkObjectHandlingMark: TypeAlias = Literal[
    "pending_sync_confirmation",
    "handled_elsewhere",
]


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
    status: Literal["assigned", "department_pending"] | None = None
    version: int | None = None

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
                or self.status != "assigned"
            ):
                raise ValueError("manual user target is inconsistent")
        elif (
            self.assignee_directory_user_id is not None
            or self.assignee_display_name is None
            or self.status != "department_pending"
        ):
            raise ValueError("manual department target is inconsistent")
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


class WorkObjectStorePort(Protocol):
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
    "OAPendingWorkSnapshot",
    "OAPendingWorkSnapshotCollection",
    "OAWorkObjectRecord",
    "WORK_OBJECT_LIST_FETCH_LIMIT",
    "WORK_OBJECT_LIST_LIMIT",
    "WorkObjectHandlingMark",
    "WorkObjectRecord",
    "WorkObjectStorePort",
)
