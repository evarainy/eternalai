"""Validate all Golden Task JSON fixtures conform to the required schema."""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    load_fixture: Callable[[str], dict[str, Any]]
else:
    from scripts.golden_task_fixture_support import load_fixture

GT_IDS = [
    "GT-001",
    "GT-002",
    "GT-003",
    "GT-004",
    "GT-005",
    "GT-006",
    "GT-007",
    "GT-008",
    "GT-009",
    "GT-010",
    "GT-012",
    "GT-013",
    "GT-014",
    "GT-015",
    "GT-016",
    "GT-017",
    "GT-018",
    "GT-019",
]
REQUIRED_TOP_LEVEL = [
    "golden_task_id",
    "title",
    "category",
    "given",
    "when",
    "then_response",
    "then_trace",
    "then_forbidden",
    "adapter_assertion",
]
REQUIRED_GIVEN = [
    "ai_user_id",
    "roles",
    "identity_mappings",
    "registered_capabilities",
]
REQUIRED_ADAPTER_ASSERTION = [
    "must_be_called",
    "must_not_be_called",
]


@pytest.mark.parametrize("gt_id", GT_IDS)
def test_fixture_loads_without_error(gt_id: str) -> None:
    fixture = load_fixture(gt_id)
    assert isinstance(fixture, dict)


@pytest.mark.parametrize("gt_id", GT_IDS)
def test_fixture_has_required_top_level_fields(gt_id: str) -> None:
    fixture = load_fixture(gt_id)
    for field in REQUIRED_TOP_LEVEL:
        assert field in fixture, f"{gt_id} missing field: {field}"


@pytest.mark.parametrize("gt_id", GT_IDS)
def test_fixture_golden_task_id_matches_filename(gt_id: str) -> None:
    fixture = load_fixture(gt_id)
    assert fixture["golden_task_id"] == gt_id


@pytest.mark.parametrize("gt_id", GT_IDS)
def test_fixture_category_is_valid(gt_id: str) -> None:
    fixture = load_fixture(gt_id)
    assert fixture["category"] in {"positive", "negative"}


@pytest.mark.parametrize("gt_id", GT_IDS)
def test_fixture_given_has_required_fields(gt_id: str) -> None:
    fixture = load_fixture(gt_id)
    given = fixture["given"]
    for field in REQUIRED_GIVEN:
        assert field in given, f"{gt_id}.given missing: {field}"


@pytest.mark.parametrize("gt_id", GT_IDS)
def test_fixture_adapter_assertion_has_both_fields(gt_id: str) -> None:
    fixture = load_fixture(gt_id)
    adapter_assertion = fixture["adapter_assertion"]
    for field in REQUIRED_ADAPTER_ASSERTION:
        assert field in adapter_assertion, f"{gt_id}.adapter_assertion missing: {field}"


def test_at_least_three_fixtures_have_must_not_be_called() -> None:
    must_not_called = [
        gt_id
        for gt_id in GT_IDS
        if load_fixture(gt_id)["adapter_assertion"].get("must_not_be_called") is True
    ]
    assert len(must_not_called) >= 3


def test_gt_012_has_fallback_forbidden_items() -> None:
    fixture = load_fixture("GT-012")
    forbidden = fixture["then_forbidden"]
    assert "fallback_to_first_binding" in forbidden
    assert "fallback_to_system_scope" in forbidden


@pytest.mark.parametrize(
    ("gt_id", "expected_error_code"),
    (
        ("GT-015", "identity_expired"),
        ("GT-016", "identity_revoked"),
    ),
)
def test_b3_inactive_binding_fixtures_require_exact_identity_short_circuit(
    gt_id: str,
    expected_error_code: str,
) -> None:
    fixture = load_fixture(gt_id)
    response = fixture["then_response"]
    events = fixture["then_trace"]["event_sequence"]

    assert response["status"] == "blocked"
    assert response["envelope"]["ui.action"] == "bind_required"
    assert response["envelope"]["ui.reason_code"] == expected_error_code
    assert fixture["then_trace"]["reason"] == expected_error_code
    assert fixture["mock_failure_injection"]["expected_error_code"] == (expected_error_code)
    assert events.index("blocked_by_identity") == events.index("identity_check") + 1
    assert "policy_checked" not in events
    assert fixture["policy_assertion"] == {
        "must_be_called": False,
        "must_not_be_called": True,
    }
    assert fixture["adapter_assertion"] == {
        "must_be_called": False,
        "must_not_be_called": True,
    }


@pytest.mark.parametrize(
    ("gt_id", "request_field", "mapping_field"),
    (
        ("GT-017", "account_set_id", "account_set_id"),
        ("GT-018", "device_domain_id", "device_domain_id"),
        ("GT-019", "resource_scope", "binding_scope"),
    ),
)
def test_b3_explicit_scope_ambiguity_fixtures_cannot_degrade_to_gt_012(
    gt_id: str,
    request_field: str,
    mapping_field: str,
) -> None:
    fixture = load_fixture(gt_id)
    requested_scope = fixture["when"]["arguments"][request_field]
    mappings = fixture["given"]["identity_mappings"]
    delegated_matches = [
        mapping
        for mapping in mappings
        if mapping.get("status") == "active"
        and mapping.get("execution_identity") == "user_delegated"
        and mapping.get(mapping_field) == requested_scope
    ]
    system_scope_traps = [
        mapping
        for mapping in mappings
        if mapping.get("status") == "active"
        and mapping.get("execution_identity") == "system_scope"
        and mapping.get(mapping_field) == requested_scope
    ]
    response = fixture["then_response"]

    assert len(delegated_matches) == 2
    assert system_scope_traps
    assert response["status"] == "blocked"
    assert response["envelope"]["ui.action"] == "clarify_scope"
    assert response["envelope"]["ui.reason_code"] == "needs_binding_scope"
    assert fixture["then_trace"]["reason"] == "needs_binding_scope"
    assert "fallback_to_first_binding" in fixture["then_forbidden"]
    assert "fallback_to_system_scope" in fixture["then_forbidden"]
    assert fixture["policy_assertion"]["must_not_be_called"] is True
    assert fixture["adapter_assertion"]["must_not_be_called"] is True


@pytest.mark.parametrize(
    "gt_id",
    [
        "GT-001",
        "GT-002",
        "GT-003",
        "GT-004",
        "GT-005",
        "GT-007",
    ],
)
def test_positive_fixtures_with_adapter_calls_have_failure_injection(gt_id: str) -> None:
    fixture = load_fixture(gt_id)
    if fixture["adapter_assertion"].get("must_be_called"):
        assert fixture["mock_failure_injection"]["enabled"] is True
