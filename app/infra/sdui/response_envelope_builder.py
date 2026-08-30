"""Minimal SDUI ResponseEnvelope builder for Phase 0."""

from __future__ import annotations

from typing import Any

from app.infra.sdui.credential_markers import has_credential_marker
from app.ports.response_envelope import (
    BindingRequiredCard,
    ConfirmCard,
    OperatorHandbackCard,
    ResponseEnvelope,
    ResponseEnvelopeStatus,
    TargetSystem,
    UIComponent,
)

REDACTED_TEXT = "[REDACTED]"
_DEFAULT_FAILURE_MESSAGE = "Unable to build response envelope."
_DEFAULT_FAILURE_FALLBACK = "Unable to produce a response. Please retry."
_DATALESS_STATUSES = {"blocked", "waiting_user", "no_capability_found"}


class ResponseEnvelopeBuilder:
    """Build validated Phase 0 SDUI ResponseEnvelope instances."""

    def build_message(
        self,
        response_id: str,
        task_id: str,
        session_id: str,
        message: str,
        fallback_text: str,
        trace_id: str,
        status: ResponseEnvelopeStatus = "completed",
        data: dict[str, Any] | None = None,
        trace_summary: str | None = None,
    ) -> ResponseEnvelope:
        return self._build_envelope(
            response_id,
            task_id,
            session_id,
            message,
            fallback_text,
            trace_id,
            status,
            UIComponent,
            {"component_type": "none", "action": "none", "payload": {}},
            data,
            trace_summary,
        )

    def build_confirm_card(
        self,
        response_id: str,
        task_id: str,
        session_id: str,
        message: str,
        fallback_text: str,
        trace_id: str,
        data: dict[str, Any] | None = None,
        trace_summary: str | None = None,
        payload: dict[str, Any] | None = None,
        target_system: TargetSystem | None = None,
    ) -> ResponseEnvelope:
        return self._build_envelope(
            response_id,
            task_id,
            session_id,
            message,
            fallback_text,
            trace_id,
            "waiting_user",
            ConfirmCard,
            {
                "action": "confirm",
                "target_system": target_system,
                "payload": payload or {},
            },
            data,
            trace_summary,
        )

    def build_binding_required(
        self,
        response_id: str,
        task_id: str,
        session_id: str,
        message: str,
        fallback_text: str,
        trace_id: str,
        target_system: TargetSystem,
        data: dict[str, Any] | None = None,
        trace_summary: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> ResponseEnvelope:
        return self._build_envelope(
            response_id,
            task_id,
            session_id,
            message,
            fallback_text,
            trace_id,
            "blocked",
            BindingRequiredCard,
            {
                "action": "bind_required",
                "target_system": target_system,
                "reason_code": "identity_unbound",
                "payload": payload or {},
            },
            data,
            trace_summary,
        )

    def build_operator_handback(
        self,
        response_id: str,
        task_id: str,
        session_id: str,
        message: str,
        fallback_text: str,
        trace_id: str,
        data: dict[str, Any] | None = None,
        trace_summary: str | None = None,
        payload: dict[str, Any] | None = None,
        target_system: TargetSystem | None = None,
    ) -> ResponseEnvelope:
        return self._build_envelope(
            response_id,
            task_id,
            session_id,
            message,
            fallback_text,
            trace_id,
            "blocked",
            OperatorHandbackCard,
            {
                "action": "clarify_scope",
                "target_system": target_system,
                "reason_code": "needs_binding_scope",
                "payload": payload or {},
            },
            data,
            trace_summary,
        )

    def build_no_capability_found(
        self,
        response_id: str,
        task_id: str,
        session_id: str,
        message: str,
        fallback_text: str,
        trace_id: str,
        trace_summary: str | None = None,
    ) -> ResponseEnvelope:
        return self._build_operator_handback_none(
            response_id,
            task_id,
            session_id,
            message,
            fallback_text,
            trace_id,
            "no_capability_found",
            trace_summary=trace_summary,
        )

    def build_policy_denied(
        self,
        response_id: str,
        task_id: str,
        session_id: str,
        message: str,
        fallback_text: str,
        trace_id: str,
        trace_summary: str | None = None,
    ) -> ResponseEnvelope:
        return self._build_operator_handback_none(
            response_id,
            task_id,
            session_id,
            message,
            fallback_text,
            trace_id,
            "blocked",
            trace_summary=trace_summary,
        )

    def build_operator_handback_bind_required(
        self,
        response_id: str,
        task_id: str,
        session_id: str,
        message: str,
        fallback_text: str,
        trace_id: str,
        target_system: TargetSystem | None,
        data: dict[str, Any] | None = None,
        trace_summary: str | None = None,
        payload: dict[str, Any] | None = None,
        reason_code: str = "identity_unbound",
    ) -> ResponseEnvelope:
        return self._build_envelope(
            response_id,
            task_id,
            session_id,
            message,
            fallback_text,
            trace_id,
            "blocked",
            OperatorHandbackCard,
            {
                "action": "bind_required",
                "target_system": target_system,
                "reason_code": reason_code,
                "payload": payload or {},
            },
            data,
            trace_summary,
        )

    def build_failed(
        self,
        response_id: str,
        task_id: str,
        session_id: str,
        message: str,
        fallback_text: str,
        trace_id: str,
        data: dict[str, Any] | None = None,
        trace_summary: str | None = None,
    ) -> ResponseEnvelope:
        return self._build_envelope(
            response_id,
            task_id,
            session_id,
            message,
            fallback_text,
            trace_id,
            "failed",
            UIComponent,
            {"component_type": "none", "action": "none", "payload": {}},
            data,
            trace_summary,
        )

    def serialize(self, envelope: ResponseEnvelope) -> str:
        return envelope.model_dump_json()

    def _build_envelope(
        self,
        response_id: str,
        task_id: str,
        session_id: str,
        message: str,
        fallback_text: str,
        trace_id: str,
        status: ResponseEnvelopeStatus,
        ui_model: type[UIComponent],
        ui_fields: dict[str, Any],
        data: dict[str, Any] | None,
        trace_summary: str | None,
    ) -> ResponseEnvelope:
        context = {
            "response": response_id,
            "task": task_id,
            "sid": session_id,
            "trace": trace_id,
        }
        try:
            sanitized_ui_fields = _sanitize_ui_fields(ui_fields)
            ui = ui_model(**sanitized_ui_fields)
            sanitized_fields = _sanitize_envelope_fields(
                message=message,
                fallback_text=fallback_text,
                data=data,
                trace_summary=trace_summary,
            )
            fallback = sanitized_fields["fallback_text"]
            if not isinstance(fallback, str) or not fallback.strip():
                raise ValueError("fallback_text must be non-empty")

            envelope_fields: dict[str, Any] = {
                "response_id": _sanitize_identifier(response_id),
                "task_id": _sanitize_identifier(task_id),
                "session_id": _sanitize_identifier(session_id),
                "status": status,
                "message": sanitized_fields["message"],
                "fallback_text": fallback,
                "ui": ui,
                "data": _normalized_data(status, sanitized_fields["data"]),
                "trace_id": _sanitize_identifier(trace_id),
                "trace_summary": sanitized_fields["trace_summary"],
            }
            return ResponseEnvelope(**envelope_fields)
        except Exception:
            return _failed_envelope(context)

    def _build_operator_handback_none(
        self,
        response_id: str,
        task_id: str,
        session_id: str,
        message: str,
        fallback_text: str,
        trace_id: str,
        status: ResponseEnvelopeStatus,
        trace_summary: str | None = None,
    ) -> ResponseEnvelope:
        return self._build_envelope(
            response_id,
            task_id,
            session_id,
            message,
            fallback_text,
            trace_id,
            status,
            UIComponent,
            {
                "component_type": "operator_handback_card",
                "action": "none",
                "payload": {},
            },
            None,
            trace_summary,
        )


def _sanitize_envelope_fields(
    *,
    message: str,
    fallback_text: str,
    data: dict[str, Any] | None,
    trace_summary: str | None,
) -> dict[str, Any]:
    return {
        "message": _sanitize_value(message),
        "fallback_text": _sanitize_value(fallback_text),
        "data": _sanitize_value(data),
        "trace_summary": _sanitize_value(trace_summary),
    }


def _sanitize_ui_fields(ui_fields: dict[str, Any]) -> dict[str, Any]:
    sanitized_fields = dict(ui_fields)
    sanitized_fields["payload"] = _sanitize_value(sanitized_fields.get("payload", {}))
    return sanitized_fields


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, str):
        if has_credential_marker(value):
            return REDACTED_TEXT
        return value
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, dict):
        return _sanitize_mapping(value)
    return value


