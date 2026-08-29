"""Validate all Golden Task JSON fixtures conform to the required schema."""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import pytest

from app.runtime.response_projection import canonical_schema_digest

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
    "GT-020",
    "GT-021",
    "GT-022",
    "GT-023",
    "GT-024",
    "GT-025",
    "GT-026",
    "GT-027",
    "GT-028",
    "GT-029",
    "GT-030",
    "GT-031",
    "GT-032",
    "GT-033",
]

CHANGED_OUTPUT_SCHEMA_GT_IDS = (
    "GT-001",
    "GT-003",
    "GT-004",
    "GT-005",
    "GT-007",
    "GT-020",
    "GT-024",
    "GT-025",
    "GT-027",
)
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
PENDING_COLLECTION_FIELDS = {
    "workflows",
    "returned_count",
    "authoritative_count",
    "is_complete",
}
PENDING_WORKFLOW_FIELDS = {
    "todo_id",
    "title",
    "status",
    "received_at",
    "created_at",
    "workflow_type_id",
}
MESSAGE_CENTER_FIELDS = {
    "message_id",
    "content",
    "source_name",
    "occurred_at",
    "business_state",
    "link",
    "mobile_link",
}


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


@pytest.mark.parametrize("gt_id", ["GT-020", "GT-021", "GT-022", "GT-023", "GT-024"])
def test_b4_workflow_fixtures_bind_definition_trace_and_exact_calls(gt_id: str) -> None:
    fixture = load_fixture(gt_id)
    definitions = fixture["given"]["workflow_definitions"]
    workflow_capabilities = [
        capability
        for capability in fixture["given"]["registered_capabilities"]
        if capability["type"] == "workflow"
    ]

    assert len(definitions) == 1
    assert len(workflow_capabilities) == 1
    assert definitions[0]["workflow_id"] == workflow_capabilities[0]["capability_id"]
    assert definitions[0]["version"] == workflow_capabilities[0]["version"]
    assert definitions[0]["steps"]
    assert fixture["then_trace"]["event_details"]
    assert fixture["then_workflow"]["event_sequence"][0] == "workflow_started"
    assert "workflow_step_finished" in fixture["then_workflow"]["event_sequence"]
    assert fixture["then_workflow"]["workflow_version"] == definitions[0]["version"]
    assert fixture["adapter_assertion"]["exact_calls"]


def test_gt_020_locks_io_mapping_and_unselected_branch_zero_calls() -> None:
    fixture = load_fixture("GT-020")
    steps = fixture["given"]["workflow_definitions"][0]["steps"]
    mapped_step = steps[1]
    skipped_step = steps[2]

    assert mapped_step["input_mapping"]["account_set_id"] == {
        "source": "step_output",
        "step_id": "lookup_document",
        "key": "account_set_id",
    }
    assert skipped_step["when"]["value"]["source"] == "step_output"
    assert fixture["adapter_assertion"]["exact_calls"] == {
        "u8.b4.document.lookup": 1,
        "u8.b4.vendor.balance": 1,
        "u8.b4.branch.never": 0,
    }


def test_gt_021_locks_retry_exhaustion_without_later_step_or_completion() -> None:
    fixture = load_fixture("GT-021")

    assert fixture["then_response"]["status"] == "failed"
    assert fixture["then_trace"]["reason"] == "adapter_timeout"
    assert "task_completed" not in fixture["then_trace"]["event_sequence"]
    assert fixture["then_workflow"]["terminal_status"] == "timeout"
    assert fixture["then_workflow"]["terminal_error_code"] == "adapter_timeout"
    assert fixture["adapter_assertion"]["exact_calls"] == {
        "oa.b4.timeout.unstable": 2,
        "oa.b4.timeout.later": 0,
    }


@pytest.mark.parametrize(
    ("gt_id", "zero_capabilities"),
    (
        (
            "GT-023",
            {
                "oa.b4.policy.confirm",
                "oa.b4.policy.confirmed",
                "oa.b4.policy.confirm_later",
            },
        ),
        ("GT-024", {"oa.b4.human_gate"}),
    ),
)
def test_b4_confirm_fixtures_keep_required_zero_call_assertions(
    gt_id: str,
    zero_capabilities: set[str],
) -> None:
    exact_calls = load_fixture(gt_id)["adapter_assertion"]["exact_calls"]

    assert {capability_id for capability_id, count in exact_calls.items() if count == 0} >= (
        zero_capabilities
    )


def test_gt_024_is_one_confirmation_round_with_definition_drift_trap() -> None:
    fixture = load_fixture("GT-024")
    original = fixture["given"]["workflow_definitions"][0]
    replacement = fixture["given"]["workflow_definition_after_wait"]
    evidence = fixture["then_workflow"]

    assert fixture["when"]["confirmation_message"] == "确认"
    assert replacement["workflow_id"] == original["workflow_id"]
    assert replacement["version"] == "2.0.0"
    assert replacement["version"] != original["version"]
    assert evidence["workflow_version"] == "1.0.0"
    assert evidence["source_definition_version_after_first"] == "2.0.0"
    assert evidence["first_round"]["response"]["status"] == "waiting_user"
    assert all(count == 0 for count in evidence["first_round"]["exact_calls"].values())


