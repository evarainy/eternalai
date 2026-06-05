from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from app.infra.sdui.response_envelope_builder import ResponseEnvelopeBuilder
from app.ports.response_envelope import (
    BindingRequiredCard,
    ResponseEnvelope,
    UIComponent,
)

RAW_MARKER = "eyABC123opaque"


def _builder() -> ResponseEnvelopeBuilder:
    return ResponseEnvelopeBuilder()


def _message_args(
    message: str = "Message ready.",
    fallback_text: str = "Message ready.",
) -> tuple[str, str, str, str, str, str]:
    return (
        "resp-001",
        "task-001",
        "sid-001",
        message,
        fallback_text,
        "trace-001",
    )


def _base_envelope_kwargs() -> dict[str, Any]:
    return {
        "response_id": "resp-001",
        "task_id": "task-001",
        "session_id": "sid-001",
        "status": "completed",
        "message": "Message ready.",
        "fallback_text": "Message ready.",
        "ui": UIComponent(component_type="none"),
        "trace_id": "trace-001",
    }


def _serialized(envelope: ResponseEnvelope) -> dict[str, Any]:
    return json.loads(envelope.model_dump_json())


def test_build_message_json_contains_all_required_fields() -> None:
    envelope = _builder().build_message(
        *_message_args(),
        data={"result": "ok"},
        trace_summary="trace summary",
    )

    payload = json.loads(_builder().serialize(envelope))

    assert list(payload) == [
        "schema_version",
        "response_id",
        "task_id",
        "session_id",
        "status",
        "message",
        "fallback_text",
        "ui",
        "data",
        "trace_id",
        "trace_summary",
    ]
    assert payload["schema_version"] == "phase0.sdui.v1"
    assert payload["response_id"] == "resp-001"
    assert payload["task_id"] == "task-001"
    assert payload["session_id"] == "sid-001"
    assert payload["status"] == "completed"
    assert payload["message"] == "Message ready."
    assert payload["fallback_text"] == "Message ready."
    assert payload["ui"] == {
        "component_type": "none",
        "action": "none",
        "target_system": None,
        "reason_code": None,
        "payload": {},
    }
    assert payload["data"] == {"result": "ok"}
    assert payload["trace_id"] == "trace-001"
    assert payload["trace_summary"] == "trace summary"


def test_build_confirm_card_action_confirm() -> None:
    envelope = _builder().build_confirm_card(*_message_args())

    assert envelope.ui.component_type == "confirm_card"
    assert envelope.ui.action == "confirm"
    assert envelope.status == "waiting_user"


def test_build_binding_required_uses_binding_required_card() -> None:
    envelope = _builder().build_binding_required(
        *_message_args(),
        target_system="oa",
    )

    assert envelope.ui.component_type == "binding_required_card"
    assert envelope.ui.action == "bind_required"
    assert envelope.ui.reason_code == "identity_unbound"
    assert envelope.status == "blocked"


def test_build_operator_handback_uses_clarify_scope() -> None:
    envelope = _builder().build_operator_handback(*_message_args())

    assert envelope.ui.component_type == "operator_handback_card"
    assert envelope.ui.action == "clarify_scope"
    assert envelope.ui.reason_code == "unclear_scope"


def test_build_failed_fallback_text_non_empty() -> None:
    envelope = _builder().build_failed(
        "resp-001",
        "task-001",
        "sid-001",
        "Failed.",
        "",
        "trace-001",
    )

    assert envelope.status == "failed"
    assert envelope.fallback_text.strip()


def test_sanitizer_redacts_credential_in_message() -> None:
    envelope = _builder().build_message(
        *_message_args(message=f"Bearer {RAW_MARKER}"),
    )

    serialized = _builder().serialize(envelope)

    assert "[REDACTED]" in serialized
    assert RAW_MARKER not in serialized


def test_sanitizer_runs_before_envelope_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    original_init = ResponseEnvelope.__init__

    def spy(self: ResponseEnvelope, **kwargs: Any) -> None:
        calls.append(kwargs.copy())
        original_init(self, **kwargs)

    monkeypatch.setattr(ResponseEnvelope, "__init__", spy)

    envelope = _builder().build_message(
        *_message_args(message=f"Authorization bearer {RAW_MARKER}"),
        data={"details": {"access_token": RAW_MARKER}},
        trace_summary=f"Bearer {RAW_MARKER}",
    )

    constructor_payload = json.dumps(calls, default=str)

    assert isinstance(envelope, ResponseEnvelope)
    assert calls
    assert "[REDACTED]" in constructor_payload
    assert RAW_MARKER not in constructor_payload


