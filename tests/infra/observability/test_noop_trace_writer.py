from __future__ import annotations

import asyncio
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from app.infra.observability.noop_trace_writer import (
    NoopTraceWriter,
    TraceSanitizationError,
)
from app.ports.trace import TraceEvent, TraceEventStatus, TraceEventType


class _FakeLogger:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.raise_on_debug = False
        self.on_debug: Any | None = None

    def debug(self, *args: Any, **kwargs: Any) -> None:
        if self.on_debug:
            self.on_debug()
        if self.raise_on_debug:
            raise RuntimeError("logger unavailable")
        self.calls.append((args, kwargs))


class _CapturingTraceWriter(NoopTraceWriter):
    def __init__(self) -> None:
        super().__init__(logger=_FakeLogger())
        self.events: list[TraceEvent] = []

    async def record_event(self, event: TraceEvent) -> None:
        self.events.append(event)


def _trace_event(attributes: dict[str, Any] | None = None) -> TraceEvent:
    return TraceEvent(
        trace_id="trace-1",
        task_id="task-1",
        session_id="session-1",
        event_type="task_created",
        status="ok",
        attributes=attributes or {},
    )


def _logged_trace_event(logger: _FakeLogger) -> dict[str, Any]:
    assert logger.calls
    return logger.calls[-1][1]["extra"]["trace_event"]


@pytest.mark.parametrize("environment", [None, "production", "staging", "development"])
def test_noop_trace_writer_rejects_non_test_environments(
    monkeypatch: pytest.MonkeyPatch,
    environment: str | None,
) -> None:
    if environment is None:
        monkeypatch.delenv("ENV", raising=False)
    else:
        monkeypatch.setenv("ENV", environment)
    monkeypatch.delenv("PHASE0_MOCK_MODE", raising=False)

    with pytest.raises(RuntimeError, match="persistent TracePort is required"):
        NoopTraceWriter()


@pytest.mark.parametrize(
    ("environment", "mock_mode"),
    [("testing", None), (None, "true")],
)
def test_noop_trace_writer_allows_explicit_test_or_mock_environment(
    monkeypatch: pytest.MonkeyPatch,
    environment: str | None,
    mock_mode: str | None,
) -> None:
    if environment is None:
        monkeypatch.delenv("ENV", raising=False)
    else:
        monkeypatch.setenv("ENV", environment)
    if mock_mode is None:
        monkeypatch.delenv("PHASE0_MOCK_MODE", raising=False)
    else:
        monkeypatch.setenv("PHASE0_MOCK_MODE", mock_mode)

    writer = NoopTraceWriter()

    assert isinstance(writer, NoopTraceWriter)


def test_set_sanitizer_and_record_event_calls_it_before_logging() -> None:
    call_order: list[str] = []
    logger = _FakeLogger()
    logger.on_debug = lambda: call_order.append("log")
    writer = NoopTraceWriter(logger=logger)

    def sanitizer(attributes: dict[str, Any]) -> dict[str, Any]:
        call_order.append("sanitize")
        return attributes

    writer.set_sanitizer(sanitizer)

    asyncio.run(writer.record_event(_trace_event()))

    assert call_order == ["sanitize", "log"]


def test_record_event_logs_sanitized_attributes_only() -> None:
    logger = _FakeLogger()
    writer = NoopTraceWriter(logger=logger)
    raw_value = "Bearer " + "eyABC123"
    writer.set_sanitizer(lambda attributes: {"auth_header": "[REDACTED]"})

    asyncio.run(writer.record_event(_trace_event({"auth_header": raw_value})))

    logged_event = _logged_trace_event(logger)
    assert logged_event["attributes"] == {"auth_header": "[REDACTED]"}
    assert raw_value not in str(logged_event)


def test_record_event_default_sanitizer_preserves_safe_attributes() -> None:
    logger = _FakeLogger()
    writer = NoopTraceWriter(logger=logger)
    attributes = {"capability_id": "oa_leave_apply", "latency_ms": 25}

    asyncio.run(writer.record_event(_trace_event(attributes)))

    logged_event = _logged_trace_event(logger)
    assert logged_event["attributes"] == attributes


