"""Negative meta-tests for Golden Task runner assertions."""

from __future__ import annotations

import importlib
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import pytest

from scripts.check_weak_tests import check_source

if TYPE_CHECKING:

    class _AssertionJudgement(Protocol):
        status: str
        reasons: list[str]

    assert_adapter_calls: Callable[[Mapping[str, Any], Mapping[str, Any]], None]
    assert_forbidden_absent: Callable[
        [Iterable[str], Any, Iterable[Any], Mapping[str, Any]],
        None,
    ]
    assert_response_matches: Callable[[Any, Mapping[str, Any]], None]
    assert_terminal_state_matrix: Callable[[Iterable[Any], str | None], None]
    assert_trace_sequence_contains: Callable[[Iterable[Any], Iterable[str]], None]
    judge_assertions: Callable[..., _AssertionJudgement]
else:
    from tests.golden_tasks.assertions import (
        assert_adapter_calls,
        assert_forbidden_absent,
        assert_response_matches,
        assert_terminal_state_matrix,
        assert_trace_sequence_contains,
        judge_assertions,
    )


def test_missing_response_substring_raises_assertion_error() -> None:
    envelope = {
        "status": "completed",
        "message": "操作完成",
        "ui": {"component_type": "none", "action": "none"},
        "data": {},
    }
    expected = {
        "status": "completed",
        "envelope": {"message_contains": ["待办"]},
    }

    with pytest.raises(AssertionError, match="message"):
        assert_response_matches(envelope, expected)

    assert envelope["message"] == "操作完成"


def test_wrong_dotted_length_raises_assertion_error() -> None:
    envelope: dict[str, Any] = {
        "status": "completed",
        "message": "OA 待办共 2 条",
        "ui": {"component_type": "none", "action": "none"},
        "data": {"workflows": [{"id": "1"}, {"id": "2"}]},
    }
    expected = {
        "status": "completed",
        "envelope": {"data.workflows.length": 3},
    }

    with pytest.raises(AssertionError, match="data.workflows.length"):
        assert_response_matches(envelope, expected)

    assert len(envelope["data"]["workflows"]) == 2


def test_missing_trace_event_raises_assertion_error() -> None:
    actual_trace = [
        {"event_type": "task_created"},
        {"event_type": "intent_parsed"},
        {"event_type": "response_envelope_created"},
    ]

    with pytest.raises(AssertionError, match="capability_selected"):
        assert_trace_sequence_contains(
            actual_trace,
            ["task_created", "capability_selected", "response_envelope_created"],
        )

    assert actual_trace[-1]["event_type"] == "response_envelope_created"


def test_terminal_matrix_rejects_must_not_have_event() -> None:
    actual_trace = [
        {"event_type": "task_created"},
        {"event_type": "intent_parsed"},
        {"event_type": "no_capability_found"},
        {"event_type": "gateway_pre_recorded"},
        {"event_type": "response_envelope_created"},
    ]

    with pytest.raises(AssertionError, match="must not"):
        assert_terminal_state_matrix(actual_trace, "no_capability_found")

    assert actual_trace[3]["event_type"] == "gateway_pre_recorded"


def test_rewrite_detected_by_weak_test_checker(tmp_path: Path) -> None:
    weak_test_file = tmp_path / "test_rewrite_detected.py"
    weak_test_file.write_text(
        """
def test_rewrite_detected():
    assert True
""".lstrip(),
        encoding="utf-8",
    )
    strong_test_file = tmp_path / "test_strong_assertion.py"
    strong_test_file.write_text(
        """
def test_real_assertion():
    actual = {"status": "failed"}
    assert actual["status"] == "failed"
""".lstrip(),
        encoding="utf-8",
    )

    weak_findings = check_source(weak_test_file)
    strong_findings = check_source(strong_test_file)

    assert [(finding.function_name, finding.kind) for finding in weak_findings] == [
        ("test_rewrite_detected", "tautology")
    ]
    assert "assert True" in weak_findings[0].description
    assert strong_findings == []