def test_sanitizer_credential_under_innocuous_key() -> None:
    envelope = _builder().build_message(
        *_message_args(),
        data={"result": f"Bearer {RAW_MARKER}"},
    )

    payload = _serialized(envelope)

    assert payload["data"] == {"result": "[REDACTED]"}
    assert RAW_MARKER not in _builder().serialize(envelope)


def test_invalid_input_returns_failed_envelope() -> None:
    envelope = _builder().build_message(
        *_message_args(),
        status="in_progress",
    )

    assert isinstance(envelope, ResponseEnvelope)
    assert envelope.status == "failed"
    assert envelope.fallback_text.strip()


def test_fallback_text_non_empty_all_paths() -> None:
    builder = _builder()
    envelopes = (
        builder.build_message(*_message_args()),
        builder.build_confirm_card(*_message_args()),
        builder.build_binding_required(*_message_args(), target_system="oa"),
        builder.build_operator_handback(*_message_args()),
        builder.build_failed(*_message_args(message="Failed.", fallback_text="Failed.")),
    )

    assert all(envelope.fallback_text.strip() for envelope in envelopes)


def test_data_null_for_blocked_waiting_user_no_capability_found() -> None:
    builder = _builder()

    for status in ("blocked", "waiting_user", "no_capability_found"):
        envelope = builder.build_message(*_message_args(), status=status)

        assert envelope.status == status
        assert envelope.data is None
        assert _serialized(envelope)["data"] is None


def test_all_response_envelope_status_values() -> None:
    builder = _builder()

    for status in (
        "completed",
        "blocked",
        "waiting_user",
        "failed",
        "no_capability_found",
    ):
        envelope = builder.build_message(*_message_args(), status=status)

        assert envelope.status == status


def test_all_ui_component_types() -> None:
    builder = _builder()
    envelopes = (
        builder.build_message(*_message_args()),
        builder.build_confirm_card(*_message_args()),
        builder.build_operator_handback(*_message_args()),
        builder.build_binding_required(*_message_args(), target_system="oa"),
    )

    assert [envelope.ui.component_type for envelope in envelopes] == [
        "none",
        "confirm_card",
        "operator_handback_card",
        "binding_required_card",
    ]


def test_open_str_fields() -> None:
    envelope = _builder().build_message(
        "resp:open/123",
        "task:open/123",
        "sid:open/123",
        "Arbitrary message {}.",
        "Arbitrary fallback {}.",
        "trace:open/123",
    )

    assert envelope.response_id == "resp:open/123"
    assert envelope.task_id == "task:open/123"
    assert envelope.session_id == "sid:open/123"
    assert envelope.message == "Arbitrary message {}."
    assert envelope.fallback_text == "Arbitrary fallback {}."
    assert envelope.trace_id == "trace:open/123"


def test_isinstance_response_envelope() -> None:
    envelope = _builder().build_message(*_message_args())

    assert isinstance(envelope, ResponseEnvelope)


def test_invalid_status_raises() -> None:
    kwargs = _base_envelope_kwargs()
    kwargs["status"] = "in_progress"

    with pytest.raises(ValidationError):
        ResponseEnvelope(**kwargs)


def test_invalid_component_type_raises() -> None:
    with pytest.raises(ValidationError):
        UIComponent(component_type="table_card")


def test_invalid_ui_action_raises() -> None:
    with pytest.raises(ValidationError):
        UIComponent(component_type="none", action="cancel")


def test_envelope_extra_field_raises() -> None:
    kwargs = _base_envelope_kwargs()
    kwargs["unexpected"] = "blocked"

    with pytest.raises(ValidationError):
        ResponseEnvelope(**kwargs)


def test_ui_component_extra_field_raises() -> None:
    with pytest.raises(ValidationError):
        UIComponent(component_type="none", unexpected="blocked")


def test_all_target_system_values_in_binding_required() -> None:
    builder = _builder()

    for target in ("oa", "u8", "hikvision_ivms"):
        envelope = builder.build_binding_required(
            *_message_args(),
            target_system=target,
        )

        assert envelope.ui.component_type == "binding_required_card"
        assert envelope.ui.target_system == target


def test_invalid_target_system_raises() -> None:
    with pytest.raises(ValidationError):
        BindingRequiredCard(action="bind_required", target_system="sap")
