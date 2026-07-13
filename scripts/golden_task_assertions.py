"""Assertion helpers for Golden Task runner judgments."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

JudgementStatus = Literal["passed", "failed"]


@dataclass(frozen=True)
class AssertionJudgement:
    status: JudgementStatus
    reasons: list[str]


_TERMINAL_EVENT_MATRIX: dict[str, dict[str, tuple[str, ...]]] = {
    "no_capability_found": {
        "must_have": (
            "task_created",
            "intent_parsed",
            "no_capability_found",
            "response_envelope_created",
        ),
        "must_not_have": (
            "identity_check",
            "policy_checked",
            "adapter_called",
            "gateway_pre_recorded",
            "gateway_post_recorded",
        ),
    },
    "policy_denied": {
        "must_have": (
            "task_created",
            "capability_selected",
            "identity_check",
            "policy_checked",
            "blocked_by_policy",
            "response_envelope_created",
        ),
        "must_not_have": (
            "adapter_called",
            "gateway_post_recorded",
            "task_completed",
        ),
    },
    "identity_unbound": {
        "must_have": (
            "task_created",
            "intent_parsed",
            "capability_selected",
            "identity_check",
            "blocked_by_identity",
            "response_envelope_created",
        ),
        "must_not_have": (
            "policy_checked",
            "adapter_called",
            "fallback_to_system_scope",
        ),
    },
    "needs_binding_scope": {
        "must_have": (
            "task_created",
            "intent_parsed",
            "capability_selected",
            "identity_check",
            "blocked_by_identity",
            "response_envelope_created",
        ),
        "must_not_have": (
            "adapter_called",
            "fallback_to_first_binding",
            "fallback_to_system_scope",
        ),
    },
    "confirm_required": {
        "must_have": (
            "task_created",
            "capability_selected",
            "identity_check",
            "policy_checked",
            "confirm_required",
            "response_envelope_created",
        ),
        "must_not_have": (
            "adapter_called",
            "gateway_post_recorded",
            "task_completed",
        ),
    },
    "adapter_timeout": {
        "must_have": (
            "task_created",
            "capability_selected",
            "identity_check",
            "policy_checked",
            "gateway_pre_recorded",
            "adapter_called",
            "gateway_post_recorded",
            "adapter_error_mapped",
            "response_envelope_created",
        ),
        "must_not_have": ("task_completed",),
    },
}
_TERMINAL_STATE_ALIASES = {
    "unbound": "identity_unbound",
    "capability_not_found": "no_capability_found",
}

_CREDENTIAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\bauthorization\s*:\s*(bearer|basic)\s+\S+"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)\bcookie\s*:\s*.*\b(sessionid|access_token|refresh_token)\s*="),
    re.compile(r"(?i)\bsessionid\s*=\s*[^;\s]+"),
    re.compile(r"(?i)\b(access_token|refresh_token|api_key)\s*[:=]\s*[^;\s]+"),
    re.compile(r"(?i)\b(password|passwd)\s*[:=]\s*[^;\s]+"),
)
_INTERNAL_URL_PATTERN = re.compile(
    r"(?i)\bhttps?://(?:localhost|127\.0\.0\.1|10\.\d+\.\d+\.\d+|"
    r"172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+|192\.168\.\d+\.\d+|[^/\s]+\.internal)\b"
)


def assert_response_matches(envelope: Any, expected_response: Mapping[str, Any]) -> None:
    envelope_data = _to_plain(envelope)
    if not isinstance(envelope_data, Mapping):
        raise AssertionError("response envelope is not mapping-like")

    expected_status = expected_response.get("status")
    if expected_status is not None:
        actual_status = envelope_data.get("status")
        if actual_status != expected_status:
            raise AssertionError(
                f"response status expected {expected_status!r}, got {actual_status!r}"
            )

    if "error_code" in expected_response:
        _assert_dotted_value(envelope_data, "error_code", expected_response["error_code"])

    expected_envelope = expected_response.get("envelope") or {}
    if not isinstance(expected_envelope, Mapping):
        raise AssertionError("then_response.envelope must be a mapping")

    message = str(envelope_data.get("message", ""))
    for substring in _as_list(expected_envelope.get("message_contains")):
        if str(substring) not in message:
            raise AssertionError(
                f"response message missing substring {substring!r}: {message!r}"
            )

    for dotted_key, expected_value in expected_envelope.items():
        if dotted_key == "message_contains":
            continue
        if not isinstance(dotted_key, str):
            raise AssertionError(f"response assertion key is not a string: {dotted_key!r}")
        _assert_dotted_value(envelope_data, dotted_key, expected_value)


def assert_trace_sequence_contains(
    trace_steps: Iterable[Any],
    expected_events: Iterable[str],
) -> None:
    actual_events = [_event_type(step) for step in trace_steps]
    search_from = 0
    for expected_event in expected_events:
        for index in range(search_from, len(actual_events)):
            if actual_events[index] == expected_event:
                search_from = index + 1
                break
        else:
            raise AssertionError(
                f"trace missing expected event {expected_event!r} after index "
                f"{search_from - 1}; actual={actual_events!r}"
            )


def assert_terminal_state_matrix(
    trace_steps: Iterable[Any],
    terminal_state: str | None,
) -> None:
    if terminal_state is None or terminal_state not in _TERMINAL_EVENT_MATRIX:
        return

    actual_events = [_event_type(step) for step in trace_steps]
    matrix = _TERMINAL_EVENT_MATRIX[terminal_state]
    for event in matrix["must_have"]:
        if event not in actual_events:
            raise AssertionError(
                f"terminal state {terminal_state!r} missing must-have event {event!r}"
            )
    for event in matrix["must_not_have"]:
        if event in actual_events:
            raise AssertionError(
                f"terminal state {terminal_state!r} must not include event {event!r}"
            )


def assert_forbidden_absent(
    forbidden_items: Iterable[str],
    envelope: Any,
    trace_steps: Iterable[Any],
    adapter_calls: Mapping[str, Any],
) -> None:
    envelope_data = _to_plain(envelope)
    trace_data = _to_plain(list(trace_steps))
    value_strings = list(_iter_string_values(envelope_data)) + list(
        _iter_string_values(trace_data)
    )
    key_value_strings = list(_iter_key_value_strings(envelope_data)) + list(
        _iter_key_value_strings(trace_data)
    )
    all_strings = value_strings + key_value_strings

    for item in forbidden_items:
        if item == "trace_contains_token":
            _assert_no_credential_values(all_strings)
        elif item == "response_contains_internal_url":
            _assert_no_internal_urls(all_strings)
        elif item == "response_data_not_null":
            if isinstance(envelope_data, Mapping) and envelope_data.get("data") is not None:
                raise AssertionError("response data must be null")
        elif item == "mock_adapter_was_called":
            if _total_adapter_calls(adapter_calls) > 0:
                raise AssertionError("mock adapter was called")
        elif item == "mock_oa_was_called":
            if _adapter_call_count(adapter_calls, "oa") > 0:
                raise AssertionError("mock OA adapter was called")
        elif item == "mock_u8_was_called":
            if _adapter_call_count(adapter_calls, "u8") > 0:
                raise AssertionError("mock U8 adapter was called")
        elif item == "mock_ivms_was_called":
            if _adapter_call_count(adapter_calls, "hikvision_ivms") > 0:
                raise AssertionError("mock iVMS adapter was called")
        elif item == "fallback_to_first_binding":
            if _contains_tokenish_value(all_strings, "fallback_to_first_binding"):
                raise AssertionError("fallback_to_first_binding appeared")
        elif item == "fallback_to_system_scope":
            if _contains_tokenish_value(all_strings, "fallback_to_system_scope"):
                raise AssertionError("fallback_to_system_scope appeared")
            if any(value == "system_scope" for value in all_strings):
                raise AssertionError("system_scope fallback appeared")
        elif item == "video_frame_payload":
            if any(
                "video_frame" in value or "frame_payload" in value
                for value in all_strings
            ):
                raise AssertionError("video frame payload appeared")
        elif item == "multi_turn_confirm_loop":
            if [_event_type(step) for step in trace_data].count("confirm_required") > 1:
                raise AssertionError("multi-turn confirm loop appeared")
        else:
            raise AssertionError(f"unknown forbidden item: {item}")


def assert_adapter_calls(
    adapter_assertion: Mapping[str, Any],
    adapter_calls: Mapping[str, Any],
) -> None:
    total_calls = _total_adapter_calls(adapter_calls)
    if adapter_assertion.get("must_be_called") is True and total_calls <= 0:
        raise AssertionError("adapter must be called at least once")
    if adapter_assertion.get("must_not_be_called") is True and total_calls > 0:
        raise AssertionError("adapter must not be called")


def judge_assertions(
    *,
    envelope: Any,
    expected_response: Mapping[str, Any],
    trace_steps: list[Any],
    expected_trace: Mapping[str, Any],
    forbidden_items: Iterable[str],
    adapter_assertion: Mapping[str, Any],
    adapter_calls: Mapping[str, Any],
) -> AssertionJudgement:
    reasons: list[str] = []
    _capture_failure(reasons, assert_response_matches, envelope, expected_response)
    _capture_failure(
        reasons,
        assert_trace_sequence_contains,
        trace_steps,
        _as_list(expected_trace.get("event_sequence")),
    )
    terminal_state = _terminal_state(expected_response, expected_trace)
    _capture_failure(reasons, assert_terminal_state_matrix, trace_steps, terminal_state)
    _capture_failure(
        reasons,
        assert_forbidden_absent,
        forbidden_items,
        envelope,
        trace_steps,
        adapter_calls,
    )
    _capture_failure(reasons, assert_adapter_calls, adapter_assertion, adapter_calls)
    return AssertionJudgement(
        status="failed" if reasons else "passed",
        reasons=reasons,
    )


def _capture_failure(
    reasons: list[str],
    assertion: Any,
    *args: Any,
) -> None:
    try:
        assertion(*args)
    except AssertionError as exc:
        reasons.append(str(exc))


def _terminal_state(
    expected_response: Mapping[str, Any],
    expected_trace: Mapping[str, Any],
) -> str | None:
    reason = expected_trace.get("reason")
    if isinstance(reason, str):
        return _normalize_terminal_state(reason)
    error_code = expected_response.get("error_code")
    if isinstance(error_code, str):
        return _normalize_terminal_state(error_code)
    status = expected_response.get("status")
    if isinstance(status, str):
        return _normalize_terminal_state(status)
    return None


def _normalize_terminal_state(value: str) -> str:
    return _TERMINAL_STATE_ALIASES.get(value, value)


def _assert_dotted_value(
    envelope_data: Mapping[str, Any],
    dotted_key: str,
    expected_value: Any,
) -> None:
    try:
        actual_value = _resolve_dotted_key(envelope_data, dotted_key)
    except KeyError as exc:
        raise AssertionError(f"response key {dotted_key!r} missing") from exc
    if actual_value != expected_value:
        raise AssertionError(
            f"response key {dotted_key!r} expected {expected_value!r}, "
            f"got {actual_value!r}"
        )


def _resolve_dotted_key(root: Any, dotted_key: str) -> Any:
    parts = dotted_key.split(".")
    if parts and parts[-1] == "length":
        parent = _resolve_parts(root, parts[:-1])
        try:
            return len(parent)
        except TypeError as exc:
            raise KeyError(dotted_key) from exc
    return _resolve_parts(root, parts)


def _resolve_parts(root: Any, parts: list[str]) -> Any:
    current = root
    for part in parts:
        current = _to_plain(current)
        if isinstance(current, Mapping):
            if part not in current:
                raise KeyError(part)
            current = current[part]
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            try:
                current = current[index]
            except IndexError as exc:
                raise KeyError(part) from exc
        elif hasattr(current, part):
            current = getattr(current, part)
        else:
            raise KeyError(part)
    return _to_plain(current)


def _event_type(step: Any) -> str:
    if isinstance(step, str):
        return step
    if isinstance(step, Mapping):
        value = step.get("event_type") or step.get("event") or step.get("type")
        return str(value) if value is not None else ""
    value = getattr(step, "event_type", None)
    return str(value) if value is not None else ""


def _to_plain(value: Any) -> Any:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump()
    if isinstance(value, Mapping):
        return {str(key): _to_plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_plain(item) for item in value]
    if isinstance(value, tuple):
        return [_to_plain(item) for item in value]
    return value


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _iter_string_values(value: Any) -> Iterable[str]:
    value = _to_plain(value)
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _iter_string_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_string_values(item)


def _iter_key_value_strings(value: Any) -> Iterable[str]:
    value = _to_plain(value)
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(item, str):
                yield f"{key}: {item}"
            yield from _iter_key_value_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_key_value_strings(item)


def _assert_no_credential_values(values: Iterable[str]) -> None:
    for value in values:
        for pattern in _CREDENTIAL_PATTERNS:
            if pattern.search(value):
                raise AssertionError(f"forbidden credential value pattern detected: {value!r}")


def _assert_no_internal_urls(values: Iterable[str]) -> None:
    for value in values:
        if _INTERNAL_URL_PATTERN.search(value):
            raise AssertionError(f"forbidden internal URL detected: {value!r}")


def _contains_tokenish_value(values: Iterable[str], needle: str) -> bool:
    return any(needle in value for value in values)


def _adapter_call_count(adapter_calls: Mapping[str, Any], adapter_name: str) -> int:
    value = adapter_calls.get(adapter_name, 0)
    if isinstance(value, int):
        return value
    call_count = getattr(value, "call_count", 0)
    if isinstance(call_count, int):
        return call_count
    return 0


def _total_adapter_calls(adapter_calls: Mapping[str, Any]) -> int:
    return sum(_adapter_call_count(adapter_calls, key) for key in adapter_calls)


__all__ = (
    "AssertionJudgement",
    "assert_adapter_calls",
    "assert_forbidden_absent",
    "assert_response_matches",
    "assert_terminal_state_matrix",
    "assert_trace_sequence_contains",
    "judge_assertions",
)
