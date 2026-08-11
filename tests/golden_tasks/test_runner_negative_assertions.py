"""Negative meta-tests for Golden Task runner assertions."""

from __future__ import annotations

import hashlib
import importlib
import json
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
    from scripts.golden_task_assertions import (
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


def _synthetic_credential_value(label: str) -> str:
    digest = hashlib.sha256(label.encode("utf-8")).hexdigest()
    return f"{digest}{digest[:5]}"


@pytest.mark.parametrize(
    "credential_shape",
    (
        "authorization_basic",
        "authorization_bearer",
        "bare_bearer",
        "cookie_sessionid",
        "cookie_folded_sessionid",
        "cookie_folded_sessionid_equals",
        "cookie_repeated_header_folded_sessionid",
        "cookie_access_token",
        "cookie_refresh_token",
        "sessionid",
        "access_token",
        "refresh_token",
        "api_key",
        "password",
        "passwd",
        "sessionkey",
        "datakey",
    ),
)
def test_forbidden_credential_value_patterns_raise_assertion_error(
    credential_shape: str,
) -> None:
    credential_value = _synthetic_credential_value(credential_shape)
    credential_text = {
        "authorization_basic": f"Authorization: Basic {credential_value}",
        "authorization_bearer": f"Authorization: Bearer {credential_value}",
        "bare_bearer": f"Bearer {credential_value}",
        "cookie_sessionid": "Cookie: sessionid=",
        "cookie_folded_sessionid": "Cookie:\nsessionid=",
        "cookie_folded_sessionid_equals": "Cookie: sessionid\n=",
        "cookie_repeated_header_folded_sessionid": (
            "Cookie: ordinary Cookie:\nsessionid="
        ),
        "cookie_access_token": f"Cookie: access_token={credential_value}",
        "cookie_refresh_token": f"Cookie: refresh_token={credential_value}",
        "sessionid": f"sessionid={credential_value}",
        "access_token": f"access_token={credential_value}",
        "refresh_token": f"refresh_token={credential_value}",
        "api_key": f"api_key={credential_value}",
        "password": f"password={credential_value}",
        "passwd": f"passwd={credential_value}",
        "sessionkey": f"sessionkey={credential_value}",
        "datakey": f"datakey={credential_value}",
    }[credential_shape]
    envelope = {
        "status": "completed",
        "message": credential_text,
        "ui": {"component_type": "none", "action": "none"},
        "data": None,
    }

    with pytest.raises(AssertionError, match="credential"):
        assert_forbidden_absent(["trace_contains_token"], envelope, [], {})

    assert credential_text == envelope["message"]


@pytest.mark.parametrize("field_name", ("sessionkey", "dataKey"))
def test_structured_credential_failure_does_not_echo_canary(field_name: str) -> None:
    credential_value = _synthetic_credential_value(f"safe-reason-{field_name}")
    envelope = {
        "status": "completed",
        "message": "操作完成",
        "ui": {"component_type": "none", "action": "none"},
        "data": {field_name: credential_value},
    }

    with pytest.raises(AssertionError) as exc_info:
        assert_forbidden_absent(["trace_contains_token"], envelope, [], {})

    assert "credential" in str(exc_info.value)
    assert credential_value not in str(exc_info.value)
    assert credential_value not in repr(exc_info.value)


@pytest.mark.parametrize(
    "field_name",
    ("password", "access_token", "dataKey"),
)
def test_serialized_json_credential_mapping_is_rejected(field_name: str) -> None:
    credential_value = _synthetic_credential_value(f"serialized-{field_name}")
    serialized_mapping = json.dumps(
        {field_name: credential_value},
        separators=(",", ":"),
        sort_keys=True,
    )
    envelope = {
        "status": "completed",
        "message": serialized_mapping,
        "ui": {"component_type": "none", "action": "none"},
        "data": None,
    }

    with pytest.raises(AssertionError) as exc_info:
        assert_forbidden_absent(["trace_contains_token"], envelope, [], {})

    assert "credential" in str(exc_info.value)
    assert credential_value not in str(exc_info.value)
    assert credential_value not in repr(exc_info.value)


@pytest.mark.parametrize(
    "field_name",
    ("password", "access_token", "sessionkey", "dataKey"),
)
@pytest.mark.parametrize("serialized", (False, True))
def test_comma_prefixed_credential_value_remains_rejected(
    field_name: str,
    serialized: bool,
) -> None:
    credential_value = "," + _synthetic_credential_value(f"comma-{field_name}")
    credential_data: Any = {field_name: credential_value}
    if serialized:
        credential_data = json.dumps(
            credential_data,
            separators=(",", ":"),
            sort_keys=True,
        )
    envelope = {
        "status": "completed",
        "message": "操作完成",
        "ui": {"component_type": "none", "action": "none"},
        "data": credential_data,
    }

    with pytest.raises(AssertionError) as exc_info:
        assert_forbidden_absent(["trace_contains_token"], envelope, [], {})

    assert "credential" in str(exc_info.value)
    assert credential_value not in str(exc_info.value)
    assert credential_value not in repr(exc_info.value)


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    (
        ("Cookie", "sessionid="),
        ("Cookie", "access_token="),
        ("password=synthetic-canary", "ordinary"),
    ),
)
def test_structured_mapping_preserves_legacy_key_value_detection(
    field_name: str,
    field_value: str,
) -> None:
    envelope = {
        "status": "completed",
        "message": "操作完成",
        "ui": {"component_type": "none", "action": "none"},
        "data": {field_name: field_value},
    }

    with pytest.raises(AssertionError, match="credential"):
        assert_forbidden_absent(["trace_contains_token"], envelope, [], {})


