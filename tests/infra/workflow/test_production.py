"""Registry activation contract tests; no database connections."""

import asyncio
from dataclasses import replace

import pytest

from app.infra.adapters.oa.capabilities import expected_oa_capabilities
from app.infra.workflow.catalog import production_workflow_capabilities
from app.infra.workflow.production import (
    validate_production_workflows,
    validate_selected_workflow,
    validate_workflow_configuration,
)
from app.ports.human_gate import VersionBindingMismatchError
from app.workflow.definitions import production_workflow_definitions
from tests.runtime.registry_fakes import StaticCapabilityRegistry


def catalog():
    definitions = production_workflow_definitions()
    descriptors = {item.capability_id: item for item in production_workflow_capabilities()}
    return definitions, descriptors


@pytest.mark.parametrize("fault", ["missing_rule", "old_version"])
def test_startup_rejects_missing_or_unsupported_required_rule(monkeypatch, fault):
    import app.infra.workflow.production as production

    definitions, descriptors = catalog()
    if fault == "missing_rule":
        monkeypatch.setattr(production, "required_postcondition_rule", lambda _: None)
    else:
        definitions["oa.read_overview"] = replace(
            definitions["oa.read_overview"], version="1.0.0",
        )
        descriptors["oa.read_overview"] = descriptors["oa.read_overview"].model_copy(
            update={"version": "1.0.0"},
        )
    with pytest.raises(RuntimeError, match="^workflow_configuration_invalid$"):
        validate_workflow_configuration(definitions, descriptors)


@pytest.mark.parametrize(
    "fault,error",
    [
        ("none", None),
        ("missing", None),
        ("disabled", None),
        ("draft", None),
        ("unknown", "workflow_definition_unavailable"),
        ("version", "workflow_contract_mismatch"),
        ("field", "workflow_contract_mismatch"),
        ("type", "workflow_contract_mismatch"),
        ("leaf_missing", "workflow_dependency_invalid"),
        ("leaf_disabled", "workflow_dependency_invalid"),
        ("leaf_high", "workflow_dependency_invalid"),
        ("leaf_workflow", "workflow_dependency_invalid"),
        ("leaf_schema", "workflow_dependency_invalid"),
        ("unavailable", "workflow_registry_unavailable"),
    ],
)
def test_startup_rejects_invalid_enabled_workflow(fault, error) -> None:
    definitions, descriptors = catalog()
    overview = descriptors["oa.read_overview"]
    leaves = list(expected_oa_capabilities())
    if fault in {"disabled", "draft"}:
        overview = overview.model_copy(update={"status": fault})
    if fault == "unknown":
        overview = overview.model_copy(update={"capability_id": "oa.unknown"})
    if fault == "version":
        overview = overview.model_copy(update={"version": "9.0.0"})
    if fault == "field":
        overview = overview.model_copy(update={"automation_level": "manual"})
    if fault == "type":
        overview = overview.model_copy(update={"type": "query"})
    if fault == "leaf_missing":
        leaves.pop(0)
    updates = {
        "leaf_disabled": {"status": "disabled"},
        "leaf_high": {"risk_level": "high"},
        "leaf_workflow": {"type": "workflow"},
        "leaf_schema": {"input_schema": {}},
    }
    if fault in updates:
        leaves[0] = leaves[0].model_copy(update=updates[fault])
    registry = StaticCapabilityRegistry(*leaves, *([] if fault == "missing" else [overview]))
    if fault == "unavailable":

        async def unavailable(**kwargs):
            raise OSError("synthetic-private-diagnostic")

        registry.list = unavailable
    operation = validate_production_workflows(
        registry=registry,
        definitions=definitions,
        descriptors=descriptors,
    )
    if error:
        with pytest.raises(RuntimeError, match=f"^{error}$") as caught:
            asyncio.run(operation)
        assert caught.value.__suppress_context__ is True
    else:
        assert asyncio.run(operation) is None


@pytest.mark.parametrize("selected", [("",), ("pending", "pending"), ("missing",)])
def test_invalid_static_catalog_is_rejected(selected) -> None:
    definitions, descriptors = catalog()
    definitions["oa.read_overview"] = replace(
        definitions["oa.read_overview"],
        output_step_ids=selected,
    )
    with pytest.raises(RuntimeError, match="^workflow_configuration_invalid$"):
        validate_workflow_configuration(definitions, descriptors)


@pytest.mark.parametrize("fault", ["disabled", "version", "dependency", "unknown", "unavailable"])
def test_selected_contract_is_rechecked_without_leaking_registry_errors(fault) -> None:
    definitions, descriptors = catalog()
    selected = descriptors["oa.read_overview"]
    leaves = list(expected_oa_capabilities())
    current = selected
    if fault in {"disabled", "version"}:
        current = selected.model_copy(
            update={
                "status": "disabled" if fault == "disabled" else "active",
                "version": "2.0.0" if fault == "version" else selected.version,
            }
        )
    if fault == "dependency":
        leaves[0] = leaves[0].model_copy(update={"policy_digest": "changed"})
    if fault == "unknown":
        selected = current = selected.model_copy(update={"capability_id": "oa.unknown"})
    registry = StaticCapabilityRegistry(current, *leaves)
    if fault == "unavailable":

        async def unavailable(*args):
            raise OSError("synthetic-private-diagnostic")

        registry.get = unavailable
    with pytest.raises(
        VersionBindingMismatchError, match="^Workflow activation contract is unavailable$"
    ):
        asyncio.run(
            validate_selected_workflow(
                selected,
                registry=registry,
                definitions=definitions,
                descriptors=descriptors,
            )
        )
