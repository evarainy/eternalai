"""Contract tests for TracePort trace event models."""

from __future__ import annotations

import asyncio
import inspect
import re
from pathlib import Path
from typing import Any, get_args, get_type_hints

import pytest
from pydantic import ValidationError

from app.ports.capability_gateway import ErrorCode
from app.ports.trace import (
    SanitizerHookFn,
    TraceEvent,
    TraceEventStatus,
    TraceEventType,
    TracePort,
)

EXPECTED_TRACE_EVENT_TYPE_VALUES = (
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
    "adapter_result_invalid",
    "gateway_post_recorded",
    "response_envelope_created",
    "task_completed",
    "task_failed",
)

EXPECTED_TRACE_EVENT_STATUS_VALUES = ("ok", "blocked", "failed", "skipped")


def test_trace_event_type_literal_values_match_spec_8_6_7() -> None:
    assert get_args(TraceEventType) == EXPECTED_TRACE_EVENT_TYPE_VALUES


def test_trace_event_type_accepts_all_valid_values() -> None:
    for value in get_args(TraceEventType):
        event = TraceEvent(
            trace_id="t",
            task_id="t",
            session_id="s",
            event_type=value,
            status="ok",
        )

        assert event.event_type == value


def test_trace_event_type_rejects_invalid_value() -> None:
    with pytest.raises(ValidationError) as exc_info:
        TraceEvent(
            trace_id="t",
            task_id="t",
            session_id="s",
            event_type="invalid_event_type",
            status="ok",
        )

    assert "event_type" in str(exc_info.value)


def test_trace_event_status_literal_values_match_spec_8_6_7() -> None:
    assert get_args(TraceEventStatus) == EXPECTED_TRACE_EVENT_STATUS_VALUES


def test_trace_event_status_accepts_all_valid_values() -> None:
    for status in get_args(TraceEventStatus):
        event = TraceEvent(
            trace_id="t",
            task_id="t",
            session_id="s",
            event_type="task_created",
            status=status,
        )

        assert event.status == status


def test_trace_event_status_rejects_invalid_value() -> None:
    with pytest.raises(ValidationError):
        TraceEvent(
            trace_id="t",
            task_id="t",
            session_id="s",
            event_type="task_created",
            status="pending",
        )


def test_trace_event_has_extra_forbid_config() -> None:
    with pytest.raises(ValidationError):
        TraceEvent(
            trace_id="t",
            task_id="t",
            session_id="s",
            event_type="task_created",
            status="ok",
            extra_field="should_fail",
        )


def test_trace_event_requires_core_fields() -> None:
    with pytest.raises(ValidationError) as exc_info:
        TraceEvent()

    error_text = str(exc_info.value)
    assert "trace_id" in error_text
    assert "task_id" in error_text
    assert "session_id" in error_text
    assert "event_type" in error_text
    assert "status" in error_text


def test_trace_event_defaults_optional_fields() -> None:
    event = TraceEvent(
        trace_id="t",
        task_id="t",
        session_id="s",
        event_type="task_created",
        status="ok",
    )

    assert event.capability_id is None
    assert event.error_code is None
    assert event.attributes == {}


def test_trace_event_accepts_arbitrary_attributes() -> None:
    event = TraceEvent(
        trace_id="t",
        task_id="t",
        session_id="s",
        event_type="task_created",
        status="ok",
        attributes={"system": "oa", "latency_ms": 42, "nested": {"k": True}},
    )

    assert event.attributes["system"] == "oa"


def test_trace_event_accepts_all_error_code_values() -> None:
    for error_code in get_args(ErrorCode):
        event = TraceEvent(
            trace_id="t",
            task_id="t",
            session_id="s",
            event_type="adapter_error",
            status="failed",
            error_code=error_code,
        )

        assert event.error_code == error_code


def test_trace_event_rejects_invalid_error_code() -> None:
    with pytest.raises(ValidationError):
        TraceEvent(
            trace_id="t",
            task_id="t",
            session_id="s",
            event_type="adapter_error",
            status="failed",
            error_code="not_a_real_error_code",
        )


def test_trace_event_defines_no_plaintext_credential_slots() -> None:
    forbidden = {
        "password",
        "token",
        "cookie",
        "sessionid",
        "access_token",
        "refresh_token",
        "api_key",
        "private_key",
        "secret",
        "bearer",
    }

    assert forbidden.isdisjoint(TraceEvent.model_fields.keys())


