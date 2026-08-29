"""Fail-closed ResponseEnvelope data projection contracts."""

from __future__ import annotations

from typing import Any

import pytest

from app.infra.adapters.oa.contracts import (
    OAPendingWorkflowCollection,
    OASystemMessageCollection,
)
from app.infra.sdui.credential_markers import has_credential_marker
from app.runtime.response_projection import (
    ProjectionContractSnapshot,
    canonical_schema_digest,
    project_response_data,
    schema_has_credential_property,
)
from tests.runtime.registry_fakes import active_capability


def test_real_pending_contract_projects_nested_object_array() -> None:
    schema = OAPendingWorkflowCollection.model_json_schema()
    data = {
        "workflows": [
            {
                "todo_id": "SYNTHETIC_TODO_1",
                "title": "SYNTHETIC_TITLE",
                "status": "pending",
                "received_at": "2026-08-30T01:00:00Z",
                "created_at": "2026-08-30T00:00:00Z",
                "workflow_type_id": "SYNTHETIC_TYPE",
                "undeclared": "SYNTHETIC_NESTED_DROP",
            }
        ],
        "returned_count": 1,
        "authoritative_count": 1,
        "is_complete": True,
        "undeclared_root": "SYNTHETIC_ROOT_DROP",
    }

    projected = project_response_data(data, schema)

    assert projected == {
        "workflows": [
            {
                "todo_id": "SYNTHETIC_TODO_1",
                "title": "SYNTHETIC_TITLE",
                "status": "pending",
                "received_at": "2026-08-30T01:00:00Z",
                "created_at": "2026-08-30T00:00:00Z",
                "workflow_type_id": "SYNTHETIC_TYPE",
            }
        ],
        "returned_count": 1,
        "authoritative_count": 1,
        "is_complete": True,
    }
    assert "SYNTHETIC_NESTED_DROP" not in repr(projected)
    assert "SYNTHETIC_ROOT_DROP" not in repr(projected)


@pytest.mark.parametrize(
    ("link", "mobile_link"),
    ((None, "/SYNTHETIC_MOBILE"), ("/SYNTHETIC_LINK", None)),
)
def test_real_system_message_contract_preserves_nullable_values(
    link: str | None,
    mobile_link: str | None,
) -> None:
    schema = OASystemMessageCollection.model_json_schema()
    data = {
        "messages": [
            {
                "message_id": "SYNTHETIC_MESSAGE",
                "title": "SYNTHETIC_TITLE",
                "content": "SYNTHETIC_CONTENT",
                "source_name": "SYNTHETIC_SOURCE",
                "occurred_at": "2026-08-30T00:00:00Z",
                "business_state": "1",
                "link": link,
                "mobile_link": mobile_link,
            }
        ],
        "returned_count": 1,
        "is_complete": False,
    }

    projected = project_response_data(data, schema)

    assert projected is not None
    assert projected["messages"][0]["link"] == link
    assert projected["messages"][0]["mobile_link"] == mobile_link


def test_scalar_nested_object_and_object_array_are_projected_without_unknowns() -> None:
    schema = {
        "type": "object",
        "properties": {
            "safe": {"type": "string"},
            "nested": {
                "type": "object",
                "properties": {"count": {"type": "integer"}},
            },
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                },
            },
        },
    }
    data = {
        "safe": "SYNTHETIC_SAFE",
        "nested": {"count": 2, "unknown": "SYNTHETIC_NESTED_UNKNOWN"},
        "items": [
            {"name": "SYNTHETIC_ITEM", "unknown": "SYNTHETIC_ITEM_UNKNOWN"}
        ],
        "unknown": "SYNTHETIC_ROOT_UNKNOWN",
    }

    assert project_response_data(data, schema) == {
        "safe": "SYNTHETIC_SAFE",
        "nested": {"count": 2},
        "items": [{"name": "SYNTHETIC_ITEM"}],
    }


@pytest.mark.parametrize(
    ("schema", "value"),
    (
        ({"type": "string"}, {"raw": "SYNTHETIC_STRING_OBJECT"}),
        ({"type": "object", "properties": {"safe": {"type": "string"}}}, "bad"),
        ({"type": "array", "items": {"type": "string"}}, {"bad": "array"}),
    ),
)
def test_type_mismatch_drops_declared_field(
    schema: dict[str, Any],
    value: Any,
) -> None:
    root = {"type": "object", "properties": {"field": schema}}

    assert project_response_data({"field": value}, root) == {}


