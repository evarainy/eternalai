"""No-op TracePort implementation for Phase 0 when OTel SDK is unavailable."""

from __future__ import annotations

import logging
import os
from typing import Any

from app.ports.capability_gateway import ErrorCode
from app.ports.trace import (
    SanitizerHookFn,
    TraceEvent,
    TraceEventStatus,
    TraceEventType,
    redact_trace_attributes,
)


class TraceSanitizationError(RuntimeError):
    """Raised when trace attributes cannot be sanitized safely."""


class NoopTraceWriter:
    """TracePort-compatible writer that emits sanitized DEBUG logs only."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        if not (
            os.environ.get("ENV", "").lower() == "testing"
            or os.environ.get("PHASE0_MOCK_MODE", "").lower() == "true"
        ):
            raise RuntimeError(
                "persistent TracePort is required outside testing or mock mode"
            )
        self._logger = logger or logging.getLogger(__name__)
        self._sanitizer: SanitizerHookFn = redact_trace_attributes

    def set_sanitizer(self, hook: SanitizerHookFn) -> None:
        self._sanitizer = hook

    async def record_event(self, event: TraceEvent) -> None:
        try:
            attributes = self._sanitizer(event.attributes)
            attributes = redact_trace_attributes(attributes)
        except Exception:
            raise TraceSanitizationError("trace attribute sanitization failed") from None

        event_payload = event.model_dump()
        event_payload["attributes"] = attributes
        sanitized_event = TraceEvent(**event_payload)

        try:
            self._logger.debug(
                "noop trace event recorded",
                extra={"trace_event": sanitized_event.model_dump()},
            )
        except Exception:
            return

    async def start_task_trace(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
    ) -> None:
        return None

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
    ) -> None:
        await self.record_event(
            TraceEvent(
                trace_id=trace_id,
                task_id=task_id,
                session_id=session_id,
                event_type=event_type,
                status=status,
                capability_id=capability_id,
                error_code=error_code,
                attributes=attributes or {},
            )
        )

    async def record_policy_decision(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
        status: TraceEventStatus,
        capability_id: str | None = None,
        error_code: ErrorCode | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        await self.record_event(
            TraceEvent(
                trace_id=trace_id,
                task_id=task_id,
                session_id=session_id,
                event_type="policy_checked",
                status=status,
                capability_id=capability_id,
                error_code=error_code,
                attributes=attributes or {},
            )
        )

    async def record_gateway_call(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
        status: TraceEventStatus,
        capability_id: str | None = None,
        error_code: ErrorCode | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        await self.record_event(
            TraceEvent(
                trace_id=trace_id,
                task_id=task_id,
                session_id=session_id,
                event_type="gateway_pre_recorded",
                status=status,
                capability_id=capability_id,
                error_code=error_code,
                attributes=attributes or {},
            )
        )

    async def finalize_task_trace(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
        status: TraceEventStatus,
        capability_id: str | None = None,
        error_code: ErrorCode | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        return None
