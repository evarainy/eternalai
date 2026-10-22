"""Neutral dispatch values shared by application and persistence adapters."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Any, Literal, Protocol, TypeAlias
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.ports.work_object_scope import AuthorizedWorkObjectScope

if TYPE_CHECKING:
    from app.ports.work_object import DispatchReceipt, InternalWorkObjectRecord
    from app.ports.work_object_lifecycle import (
        LifecycleCommand,
        LifecycleEventsView,
        LifecycleMutationResult,
        LifecycleView,
        RemarkHistoryPage,
        RemarkMutationResult,
        RemarkView,
    )

DispatchMode: TypeAlias = Literal["independent", "collaborative"]
CompletionMode: TypeAlias = Literal["submit_completes", "confirm_required"]
RelationStatus: TypeAlias = Literal[
    "department_pending", "assigned", "in_progress", "awaiting_department_review", "submitted"
]


class WorkDispatchError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class DispatchModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class UserDispatchTarget(DispatchModel):
    kind: Literal["user"]
    directory_user_id: str = Field(min_length=1, max_length=128)
    department_id: str = Field(min_length=1, max_length=128)


class DepartmentDispatchTarget(DispatchModel):
    kind: Literal["department"]
    department_id: str = Field(min_length=1, max_length=128)


DispatchTarget: TypeAlias = Annotated[
    UserDispatchTarget | DepartmentDispatchTarget, Field(discriminator="kind")
]


def target_key(target: DispatchTarget) -> tuple[str, str, str]:
    return (
        target.kind,
        target.department_id,
        target.directory_user_id if target.kind == "user" else "",
    )


class DispatchRequest(DispatchModel):
    kind: Literal["通知", "督办令", "工作任务", "提醒"]
    title: str = Field(min_length=1, max_length=200)
    requirement: str = Field(max_length=10000)
    receipt_requirement: str = Field(max_length=2000)
    due_at: datetime | None = None
    reminder_choices: list[Literal["提前 7 天", "提前 3 天", "提前 1 天", "逾期当天"]] = Field(
        default_factory=list, max_length=4
    )
    targets: list[DispatchTarget] = Field(min_length=1, max_length=100)
    dispatch_mode: DispatchMode
    completion_mode: CompletionMode
    lead_target: DispatchTarget | None = None
    attachment_ids: list[UUID] = Field(default_factory=list, max_length=10)

    @field_validator("title", "requirement", "receipt_requirement", mode="before")
    @classmethod
    def normalize_text(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("due_at", mode="before")
    @classmethod
    def aware_time(cls, value: Any) -> datetime | None:
        if value is None:
            return None
        parsed = (
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            if isinstance(value, str)
            else value
        )
        if not isinstance(parsed, datetime) or parsed.utcoffset() is None:
            raise ValueError("due_at requires a timezone")
        return parsed.astimezone(UTC)

    @model_validator(mode="after")
    def canonicalize(self) -> DispatchRequest:
        targets = {target_key(t): t for t in self.targets}
        object.__setattr__(self, "targets", [targets[k] for k in sorted(targets)])
        choices = ("提前 7 天", "提前 3 天", "提前 1 天", "逾期当天")
        object.__setattr__(
            self, "reminder_choices", [c for c in choices if c in self.reminder_choices]
        )
        object.__setattr__(self, "attachment_ids", sorted(set(self.attachment_ids), key=str))
        if self.due_at is None and self.reminder_choices:
            raise ValueError("reminders require due_at")
        if self.dispatch_mode == "independent" and self.lead_target is not None:
            raise ValueError("independent dispatch has no lead")
        if self.dispatch_mode == "collaborative" and (
            self.lead_target is None or target_key(self.lead_target) not in targets
        ):
            raise ValueError("collaboration requires one selected lead")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.model_dump(mode="json"))


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


@dataclass(frozen=True)
class DispatchSubject:
    tenant_id: str
    ai_user_id: str
    directory_user_id: str | None


class HandlingRelationRecord(DispatchModel):
    relation_id: UUID
    tenant_id: str
    work_object_id: str
    target_kind: Literal["user", "department"]
    target_department_id: str
    target_directory_user_id: str | None = None
    role: Literal["single", "lead", "collaborator"]
    assignee_directory_user_id: str | None = None
    assignee_ai_user_id: str | None = None
    status: RelationStatus
    accepted_at: datetime | None = None
    accepted_by_ai_user_id: str | None = None
    submitted_at: datetime | None = None
    submitted_by_ai_user_id: str | None = None
    submitted_result: str | None = None
    reviewed_at: datetime | None = None
    reviewed_by_ai_user_id: str | None = None
    review_result: bool | None = None
    review_reason: str | None = None
    created_at: datetime
    updated_at: datetime
    superseded_at: datetime | None = None


class HandlingRelationView(DispatchModel):
    relation_id: UUID
    target: DispatchTarget
    role: Literal["single", "lead", "collaborator"]
    status: RelationStatus
    assignee_directory_user_id: str | None = None
    accepted_at: datetime | None = None
    submitted_at: datetime | None = None
    submitted_result: str | None = None
    reviewed_at: datetime | None = None
    review_result: bool | None = None
    review_reason: str | None = None
    available_commands: list[str] = Field(default_factory=list)


class DispatchResult(DispatchModel):
    items: list[dict[str, Any]]
    created_count: int
    replayed: bool


class DispatchOptionsView(DispatchModel):
    kind: Literal["user", "department"]
    items: list[dict[str, Any]]
    next_cursor: str | None = None
    has_more: bool = False
    unselectable_count: int = 0
    snapshot_version: int
    can_dispatch_departments: bool
    can_dispatch_others: bool
    can_self_dispatch: bool = True


@dataclass(frozen=True)
class PreparedDispatch:
    subject: DispatchSubject
    scope: AuthorizedWorkObjectScope
    request: DispatchRequest
    request_fingerprint: str
    records: tuple[InternalWorkObjectRecord, ...]
    relations: tuple[HandlingRelationRecord, ...]
    receipt: DispatchReceipt
    attachment_ids: tuple[UUID, ...]
    directory_snapshot_version: int
    valid_until: float
    scheduled_at: datetime | None = None


class WorkDispatchStorePort(Protocol):
    async def create_prepared_dispatch(self, prepared: PreparedDispatch) -> DispatchResult: ...
    async def get_dispatch_lifecycle(
        self, work_object_id: str, scope: AuthorizedWorkObjectScope
    ) -> LifecycleView: ...
    async def command_dispatch_lifecycle(
        self,
        work_object_id: str,
        scope: AuthorizedWorkObjectScope,
        *,
        command: LifecycleCommand,
        expected_etag: str,
        idempotency_key: UUID,
        request_fingerprint: str,
        event_id: UUID,
        now: datetime,
        replacement_relations: tuple[HandlingRelationRecord, ...] | None = None,
        replacement_department_name: str | None = None,
        legacy_conversion_relation: HandlingRelationRecord | None = None,
    ) -> LifecycleMutationResult: ...
    async def get_dispatch_events(
        self,
        work_object_id: str,
        scope: AuthorizedWorkObjectScope,
        *,
        after_version: int,
        limit: int,
    ) -> LifecycleEventsView: ...
    async def get_remark(
        self, work_object_id: str, scope: AuthorizedWorkObjectScope
    ) -> RemarkView: ...
    async def edit_remark(
        self,
        work_object_id: str,
        scope: AuthorizedWorkObjectScope,
        *,
        text: str,
        expected_etag: str,
        idempotency_key: UUID,
        request_fingerprint: str,
        now: datetime,
    ) -> RemarkMutationResult: ...
    async def list_remark_history(
        self,
        work_object_id: str,
        scope: AuthorizedWorkObjectScope,
        *,
        before_version: int | None,
        limit: int,
    ) -> RemarkHistoryPage: ...


def dispatch_public_item(
    record: dict[str, Any], relations: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Keep the existing host view shape and omit persistence-only identity fields."""
    fields = (
        "work_object_id",
        "due_at",
        "handling_mark",
        "handling_marked_at",
        "task_record_id",
        "assignee_display_name",
        "state_authority",
        "source_system",
        "source_kind",
        "source_ref",
        "source_title",
        "source_status",
        "source_received_at",
        "source_created_at",
        "source_workflow_type_id",
        "source_fetched_at",
        "title",
        "requirement",
        "receipt_requirement",
        "owner_department_id",
        "initiator_ai_user_id",
        "kind",
        "target_kind",
        "reminder_choices",
        "version",
        "created_at",
        "updated_at",
        "status",
        "accepted_at",
        "completed_at",
        "withdrawn_at",
        "dispatch_mode",
        "completion_mode",
    )
    return {
        **{k: record[k] for k in fields},
        "handling_action": "view_only",
        "handling_capability_id": None,
        "reminder_delivery": "not_enabled",
        "frozen": record["status"] in {"completed", "withdrawn"},
        "relations": relations or [],
    }