@pytest.mark.parametrize(
    "field_name",
    ("session_key", "SESSION_KEY", "SeSsIoN_KeY", "data_key", "DATA_KEY", "DaTa_KeY"),
)
def test_underscored_credential_mapping_is_case_insensitive(
    field_name: str,
) -> None:
    credential_value = _synthetic_credential_value(f"mapping-{field_name}")
    envelope = {
        "status": "completed",
        "message": "操作完成",
        "ui": {"component_type": "none", "action": "none"},
        "data": {field_name: credential_value},
    }

    with pytest.raises(AssertionError, match="credential"):
        assert_forbidden_absent(["trace_contains_token"], envelope, [], {})


@pytest.mark.parametrize(
    "field_name",
    ("session_key", "SESSION_KEY", "SeSsIoN_KeY", "data_key", "DATA_KEY", "DaTa_KeY"),
)
def test_serialized_underscored_credential_mapping_is_case_insensitive(
    field_name: str,
) -> None:
    credential_value = _synthetic_credential_value(f"serialized-{field_name}")
    envelope = {
        "status": "completed",
        "message": json.dumps(
            {field_name: credential_value},
            separators=(",", ":"),
            sort_keys=True,
        ),
        "ui": {"component_type": "none", "action": "none"},
        "data": None,
    }

    with pytest.raises(AssertionError, match="credential"):
        assert_forbidden_absent(["trace_contains_token"], envelope, [], {})


@pytest.mark.parametrize(
    "expectation_key",
    ("expected", "then_response", "then_trace", "then_workflow", "then_custom"),
)
def test_fixture_expectation_blocks_reject_credential_canary(
    expectation_key: str,
    monkeypatch: Any,
) -> None:
    credential_value = _synthetic_credential_value(f"fixture-{expectation_key}")
    fixture = {
        "category": "negative",
        "then_response": {"status": "completed"},
        "then_trace": {"event_sequence": []},
        "then_forbidden": [],
        "adapter_assertion": {
            "must_be_called": False,
            "must_not_be_called": False,
        },
        "then_workflow": None,
    }
    fixture[expectation_key] = {
        **(fixture.get(expectation_key) or {}),
        "synthetic_expected": {"password": credential_value},
    }

    runner = importlib.import_module("scripts.golden_task_evaluator")

    async def fake_run_fixture(_fixture: dict[str, Any]) -> Any:
        return runner.FixtureObservation(
            envelope={
                "status": "completed",
                "message": "操作完成",
                "ui": {"component_type": "none", "action": "none"},
                "data": None,
            },
            trace_steps=[],
            adapter_calls={},
            adapter_arguments={},
            policy_calls=0,
            workflow_events=[],
        )

    monkeypatch.setattr(runner, "load_fixture", lambda _gt_id: fixture)
    monkeypatch.setattr(runner, "_run_fixture", fake_run_fixture)

    result = runner.evaluate_golden_task("GT-SYNTHETIC-EXPECTATION")

    assert result.golden_task_id == "GT-SYNTHETIC-EXPECTATION"
    assert result.category == "negative"
    assert result.status == "failed"
    assert any("credential" in reason for reason in result.reasons)
    assert credential_value not in str(result.reasons)
    assert credential_value not in repr(result.reasons)


