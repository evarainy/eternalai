"""Positive meta-tests for Golden Task runner assertions."""

from __future__ import annotations

import hashlib
import importlib
import json
from collections.abc import Callable, Iterable, Mapping
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    assert_adapter_calls: Callable[[Mapping[str, Any], Mapping[str, Any]], None]
    assert_forbidden_absent: Callable[
        [Iterable[str], Any, Iterable[Any], Mapping[str, Any]],
        None,
    ]
    assert_policy_calls: Callable[[Mapping[str, Any], int], None]
    assert_response_matches: Callable[[Any, Mapping[str, Any]], None]
    assert_terminal_state_matrix: Callable[[Iterable[Any], str | None], None]
    assert_trace_event_details: Callable[
        [Iterable[Any], Iterable[Mapping[str, Any]]], None
    ]
    assert_trace_sequence_contains: Callable[[Iterable[Any], Iterable[str]], None]
    assert_workflow_evidence: Callable[..., None]
else:
    from scripts.golden_task_assertions import (
        assert_adapter_calls,
        assert_forbidden_absent,
        assert_policy_calls,
        assert_response_matches,
        assert_terminal_state_matrix,
        assert_trace_event_details,
        assert_trace_sequence_contains,
        assert_workflow_evidence,
    )


class _CountingDump:
    def __init__(self, value: Any) -> None:
        self.value = value
        self.call_count = 0

    def model_dump(self) -> Any:
        self.call_count += 1
        return self.value


class _StatefulDump:
    def __init__(self, first: Any, later: Any) -> None:
        self.first = first
        self.later = later
        self.call_count = 0

    def model_dump(self) -> Any:
        self.call_count += 1
        return self.first if self.call_count == 1 else self.later


def test_injection_companion_judgement_accepts_matching_timeout_error_code() -> None:
    runner = importlib.import_module("scripts.golden_task_evaluator")
    helper = getattr(runner, "judge_injection_companion_assertions", None)
    assert helper is not None, "injection companion assertion helper is missing"
    marker = hashlib.sha256(b"injection-companion-later-dump").hexdigest()
    envelope = _StatefulDump(
        {
            "status": "failed",
            "message": "操作超时",
            "error_code": "adapter_timeout",
            "ui": {},
            "data": None,
        },
        {"status": marker},
    )
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
        envelope=envelope,
        trace_steps=trace,
        expected_error_code="adapter_timeout",
        adapter_assertion={"must_be_called": True, "must_not_be_called": False},
        adapter_calls={"oa": 1, "u8": 0, "hikvision_ivms": 0},
    )

    assert judgement.status == "passed"
    assert judgement.reasons == []
    assert envelope.call_count == 1
    assert marker not in str(judgement.reasons)


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


@pytest.mark.parametrize(
    "terminal_state",
    ("identity_expired", "identity_revoked"),
)
def test_inactive_identity_terminal_matrix_requires_exact_error_code(
    terminal_state: str,
) -> None:
    actual_trace = [
        {"event_type": "task_created"},
        {"event_type": "intent_parsed"},
        {"event_type": "capability_selected"},
        {"event_type": "identity_check", "error_code": terminal_state},
        {"event_type": "blocked_by_identity", "error_code": terminal_state},
        {"event_type": "response_envelope_created"},
        {"event_type": "task_failed", "error_code": terminal_state},
    ]

    assert_terminal_state_matrix(actual_trace, terminal_state)

    actual_trace[3]["error_code"] = "identity_unbound"
    with pytest.raises(AssertionError, match="expected error_code"):
        assert_terminal_state_matrix(actual_trace, terminal_state)


