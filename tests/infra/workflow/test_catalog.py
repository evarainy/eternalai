"""Exact code catalog, schemas, and ownership of the read-only first Workflow."""

from dataclasses import asdict

import pytest
from pydantic import ValidationError

from app.infra.adapters.oa.capabilities import expected_oa_capabilities
from app.infra.adapters.oa.contracts import OAPendingWorkflowCollection, OASystemMessageCollection
from app.infra.workflow.catalog import OAReadOverviewInput, production_workflow_capabilities
from app.ports.response_projection_contract import canonical_schema_digest
from app.workflow.definitions import production_workflow_definitions


def test_catalog_contains_only_versioned_read_overview() -> None:
    definitions = production_workflow_definitions()
    assert set(definitions) == {"oa.read_overview"}
    definition = definitions["oa.read_overview"]
    assert asdict(definition) == {
        "workflow_id": "oa.read_overview",
        "version": "1.0.0",
        "output_step_ids": ("pending", "messages"),
        "steps": tuple(
            {
                "step_id": step_id,
                "capability_id": leaf.capability_id,
                "confirmed_capability_id": None,
                "static_arguments": {},
                "input_mapping": {},
                "when": None,
            }
            for step_id, leaf in zip(
                ("pending", "messages"), expected_oa_capabilities(), strict=True
            )
        ),
    }
    (capability,) = production_workflow_capabilities()
    assert capability.model_dump(
        exclude={
            "input_schema",
            "output_schema",
            "input_schema_digest",
            "output_schema_digest",
        }
    ) == {
        "capability_id": "oa.read_overview",
        "name": "OA 待办与系统消息概览",
        "type": "workflow",
        "version": "1.0.0",
        "status": "active",
        "intent_tags": ["oa.read_overview"],
        "short_description": "一次查看当前 OA 用户的待办事宜和系统消息,只读,不提交审批或办理事项。",
        "target_system": "oa",
        "execution_identity": "user_delegated",
        "binding_required": True,
        "risk_level": "low",
        "owner": "eternalai-platform",
        "automation_level": "full",
        "policy_digest": None,
        "displayable_argument_fields": [],
        "handles_work_objects": [],
    }
    assert capability.input_schema == OAReadOverviewInput.model_json_schema()
    assert capability.input_schema["additionalProperties"] is False
    assert capability.input_schema["properties"] == {}
    assert capability.input_schema_digest == canonical_schema_digest(capability.input_schema)
    assert capability.output_schema_digest == canonical_schema_digest(capability.output_schema)
    schema = capability.output_schema
    assert schema["required"] == ["pending", "messages"]
    assert schema["additionalProperties"] is False
    for model in (OAPendingWorkflowCollection, OASystemMessageCollection):
        leaf_schema = model.model_json_schema()
        assert schema["$defs"][model.__name__] == {
            key: value for key, value in leaf_schema.items() if key != "$defs"
        }
    assert OAReadOverviewInput.model_validate({}).model_dump() == {}
    for key in ("ai_user_id", "tenant_id", "session_id", "scope", "unknown"):
        with pytest.raises(ValidationError):
            OAReadOverviewInput.model_validate({key: "synthetic-owner"})
    definitions["oa.read_overview"].steps[0].static_arguments["changed"] = True
    assert production_workflow_definitions()["oa.read_overview"].steps[0].static_arguments == {}
    capability.input_schema["properties"]["changed"] = {}
    assert production_workflow_capabilities()[0].input_schema["properties"] == {}