def test_credential_scan_depth_limit_fails_closed_without_payload_echo() -> None:
    payload_marker = _synthetic_credential_value("depth-limit-payload")
    nested: Any = payload_marker
    for _index in range(70):
        nested = {"ordinary": nested}
    envelope = {
        "status": "completed",
        "message": "操作完成",
        "ui": {"component_type": "none", "action": "none"},
        "data": nested,
    }

    with pytest.raises(AssertionError) as exc_info:
        assert_forbidden_absent(["trace_contains_token"], envelope, [], {})

    assert "rule=scan_depth_limit" in str(exc_info.value)
    assert "location=actual.envelope" in str(exc_info.value)
    assert payload_marker not in str(exc_info.value)
    assert payload_marker not in repr(exc_info.value)


def test_credential_scan_node_limit_fails_closed_without_payload_echo() -> None:
    payload_marker = _synthetic_credential_value("node-limit-payload")
    envelope = {
        "status": "completed",
        "message": "操作完成",
        "ui": {"component_type": "none", "action": "none"},
        "data": [None] * 10_001 + [payload_marker],
    }

    with pytest.raises(AssertionError) as exc_info:
        assert_forbidden_absent(["trace_contains_token"], envelope, [], {})

    assert "rule=scan_node_limit" in str(exc_info.value)
    assert "location=actual.envelope" in str(exc_info.value)
    assert payload_marker not in str(exc_info.value)
    assert payload_marker not in repr(exc_info.value)


def test_credential_scan_single_string_limit_fails_closed() -> None:
    payload_marker = _synthetic_credential_value("string-limit-payload")
    envelope = {
        "status": "completed",
        "message": f"{payload_marker}{'x' * 1_000_000}",
        "ui": {"component_type": "none", "action": "none"},
        "data": None,
    }

    with pytest.raises(AssertionError) as exc_info:
        assert_forbidden_absent(["trace_contains_token"], envelope, [], {})

    assert "rule=scan_string_char_limit" in str(exc_info.value)
    assert "location=actual.envelope" in str(exc_info.value)
    assert payload_marker not in str(exc_info.value)
    assert payload_marker not in repr(exc_info.value)


def test_credential_scan_total_string_limit_fails_closed() -> None:
    payload_marker = _synthetic_credential_value("total-limit-payload")
    bounded_chunk = f"{payload_marker}{'x' * 899_930}"
    envelope = {
        "status": "completed",
        "message": "操作完成",
        "ui": {"component_type": "none", "action": "none"},
        "data": [bounded_chunk] * 9,
    }

    with pytest.raises(AssertionError) as exc_info:
        assert_forbidden_absent(["trace_contains_token"], envelope, [], {})

    assert "rule=scan_total_char_limit" in str(exc_info.value)
    assert "location=actual.envelope" in str(exc_info.value)
    assert payload_marker not in str(exc_info.value)
    assert payload_marker not in repr(exc_info.value)


@pytest.mark.parametrize(
    ("boundary_kind", "expected_rule"),
    (
        ("characters", "scan_string_char_limit"),
        ("depth", "scan_serialized_json_depth_limit"),
        ("nodes", "scan_serialized_json_node_limit"),
    ),
)
def test_serialized_json_bounds_run_before_legacy_pattern_scan(
    boundary_kind: str,
    expected_rule: str,
    monkeypatch: Any,
) -> None:
    payload = {
        "characters": '{"ordinary":"' + ("x" * 1_000_000) + '"}',
        "depth": ('{"ordinary":' * 65) + "null" + ("}" * 65),
        "nodes": '{"ordinary":[' + ("null," * 10_001) + "null]}",
    }[boundary_kind]
    assertions = importlib.import_module("scripts.golden_task_assertions")
    original_rule = assertions._credential_text_rule
    scanned_payload_lengths: list[int] = []

    def track_payload_scan(value: str) -> str | None:
        if value == payload or value.endswith(payload):
            scanned_payload_lengths.append(len(value))
        return original_rule(value)

    monkeypatch.setattr(assertions, "_credential_text_rule", track_payload_scan)
    envelope = {
        "status": "completed",
        "message": payload,
        "ui": {"component_type": "none", "action": "none"},
        "data": None,
    }

    with pytest.raises(AssertionError) as exc_info:
        assert_forbidden_absent(["trace_contains_token"], envelope, [], {})

    assert f"rule={expected_rule}" in str(exc_info.value)
    assert scanned_payload_lengths == []