@pytest.mark.parametrize("gt_id", ["GT-027", "GT-028"])
def test_appended_pending_goldens_register_the_full_todo_output_schema(
    gt_id: str,
) -> None:
    fixture = load_fixture(gt_id)
    capabilities = fixture["given"]["registered_capabilities"]

    assert len(capabilities) == 1
    capability = capabilities[0]
    assert capability["capability_id"] == "oa.list_pending_workflows"
    assert capability["version"] == "2.0.0"
    assert capability["input_schema"]["properties"] == {}
    assert capability["input_schema"]["additionalProperties"] is False

    output_schema = capability["output_schema"]
    assert output_schema["type"] == "object"
    assert output_schema["additionalProperties"] is False
    assert set(output_schema["properties"]) == PENDING_COLLECTION_FIELDS
    assert set(output_schema["required"]) == PENDING_COLLECTION_FIELDS
    assert output_schema["properties"]["workflows"]["items"] == {
        "$ref": "#/$defs/OAPendingWorkflow"
    }
    assert output_schema["properties"]["is_complete"]["const"] is True

    definitions = output_schema["$defs"]
    assert set(definitions) == {"OAPendingWorkflow"}
    workflow_schema = definitions["OAPendingWorkflow"]
    assert workflow_schema["type"] == "object"
    assert workflow_schema["additionalProperties"] is False
    assert set(workflow_schema["properties"]) == PENDING_WORKFLOW_FIELDS
    assert set(workflow_schema["required"]) == PENDING_WORKFLOW_FIELDS
    assert all(
        workflow_schema["properties"][field]["type"] == "string"
        for field in PENDING_WORKFLOW_FIELDS
    )
    assert capability["output_schema_digest"] == (
        "87d879295d5ea7c9dd73efa55068e4e4a8c8c682a49607ff06acd739a8bfe320"
    )


@pytest.mark.parametrize("gt_id", CHANGED_OUTPUT_SCHEMA_GT_IDS)
def test_changed_output_schema_digest_is_canonical(gt_id: str) -> None:
    fixture = load_fixture(gt_id)
    capability = fixture["given"]["registered_capabilities"][0]

    assert capability["output_schema_digest"] == canonical_schema_digest(
        capability["output_schema"]
    )


def test_gt_027_locks_six_field_todo_model_complete_counts_and_zero_arguments() -> None:
    fixture = load_fixture("GT-027")
    collection = fixture["given"]["mock_oa_state"]["pending_workflows"]
    workflows = collection["workflows"]

    assert set(collection) == PENDING_COLLECTION_FIELDS
    assert collection["returned_count"] == len(workflows) == 2
    assert collection["authoritative_count"] == len(workflows)
    assert collection["is_complete"] is True
    assert all(set(workflow) == PENDING_WORKFLOW_FIELDS for workflow in workflows)
    assert all(not (set(workflow) & MESSAGE_CENTER_FIELDS) for workflow in workflows)
    assert len({workflow["todo_id"] for workflow in workflows}) == len(workflows)
    assert {workflow["status"] for workflow in workflows} == {"SYNTHETIC_PENDING"}

    response_assertions = fixture["then_response"]["envelope"]
    for field in PENDING_WORKFLOW_FIELDS:
        assert response_assertions[f"data.workflows.0.{field}"] == workflows[0][field]
    assert response_assertions["data.returned_count"] == len(workflows)
    assert response_assertions["data.authoritative_count"] == len(workflows)
    assert response_assertions["data.is_complete"] is True
    assert fixture["adapter_assertion"]["exact_calls"] == {
        "oa.list_pending_workflows": 1
    }
    assert fixture["adapter_assertion"]["exact_arguments"] == {
        "oa.list_pending_workflows": [{}]
    }


def test_gt_028_locks_count_mismatch_failure_data_null_and_faithful_trace() -> None:
    fixture = load_fixture("GT-028")
    collection = fixture["given"]["mock_oa_state"]["pending_workflows"]
    events = fixture["then_trace"]["event_sequence"]
    details = fixture["then_trace"]["event_details"]

    assert collection["returned_count"] == len(collection["workflows"])
    assert collection["authoritative_count"] > collection["returned_count"]
    assert collection["is_complete"] is True
    assert fixture["then_response"]["status"] == "failed"
    assert fixture["then_response"]["envelope"]["data"] is None
    assert fixture["then_trace"]["reason"] == "adapter_payload_invalid"
    assert "adapter_error_mapped" in events
    assert "task_failed" in events
    assert "task_completed" not in events
    assert {
        detail["event_type"]
        for detail in details
        if detail.get("error_code") == "adapter_payload_invalid"
    } == {
        "adapter_called",
        "gateway_post_recorded",
        "adapter_error_mapped",
        "task_failed",
    }
    assert fixture["adapter_assertion"]["exact_calls"] == {
        "oa.list_pending_workflows": 1
    }
    assert fixture["adapter_assertion"]["exact_arguments"] == {
        "oa.list_pending_workflows": [{}]
    }
