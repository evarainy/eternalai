"""Pure internal Work Object lifecycle contracts and authorization projections."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol, TypeAlias
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

LifecycleStatus: TypeAlias = Literal["assigned", "department_pending", "in_progress", "completed"]
LifecycleOperation: TypeAlias = Literal["accept", "feedback", "complete"]
CompletionFilter: TypeAlias = Literal["active", "completed"]
LifecycleErrorCode: TypeAlias = Literal[
    "work_object_lifecycle_request_invalid",
    "work_object_precondition_required",
    "work_object_not_found",
    "work_object_lifecycle_unsupported",
    "directory_membership_missing",
    "directory_membership_ambiguous",
    "work_object_action_forbidden",
    "work_object_transition_invalid",
    "idempotency_key_reused",
    "work_object_version_conflict",
    "organization_directory_missing",
    "organization_directory_stale",
    "organization_directory_unavailable",
    "work_object_unavailable",
    "work_object_audit_unavailable",
    "work_object_lifecycle_failed",
]


class LifecycleStoreError(RuntimeError):
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


class LifecycleCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    operation: LifecycleOperation
    text: str | None = None


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
