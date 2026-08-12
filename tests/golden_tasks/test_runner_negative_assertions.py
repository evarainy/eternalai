"""Negative meta-tests for Golden Task runner assertions."""

from __future__ import annotations

import hashlib
import importlib
import json
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from decimal import Decimal
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


class _OpaqueCredentialLeaf:
    def __init__(self, marker: str) -> None:
        self.marker = marker
        self.str_calls = 0
        self.repr_calls = 0

    def __str__(self) -> str:
        self.str_calls += 1
        return self.marker

    def __repr__(self) -> str:
        self.repr_calls += 1
        return self.marker


class _DumpValue:
    def __init__(self, value: Any) -> None:
        self.value = value
        self.call_count = 0

    def model_dump(self) -> Any:
        self.call_count += 1
        return self.value


class _NeverConsumedIterable:
    def __init__(self) -> None:
        self.iter_calls = 0

    def __iter__(self) -> Iterable[str]:
        self.iter_calls += 1
        yield "synthetic-custom-iterable-canary"


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


def test_structured_credential_failure_does_not_echo_canary() -> None:
    field_name = "sessionkey"
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
    ("field_name", "field_value", "expected_rule", "serialized"),
    (
        ("password", 482901, "password_or_passwd", False),
        ("access_token", True, "token_or_api_key", True),
        ("dataKey", True, "oa_session_or_data_key", False),
    ),
)
def test_non_string_credential_mapping_is_rejected_without_value_echo(
    field_name: str,
    field_value: int | bool,
    expected_rule: str,
    serialized: bool,
) -> None:
    credential_data: Any = {field_name: field_value}
    if serialized:
        credential_data = json.dumps(credential_data, separators=(",", ":"))
    envelope = {
        "status": "completed",
        "message": "操作完成",
        "ui": {"component_type": "none", "action": "none"},
        "data": credential_data,
    }

    with pytest.raises(AssertionError) as exc_info:
        assert_forbidden_absent(["trace_contains_token"], envelope, [], {})

    reason = str(exc_info.value)
    assert reason == (
        "forbidden credential pattern detected: "
        f"rule={expected_rule}; location=actual.envelope"
    )
    assert str(field_value) not in reason
    assert str(field_value) not in repr(exc_info.value)


def test_non_empty_bytes_credential_mapping_is_rejected_without_value_echo() -> None:
    credential_value = b"synthetic-non-empty-bytes-canary"
    envelope = {
        "status": "completed",
        "message": "操作完成",
        "ui": {"component_type": "none", "action": "none"},
        "data": {"password": credential_value},
    }

    with pytest.raises(AssertionError) as exc_info:
        assert_forbidden_absent(["trace_contains_token"], envelope, [], {})

    assert str(exc_info.value) == (
        "forbidden credential pattern detected: "
        "rule=password_or_passwd; location=actual.envelope"
    )
    assert "synthetic-non-empty-bytes-canary" not in str(exc_info.value)
    assert "synthetic-non-empty-bytes-canary" not in repr(exc_info.value)


def test_direct_opaque_credential_is_rejected_without_value_echo() -> None:
    credential_value = Decimal("482909.01")
    envelope = {
        "status": "completed",
        "message": "操作完成",
        "ui": {"component_type": "none", "action": "none"},
        "data": {"password": credential_value},
    }

    with pytest.raises(AssertionError) as exc_info:
        assert_forbidden_absent(["trace_contains_token"], envelope, [], {})

    assert str(exc_info.value) == (
        "forbidden credential pattern detected: "
        "rule=password_or_passwd; location=actual.envelope"
    )
    assert str(credential_value) not in str(exc_info.value)
    assert str(credential_value) not in repr(exc_info.value)


def test_serialized_json_duplicate_sensitive_key_is_scanned_item_by_item() -> None:
    credential_value = 482904
    envelope = {
        "status": "completed",
        "message": (
            f'{{"password":{credential_value},"password":null}}'
        ),
        "ui": {"component_type": "none", "action": "none"},
        "data": None,
    }

    with pytest.raises(AssertionError) as exc_info:
        assert_forbidden_absent(["trace_contains_token"], envelope, [], {})

    assert str(exc_info.value) == (
        "forbidden credential pattern detected: "
        "rule=password_or_passwd; location=actual.envelope"
    )
    assert str(credential_value) not in str(exc_info.value)
    assert str(credential_value) not in repr(exc_info.value)


