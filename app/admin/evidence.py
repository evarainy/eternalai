"""Credential-safe Admin Lite views for Task and Binding evidence."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.ports.identity_mapping import (
    ExecutionIdentity,
    IdentityBindStatus,
    IdentityCheckResult,
    TargetSystem,
)
from app.ports.task_store import TaskEventRecord, TaskRecord, TaskStatus
from app.ports.trace import (
    TraceEventStatus,
    TraceEventType,
    TracePersistedEvent,
    redact_trace_attributes,
)


class AdminTaskView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    session_id: str
    ai_user_id: str
    status: TaskStatus
    capability_id: str | None
    error_code: str | None

    @classmethod
    def from_record(cls, task: TaskRecord) -> AdminTaskView:
        return cls.model_validate(
            task.model_dump(
                include={
                    "task_id",
                    "session_id",
                    "ai_user_id",
                    "status",
                    "capability_id",
                    "error_code",
                }
            )
        )


class AdminTaskEventEvidence(BaseModel):
    """Explicitly safe Task event payload fields; unknown fields never leave the service."""

    model_config = ConfigDict(extra="forbid")

    capability_id: str | None = None
    selection_rule: str | None = None
    workflow_id: str | None = None
    workflow_version: str | None = None
    workflow_status: str | None = None
    error_code: str | None = None
    step_id: str | None = None
    step_index: int | None = None
    step_status: str | None = None
    attempt: int | None = None
    retry_number: int | None = None
    max_attempts: int | None = None
    waiting_step_id: str | None = None
    waiting_step_index: int | None = None
    confirmed_capability_id: str | None = None
    completed_step_ids: list[str] | None = None
    step_output_keys: dict[str, list[str]] | None = None
    recovery_input_keys: list[str] | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> AdminTaskEventEvidence:
        allowed = {key: payload[key] for key in cls.model_fields if key in payload}
        return cls.model_validate(allowed)


class AdminTaskEventView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    task_id: str
    event_type: str
    timestamp: datetime
    evidence: AdminTaskEventEvidence

    @classmethod
    def from_record(cls, event: TaskEventRecord) -> AdminTaskEventView:
        return cls(
            event_id=event.event_id,
            task_id=event.task_id,
            event_type=event.event_type,
            timestamp=event.timestamp,
            evidence=AdminTaskEventEvidence.from_payload(event.payload),
        )


class AdminBindingView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    binding_id: str | None
    target_system: TargetSystem
    execution_identity: ExecutionIdentity
    bind_status: IdentityBindStatus
    binding_scope: str | None
    account_set_id: str | None
    device_domain_id: str | None
    reason_code: str | None

    @classmethod
    def from_result(cls, result: IdentityCheckResult) -> AdminBindingView:
        return cls.model_validate(
            result.model_dump(
                include={
                    "binding_id",
                    "target_system",
                    "execution_identity",
                    "bind_status",
                    "binding_scope",
                    "account_set_id",
                    "device_domain_id",
                    "reason_code",
                }
            )
        )


class AdminTracePersistedView(BaseModel):
    """Explicit trace response allowlist with read-side credential defense."""

    model_config = ConfigDict(extra="forbid")

    event_id: str
    trace_id: str
    task_id: str
    session_id: str
    event_type: TraceEventType
    status: TraceEventStatus
    capability_id: str | None
    error_code: str | None
    attributes: dict[str, Any]
    created_at: datetime

    @classmethod
    def from_record(
        cls,
        event: TracePersistedEvent,
    ) -> AdminTracePersistedView:
        return cls(
            event_id=event.event_id,
            trace_id=event.trace_id,
            task_id=event.task_id,
            session_id=event.session_id,
            event_type=event.event_type,
            status=event.status,
            capability_id=event.capability_id,
            error_code=event.error_code,
            attributes=redact_trace_attributes(event.attributes),
            created_at=event.created_at,
        )


class AdminTaskListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AdminTaskView]


class AdminTaskEventListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AdminTaskEventView]


class AdminBindingListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ai_user_id: str
    items: list[AdminBindingView]


class AdminTraceListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AdminTracePersistedView]


__all__ = (
    "AdminBindingListResponse",
    "AdminBindingView",
    "AdminTaskEventEvidence",
    "AdminTaskEventListResponse",
    "AdminTaskEventView",
    "AdminTaskListResponse",
    "AdminTaskView",
    "AdminTraceListResponse",
    "AdminTracePersistedView",
)