def test_record_event_default_sanitizer_never_logs_credentials() -> None:
    logger = _FakeLogger()
    writer = NoopTraceWriter(logger=logger)
    password_marker = "synthetic-" + "noop-password"
    identity_marker = "1" * 17 + "X"

    asyncio.run(
        writer.record_event(
            _trace_event(
                {
                    "tuple_nested": (
                        {"userpassword": password_marker},
                        {"message": identity_marker},
                    )
                }
            )
        )
    )

    logged_event = _logged_trace_event(logger)
    assert logged_event["attributes"] == {
        "tuple_nested": [
            {"userpassword": "[REDACTED]"},
            {"message": "[REDACTED]"},
        ]
    }
    assert password_marker not in str(logged_event)
    assert identity_marker not in str(logged_event)


def test_record_event_reapplies_mandatory_redaction_after_custom_hook() -> None:
    logger = _FakeLogger()
    writer = NoopTraceWriter(logger=logger)
    password_marker = "synthetic-" + "hook-password"
    writer.set_sanitizer(lambda attributes: attributes)

    asyncio.run(
        writer.record_event(_trace_event({"userpassword": password_marker}))
    )

    logged_event = _logged_trace_event(logger)
    assert logged_event["attributes"] == {"userpassword": "[REDACTED]"}
    assert password_marker not in str(logged_event)


def test_record_event_sanitizer_failure_raises_deterministic_error() -> None:
    writer = NoopTraceWriter(logger=_FakeLogger())
    sensitive_value = "Bearer " + "eyABC123"

    def sanitizer(attributes: dict[str, Any]) -> dict[str, Any]:
        raise ValueError("bad value " + sensitive_value)

    writer.set_sanitizer(sanitizer)

    with pytest.raises(TraceSanitizationError) as exc_info:
        asyncio.run(writer.record_event(_trace_event({"auth_header": sensitive_value})))

    assert str(exc_info.value) == "trace attribute sanitization failed"
    assert sensitive_value not in str(exc_info.value)
    assert sensitive_value not in str(exc_info.value.__cause__)


def test_record_event_soft_fails_when_logger_raises() -> None:
    logger = _FakeLogger()
    logger.raise_on_debug = True
    writer = NoopTraceWriter(logger=logger)

    result = asyncio.run(writer.record_event(_trace_event({"safe": "value"})))

    assert result is None


def test_start_task_trace_is_a_non_semantic_lifecycle_hook() -> None:
    writer = _CapturingTraceWriter()

    asyncio.run(writer.start_task_trace("trace-1", "task-1", "session-1"))

    assert writer.events == []


def test_record_step_delegates_with_all_fields() -> None:
    writer = _CapturingTraceWriter()
    attributes = {"latency_ms": 42}

    asyncio.run(
        writer.record_step(
            "trace-1",
            "task-1",
            "session-1",
            event_type="adapter_error",
            status="failed",
            capability_id="oa.leave.apply",
            error_code="adapter_timeout",
            attributes=attributes,
        )
    )

    event = writer.events[0]
    assert event.event_type == "adapter_error"
    assert event.status == "failed"
    assert event.capability_id == "oa.leave.apply"
    assert event.error_code == "adapter_timeout"
    assert event.attributes == attributes


def test_record_step_supports_distinct_evaluation_event() -> None:
    writer = _CapturingTraceWriter()
    attributes = {
        "rule_id": "terminal_status_v1",
        "business_status": "failed",
        "business_error_code": "adapter_error",
        "evaluation_result": "failed",
        "reason": "business_not_completed",
    }

    asyncio.run(
        writer.record_step(
            "trace-1",
            "task-1",
            "session-1",
            event_type="evaluation_recorded",
            status="failed",
            capability_id="oa.leave.apply",
            error_code="adapter_error",
            attributes=attributes,
        )
    )

    event = writer.events[0]
    assert event.event_type == "evaluation_recorded"
    assert event.status == "failed"
    assert event.error_code == "adapter_error"
    assert event.attributes == attributes


