"""Assertion helpers for Golden Task runner judgments."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast

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
    "identity_expired": {
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
    "identity_revoked": {
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
_TERMINAL_ERROR_CODE_EVENTS: dict[str, tuple[str, ...]] = {
    "identity_expired": ("identity_check", "blocked_by_identity"),
    "identity_revoked": ("identity_check", "blocked_by_identity"),
    "needs_binding_scope": ("identity_check", "blocked_by_identity"),
}
_TERMINAL_STATE_ALIASES = {
    "unbound": "identity_unbound",
    "capability_not_found": "no_capability_found",
}

_OA_CREDENTIAL_FIELD_NAMES = (
    "sessionkey",
    "datakey",
    "session_key",
    "data_key",
)
_OA_CREDENTIAL_FIELD_ALTERNATION = "|".join(
    re.escape(name) for name in _OA_CREDENTIAL_FIELD_NAMES
)
_CredentialLocation = Literal[
    "actual.envelope",
    "actual.trace",
    "actual.assertion_inputs",
    "fixture.expectations",
]
_MAX_CREDENTIAL_SCAN_DEPTH = 64
_MAX_CREDENTIAL_SCAN_NODES = 10_000
_MAX_CREDENTIAL_SCAN_STRING_CHARS = 1_000_000
_MAX_CREDENTIAL_SCAN_TOTAL_CHARS = 8_000_000
_MAX_SERIALIZED_JSON_CHARS = 256_000

# Credential text keeps the legacy rules. Standard serialized JSON objects are
# separately parsed once after explicit character, depth, and node preflight limits.
_NON_COOKIE_CREDENTIAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "authorization_header",
        re.compile(r"(?i)\bauthorization\s*:\s*(bearer|basic)\s+\S+"),
    ),
    ("bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")),
    (
        "sessionid",
        re.compile(r"(?i)\bsessionid\s*=\s*[^;\s]+"),
    ),
    (
        "token_or_api_key",
        re.compile(
            r"(?i)\b(access_token|refresh_token|api_key)\s*[:=]\s*[^;\s]+"
        ),
    ),
    (
        "password_or_passwd",
        re.compile(r"(?i)\b(password|passwd)\s*[:=]\s*[^;\s]+"),
    ),
    (
        "oa_session_or_data_key",
        re.compile(
            rf"\b(?:{_OA_CREDENTIAL_FIELD_ALTERNATION})\s*[:=]\s*[^;\s]+",
            re.IGNORECASE,
        ),
    ),
)
_RAW_COOKIE_HEADER_PATTERN = re.compile(r"(?i)\bcookie\s*:")
_COOKIE_CREDENTIAL_NAME_PATTERN = re.compile(
    r"(?i)\b(sessionid|access_token|refresh_token)"
)
_CREDENTIAL_FAILURE_PREFIX = "forbidden credential pattern detected: "
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


def assert_trace_event_details(
    trace_steps: Iterable[Any],
    expected_details: Iterable[Mapping[str, Any]],
) -> None:
    actual_steps = [_to_plain(step) for step in trace_steps]
    search_from = 0
    for expected in expected_details:
        for index in range(search_from, len(actual_steps)):
            if _mapping_contains(actual_steps[index], expected):
                search_from = index + 1
                break
        else:
            raise AssertionError(
                f"trace missing expected event details {dict(expected)!r} after index "
                f"{search_from - 1}"
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
    for event in _TERMINAL_ERROR_CODE_EVENTS.get(terminal_state, ()):
        matching_steps = [step for step in trace_steps if _event_type(step) == event]
        if not matching_steps:
            continue
        for step in matching_steps:
            actual_error_code = _step_error_code(step)
            if actual_error_code != terminal_state:
                raise AssertionError(
                    f"terminal state {terminal_state!r} event {event!r} expected "
                    f"error_code {terminal_state!r}, got {actual_error_code!r}"
                )


def assert_forbidden_absent(
    forbidden_items: Iterable[str],
    envelope: Any,
    trace_steps: Iterable[Any],
    adapter_calls: Mapping[str, Any],
) -> None:
    forbidden_items = tuple(forbidden_items)
    trace_items = list(trace_steps)

    if "trace_contains_token" in forbidden_items:
        _assert_no_credential_values(envelope, location="actual.envelope")
        _assert_no_credential_values(trace_items, location="actual.trace")

    envelope_data = _to_plain(envelope)
    trace_data = _to_plain(trace_items)
    all_strings = (
        list(_iter_string_values(envelope_data))
        + list(_iter_string_values(trace_data))
        + list(_iter_key_value_strings(envelope_data))
        + list(_iter_key_value_strings(trace_data))
    )

    for item in forbidden_items:
        if item == "trace_contains_token":
            continue
        if item == "response_contains_internal_url":
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
    adapter_arguments: Mapping[str, Any] | None = None,
) -> None:
    total_calls = _total_adapter_calls(adapter_calls)
    if adapter_assertion.get("must_be_called") is True and total_calls <= 0:
        raise AssertionError("adapter must be called at least once")
    if adapter_assertion.get("must_not_be_called") is True and total_calls > 0:
        raise AssertionError("adapter must not be called")
    exact_calls = adapter_assertion.get("exact_calls", {})
    if not isinstance(exact_calls, Mapping):
        raise AssertionError("adapter exact_calls must be mapping-like")
    for capability_id, expected_count in exact_calls.items():
        if (
            not isinstance(expected_count, int)
            or isinstance(expected_count, bool)
            or expected_count < 0
        ):
            raise AssertionError(
                f"adapter exact call count for {capability_id!r} must be a non-negative int"
            )
        actual_count = _adapter_call_count(adapter_calls, str(capability_id))
        if actual_count != expected_count:
            raise AssertionError(
                f"adapter {capability_id!r} expected exactly {expected_count} calls, "
                f"got {actual_count}"
            )

    expected_arguments = adapter_assertion.get("exact_arguments", {})
    if not isinstance(expected_arguments, Mapping):
        raise AssertionError("adapter exact_arguments must be mapping-like")
    if expected_arguments and adapter_arguments is None:
        raise AssertionError("adapter arguments were not captured")
    for capability_id, expected in expected_arguments.items():
        actual = (adapter_arguments or {}).get(str(capability_id), [])
        if actual != expected:
            raise AssertionError(
                f"adapter {capability_id!r} arguments expected {expected!r}, got {actual!r}"
            )


def assert_workflow_evidence(
    workflow_events: Iterable[Any],
    workflow_assertion: Mapping[str, Any],
    first_round_envelope: Any = None,
    first_round_adapter_calls: Mapping[str, Any] | None = None,
    source_definition_version_after_first: str | None = None,
) -> None:
    events = [_to_plain(event) for event in workflow_events]
    actual_sequence = [_event_type(event) for event in events]
    expected_sequence = _as_list(workflow_assertion.get("event_sequence"))
    if actual_sequence != expected_sequence:
        raise AssertionError(
            f"workflow event sequence expected {expected_sequence!r}, got {actual_sequence!r}"
        )

    expected_version = workflow_assertion.get("workflow_version")
    if expected_version is not None:
        observed_versions = {
            event.get("payload", {}).get("workflow_version")
            for event in events
            if isinstance(event, Mapping) and isinstance(event.get("payload"), Mapping)
        }
        if observed_versions != {expected_version}:
            raise AssertionError(
                f"workflow event versions expected only {expected_version!r}, "
                f"got {observed_versions!r}"
            )

    expected_step_statuses = workflow_assertion.get("step_statuses")
    if expected_step_statuses is not None:
        actual_step_statuses = [
            event.get("payload", {}).get("step_status")
            for event in events
            if _event_type(event) == "workflow_step_finished"
        ]
        if actual_step_statuses != expected_step_statuses:
            raise AssertionError(
                f"workflow step statuses expected {expected_step_statuses!r}, "
                f"got {actual_step_statuses!r}"
            )

    expected_terminal_status = workflow_assertion.get("terminal_status")
    if expected_terminal_status is not None:
        terminal_payload = events[-1].get("payload", {}) if events else {}
        actual_terminal_status = terminal_payload.get("workflow_status")
        if actual_terminal_status != expected_terminal_status:
            raise AssertionError(
                f"workflow terminal status expected {expected_terminal_status!r}, "
                f"got {actual_terminal_status!r}"
            )

    expected_terminal_error_code = workflow_assertion.get("terminal_error_code")
    if expected_terminal_error_code is not None:
        terminal_payload = events[-1].get("payload", {}) if events else {}
        actual_terminal_error_code = terminal_payload.get("error_code")
        if actual_terminal_error_code != expected_terminal_error_code:
            raise AssertionError(
                f"workflow terminal error_code expected "
                f"{expected_terminal_error_code!r}, got {actual_terminal_error_code!r}"
            )

    first_round = workflow_assertion.get("first_round")
    if first_round is not None:
        if not isinstance(first_round, Mapping):
            raise AssertionError("workflow first_round assertion must be mapping-like")
        if first_round_envelope is None or first_round_adapter_calls is None:
            raise AssertionError("workflow first-round evidence was not captured")
        expected_response = first_round.get("response")
        if not isinstance(expected_response, Mapping):
            raise AssertionError("workflow first_round.response must be mapping-like")
        assert_response_matches(first_round_envelope, expected_response)
        exact_calls = first_round.get("exact_calls", {})
        if not isinstance(exact_calls, Mapping):
            raise AssertionError("workflow first_round.exact_calls must be mapping-like")
        assert_adapter_calls(
            {"exact_calls": exact_calls},
            first_round_adapter_calls,
        )

    expected_source_version = workflow_assertion.get(
        "source_definition_version_after_first"
    )
    if expected_source_version is not None and (
        source_definition_version_after_first != expected_source_version
    ):
        raise AssertionError(
            "workflow source definition version after first round expected "
            f"{expected_source_version!r}, got "
            f"{source_definition_version_after_first!r}"
        )


def assert_policy_calls(
    policy_assertion: Mapping[str, Any],
    policy_calls: int,
) -> None:
    if policy_assertion.get("must_be_called") is True and policy_calls <= 0:
        raise AssertionError("policy guard must be called at least once")
    if policy_assertion.get("must_not_be_called") is True and policy_calls > 0:
        raise AssertionError("policy guard must not be called")


def judge_assertions(
    *,
    envelope: Any,
    expected_response: Mapping[str, Any],
    trace_steps: list[Any],
    expected_trace: Mapping[str, Any],
    forbidden_items: Iterable[str],
    adapter_assertion: Mapping[str, Any],
    adapter_calls: Mapping[str, Any],
    adapter_arguments: Mapping[str, Any] | None = None,
    policy_assertion: Mapping[str, Any] | None = None,
    policy_calls: int = 0,
    workflow_assertion: Mapping[str, Any] | None = None,
    workflow_events: Iterable[Any] = (),
    first_round_envelope: Any = None,
    first_round_adapter_calls: Mapping[str, Any] | None = None,
    source_definition_version_after_first: str | None = None,
    credential_expectations: Mapping[str, Any] | None = None,
) -> AssertionJudgement:
    reasons: list[str] = []
    forbidden_items = tuple(forbidden_items)
    workflow_events = tuple(workflow_events)
    expected_credential_inputs = {
        "then_response": expected_response,
        "then_trace": expected_trace,
        "then_forbidden": forbidden_items,
        "adapter_assertion": adapter_assertion,
        "policy_assertion": policy_assertion,
        "then_workflow": workflow_assertion,
        "fixture_expectations": credential_expectations,
    }
    actual_credential_inputs = {
        "adapter_calls": adapter_calls,
        "adapter_arguments": adapter_arguments,
        "workflow_events": workflow_events,
        "first_round_envelope": first_round_envelope,
        "first_round_adapter_calls": first_round_adapter_calls,
        "source_definition_version_after_first": (
            source_definition_version_after_first
        ),
    }
    _capture_failure(
        reasons,
        _assert_judgement_credentials_absent,
        envelope,
        trace_steps,
        actual_credential_inputs,
        expected_credential_inputs,
    )
    if reasons:
        return AssertionJudgement(status="failed", reasons=reasons)

    _capture_failure(reasons, assert_response_matches, envelope, expected_response)
    _capture_failure(
        reasons,
        assert_trace_sequence_contains,
        trace_steps,
        _as_list(expected_trace.get("event_sequence")),
    )
    _capture_failure(
        reasons,
        assert_trace_event_details,
        trace_steps,
        cast(Iterable[Mapping[str, Any]], _as_list(expected_trace.get("event_details"))),
    )
    terminal_state = _terminal_state(expected_response, expected_trace)
    _capture_failure(reasons, assert_terminal_state_matrix, trace_steps, terminal_state)
    _capture_failure(
        reasons,
        assert_forbidden_absent,
        tuple(item for item in forbidden_items if item != "trace_contains_token"),
        envelope,
        trace_steps,
        adapter_calls,
    )
    _capture_failure(
        reasons,
        assert_adapter_calls,
        adapter_assertion,
        adapter_calls,
        adapter_arguments,
    )
    if policy_assertion is not None:
        _capture_failure(
            reasons,
            assert_policy_calls,
            policy_assertion,
            policy_calls,
        )
    if workflow_assertion is not None:
        _capture_failure(
            reasons,
            assert_workflow_evidence,
            workflow_events,
            workflow_assertion,
            first_round_envelope,
            first_round_adapter_calls,
            source_definition_version_after_first,
        )
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


def is_credential_failure_reason(reason: str) -> bool:
    return reason.startswith(_CREDENTIAL_FAILURE_PREFIX)


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


def _step_error_code(step: Any) -> str | None:
    plain_step = _to_plain(step)
    if not isinstance(plain_step, Mapping):
        return None
    value = plain_step.get("error_code")
    if value is None:
        attributes = plain_step.get("attributes")
        if isinstance(attributes, Mapping):
            value = attributes.get("error_code")
    return str(value) if value is not None else None


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


def _assert_no_credential_values(
    value: Any,
    *,
    location: _CredentialLocation,
) -> None:
    stack: list[tuple[Any, int, bool, bool]] = [(value, 0, False, True)]
    nodes_seen = 0
    total_chars_seen = 0
    while stack:
        current, depth, chars_counted, allow_serialized_json = stack.pop()
        nodes_seen += 1
        if nodes_seen > _MAX_CREDENTIAL_SCAN_NODES:
            _raise_credential_failure("scan_node_limit", location)
        if depth > _MAX_CREDENTIAL_SCAN_DEPTH:
            _raise_credential_failure("scan_depth_limit", location)

        model_dump = getattr(current, "model_dump", None)
        if callable(model_dump):
            current = model_dump()

        if isinstance(current, str):
            if not chars_counted:
                total_chars_seen = _account_credential_scan_chars(
                    current,
                    total_chars_seen,
                    location,
                )
            serialized_mapping = (
                _bounded_serialized_json_mapping(current, location)
                if allow_serialized_json
                else None
            )
            rule_name = _credential_text_rule(current)
            if rule_name is not None:
                _raise_credential_failure(rule_name, location)
            if serialized_mapping is not None:
                stack.append((serialized_mapping, depth + 1, False, False))
        elif isinstance(current, Mapping):
            for key, item in current.items():
                if depth >= _MAX_CREDENTIAL_SCAN_DEPTH:
                    _raise_credential_failure("scan_depth_limit", location)
                if nodes_seen + len(stack) >= _MAX_CREDENTIAL_SCAN_NODES:
                    _raise_credential_failure("scan_node_limit", location)
                if isinstance(key, str):
                    total_chars_seen = _account_credential_scan_chars(
                        key,
                        total_chars_seen,
                        location,
                    )
                    rule_name = _credential_text_rule(key)
                    if rule_name is not None:
                        _raise_credential_failure(rule_name, location)
                item_chars_counted = False
                item_allows_serialized_json = allow_serialized_json
                serialized_mapping = None
                if isinstance(item, str):
                    total_chars_seen = _account_credential_scan_chars(
                        item,
                        total_chars_seen,
                        location,
                    )
                    item_chars_counted = True
                    if allow_serialized_json:
                        serialized_mapping = _bounded_serialized_json_mapping(
                            item,
                            location,
                        )
                        item_allows_serialized_json = False
                    if isinstance(key, str):
                        pair_chars = len(key) + len(item) + 2
                        if pair_chars > _MAX_CREDENTIAL_SCAN_STRING_CHARS:
                            _raise_credential_failure(
                                "scan_string_char_limit",
                                location,
                            )
                        rule_name = _credential_text_rule(f"{key}: {item}")
                        if rule_name is not None:
                            _raise_credential_failure(rule_name, location)
                if serialized_mapping is not None:
                    stack.append((serialized_mapping, depth + 1, False, False))
                stack.append(
                    (
                        item,
                        depth + 1,
                        item_chars_counted,
                        item_allows_serialized_json,
                    )
                )
        elif isinstance(current, (list, tuple)):
            for item in current:
                if depth >= _MAX_CREDENTIAL_SCAN_DEPTH:
                    _raise_credential_failure("scan_depth_limit", location)
                if nodes_seen + len(stack) >= _MAX_CREDENTIAL_SCAN_NODES:
                    _raise_credential_failure("scan_node_limit", location)
                stack.append((item, depth + 1, False, allow_serialized_json))


def _assert_judgement_credentials_absent(
    envelope: Any,
    trace_steps: list[Any],
    actual_assertion_inputs: Mapping[str, Any],
    fixture_expectations: Mapping[str, Any],
) -> None:
    _assert_no_credential_values(envelope, location="actual.envelope")
    _assert_no_credential_values(trace_steps, location="actual.trace")
    _assert_no_credential_values(
        actual_assertion_inputs,
        location="actual.assertion_inputs",
    )
    _assert_no_credential_values(
        fixture_expectations,
        location="fixture.expectations",
    )


def _credential_text_rule(value: str) -> str | None:
    for rule_name, pattern in _NON_COOKIE_CREDENTIAL_PATTERNS[:2]:
        if pattern.search(value):
            return rule_name
    if _contains_credential_cookie(value):
        return "credential_cookie"
    for rule_name, pattern in _NON_COOKIE_CREDENTIAL_PATTERNS[2:]:
        if pattern.search(value):
            return rule_name
    return None


def _contains_credential_cookie(value: str) -> bool:
    # Preserve the legacy `Cookie\s*:\s*.*credential=` behavior without its
    # unbounded `.*`: the credential name starts on the first non-whitespace
    # content line, while whitespace on either side may fold across lines.
    credential_positions: list[int] = []
    for credential in _COOKIE_CREDENTIAL_NAME_PATTERN.finditer(value):
        equals_index = credential.end()
        while equals_index < len(value) and value[equals_index].isspace():
            equals_index += 1
        if equals_index < len(value) and value[equals_index] == "=":
            credential_positions.append(credential.start())
    if not credential_positions:
        return False

    credential_index = 0
    cached_line_end = -1
    for header in _RAW_COOKIE_HEADER_PATTERN.finditer(value):
        content_start = header.end()
        while content_start < len(value) and value[content_start].isspace():
            content_start += 1
        if content_start >= len(value):
            continue
        while (
            credential_index < len(credential_positions)
            and credential_positions[credential_index] < content_start
        ):
            credential_index += 1
        if credential_index >= len(credential_positions):
            return False
        if content_start > cached_line_end:
            cached_line_end = value.find("\n", content_start)
            if cached_line_end == -1:
                cached_line_end = len(value)
        if credential_positions[credential_index] < cached_line_end:
            return True
    return False


def _bounded_serialized_json_mapping(
    value: str,
    location: _CredentialLocation,
) -> Any | None:
    if not value.lstrip().startswith("{"):
        return None
    if len(value) > _MAX_SERIALIZED_JSON_CHARS:
        _raise_credential_failure("scan_serialized_json_char_limit", location)
    _assert_serialized_json_bounds(value, location)
    try:
        parsed = json.loads(value, object_pairs_hook=_preserve_json_object_pairs)
    except json.JSONDecodeError:
        return None
    except (RecursionError, ValueError):
        _raise_credential_failure("scan_serialized_json_parse_limit", location)
    return parsed


def _preserve_json_object_pairs(pairs: list[tuple[str, Any]]) -> list[Any]:
    return [{key: value} for key, value in pairs]


def _assert_serialized_json_bounds(
    value: str,
    location: _CredentialLocation,
) -> None:
    depth = 0
    structural_nodes = 0
    in_string = False
    escaped = False
    for character in value:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            structural_nodes += 1
            if depth > _MAX_CREDENTIAL_SCAN_DEPTH:
                _raise_credential_failure(
                    "scan_serialized_json_depth_limit",
                    location,
                )
        elif character in "]}":
            depth = max(0, depth - 1)
        elif character in ",:":
            structural_nodes += 1
        if structural_nodes > _MAX_CREDENTIAL_SCAN_NODES:
            _raise_credential_failure("scan_serialized_json_node_limit", location)


def _account_credential_scan_chars(
    value: str,
    total_chars_seen: int,
    location: _CredentialLocation,
) -> int:
    value_chars = len(value)
    if value_chars > _MAX_CREDENTIAL_SCAN_STRING_CHARS:
        _raise_credential_failure("scan_string_char_limit", location)
    total_chars_seen += value_chars
    if total_chars_seen > _MAX_CREDENTIAL_SCAN_TOTAL_CHARS:
        _raise_credential_failure("scan_total_char_limit", location)
    return total_chars_seen


def _raise_credential_failure(
    rule_name: str,
    location: _CredentialLocation,
) -> None:
    raise AssertionError(
        f"{_CREDENTIAL_FAILURE_PREFIX}rule={rule_name}; location={location}"
    )


def _assert_no_internal_urls(values: Iterable[str]) -> None:
    for value in values:
        if _INTERNAL_URL_PATTERN.search(value):
            raise AssertionError(f"forbidden internal URL detected: {value!r}")


def _contains_tokenish_value(values: Iterable[str], needle: str) -> bool:
    return any(needle in value for value in values)


def _adapter_call_count(adapter_calls: Mapping[str, Any], adapter_name: str) -> int:
    if adapter_name in adapter_calls:
        value = adapter_calls[adapter_name]
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        call_count = getattr(value, "call_count", None)
        return call_count if isinstance(call_count, int) else 0
    prefixes = {
        "oa": ("oa.",),
        "u8": ("u8.",),
        "hikvision_ivms": ("ivms.", "hikvision_ivms."),
    }.get(adapter_name, ())
    if prefixes:
        return sum(
            item
            for key, item in adapter_calls.items()
            if isinstance(item, int)
            and not isinstance(item, bool)
            and str(key).startswith(prefixes)
        )
    return 0


def _total_adapter_calls(adapter_calls: Mapping[str, Any]) -> int:
    return sum(_adapter_call_count(adapter_calls, key) for key in adapter_calls)


def _mapping_contains(actual: Any, expected: Mapping[str, Any]) -> bool:
    actual = _to_plain(actual)
    if not isinstance(actual, Mapping):
        return False
    for key, expected_value in expected.items():
        if key not in actual:
            return False
        actual_value = actual[key]
        if isinstance(expected_value, Mapping):
            if not _mapping_contains(actual_value, expected_value):
                return False
        elif actual_value != expected_value:
            return False
    return True


__all__ = (
    "AssertionJudgement",
    "assert_adapter_calls",
    "assert_forbidden_absent",
    "assert_policy_calls",
    "assert_response_matches",
    "assert_terminal_state_matrix",
    "assert_trace_event_details",
    "assert_trace_sequence_contains",
    "assert_workflow_evidence",
    "is_credential_failure_reason",
    "judge_assertions",
)