def _sanitize_mapping(value: dict[Any, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for raw_key, raw_item in value.items():
        key = str(raw_key)
        if has_credential_marker(key):
            sanitized[REDACTED_TEXT] = REDACTED_TEXT
        else:
            sanitized[key] = _sanitize_value(raw_item)
    return sanitized


def _sanitize_identifier(value: str) -> str:
    sanitized = _sanitize_value(value)
    if isinstance(sanitized, str):
        return sanitized
    return "unavailable"


def _normalized_data(status: ResponseEnvelopeStatus, data: Any) -> dict[str, Any] | None:
    if status in _DATALESS_STATUSES and data is None:
        return None
    if data is None:
        return None
    if isinstance(data, dict):
        return data
    return {"value": data}


def _failed_envelope(context: dict[str, str]) -> ResponseEnvelope:
    failed_fields: dict[str, Any] = {
        "response_id": _usable_identifier(context.get("response"), "response-unavailable"),
        "task_id": _usable_identifier(context.get("task"), "task-unavailable"),
        "session_id": _usable_identifier(context.get("sid"), "sid-unavailable"),
        "status": "failed",
        "message": _DEFAULT_FAILURE_MESSAGE,
        "fallback_text": _DEFAULT_FAILURE_FALLBACK,
        "ui": UIComponent(component_type="none", action="none"),
        "data": None,
        "trace_id": _usable_identifier(context.get("trace"), "trace-unavailable"),
        "trace_summary": None,
    }
    return ResponseEnvelope(**failed_fields)


def _usable_identifier(value: str | None, default: str) -> str:
    if isinstance(value, str) and value.strip():
        return _sanitize_identifier(value)
    return default


__all__ = ("ResponseEnvelopeBuilder",)