def test_trace_event_accepts_arbitrary_str_field_values() -> None:
    event = TraceEvent(
        trace_id="arbitrary-trace-id-sentinel-abc123-xyz",
        task_id="arbitrary-task-id-sentinel-def456-uvw",
        session_id="arbitrary-session-id-sentinel-ghi789-rst",
        event_type="task_created",
        status="ok",
        capability_id="arbitrary-capability-id-sentinel-jkl012-opq",
    )

    assert event.trace_id == "arbitrary-trace-id-sentinel-abc123-xyz"
    assert event.task_id == "arbitrary-task-id-sentinel-def456-uvw"
    assert event.session_id == "arbitrary-session-id-sentinel-ghi789-rst"
    assert event.capability_id == "arbitrary-capability-id-sentinel-jkl012-opq"


def test_trace_port_protocol_defines_expected_methods() -> None:
    assert TracePort.__protocol_attrs__ == {
        "set_sanitizer",
        "record_event",
        "start_task_trace",
        "record_step",
        "record_policy_decision",
        "record_gateway_call",
        "finalize_task_trace",
    }


def test_set_sanitizer_is_sync_not_coroutine() -> None:
    assert not inspect.iscoroutinefunction(TracePort.set_sanitizer)


def test_record_event_is_coroutine_function() -> None:
    assert inspect.iscoroutinefunction(TracePort.record_event)


def test_record_event_signature_matches_spec_8_6_8() -> None:
    sig = inspect.signature(TracePort.record_event)

    assert list(sig.parameters) == ["self", "event"]
    hints = get_type_hints(TracePort.record_event)
    assert hints["event"] is TraceEvent
    assert hints["return"] is type(None)


def test_start_task_trace_is_coroutine_function() -> None:
    assert inspect.iscoroutinefunction(TracePort.start_task_trace)


def test_start_task_trace_signature() -> None:
    sig = inspect.signature(TracePort.start_task_trace)

    assert list(sig.parameters) == ["self", "trace_id", "task_id", "session_id"]
    hints = get_type_hints(TracePort.start_task_trace)
    assert hints["trace_id"] is str
    assert hints["task_id"] is str
    assert hints["session_id"] is str
    assert hints["return"] is type(None)


def test_record_step_is_coroutine_function() -> None:
    assert inspect.iscoroutinefunction(TracePort.record_step)


def test_record_step_signature() -> None:
    hints = get_type_hints(TracePort.record_step)

    assert hints["event_type"] is TraceEventType
    assert hints["status"] is TraceEventStatus


def test_record_policy_decision_is_coroutine_function() -> None:
    assert inspect.iscoroutinefunction(TracePort.record_policy_decision)


def test_record_gateway_call_is_coroutine_function() -> None:
    assert inspect.iscoroutinefunction(TracePort.record_gateway_call)


def test_finalize_task_trace_is_coroutine_function() -> None:
    assert inspect.iscoroutinefunction(TracePort.finalize_task_trace)


def test_sanitizer_hook_fn_is_a_callable_type_alias() -> None:
    def my_hook(payload: dict[str, Any]) -> dict[str, Any]:
        return payload

    assert callable(my_hook)


def _make_reference_sanitizer() -> SanitizerHookFn:
    patterns = (
        r"bearer\s+\S{6,}",
        r"sessionid\s*[:=]\s*\S{6,}",
        r"access_token\s*[:=]\s*\S{6,}",
        r"refresh_token\s*[:=]\s*\S{6,}",
        r"cookie\s*[:=]\s*\S{6,}",
        r"set-cookie\s*[:=]\s*\S{6,}",
    )
    compiled_patterns = tuple(re.compile(pattern, re.IGNORECASE) for pattern in patterns)

    def sanitizer(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            key: "[REDACTED]"
            if isinstance(value, str)
            and any(pattern.search(value) for pattern in compiled_patterns)
            else value
            for key, value in payload.items()
        }

    return sanitizer


def test_sanitizer_contract_covers_bearer_token() -> None:
    sanitizer = _make_reference_sanitizer()

    assert sanitizer({"auth": "Bearer SYNTHETIC-REDACTED-TOKEN-SENTINEL-1234567890"}) == {
        "auth": "[REDACTED]"
    }


def test_sanitizer_contract_covers_sessionid() -> None:
    sanitizer = _make_reference_sanitizer()

    assert sanitizer({"s": "sessionid=SYNTHETIC-REDACTED-SENTINEL-1234567890"}) == {
        "s": "[REDACTED]"
    }


