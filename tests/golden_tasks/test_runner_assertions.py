"""Positive meta-tests for Golden Task runner assertions."""

from __future__ import annotations

import importlib
from collections.abc import Callable, Iterable, Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    assert_adapter_calls: Callable[[Mapping[str, Any], Mapping[str, Any]], None]
    assert_forbidden_absent: Callable[
        [Iterable[str], Any, Iterable[Any], Mapping[str, Any]],
        None,
    ]
    assert_response_matches: Callable[[Any, Mapping[str, Any]], None]
    assert_terminal_state_matrix: Callable[[Iterable[Any], str | None], None]
    assert_trace_sequence_contains: Callable[[Iterable[Any], Iterable[str]], None]
else:
    from scripts.golden_task_assertions import (
        assert_adapter_calls,
        assert_forbidden_absent,
        assert_response_matches,
        assert_terminal_state_matrix,
        assert_trace_sequence_contains,
    )


def test_injection_companion_judgement_accepts_matching_timeout_error_code() -> None:
    runner = importlib.import_module("scripts.golden_task_evaluator")
    helper = getattr(runner, "judge_injection_companion_assertions", None)
    assert helper is not None, "injection companion assertion helper is missing"
    trace = [
        {"event_type": "task_created"},
        {"event_type": "capability_selected"},
        {"event_type": "identity_check"},
        {"event_type": "policy_checked"},
        {"event_type": "gateway_pre_recorded"},
        {"event_type": "adapter_called"},
        {"event_type": "gateway_post_recorded", "error_code": "adapter_timeout"},
        {"event_type": "adapter_error_mapped", "error_code": "adapter_timeout"},
        {"event_type": "response_envelope_created"},
    ]

    judgement = helper(
        envelope={"status": "failed", "message": "操作超时", "ui": {}, "data": None},
        trace_steps=trace,
        expected_error_code="adapter_timeout",
        adapter_assertion={"must_be_called": True, "must_not_be_called": False},
        adapter_calls={"oa": 1, "u8": 0, "hikvision_ivms": 0},
    )

    assert judgement.status == "passed"
    assert judgement.reasons == []


def test_response_assertions_accept_dotted_keys_and_length() -> None:
    envelope: dict[str, Any] = {
        "status": "completed",
        "message": "OA 待办共 3 条",
        "ui": {"component_type": "none", "action": "none"},
        "data": {
            "workflows": [
                {"workflow_id": "OA-WF-2026-0001"},
                {"workflow_id": "OA-WF-2026-0002"},
                {"workflow_id": "OA-WF-2026-0003"},
            ]
        },
    }
    expected = {
        "status": "completed",
        "envelope": {
            "message_contains": ["OA", "待办"],
            "ui.component_type": "none",
            "data.workflows.length": 3,
        },
    }

    assert_response_matches(envelope, expected)

    assert len(envelope["data"]["workflows"]) == 3


def test_trace_sequence_assertion_accepts_ordered_subsequence() -> None:
    actual_trace = [
        {"event_type": "task_created"},
        {"event_type": "intent_parsed"},
        {"event_type": "capability_selected"},
        {"event_type": "gateway_pre_recorded"},
        {"event_type": "gateway_post_recorded"},
        {"event_type": "response_envelope_created"},
    ]

    assert_trace_sequence_contains(
        actual_trace,
        ["task_created", "capability_selected", "response_envelope_created"],
    )

    assert actual_trace[2]["event_type"] == "capability_selected"


def test_terminal_matrix_accepts_no_capability_found_short_circuit() -> None:
    actual_trace = [
        {"event_type": "task_created"},
        {"event_type": "intent_parsed"},
        {"event_type": "no_capability_found"},
        {"event_type": "response_envelope_created"},
    ]

    assert_terminal_state_matrix(actual_trace, "no_capability_found")

    assert len(actual_trace) == 4


def test_forbidden_credentials_ignore_fixture_placeholder_identifiers() -> None:
    envelope: dict[str, Any] = {
        "status": "completed",
        "message": "绑定 bind_oa_001 的用户 user_employee_001 已处理",
        "session_id": "session-1",
        "ui": {"component_type": "none", "action": "none"},
        "data": {"binding_id": "bind_oa_001", "ai_user_id": "user_employee_001"},
    }
    trace = [
        {
            "event_type": "identity_check",
            "attributes": {
                "ai_user_id": "user_employee_001",
                "binding_id": "bind_oa_001",
            },
        }
    ]

    assert_forbidden_absent(["trace_contains_token"], envelope, trace, {})

    assert envelope["data"]["binding_id"] == "bind_oa_001"


def test_adapter_assertion_accepts_required_call_count() -> None:
    adapter_calls = {
        "oa": 1,
        "u8": 0,
        "hikvision_ivms": 0,
    }

    assert_adapter_calls(
        {"must_be_called": True, "must_not_be_called": False},
        adapter_calls,
    )

    assert sum(adapter_calls.values()) == 1