def test_forbidden_credentials_ignore_fixture_placeholder_identifiers() -> None:
    def digest(label: str) -> str:
        return hashlib.sha256(label.encode("utf-8")).hexdigest()

    input_schema_digest = digest("input-schema")
    output_schema_digest = digest("output-schema")
    trace_id = f"trace_{digest('trace-prefix')}{digest('trace-suffix')}"
    task_id = f"task_{digest('task-prefix')}{digest('task-suffix')}"
    binding_id = f"binding_{digest('binding-prefix')}{digest('binding-suffix')}"
    envelope: dict[str, Any] = {
        "status": "completed",
        "message": "绑定 bind_oa_001 的用户 user_employee_001 已处理",
        "session_id": "session-1",
        "ui": {"component_type": "none", "action": "none"},
        "data": {
            "input_schema_digest": input_schema_digest,
            "output_schema_digest": output_schema_digest,
            "binding_id": binding_id,
            "ai_user_id": "user_employee_001",
        },
    }
    trace = [
        {
            "event_type": "identity_check",
            "attributes": {
                "ai_user_id": "user_employee_001",
                "trace_id": trace_id,
                "task_id": task_id,
                "binding_id": binding_id,
            },
        }
    ]

    assert_forbidden_absent(["trace_contains_token"], envelope, trace, {})

    assert len(envelope["data"]["input_schema_digest"]) == 64
    assert len(envelope["data"]["output_schema_digest"]) == 64
    assert trace[0]["attributes"] == {
        "ai_user_id": "user_employee_001",
        "trace_id": trace_id,
        "task_id": task_id,
        "binding_id": binding_id,
    }


def test_ordinary_json_business_text_does_not_trigger_credential_detection() -> None:
    opaque_value = hashlib.sha256(b"ordinary-json-opaque-value").hexdigest()
    envelope = {
        "status": "completed",
        "message": json.dumps(
            {
                "message": "Please reset your password after the user session ends",
                "password_policy": opaque_value,
                "result": "ordinary business text",
            },
            sort_keys=True,
        ),
        "ui": {"component_type": "none", "action": "none"},
        "data": None,
    }

    assert_forbidden_absent(["trace_contains_token"], envelope, [], {})

    assert "password" in envelope["message"]
    assert "session" in envelope["message"]
    assert opaque_value in envelope["message"]


@pytest.mark.parametrize(
    "ordinary_value",
    (None, ""),
)
@pytest.mark.parametrize("serialized", (False, True))
def test_value_free_credential_fields_do_not_false_positive(
    ordinary_value: Any,
    serialized: bool,
) -> None:
    credential_data: Any = {"password": ordinary_value}
    if serialized:
        credential_data = json.dumps(
            credential_data,
            separators=(",", ":"),
            sort_keys=True,
        )
    envelope = {
        "status": "completed",
        "message": credential_data if serialized else "操作完成",
        "ui": {"component_type": "none", "action": "none"},
        "data": None if serialized else credential_data,
    }

    assert_forbidden_absent(["trace_contains_token"], envelope, [], {})

    assert "password" in (envelope["message"] if serialized else envelope["data"])


@pytest.mark.parametrize("serialized", (False, True))
@pytest.mark.parametrize(
    "container_value",
    (
        {"ordinary": "NOT_A_REAL_SECRET"},
        ["NOT_A_REAL_SECRET"],
        ("NOT_A_REAL_SECRET",),
    ),
)
def test_sensitive_key_non_empty_container_fails_closed(
    container_value: Any,
    serialized: bool,
) -> None:
    credential_data: Any = {"password": container_value}
    if serialized:
        credential_data = json.dumps(credential_data, separators=(",", ":"))
    envelope = {
        "status": "completed",
        "message": credential_data if serialized else "操作完成",
        "ui": {"component_type": "none", "action": "none"},
        "data": None if serialized else credential_data,
    }

    with pytest.raises(AssertionError) as exc_info:
        assert_forbidden_absent(["trace_contains_token"], envelope, [], {})

    assert str(exc_info.value) == (
        "forbidden credential pattern detected: "
        "rule=password_or_passwd; location=actual.envelope"
    )
    assert "NOT_A_REAL_SECRET" not in str(exc_info.value)
    assert "NOT_A_REAL_SECRET" not in repr(exc_info.value)