def test_record_step_supports_admin_action_event() -> None:
    writer = _CapturingTraceWriter()

    asyncio.run(
        writer.record_step(
            "trace-admin",
            "admin-request:trace-admin",
            "admin-lite",
            event_type="admin_action",
            status="blocked",
            capability_id="oa.leave.apply",
            attributes={
                "action": "get",
                "authorization_decision": "deny",
                "role_claim_authenticated": False,
            },
        )
    )

    event = writer.events[0]
    assert event.event_type == "admin_action"
    assert event.status == "blocked"
    assert event.attributes["role_claim_authenticated"] is False


def test_record_policy_decision_delegates_policy_checked() -> None:
    writer = _CapturingTraceWriter()

    asyncio.run(
        writer.record_policy_decision(
            "trace-1",
            "task-1",
            "session-1",
            status="blocked",
            capability_id="oa.leave.apply",
            error_code="policy_denied",
            attributes={"decision": "deny"},
        )
    )

    event = writer.events[0]
    assert event.event_type == "policy_checked"
    assert event.status == "blocked"
    assert event.capability_id == "oa.leave.apply"
    assert event.error_code == "policy_denied"
    assert event.attributes == {"decision": "deny"}


def test_record_gateway_call_delegates_gateway_pre_recorded() -> None:
    writer = _CapturingTraceWriter()

    asyncio.run(
        writer.record_gateway_call(
            "trace-1",
            "task-1",
            "session-1",
            status="ok",
            capability_id="oa.leave.apply",
            attributes={"gateway": "pre"},
        )
    )

    event = writer.events[0]
    assert event.event_type == "gateway_pre_recorded"
    assert event.status == "ok"
    assert event.capability_id == "oa.leave.apply"
    assert event.attributes == {"gateway": "pre"}


def test_finalize_task_trace_is_a_non_semantic_lifecycle_hook() -> None:
    writer = _CapturingTraceWriter()

    asyncio.run(
        writer.finalize_task_trace(
            "trace-1",
            "task-1",
            "session-1",
            status="failed",
            capability_id="oa.leave.apply",
            error_code="internal_error",
            attributes={"summary": "failed"},
        )
    )

    assert writer.events == []


def test_trace_event_extra_field_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        TraceEvent(
            trace_id="trace-1",
            task_id="task-1",
            session_id="session-1",
            event_type="task_created",
            status="ok",
            extra_field="unexpected",
        )


def test_all_trace_event_type_values_construct_valid_events() -> None:
    # 20 = prior 19-event contract + B5 Admin Lite action trace.
    assert len(get_args(TraceEventType)) == 20
    for event_type in get_args(TraceEventType):
        event = TraceEvent(
            trace_id="trace-1",
            task_id="task-1",
            session_id="session-1",
            event_type=event_type,
            status="ok",
        )
        assert event.event_type == event_type


def test_all_trace_event_status_values_construct_valid_events() -> None:
    for status in get_args(TraceEventStatus):
        event = TraceEvent(
            trace_id="trace-1",
            task_id="task-1",
            session_id="session-1",
            event_type="task_created",
            status=status,
        )
        assert event.status == status


def test_invalid_trace_event_type_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        TraceEvent(
            trace_id="trace-1",
            task_id="task-1",
            session_id="session-1",
            event_type="invalid_event_type",
            status="ok",
        )


def test_invalid_trace_event_status_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        TraceEvent(
            trace_id="trace-1",
            task_id="task-1",
            session_id="session-1",
            event_type="task_created",
            status="pending",
        )


def test_trace_event_accepts_arbitrary_trace_task_session_strings() -> None:
    event = TraceEvent(
        trace_id="arbitrary-trace-id-sentinel-abc123-xyz",
        task_id="arbitrary-task-id-sentinel-def456-uvw",
        session_id="arbitrary-session-id-sentinel-ghi789-rst",
        event_type="task_created",
        status="ok",
    )

    assert event.trace_id == "arbitrary-trace-id-sentinel-abc123-xyz"
    assert event.task_id == "arbitrary-task-id-sentinel-def456-uvw"
    assert event.session_id == "arbitrary-session-id-sentinel-ghi789-rst"