@pytest.mark.parametrize("schema_type", ("integer", "number"))
def test_boolean_never_projects_as_number(schema_type: str) -> None:
    schema = {
        "type": "object",
        "properties": {"value": {"type": schema_type}},
    }

    assert project_response_data({"value": True}, schema) == {}


@pytest.mark.parametrize(
    "schema",
    (
        {"type": "object"},
        {"type": "array"},
        {"type": "object", "properties": {}, "additionalProperties": True},
        {"type": "object", "properties": {"safe": {"type": "string"}}, "oneOf": []},
        {"type": "object", "properties": {"safe": {"type": "string"}}, "unknown": 1},
    ),
)
def test_unsupported_or_unbounded_root_schema_fails_closed(
    schema: dict[str, Any],
) -> None:
    assert project_response_data({"safe": "SYNTHETIC_SAFE"}, schema) is None


@pytest.mark.parametrize(
    "schema",
    (
        {"$ref": "#/$defs/Missing", "$defs": {}},
        {"$ref": "https://example.test/schema"},
        {
            "$ref": "#/$defs/Loop",
            "$defs": {"Loop": {"$ref": "#/$defs/Loop"}},
        },
        {
            "$ref": "#/$defs/Value",
            "type": "object",
            "$defs": {
                "Value": {
                    "type": "object",
                    "properties": {"safe": {"type": "string"}},
                }
            },
        },
    ),
)
def test_invalid_reference_fails_closed(schema: dict[str, Any]) -> None:
    assert project_response_data({"safe": "SYNTHETIC_SAFE"}, schema) is None


def test_nullable_null_branch_with_unknown_keyword_is_dropped() -> None:
    schema = {
        "type": "object",
        "properties": {
            "value": {
                "anyOf": [
                    {"type": "string"},
                    {"type": "null", "unknown_structure": True},
                ]
            }
        },
    }

    assert project_response_data({"value": None}, schema) == {}


def test_dynamic_additional_properties_requires_schema_and_drops_marker_keys() -> None:
    schema = {
        "type": "object",
        "additionalProperties": {"type": "string"},
    }

    assert project_response_data(
        {
            "safe_dynamic": "SYNTHETIC_SAFE",
            "session_token": "SYNTHETIC_PRIVATE",
        },
        schema,
    ) == {"safe_dynamic": "SYNTHETIC_SAFE"}


def test_recursive_credential_property_invalidates_entire_contract() -> None:
    schema = {
        "type": "object",
        "properties": {
            "safe": {"type": "string"},
            "nested": {
                "type": "object",
                "properties": {"access_token": {"type": "string"}},
            },
        },
    }

    assert has_credential_marker("access_token")
    assert schema_has_credential_property(schema)
    assert project_response_data({"safe": "SYNTHETIC_SAFE"}, schema) is None


def test_empty_schema_and_missing_data_fail_closed_but_valid_empty_object_is_kept() -> None:
    schema = {
        "type": "object",
        "properties": {"safe": {"type": "string"}},
    }

    assert project_response_data({"safe": "SYNTHETIC_SAFE"}, {}) is None
    assert project_response_data(None, schema) is None
    assert project_response_data({"unknown": "SYNTHETIC_DROP"}, schema) == {}


def test_snapshot_is_canonical_and_immune_to_nested_source_mutation() -> None:
    schema = {
        "type": "object",
        "properties": {"safe": {"type": "string"}},
    }
    capability = active_capability("SYNTHETIC_CAPABILITY", output_schema=schema)
    snapshot = ProjectionContractSnapshot.from_capability(capability)
    original_json = snapshot.output_schema_json

    capability.output_schema["properties"]["safe"]["type"] = "integer"
    schema["properties"]["safe"]["type"] = "boolean"

    assert snapshot.output_schema_json == original_json
    assert snapshot.load_output_schema()["properties"]["safe"]["type"] == "string"
    assert canonical_schema_digest(snapshot.load_output_schema()) == (
        capability.output_schema_digest
    )