def test_fixture_credential_preflight_prevents_later_assertion_value_echo() -> None:
    credential_value = _synthetic_credential_value("preflight-before-value-echo")
    result = judge_assertions(
        envelope={
            "status": "completed",
            "message": "操作完成",
            "ui": {"component_type": "none", "action": "none"},
            "data": None,
        },
        expected_response={
            "status": credential_value,
            "synthetic_expected": {"password": credential_value},
        },
        trace_steps=[],
        expected_trace={"event_sequence": []},
        forbidden_items=["trace_contains_token"],
        adapter_assertion={"must_be_called": False, "must_not_be_called": False},
        adapter_calls={"oa": 0, "u8": 0, "hikvision_ivms": 0},
    )

    runner = importlib.import_module("scripts.golden_task_evaluator")
    summary = runner.build_summary(
        [
            runner.GoldenTaskResult(
                golden_task_id="GT-SYNTHETIC-PREFLIGHT",
                category="negative",
                status=result.status,
                reasons=result.reasons,
            )
        ]
    )
    serialized_summary = json.dumps(summary, ensure_ascii=False, sort_keys=True)

    assert result.status == "failed"
    assert len(result.reasons) == 1
    assert "credential" in result.reasons[0]
    assert credential_value not in str(result)
    assert credential_value not in repr(result)
    assert credential_value not in str(result.reasons)
    assert credential_value not in repr(result.reasons)
    assert summary["failed"] == 1
    assert summary["negative_total"] == 1
    assert summary["negative_passed"] == 0
    assert credential_value not in repr(summary)
    assert credential_value not in serialized_summary


@pytest.mark.parametrize("credential_surface", ("expected", "actual"))
def test_adapter_assertion_inputs_are_credential_preflighted(
    credential_surface: str,
) -> None:
    credential_value = _synthetic_credential_value(
        f"adapter-assertion-{credential_surface}"
    )
    expected_arguments: dict[str, Any] = {"oa.synthetic": []}
    actual_arguments: dict[str, Any] = {"oa.synthetic": []}
    target = expected_arguments if credential_surface == "expected" else actual_arguments
    target["oa.synthetic"] = [{"password": credential_value}]

    result = judge_assertions(
        envelope={
            "status": "completed",
            "message": "操作完成",
            "ui": {"component_type": "none", "action": "none"},
            "data": None,
        },
        expected_response={"status": "completed"},
        trace_steps=[],
        expected_trace={"event_sequence": []},
        forbidden_items=[],
        adapter_assertion={
            "must_be_called": False,
            "must_not_be_called": False,
            "exact_arguments": expected_arguments,
        },
        adapter_calls={},
        adapter_arguments=actual_arguments,
    )

    assert result.status == "failed"
    assert len(result.reasons) == 1
    assert "credential" in result.reasons[0]
    assert credential_value not in str(result.reasons)
    assert credential_value not in repr(result.reasons)


def test_forbidden_sessionkey_field_name_in_envelope_data_raises() -> None:
    credential_value = _synthetic_credential_value("envelope-sessionkey")
    envelope = {
        "status": "completed",
        "message": "操作完成",
        "ui": {"component_type": "none", "action": "none"},
        "data": {"sessionkey": credential_value},
    }

    with pytest.raises(AssertionError, match="credential"):
        assert_forbidden_absent(["trace_contains_token"], envelope, [], {})

    assert envelope["data"]["sessionkey"] == credential_value


def test_forbidden_sessionkey_field_name_in_deep_trace_attributes_raises() -> None:
    credential_value = _synthetic_credential_value("trace-sessionkey")
    trace = [
        {
            "event_type": "gateway_pre_recorded",
            "attributes": {
                "parameters": [
                    {"request": {"credentials": {"sessionkey": credential_value}}}
                ]
            },
        }
    ]

    with pytest.raises(AssertionError, match="credential"):
        assert_forbidden_absent(
            ["trace_contains_token"],
            {"status": "completed", "message": "操作完成", "data": None},
            trace,
            {},
        )

    assert trace[0]["attributes"]["parameters"][0]["request"]["credentials"] == {
        "sessionkey": credential_value
    }


