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
        attributes = self._sanitize_attributes(event.attributes)
        if attributes is None:
            raise TraceSanitizationError("trace attribute sanitization failed")

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

    def _sanitize_attributes(
        self,
        attributes: dict[str, Any],
    ) -> dict[str, Any] | None:
        try:
            custom_sanitized = self._sanitizer(attributes)
            return redact_trace_attributes(custom_sanitized)
        except Exception:
            return None

    async def start_task_trace(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
        *,
        tenant_id: str,
        ai_user_id: str,
    ) -> None:
        del tenant_id, ai_user_id
        return None

    async def record_step(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
        *,
        tenant_id: str,
        ai_user_id: str,
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
                tenant_id=tenant_id,
                ai_user_id=ai_user_id,
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
        *,
        tenant_id: str,
        ai_user_id: str,
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
                tenant_id=tenant_id,
                ai_user_id=ai_user_id,
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
        *,
        tenant_id: str,
        ai_user_id: str,
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
                tenant_id=tenant_id,
                ai_user_id=ai_user_id,
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
        *,
        tenant_id: str,
        ai_user_id: str,
        status: TraceEventStatus,
        capability_id: str | None = None,
        error_code: ErrorCode | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        del tenant_id, ai_user_id
        return None