@pytest.mark.parametrize("serialized", (False, True))
@pytest.mark.parametrize("container_value", ({}, [], ()))
def test_sensitive_key_empty_container_remains_value_free(
    container_value: Any,
    serialized: bool,
) -> None:
    credential_data: Any = {"password": container_value}
    if serialized:
        credential_data = json.dumps(credential_data, separators=(",", ":"))
    envelope = {
        "status": "completed",
        "message": credential_data if serialized else "操作完成",
        "ui": {"component_type": "none", "action": "none"},
        "data": None if serialized else credential_data,
    }

    assert_forbidden_absent(["trace_contains_token"], envelope, [], {})

    observed = json.loads(envelope["message"]) if serialized else envelope["data"]
    expected_container = (
        json.loads(json.dumps(container_value)) if serialized else container_value
    )
    assert observed == {"password": expected_container}


@pytest.mark.parametrize("serialized", (False, True))
def test_non_sensitive_business_container_remains_allowed(serialized: bool) -> None:
    business_data: Any = {"ordinary": {"result": ["business value"]}}
    if serialized:
        business_data = json.dumps(business_data, separators=(",", ":"))
    envelope = {
        "status": "completed",
        "message": business_data if serialized else "操作完成",
        "ui": {"component_type": "none", "action": "none"},
        "data": None if serialized else business_data,
    }

    assert_forbidden_absent(["trace_contains_token"], envelope, [], {})

    observed = json.loads(envelope["message"]) if serialized else envelope["data"]
    assert observed == {"ordinary": {"result": ["business value"]}}


@pytest.mark.parametrize(
    "dumped_value",
    (
        None,
        "",
        b"",
        bytearray(),
        memoryview(b""),
        {},
        [],
        (),
    ),
)
def test_model_dump_empty_value_preserves_existing_semantics(
    dumped_value: Any,
) -> None:
    model = _CountingDump(dumped_value)
    envelope = {
        "status": "completed",
        "message": "操作完成",
        "ui": {"component_type": "none", "action": "none"},
        "data": {"password": model},
    }

    assert_forbidden_absent(["trace_contains_token"], envelope, [], {})

    assert model.call_count == 1
    assert model.value is dumped_value
    assert envelope["data"]["password"] is model


@pytest.mark.parametrize(
    "dumped_value",
    (
        {"ordinary": "NOT_A_REAL_SECRET"},
        ["NOT_A_REAL_SECRET"],
        ("NOT_A_REAL_SECRET",),
    ),
)
def test_model_dump_non_empty_container_under_sensitive_key_fails_closed(
    dumped_value: Any,
) -> None:
    model = _CountingDump(dumped_value)
    envelope = {
        "status": "completed",
        "message": "操作完成",
        "ui": {"component_type": "none", "action": "none"},
        "data": {"password": model},
    }

    with pytest.raises(AssertionError) as exc_info:
        assert_forbidden_absent(["trace_contains_token"], envelope, [], {})

    assert str(exc_info.value) == (
        "forbidden credential pattern detected: "
        "rule=password_or_passwd; location=actual.envelope"
    )
    assert model.call_count == 1
    assert model.value is dumped_value
    assert "NOT_A_REAL_SECRET" not in str(exc_info.value)
    assert "NOT_A_REAL_SECRET" not in repr(exc_info.value)


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


def test_adapter_assertion_requires_exact_per_capability_counts_and_arguments() -> None:
    calls = {"oa.unstable": 2, "oa.later": 0}
    arguments = {"oa.unstable": [{"attempt": 1}, {"attempt": 2}]}

    assert_adapter_calls(
        {
            "must_be_called": True,
            "must_not_be_called": False,
            "exact_calls": {"oa.unstable": 2, "oa.later": 0},
            "exact_arguments": {
                "oa.unstable": [{"attempt": 1}, {"attempt": 2}]
            },
        },
        calls,
        arguments,
    )

    with pytest.raises(AssertionError, match="expected exactly 2 calls, got 1"):
        assert_adapter_calls(
            {"exact_calls": {"oa.unstable": 2}},
            {"oa.unstable": 1},
        )
    with pytest.raises(AssertionError, match="expected exactly 0 calls, got 1"):
        assert_adapter_calls(
            {"exact_calls": {"oa.later": 0}},
            {"oa.later": 1},
        )