@pytest.mark.parametrize("field_name", ("dataKey", "DATAKEY"))
def test_forbidden_datakey_field_name_is_case_insensitive(field_name: str) -> None:
    credential_value = _synthetic_credential_value(field_name)
    envelope = {
        "status": "completed",
        "message": "操作完成",
        "ui": {"component_type": "none", "action": "none"},
        "data": {field_name: credential_value},
    }

    with pytest.raises(AssertionError, match="credential"):
        assert_forbidden_absent(["trace_contains_token"], envelope, [], {})

    assert envelope["data"][field_name] == credential_value


def test_forbidden_datakey_field_name_in_deep_parameters_raises() -> None:
    credential_value = _synthetic_credential_value("nested-datakey")
    trace = [
        {
            "event_type": "gateway_pre_recorded",
            "attributes": {
                "parameters": [
                    {"request": {"nested": [{"dataKey": credential_value}]}}
                ]
            },
        }
    ]

    with pytest.raises(AssertionError, match="credential"):
        assert_forbidden_absent(
            ["trace_contains_token"],
            {"status": "completed", "message": "操作完成", "data": None},
            trace,
            {},
        )

    assert trace[0]["attributes"]["parameters"][0]["request"]["nested"] == [
        {"dataKey": credential_value}
    ]


@pytest.mark.parametrize("separator", ("=", ": "))
def test_forbidden_sessionkey_text_separators_raise(separator: str) -> None:
    credential_value = _synthetic_credential_value(f"separator-{separator}")
    envelope = {
        "status": "completed",
        "message": f"sessionkey{separator}{credential_value}",
        "ui": {"component_type": "none", "action": "none"},
        "data": None,
    }

    with pytest.raises(AssertionError, match="credential"):
        assert_forbidden_absent(["trace_contains_token"], envelope, [], {})

    assert separator in envelope["message"]


def test_credential_detection_is_enforced_by_main_judgement() -> None:
    credential_value = _synthetic_credential_value("main-judgement")
    result = judge_assertions(
        envelope={
            "status": "completed",
            "message": f"sessionkey={credential_value}",
            "ui": {"component_type": "none", "action": "none"},
            "data": None,
        },
        expected_response={"status": "completed"},
        trace_steps=[],
        expected_trace={"event_sequence": []},
        forbidden_items=["trace_contains_token"],
        adapter_assertion={"must_be_called": False, "must_not_be_called": False},
        adapter_calls={"oa": 0, "u8": 0, "hikvision_ivms": 0},
    )

    assert result.status == "failed"
    assert len(result.reasons) == 1
    assert "credential" in result.reasons[0]
    assert credential_value not in str(result.reasons)
    assert credential_value not in repr(result.reasons)

    runner = importlib.import_module("scripts.golden_task_evaluator")
    summary = runner.build_summary(
        [
            runner.GoldenTaskResult(
                golden_task_id="GT-SYNTHETIC-CREDENTIAL",
                category="negative",
                status=result.status,
                reasons=result.reasons,
            )
        ]
    )
    serialized_summary = json.dumps(summary, ensure_ascii=False, sort_keys=True)
    assert summary["failed"] == 1
    assert summary["negative_total"] == 1
    assert summary["negative_passed"] == 0
    assert credential_value not in serialized_summary


def test_unknown_forbidden_item_raises_assertion_error() -> None:
    with pytest.raises(AssertionError, match="unknown forbidden item"):
        assert_forbidden_absent(
            ["unknown_forbidden_item"],
            {"status": "completed", "message": "ok", "data": None},
            [],
            {"oa": 0, "u8": 0, "hikvision_ivms": 0},
        )


def test_injection_companion_judgement_rejects_absent_or_mismatched_error_code() -> None:
    runner = importlib.import_module("scripts.golden_task_evaluator")
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


def test_injection_companion_credential_failure_short_circuits_without_echo() -> None:
    runner = importlib.import_module("scripts.golden_task_evaluator")
    credential_value = _synthetic_credential_value("injection-error-code")

    result = runner.judge_injection_companion_assertions(
        envelope={
            "status": "failed",
            "message": "操作失败",
            "error_code": f"password={credential_value}",
            "ui": {},
            "data": None,
        },
        trace_steps=[],
        expected_error_code="adapter_timeout",
        adapter_assertion={"must_be_called": False, "must_not_be_called": False},
        adapter_calls={},
        forbidden_items=(),
    )

    assert result.status == "failed"
    assert len(result.reasons) == 1
    assert "credential" in result.reasons[0]
    assert credential_value not in str(result.reasons)
    assert credential_value not in repr(result.reasons)


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