@pytest.mark.parametrize(
    ("field_name", "serialized"),
    (("password", False), ("dataKey", True)),
)
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
    ("field_name", "serialized"),
    (
        ("session_key", False),
        ("DATA_KEY", True),
    ),
)
def test_underscored_credential_mapping_is_case_insensitive(
    field_name: str,
    serialized: bool,
) -> None:
    credential_value = _synthetic_credential_value(f"underscored-{field_name}")
    credential_data: Any = {field_name: credential_value}
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

    with pytest.raises(AssertionError, match="credential"):
        assert_forbidden_absent(["trace_contains_token"], envelope, [], {})


@pytest.mark.parametrize(
    "expectation_key",
    ("expected", "then_custom"),
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
        ("candidate_characters", "scan_serialized_json_char_limit"),
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
        "candidate_characters": '{"ordinary":"' + ("x" * 256_000) + '"}',
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


@pytest.mark.parametrize(
    ("credential_surface", "expected_location"),
    (
        ("actual", "actual.envelope"),
        ("expected", "fixture.expectations"),
    ),
)
def test_serialized_json_declared_depth_boundary_is_64_containers(
    credential_surface: str,
    expected_location: str,
) -> None:
    payload_marker = _synthetic_credential_value(
        f"serialized-depth-{credential_surface}"
    )

    def nested_json(depth: int) -> str:
        return ('{"ordinary":' * depth) + json.dumps(payload_marker) + ("}" * depth)

    def judge_payload(payload: str) -> Any:
        envelope = {
            "status": "completed",
            "message": payload if credential_surface == "actual" else "操作完成",
            "ui": {"component_type": "none", "action": "none"},
            "data": None,
        }
        expectations = (
            {"then_custom": {"ordinary": payload}}
            if credential_surface == "expected"
            else None
        )
        return judge_assertions(
            envelope=envelope,
            expected_response={"status": "completed"},
            trace_steps=[],
            expected_trace={"event_sequence": []},
            forbidden_items=[],
            adapter_assertion={
                "must_be_called": False,
                "must_not_be_called": False,
            },
            adapter_calls={},
            credential_expectations=expectations,
        )

    boundary = judge_payload(nested_json(64))
    over_boundary = judge_payload(nested_json(65))

    assert boundary.status == "passed"
    assert boundary.reasons == []
    assert over_boundary.status == "failed"
    assert over_boundary.reasons == [
        "forbidden credential pattern detected: "
        f"rule=scan_serialized_json_depth_limit; location={expected_location}"
    ]
    assert payload_marker not in str(over_boundary.reasons)
    assert payload_marker not in repr(over_boundary.reasons)


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

    assert result.status == "failed"
    assert len(result.reasons) == 1
    assert "credential" in result.reasons[0]
    assert credential_value not in str(result)
    assert credential_value not in repr(result)
    assert credential_value not in str(result.reasons)
    assert credential_value not in repr(result.reasons)


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


def _judge_sensitive_dynamic_surface(surface: str, value: Any) -> Any:
    envelope: Any = {
        "status": "completed",
        "message": "操作完成",
        "ui": {"component_type": "none", "action": "none"},
        "data": None,
    }
    trace_steps: list[Any] = []
    adapter_arguments: Mapping[str, Any] | None = None
    credential_expectations: Mapping[str, Any] | None = None
    if surface == "actual.envelope":
        envelope["data"] = {"password": value}
    elif surface == "actual.trace":
        trace_steps = [{"password": value}]
    elif surface == "actual.assertion_inputs":
        adapter_arguments = {"oa.synthetic": [{"password": value}]}
    elif surface == "fixture.expectations":
        credential_expectations = {"then_custom": {"password": value}}
    else:  # pragma: no cover - the parametrization is the closed surface grammar
        raise AssertionError("unknown synthetic credential surface")
    return judge_assertions(
        envelope=envelope,
        expected_response={"status": "completed"},
        trace_steps=trace_steps,
        expected_trace={"event_sequence": []},
        forbidden_items=[],
        adapter_assertion={"must_be_called": False, "must_not_be_called": False},
        adapter_calls={},
        adapter_arguments=adapter_arguments,
        credential_expectations=credential_expectations,
    )


@pytest.mark.parametrize(
    ("surface", "dump_kind"),
    (
        ("actual.envelope", "string"),
        ("actual.trace", "decimal"),
        ("actual.assertion_inputs", "opaque"),
        ("fixture.expectations", "string"),
        ("fixture.expectations", "decimal"),
        ("fixture.expectations", "opaque"),
    ),
)
def test_model_dump_leaf_reuses_sensitive_edge_rule_on_every_surface(
    surface: str,
    dump_kind: str,
) -> None:
    marker = _synthetic_credential_value(f"dump-leaf-{surface}-{dump_kind}")
    opaque = _OpaqueCredentialLeaf(marker)
    dumped_value: Any = {
        "string": marker,
        "decimal": Decimal("482912.01"),
        "opaque": opaque,
    }[dump_kind]
    canary = str(dumped_value) if dump_kind == "decimal" else marker
    model = _DumpValue(dumped_value)

    result = _judge_sensitive_dynamic_surface(surface, model)

    assert result.status == "failed"
    assert result.reasons == [
        "forbidden credential pattern detected: "
        f"rule=password_or_passwd; location={surface}"
    ]
    assert model.call_count == 1
    assert model.value is dumped_value
    assert canary not in str(result.reasons)
    assert canary not in repr(result.reasons)
    if dump_kind == "opaque":
        assert opaque.str_calls == 0
        assert opaque.repr_calls == 0


@pytest.mark.parametrize(
    "cycle_kind",
    ("self_list", "mutual_lists", "dump_container_backref"),
)
def test_dynamic_cycles_fail_closed_with_stable_location(cycle_kind: str) -> None:
    if cycle_kind == "self_list":
        value: Any = []
        value.append(value)
    elif cycle_kind == "mutual_lists":
        value = []
        sibling = [value]
        value.append(sibling)
    else:
        value = _DumpValue(None)
        value.value = {"ordinary": value}

    result = _judge_sensitive_dynamic_surface("actual.envelope", value)

    assert result.status == "failed"
    assert result.reasons == [
        "forbidden credential pattern detected: "
        "rule=scan_cycle; location=actual.envelope"
    ]


@pytest.mark.parametrize(
    "iterable_kind",
    ("set", "deque", "range", "generator", "custom"),
)
def test_unsupported_iterable_is_opaque_and_not_consumed_under_sensitive_key(
    iterable_kind: str,
) -> None:
    marker = _synthetic_credential_value(f"opaque-iterable-{iterable_kind}")
    consumed: list[str] = []
    custom = _NeverConsumedIterable()

    def values() -> Iterable[str]:
        consumed.append(marker)
        yield marker

    value: Any = {
        "set": {marker},
        "deque": deque([marker]),
        "range": range(3),
        "generator": values(),
        "custom": custom,
    }[iterable_kind]

    result = _judge_sensitive_dynamic_surface("actual.envelope", value)

    assert result.status == "failed"
    assert result.reasons == [
        "forbidden credential pattern detected: "
        "rule=password_or_passwd; location=actual.envelope"
    ]
    assert marker not in str(result.reasons)
    assert marker not in repr(result.reasons)
    assert consumed == []
    assert custom.iter_calls == 0


def test_model_dump_alias_is_materialized_once_but_each_edge_is_reclassified() -> None:
    marker = _synthetic_credential_value("model-dump-cross-root-alias")
    shared = _DumpValue(marker)

    result = judge_assertions(
        envelope={
            "status": "completed",
            "message": "操作完成",
            "ui": {"component_type": "none", "action": "none"},
            "data": {"ordinary": shared},
        },
        expected_response={"status": "completed"},
        trace_steps=[],
        expected_trace={"event_sequence": []},
        forbidden_items=[],
        adapter_assertion={"must_be_called": False, "must_not_be_called": False},
        adapter_calls={},
        credential_expectations={"then_custom": {"password": shared}},
    )

    assert result.status == "failed"
    assert result.reasons == [
        "forbidden credential pattern detected: "
        "rule=password_or_passwd; location=fixture.expectations"
    ]
    assert shared.call_count == 1
    assert marker not in str(result.reasons)
    assert marker not in repr(result.reasons)


@pytest.mark.parametrize(
    ("boundary_kind", "expected_rule"),
    (
        ("characters", "scan_string_char_limit"),
        ("depth", "scan_depth_limit"),
        ("nodes", "scan_node_limit"),
    ),
)
def test_model_dump_output_obeys_existing_snapshot_resource_limits(
    boundary_kind: str,
    expected_rule: str,
) -> None:
    transformation_chain: Any = None
    for _index in range(66):
        transformation_chain = _DumpValue(transformation_chain)
    dumped_value: Any = {
        "characters": "x" * 1_000_001,
        "depth": transformation_chain,
        "nodes": [None] * 10_001,
    }[boundary_kind]
    model = _DumpValue(dumped_value)

    result = judge_assertions(
        envelope={
            "status": "completed",
            "message": "操作完成",
            "ui": {"component_type": "none", "action": "none"},
            "data": {"ordinary": model},
        },
        expected_response={"status": "completed"},
        trace_steps=[],
        expected_trace={"event_sequence": []},
        forbidden_items=[],
        adapter_assertion={"must_be_called": False, "must_not_be_called": False},
        adapter_calls={},
    )

    assert result.status == "failed"
    assert result.reasons == [
        "forbidden credential pattern detected: "
        f"rule={expected_rule}; location=actual.envelope"
    ]
    assert model.call_count == 1
