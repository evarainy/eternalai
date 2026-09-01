"""Work Object aggregate and persistence contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Protocol, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

WORK_OBJECT_LIST_LIMIT = 200
WORK_OBJECT_LIST_FETCH_LIMIT = WORK_OBJECT_LIST_LIMIT + 1

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
    assignee_ai_user_id: str
    assignee_display_name: str
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
    """Minimal internal-authority arm; business fields arrive in a later lane."""

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

    async def list_for_assignee(
        self,
        assignee_ai_user_id: str,
        *,
        search_term: str | None = None,
        limit: int = WORK_OBJECT_LIST_FETCH_LIMIT,
    ) -> list[WorkObjectRecord]: ...

    async def get_for_assignee(
        self,
        work_object_id: str,
        assignee_ai_user_id: str,
    ) -> WorkObjectRecord | None: ...

    async def set_handling_mark_for_assignee(
        self,
        work_object_id: str,
        assignee_ai_user_id: str,
        mark: WorkObjectHandlingMark,
        *,
        marked_at: datetime,
    ) -> WorkObjectRecord | None: ...


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