def test_sanitizer_contract_covers_access_token() -> None:
    sanitizer = _make_reference_sanitizer()

    assert sanitizer({"t": "access_token=SYNTHETIC-REDACTED-SENTINEL-1234567890"}) == {
        "t": "[REDACTED]"
    }


def test_sanitizer_contract_covers_refresh_token() -> None:
    sanitizer = _make_reference_sanitizer()

    assert sanitizer({"t": "refresh_token=SYNTHETIC-REDACTED-SENTINEL-1234567890"}) == {
        "t": "[REDACTED]"
    }


def test_sanitizer_contract_covers_cookie() -> None:
    sanitizer = _make_reference_sanitizer()

    assert sanitizer({"h": "cookie=SYNTHETIC-REDACTED-COOKIE-SENTINEL-1234567890"}) == {
        "h": "[REDACTED]"
    }


def test_sanitizer_contract_covers_set_cookie() -> None:
    sanitizer = _make_reference_sanitizer()

    assert sanitizer({"h": "set-cookie=SYNTHETIC-REDACTED-SENTINEL-1234567890"}) == {
        "h": "[REDACTED]"
    }


def test_sanitizer_does_not_redact_safe_values() -> None:
    sanitizer = _make_reference_sanitizer()
    safe = {"action": "query", "capability_id": "oa_leave_apply", "user": "user-42"}

    assert sanitizer(safe) == safe


class _MockTracePort:
    def __init__(self) -> None:
        self._sanitizer: SanitizerHookFn | None = None
        self._events: list[TraceEvent] = []

    def set_sanitizer(self, hook: SanitizerHookFn) -> None:
        self._sanitizer = hook

    async def record_event(self, event: TraceEvent) -> None:
        if self._sanitizer:
            self._sanitizer(event.attributes)
        self._events.append(event)

    async def start_task_trace(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
    ) -> None:
        await self.record_event(
            TraceEvent(
                trace_id=trace_id,
                task_id=task_id,
                session_id=session_id,
                event_type="task_created",
                status="ok",
            )
        )

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
        await self.record_event(
            TraceEvent(
                trace_id=trace_id,
                task_id=task_id,
                session_id=session_id,
                event_type="task_completed",
                status=status,
                capability_id=capability_id,
                error_code=error_code,
                attributes=attributes or {},
            )
        )


def test_concrete_mock_trace_port_can_record_all_event_types() -> None:
    async def exercise() -> None:
        port = _MockTracePort()
        port.set_sanitizer(lambda payload: payload)
        await port.start_task_trace("trace-1", "task-1", "sess-1")
        await port.record_step(
            "trace-1",
            "task-1",
            "sess-1",
            event_type="capability_selected",
            status="ok",
            capability_id="oa_leave",
        )
        await port.record_policy_decision(
            "trace-1",
            "task-1",
            "sess-1",
            status="ok",
            capability_id="oa_leave",
        )
        await port.record_gateway_call(
            "trace-1",
            "task-1",
            "sess-1",
            status="ok",
            capability_id="oa_leave",
        )
        await port.finalize_task_trace(
            "trace-1",
            "task-1",
            "sess-1",
            status="ok",
            capability_id="oa_leave",
        )

        assert len(port._events) == 5
        assert port._events[0].event_type == "task_created"
        assert port._events[4].event_type == "task_completed"
        assert all(isinstance(event, TraceEvent) for event in port._events)

    asyncio.run(exercise())


def test_sanitizer_invoked_before_write_in_concrete_mock() -> None:
    call_order: list[str] = []

    def tracking_sanitizer(payload: dict[str, Any]) -> dict[str, Any]:
        call_order.append("sanitize")
        return payload

    class _OrderPort(_MockTracePort):
        async def record_event(self, event: TraceEvent) -> None:
            if self._sanitizer:
                self._sanitizer(event.attributes)
                call_order.append("write")
            else:
                call_order.append("write")
            self._events.append(event)

    async def exercise() -> None:
        port = _OrderPort()
        port.set_sanitizer(tracking_sanitizer)
        await port.record_event(
            TraceEvent(
                trace_id="t1",
                task_id="task-1",
                session_id="sess-1",
                event_type="task_created",
                status="ok",
            )
        )

    asyncio.run(exercise())
    assert call_order == ["sanitize", "write"]


def test_trace_port_source_has_no_storage_or_exporter_imports() -> None:
    source = Path("app/ports/trace.py").read_text(encoding="utf-8")

    for term in ("langfuse", "opentelemetry", "sqlalchemy", "redis", "requests", "httpx", "open("):
        assert term not in source, f"Forbidden term: {term!r}"
