"""Pure internal Work Object lifecycle contracts and authorization projections."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal, Protocol, TypeAlias
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.ports.work_object_dispatch import (
    CompletionMode,
    DispatchMode,
    DispatchTarget,
    HandlingRelationRecord,
    HandlingRelationView,
    RelationStatus,
    WorkDispatchError,
)
from app.ports.work_object_scope import AuthorizedWorkObjectScope

LifecycleStatus: TypeAlias = Literal[
    "assigned",
    "department_pending",
    "in_progress",
    "completed",
    "awaiting_department_review",
    "awaiting_acceptance",
    "withdrawn",
]
LifecycleOperation: TypeAlias = Literal[
    "assign",
    "accept",
    "feedback",
    "complete",
    "submit",
    "department_review",
    "submit_summary",
    "confirm",
    "return",
    "revise",
    "withdraw",
    "extend",
    "reassign",
]
CompletionFilter: TypeAlias = Literal["active", "completed", "withdrawn"]
LifecycleErrorCode: TypeAlias = str


class LifecycleStoreError(WorkDispatchError):
    def __init__(self, code: LifecycleErrorCode) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class LifecycleActor:
    tenant_id: str
    ai_user_id: str
    directory_user_id: str
    department_id: str
    snapshot_version: int
    valid_until: float


class _Command(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class AssignCommand(_Command):
    operation: Literal["assign"]
    handling_relation_id: UUID
    directory_user_id: str = Field(min_length=1, max_length=128)


class AcceptCommand(_Command):
    operation: Literal["accept"]
    handling_relation_id: UUID


class FeedbackCommand(_Command):
    operation: Literal["feedback"]
    handling_relation_id: UUID
    text: str = Field(min_length=1, max_length=10000)


class SubmitCommand(_Command):
    operation: Literal["submit"]
    handling_relation_id: UUID
    text: str = Field(min_length=1, max_length=10000)
    attachment_ids: list[UUID] = Field(default_factory=list, max_length=10)


class CompleteCommand(_Command):
    operation: Literal["complete"]
    text: str = Field(min_length=1, max_length=10000)


class DepartmentReviewCommand(_Command):
    operation: Literal["department_review"]
    handling_relation_id: UUID
    approved: bool
    reason: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def rejection_reason(self) -> DepartmentReviewCommand:
        if not self.approved and not self.reason.strip():
            raise ValueError("rejection requires a reason")
        return self


class SubmitSummaryCommand(_Command):
    operation: Literal["submit_summary"]
    text: str = Field(min_length=1, max_length=10000)
    attachment_ids: list[UUID] = Field(default_factory=list, max_length=10)


class ConfirmCommand(_Command):
    operation: Literal["confirm"]


class ReturnCommand(_Command):
    operation: Literal["return"]
    reason: str = Field(min_length=1, max_length=2000)


class ReviseCommand(_Command):
    operation: Literal["revise"]
    title: str = Field(min_length=1, max_length=200)
    requirement: str = Field(max_length=10000)
    receipt_requirement: str = Field(max_length=2000)
    completion_mode: CompletionMode

    @field_validator("title", "requirement", "receipt_requirement", mode="before")
    @classmethod
    def normalized(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class WithdrawCommand(_Command):
    operation: Literal["withdraw"]
    reason: str = Field(min_length=1, max_length=2000)


class ExtendCommand(_Command):
    operation: Literal["extend"]
    due_at: datetime

    @field_validator("due_at", mode="before")
    @classmethod
    def aware(cls, value: Any) -> datetime:
        parsed = (
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            if isinstance(value, str)
            else value
        )
        if not isinstance(parsed, datetime) or parsed.utcoffset() is None:
            raise ValueError("due_at requires timezone")
        return parsed


class ReassignCommand(_Command):
    operation: Literal["reassign"]
    targets: list[DispatchTarget] = Field(min_length=1, max_length=100)
    lead_target: DispatchTarget | None = None


LifecycleCommand: TypeAlias = Annotated[
    AssignCommand
    | AcceptCommand
    | FeedbackCommand
    | SubmitCommand
    | CompleteCommand
    | DepartmentReviewCommand
    | SubmitSummaryCommand
    | ConfirmCommand
    | ReturnCommand
    | ReviseCommand
    | WithdrawCommand
    | ExtendCommand
    | ReassignCommand,
    Field(discriminator="operation"),
]


class LifecycleEventRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    event_id: UUID
    work_object_id: str
    tenant_id: str
    actor_ai_user_id: str
    operation: LifecycleOperation
    idempotency_key: UUID
    request_fingerprint: str
    from_status: LifecycleStatus
    to_status: LifecycleStatus
    result_version: int = Field(ge=2)
    occurred_at: datetime
    text: str | None
    event_model_version: int | None = None
    handling_relation_id: UUID | None = None
    relation_from_status: RelationStatus | None = None
    relation_to_status: RelationStatus | None = None
    payload: dict[str, Any] | None = None
    visibility: Literal["shared", "department_internal"] | None = None


@dataclass(frozen=True)
class LifecycleMutationResult:
    event: LifecycleEventRecord
    replayed: bool


class LifecycleRecord(Protocol):
    @property
    def tenant_id(self) -> str | None: ...
    @property
    def owner_department_id(self) -> str | None: ...
    @property
    def target_kind(self) -> Literal["user", "department"] | None: ...
    @property
    def assignee_directory_user_id(self) -> str | None: ...
    @property
    def accepted_by_ai_user_id(self) -> str | None: ...
    @property
    def status(self) -> LifecycleStatus | None: ...


def lifecycle_role_allowed(
    record: LifecycleRecord,
    actor: LifecycleActor,
    operation: LifecycleOperation,
) -> bool:
    target = (
        record.tenant_id == actor.tenant_id
        and record.owner_department_id == actor.department_id
        and (
            record.target_kind == "department"
            or (
                record.target_kind == "user"
                and record.assignee_directory_user_id == actor.directory_user_id
            )
        )
    )
    return target and (operation == "accept" or record.accepted_by_ai_user_id == actor.ai_user_id)


def lifecycle_transition_allowed(record: LifecycleRecord, operation: LifecycleOperation) -> bool:
    return (
        record.status in {"assigned", "department_pending"}
        if operation == "accept"
        else record.status == "in_progress"
    )


def compute_lifecycle_actions(
    record: LifecycleRecord,
    actor: LifecycleActor | None,
) -> list[LifecycleOperation]:
    operations: tuple[LifecycleOperation, ...] = ("accept", "feedback", "complete")
    return [
        operation
        for operation in operations
        if actor is not None
        and lifecycle_role_allowed(record, actor, operation)
        and lifecycle_transition_allowed(record, operation)
    ]


def can_replay_lifecycle_event(
    record: LifecycleRecord,
    actor: LifecycleActor,
    event: LifecycleEventRecord,
) -> bool:
    return (
        event.tenant_id == actor.tenant_id
        and event.actor_ai_user_id == actor.ai_user_id == record.accepted_by_ai_user_id
        and lifecycle_role_allowed(record, actor, event.operation)
    )


def lifecycle_etag(representation: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        representation, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return '"wolc-' + hashlib.sha256(encoded).hexdigest() + '"'


class LifecycleView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    work_object_id: str
    status: LifecycleStatus
    version: int
    accepted_at: datetime | None = None
    completed_at: datetime | None = None
    dispatch_mode: DispatchMode | None = None
    completion_mode: CompletionMode | None = None
    title: str
    requirement: str
    receipt_requirement: str
    due_at: datetime | None = None
    frozen: bool
    relations: list[HandlingRelationView]
    available_commands: list[str]
    unavailable_reason: str | None = None

    @property
    def etag(self) -> str:
        return lifecycle_etag(self.model_dump(mode="json"))


class LifecycleEventsView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    items: list[LifecycleEventRecord]
    next_after_version: int | None = None


class RemarkView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    work_object_id: str
    text: str = Field(max_length=2000)
    version: int = Field(ge=0)
    updated_by: str | None = None
    updated_at: datetime | None = None
    editable: bool

    @property
    def etag(self) -> str:
        encoded = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
        return '"worm-' + hashlib.sha256(encoded).hexdigest() + '"'


class RemarkHistoryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    work_object_id: str
    remark_version: int
    text: str
    actor_ai_user_id: str
    occurred_at: datetime


class RemarkHistoryPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    items: list[RemarkHistoryEntry]
    next_cursor: str | None = None


@dataclass(frozen=True)
class RemarkMutationResult:
    remark: RemarkView
    replayed: bool


def relation_actor_allowed(
    relation: HandlingRelationRecord, scope: AuthorizedWorkObjectScope
) -> bool:
    return relation.superseded_at is None and (
        relation.assignee_ai_user_id == scope.principal_ai_user_id
        or (
            scope.principal_directory_user_id is not None
            and scope.principal_directory_user_id
            == (relation.assignee_directory_user_id or relation.target_directory_user_id)
        )
    )


def relation_head_allowed(
    relation: HandlingRelationRecord, scope: AuthorizedWorkObjectScope
) -> bool:
    return (
        relation.superseded_at is None
        and relation.target_kind == "department"
        and relation.target_department_id in scope.responsible_department_ids
    )


def relation_private_allowed(
    relation: HandlingRelationRecord, scope: AuthorizedWorkObjectScope
) -> bool:
    return relation_actor_allowed(relation, scope) or relation_head_allowed(relation, scope)


def relation_actions(
    relation: HandlingRelationRecord, scope: AuthorizedWorkObjectScope, *, frozen: bool
) -> list[str]:
    if frozen:
        return []
    actions: list[str] = []
    if relation_head_allowed(relation, scope):
        actions.append("assign")
        if relation.status == "awaiting_department_review":
            actions.append("department_review")
    if relation_actor_allowed(relation, scope):
        if relation.status == "assigned":
            actions.append("accept")
        if relation.status == "in_progress":
            actions.extend(["feedback", "submit"])
    return actions


def encode_history_cursor(*, occurred_at: datetime, work_object_id: str) -> str:
    import base64

    return base64.urlsafe_b64encode(
        (occurred_at.isoformat() + "|" + work_object_id).encode()
    ).decode()