def test_forbidden_adapter_assertion_uses_capability_keyed_counts() -> None:
    with pytest.raises(AssertionError, match="mock OA adapter was called"):
        assert_forbidden_absent(
            ["mock_oa_was_called"],
            {},
            [],
            {"oa.b4.policy.denied": 1},
        )

    assert_forbidden_absent(
        ["mock_oa_was_called"],
        {},
        [],
        {"u8.b4.document.lookup": 1},
    )

    with pytest.raises(AssertionError, match="mock U8 adapter was called"):
        assert_forbidden_absent(
            ["mock_u8_was_called"],
            {},
            [],
            {"u8.b4.document.lookup": 1},
        )


def test_trace_event_details_require_workflow_step_metadata() -> None:
    trace = [
        {"event_type": "capability_selected", "capability_id": "workflow.b4"},
        {
            "event_type": "capability_selected",
            "capability_id": "oa.step",
            "attributes": {
                "workflow_id": "workflow.b4",
                "workflow_version": "1.0.0",
                "step_id": "step-1",
            },
        },
    ]
    expected = [
        {
            "event_type": "capability_selected",
            "capability_id": "oa.step",
            "attributes": {
                "workflow_id": "workflow.b4",
                "workflow_version": "1.0.0",
                "step_id": "step-1",
            },
        }
    ]

    assert_trace_event_details(trace, expected)

    expected[0]["attributes"]["workflow_version"] = "2.0.0"
    with pytest.raises(AssertionError, match="trace missing expected event details"):
        assert_trace_event_details(trace, expected)


def test_workflow_evidence_locks_first_round_and_snapshot_version() -> None:
    workflow_events = [
        {
            "event_type": "workflow_started",
            "payload": {"workflow_version": "1.0.0"},
        },
        {
            "event_type": "workflow_step_finished",
            "payload": {
                "workflow_version": "1.0.0",
                "step_status": "waiting_confirm",
            },
        },
        {
            "event_type": "workflow_waiting_confirm",
            "payload": {"workflow_version": "1.0.0"},
        },
    ]
    expected = {
        "event_sequence": [
            "workflow_started",
            "workflow_step_finished",
            "workflow_waiting_confirm",
        ],
        "workflow_version": "1.0.0",
        "step_statuses": ["waiting_confirm"],
        "first_round": {
            "response": {"status": "waiting_user"},
            "exact_calls": {"oa.waiting": 0},
        },
        "source_definition_version_after_first": "2.0.0",
    }

    assert_workflow_evidence(
        workflow_events,
        expected,
        {"status": "waiting_user", "ui": {}},
        {},
        "2.0.0",
    )

    with pytest.raises(AssertionError, match="workflow event versions"):
        assert_workflow_evidence(
            workflow_events,
            {**expected, "workflow_version": "2.0.0"},
            {"status": "waiting_user", "ui": {}},
            {},
            "2.0.0",
        )


def test_workflow_evidence_requires_exact_terminal_error_code() -> None:
    workflow_events = [
        {
            "event_type": "workflow_started",
            "payload": {"workflow_version": "1.0.0"},
        },
        {
            "event_type": "workflow_step_finished",
            "payload": {
                "workflow_version": "1.0.0",
                "step_status": "timeout",
                "error_code": "adapter_timeout",
            },
        },
        {
            "event_type": "workflow_failed",
            "payload": {
                "workflow_version": "1.0.0",
                "workflow_status": "timeout",
                "error_code": "adapter_http_500",
            },
        },
    ]
    expected = {
        "event_sequence": [
            "workflow_started",
            "workflow_step_finished",
            "workflow_failed",
        ],
        "workflow_version": "1.0.0",
        "step_statuses": ["timeout"],
        "terminal_status": "timeout",
        "terminal_error_code": "adapter_timeout",
    }

    with pytest.raises(AssertionError, match="terminal error_code expected"):
        assert_workflow_evidence(workflow_events, expected)


def test_policy_assertion_accepts_zero_calls_for_identity_short_circuit() -> None:
    assert_policy_calls(
        {"must_be_called": False, "must_not_be_called": True},
        0,
    )

    with pytest.raises(AssertionError, match="policy guard must not be called"):
        assert_policy_calls(
            {"must_be_called": False, "must_not_be_called": True},
            1,
        )