def test_forbidden_credential_value_patterns_raise_assertion_error() -> None:
    envelope = {
        "status": "completed",
        "message": "Authorization: Bearer live-secret-token",
        "ui": {"component_type": "none", "action": "none"},
        "data": {"cookie_header": "cookie: sessionid=abc123"},
    }
    trace = [
        {
            "event_type": "gateway_pre_recorded",
            "attributes": {"request_headers": {"cookie": "sessionid=abc123"}},
        }
    ]

    with pytest.raises(AssertionError, match="credential"):
        assert_forbidden_absent(["trace_contains_token"], envelope, trace, {})

    assert "Bearer" in envelope["message"]


def test_unknown_forbidden_item_raises_assertion_error() -> None:
    with pytest.raises(AssertionError, match="unknown forbidden item"):
        assert_forbidden_absent(
            ["unknown_forbidden_item"],
            {"status": "completed", "message": "ok", "data": None},
            [],
            {"oa": 0, "u8": 0, "hikvision_ivms": 0},
        )


def test_injection_companion_judgement_rejects_absent_or_mismatched_error_code() -> None:
    runner = importlib.import_module("tests.golden_tasks.test_golden_tasks")
    helper = getattr(runner, "judge_injection_companion_assertions", None)
    assert helper is not None, "injection companion assertion helper is missing"
    base_trace = [
        {"event_type": "task_created"},
        {"event_type": "capability_selected"},
        {"event_type": "identity_check"},
        {"event_type": "policy_checked"},
        {"event_type": "gateway_pre_recorded"},
        {"event_type": "adapter_called"},
        {"event_type": "gateway_post_recorded"},
        {"event_type": "adapter_error_mapped"},
        {"event_type": "response_envelope_created"},
    ]

    absent = helper(
        envelope={"status": "failed", "message": "操作失败", "ui": {}, "data": None},
        trace_steps=base_trace,
        expected_error_code="adapter_timeout",
        adapter_assertion={"must_be_called": True, "must_not_be_called": False},
        adapter_calls={"oa": 1, "u8": 0, "hikvision_ivms": 0},
    )
    mismatched = helper(
        envelope={"status": "failed", "message": "操作失败", "ui": {}, "data": None},
        trace_steps=[
            *base_trace[:-2],
            {"event_type": "adapter_error_mapped", "error_code": "adapter_payload_invalid"},
            {"event_type": "response_envelope_created"},
        ],
        expected_error_code="adapter_timeout",
        adapter_assertion={"must_be_called": True, "must_not_be_called": False},
        adapter_calls={"oa": 1, "u8": 0, "hikvision_ivms": 0},
    )

    assert absent.status == "failed"
    assert any("expected injected error_code" in reason for reason in absent.reasons)
    assert mismatched.status == "failed"
    assert any("expected injected error_code" in reason for reason in mismatched.reasons)


def test_adapter_must_not_be_called_raises_when_spy_count_is_positive() -> None:
    adapter_calls = {
        "oa": 0,
        "u8": 1,
        "hikvision_ivms": 0,
    }

    with pytest.raises(AssertionError, match="must not be called"):
        assert_adapter_calls(
            {"must_be_called": False, "must_not_be_called": True},
            adapter_calls,
        )

    assert adapter_calls["u8"] == 1


def test_assertion_failure_is_classified_failed_not_skipped_or_not_applicable() -> None:
    result = judge_assertions(
        envelope={
            "status": "completed",
            "message": "操作完成",
            "ui": {"component_type": "none", "action": "none"},
            "data": {},
        },
        expected_response={
            "status": "completed",
            "envelope": {"message_contains": ["操作"]},
        },
        trace_steps=[{"event_type": "task_created"}],
        expected_trace={"event_sequence": ["task_created", "adapter_called"]},
        forbidden_items=[],
        adapter_assertion={"must_be_called": False, "must_not_be_called": False},
        adapter_calls={"oa": 0, "u8": 0, "hikvision_ivms": 0},
    )

    assert result.status == "failed"
    assert result.status not in {"skipped", "not_applicable"}
    assert any("adapter_called" in reason for reason in result.reasons)
