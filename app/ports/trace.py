"""Trace interface contract."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, Protocol, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

from app.ports.capability_gateway import ErrorCode

TraceEventType: TypeAlias = Literal[
    "task_created",
    "intent_parsed",
    "capability_selected",
    "no_capability_found",
    "identity_check",
    "blocked_by_identity",
    "policy_checked",
    "blocked_by_policy",
    "confirm_required",
    "gateway_pre_recorded",
    "adapter_called",
    "adapter_error",
    "adapter_error_mapped",
    "adapter_result_invalid",
    "gateway_post_recorded",
    "response_envelope_created",
    "task_completed",
    "task_failed",
    "evaluation_recorded",
    "admin_action",
]

TraceEventStatus: TypeAlias = Literal["ok", "blocked", "failed", "skipped"]

SanitizerHookFn: TypeAlias = Callable[[dict[str, Any]], dict[str, Any]]


class TraceEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: str
    task_id: str
    session_id: str
    event_type: TraceEventType
    status: TraceEventStatus
    capability_id: str | None = None
    error_code: ErrorCode | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class TracePort(Protocol):
    def set_sanitizer(self, hook: SanitizerHookFn) -> None: ...

    async def record_event(self, event: TraceEvent) -> None: ...

    async def start_task_trace(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
    ) -> None: ...

    async def record_step(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
        event_type: TraceEventType,
        status: TraceEventStatus,
        capability_id: str | None = None,
        error_code: ErrorCode | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None: ...

    async def record_policy_decision(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
        status: TraceEventStatus,
        capability_id: str | None = None,
        error_code: ErrorCode | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None: ...

    async def record_gateway_call(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
        status: TraceEventStatus,
        capability_id: str | None = None,
        error_code: ErrorCode | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None: ...

    async def finalize_task_trace(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
        status: TraceEventStatus,
        capability_id: str | None = None,
        error_code: ErrorCode | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None: ...
