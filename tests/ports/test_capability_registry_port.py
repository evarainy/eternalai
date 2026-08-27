"""Contract tests for CapabilityRegistryPort and CapabilitySpec."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any, get_type_hints

import pytest
from pydantic import ValidationError

from app.ports.capability_registry import CapabilityRegistryPort, CapabilitySpec
from app.ports.work_object_handling import WorkObjectHandlingSelector

REPO_ROOT = Path(__file__).resolve().parents[2]
CAPABILITY_REGISTRY_SOURCE = REPO_ROOT / "app" / "ports" / "capability_registry.py"

EXPECTED_CAPABILITY_SPEC_FIELDS = {
    "capability_id",
    "name",
    "type",
    "intent_tags",
    "input_schema",
    "output_schema",
    "input_schema_digest",
    "output_schema_digest",
    "risk_level",
    "owner",
    "version",
    "status",
    "short_description",
    "target_system",
    "execution_identity",
    "binding_required",
    "policy_digest",
    "automation_level",
    "displayable_argument_fields",
    "handles_work_objects",
}


def minimal_capability_data() -> dict[str, Any]:
    return {
        "capability_id": "capability-001",
        "name": "Lookup employee profile",
        "input_schema_digest": "sha256:input-schema",
        "output_schema_digest": "sha256:output-schema",
        "risk_level": "low",
        "owner": "domain-team",
        "version": "1.0.0",
        "status": "active",
        "short_description": "Reads a non-sensitive employee profile summary.",
        "execution_identity": "user_delegated",
        "binding_required": True,
    }


def full_capability_data() -> dict[str, Any]:
    data = minimal_capability_data()
    data.update(
        {
            "type": "query",
            "intent_tags": ["employee_lookup"],
            "input_schema": {"type": "object", "properties": {"employee_id": {"type": "string"}}},
            "output_schema": {"type": "object", "properties": {"display_name": {"type": "string"}}},
            "target_system": "oa",
            "policy_digest": "policy-digest-v1",
        }
    )
    return data


def test_capability_spec_field_set_matches_spec_8_6_2() -> None:
    assert set(CapabilitySpec.model_fields.keys()) == EXPECTED_CAPABILITY_SPEC_FIELDS


def test_capability_spec_accepts_full_spec_8_6_2_shape() -> None:
    spec = CapabilitySpec(**full_capability_data())

    assert spec.capability_id == "capability-001"
    assert spec.name == "Lookup employee profile"
    assert spec.type == "query"
    assert spec.intent_tags == ["employee_lookup"]
    assert spec.input_schema["properties"]["employee_id"]["type"] == "string"
    assert spec.output_schema["properties"]["display_name"]["type"] == "string"
    assert spec.input_schema_digest == "sha256:input-schema"
    assert spec.output_schema_digest == "sha256:output-schema"
    assert spec.risk_level == "low"
    assert spec.owner == "domain-team"
    assert spec.version == "1.0.0"
    assert spec.status == "active"
    assert spec.short_description == "Reads a non-sensitive employee profile summary."
    assert spec.target_system == "oa"
    assert spec.execution_identity == "user_delegated"
    assert spec.binding_required is True
    assert spec.policy_digest == "policy-digest-v1"
    assert spec.automation_level == "manual"
    assert spec.displayable_argument_fields == []
    assert spec.handles_work_objects == []


def test_capability_spec_optional_target_system_and_policy_digest_default_to_none() -> None:
    spec = CapabilitySpec(type="query", **minimal_capability_data())

    assert spec.target_system is None
    assert spec.policy_digest is None


def test_capability_spec_intent_tags_default_is_isolated_between_instances() -> None:
    first = CapabilitySpec(type="query", **minimal_capability_data())
    second = CapabilitySpec(type="query", **minimal_capability_data())

    first.intent_tags.append("new_intent")

    assert first.intent_tags == ["new_intent"]
    assert second.intent_tags == []


def test_capability_spec_input_schema_default_is_isolated_between_instances() -> None:
    first = CapabilitySpec(type="query", **minimal_capability_data())
    second = CapabilitySpec(type="query", **minimal_capability_data())

    first.input_schema["properties"] = {"employee_id": {"type": "string"}}

    assert first.input_schema == {"properties": {"employee_id": {"type": "string"}}}
    assert second.input_schema == {}


def test_capability_spec_output_schema_default_is_isolated_between_instances() -> None:
    first = CapabilitySpec(type="query", **minimal_capability_data())
    second = CapabilitySpec(type="query", **minimal_capability_data())

    first.output_schema["properties"] = {"display_name": {"type": "string"}}

    assert first.output_schema == {"properties": {"display_name": {"type": "string"}}}
    assert second.output_schema == {}


def test_capability_spec_new_fail_closed_defaults_are_isolated_between_instances() -> None:
    first = CapabilitySpec(type="query", **minimal_capability_data())
    second = CapabilitySpec(type="query", **minimal_capability_data())

    first.displayable_argument_fields.append("employee_id")
    first.handles_work_objects.append(
        WorkObjectHandlingSelector(
            source_system="oa",
            source_kind="pending_workflow",
            source_workflow_type_id="workflow-1",
        )
    )

    assert first.automation_level == "manual"
    assert second.automation_level == "manual"
    assert second.displayable_argument_fields == []
    assert second.handles_work_objects == []


def test_capability_spec_rejects_unknown_displayable_argument_field() -> None:
    data = full_capability_data()
    data["displayable_argument_fields"] = ["employee_typo"]

    with pytest.raises(ValidationError, match="input_schema.properties"):
        CapabilitySpec(**data)


def test_capability_spec_rejects_duplicate_displayable_argument_fields() -> None:
    data = full_capability_data()
    data["displayable_argument_fields"] = ["employee_id", "employee_id"]

    with pytest.raises(ValidationError, match="must not contain duplicates"):
        CapabilitySpec(**data)


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("type", "report"),
        ("risk_level", "critical"),
        ("status", "archived"),
        ("target_system", "erp"),
        ("execution_identity", "service_account"),
        ("automation_level", "automatic"),
    ],
)
def test_capability_spec_rejects_values_outside_common_contract(
    field: str,
    invalid_value: str,
) -> None:
    data = full_capability_data()
    data[field] = invalid_value

    try:
        CapabilitySpec(**data)
    except ValidationError as exc:
        assert "Input should be" in str(exc)
    else:
        raise AssertionError(f"CapabilitySpec accepted invalid {field!r}")


class TestCapabilityRegistryPortProtocol:
    def test_protocol_is_not_runtime_checkable(self) -> None:
        assert hasattr(CapabilityRegistryPort, "__protocol_attrs__")
        assert not getattr(CapabilityRegistryPort, "_is_runtime_protocol", False)

    def test_protocol_defines_only_spec_8_6_8_methods(self) -> None:
        assert set(CapabilityRegistryPort.__protocol_attrs__) == {
            "create",
            "get",
            "list",
            "update",
            "disable",
        }

    def test_create_signature_matches_spec_8_6_8(self) -> None:
        hints = get_type_hints(CapabilityRegistryPort.create)
        signature = inspect.signature(CapabilityRegistryPort.create)

        assert CapabilityRegistryPort.create.__name__ == "create"
        assert list(signature.parameters) == ["self", "capability"]
        assert hints["capability"] is CapabilitySpec
        assert hints["return"] is CapabilitySpec
        assert inspect.iscoroutinefunction(CapabilityRegistryPort.create)

    def test_get_signature_matches_spec_8_6_8(self) -> None:
        hints = get_type_hints(CapabilityRegistryPort.get)
        signature = inspect.signature(CapabilityRegistryPort.get)

        assert CapabilityRegistryPort.get.__name__ == "get"
        assert list(signature.parameters) == ["self", "capability_id"]
        assert hints["capability_id"] is str
        assert hints["return"] == CapabilitySpec | None
        assert inspect.iscoroutinefunction(CapabilityRegistryPort.get)

    def test_list_signature_matches_spec_8_6_8_without_tightened_filter_types(self) -> None:
        hints = get_type_hints(CapabilityRegistryPort.list)
        signature = inspect.signature(CapabilityRegistryPort.list)

        assert CapabilityRegistryPort.list.__name__ == "list"
        assert list(signature.parameters) == ["self", "target_system", "type", "status"]
        assert signature.parameters["target_system"].default is None
        assert signature.parameters["type"].default is None
        assert signature.parameters["status"].default is None
        assert hints["target_system"] == str | None
        assert hints["type"] == str | None
        assert hints["status"] == str | None
        assert hints["return"] == list[CapabilitySpec]
        assert inspect.iscoroutinefunction(CapabilityRegistryPort.list)

    def test_update_signature_matches_spec_8_6_8(self) -> None:
        hints = get_type_hints(CapabilityRegistryPort.update)
        signature = inspect.signature(CapabilityRegistryPort.update)

        assert CapabilityRegistryPort.update.__name__ == "update"
        assert list(signature.parameters) == ["self", "capability_id", "patch"]
        assert hints["capability_id"] is str
        assert hints["patch"] == dict[str, Any]
        assert hints["return"] is CapabilitySpec
        assert inspect.iscoroutinefunction(CapabilityRegistryPort.update)

    def test_disable_signature_matches_spec_8_6_8(self) -> None:
        hints = get_type_hints(CapabilityRegistryPort.disable)
        signature = inspect.signature(CapabilityRegistryPort.disable)

        assert CapabilityRegistryPort.disable.__name__ == "disable"
        assert list(signature.parameters) == ["self", "capability_id"]
        assert hints["capability_id"] is str
        assert hints["return"] is CapabilitySpec
        assert inspect.iscoroutinefunction(CapabilityRegistryPort.disable)


def test_capability_registry_source_does_not_contain_concrete_storage_or_helpers() -> None:
    source = CAPABILITY_REGISTRY_SOURCE.read_text(encoding="utf-8")

    forbidden_terms = (
        "sqlalchemy",
        "redis",
        "sqlite",
        "postgres",
        "open(",
        "Repository",
        "CapabilityPatch",
        "__eq__",
        "__hash__",
        "json",
        "digest(",
        "TaskRecord",
        "SessionRecord",
        "app.ports.task_store",
    )
    assert not any(term in source for term in forbidden_terms)
