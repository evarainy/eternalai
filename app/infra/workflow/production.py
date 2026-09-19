"""Fail-closed validation of code-owned Workflow activation; never writes Registry."""

from collections.abc import Mapping

from app.evaluator.overview import OVERVIEW_VERSION, required_postcondition_rule
from app.infra.adapters.oa.capabilities import expected_oa_capabilities
from app.infra.workflow.catalog import OVERVIEW_ID
from app.ports.capability_registry import CapabilityRegistryPort, CapabilitySpec
from app.ports.human_gate import VersionBindingMismatchError
from app.workflow.definitions import production_workflow_definitions
from app.workflow.engine import WorkflowEngine
from app.workflow.models import WorkflowDefinition


def validate_workflow_configuration(
    definitions: Mapping[str, WorkflowDefinition],
    descriptors: Mapping[str, CapabilitySpec],
) -> None:
    try:
        if not definitions or set(definitions) != set(descriptors):
            raise ValueError("catalog")
        for key, definition in definitions.items():
            if key != definition.workflow_id or not key or not definition.version:
                raise ValueError("definition")
            WorkflowEngine.validate_structure(definition)
            descriptor = descriptors[key]
            if key == OVERVIEW_ID and (
                required_postcondition_rule(key) != "oa_read_overview_v1"
                or definition.version != OVERVIEW_VERSION
            ):
                raise ValueError("postcondition version")
            if (
                descriptor.capability_id != key
                or descriptor.type != "workflow"
                or descriptor.version != definition.version
                or descriptor.status != "active"
            ):
                raise ValueError("descriptor")
            if key == OVERVIEW_ID and definition != production_workflow_definitions()[OVERVIEW_ID]:
                raise ValueError("read contract")
    except (ValueError, TypeError, AttributeError):
        raise RuntimeError("workflow_configuration_invalid") from None


async def _validate_entry(
    capability: CapabilitySpec,
    *,
    registry: CapabilityRegistryPort,
    definitions: Mapping[str, WorkflowDefinition],
    descriptors: Mapping[str, CapabilitySpec],
) -> None:
    definition = definitions.get(capability.capability_id)
    canonical = descriptors.get(capability.capability_id)
    if definition is None or canonical is None:
        raise RuntimeError("workflow_definition_unavailable")
    if capability.capability_id == OVERVIEW_ID and (
        required_postcondition_rule(capability.capability_id) != "oa_read_overview_v1"
        or capability.version != OVERVIEW_VERSION or definition.version != OVERVIEW_VERSION
    ):
        raise RuntimeError("workflow_contract_mismatch")
    if capability != canonical or definition.version != capability.version:
        raise RuntimeError("workflow_contract_mismatch")
    canonical_leaves = {item.capability_id: item for item in expected_oa_capabilities()}
    for step in definition.steps:
        for leaf_id in (step.capability_id, step.confirmed_capability_id):
            if leaf_id is None:
                continue
            leaf = await registry.get(leaf_id)
            if (
                leaf is None
                or leaf.status != "active"
                or leaf.risk_level != "low"
                or leaf.type == "workflow"
                or (
                    capability.capability_id == OVERVIEW_ID
                    and leaf != canonical_leaves.get(leaf_id)
                )
            ):
                raise RuntimeError("workflow_dependency_invalid")


async def validate_production_workflows(
    *,
    registry: CapabilityRegistryPort,
    definitions: Mapping[str, WorkflowDefinition],
    descriptors: Mapping[str, CapabilitySpec],
) -> None:
    validate_workflow_configuration(definitions, descriptors)
    try:
        active = await registry.list(type="workflow", status="active")
        active_ids = {item.capability_id for item in active}
        for capability_id in descriptors:
            if capability_id not in active_ids:
                current = await registry.get(capability_id)
                if current is not None and current.status == "active":
                    active.append(current)
        for capability in sorted(active, key=lambda item: item.capability_id not in definitions):
            await _validate_entry(
                capability,
                registry=registry,
                definitions=definitions,
                descriptors=descriptors,
            )
    except RuntimeError as exc:
        if str(exc) in {
            "workflow_definition_unavailable",
            "workflow_contract_mismatch",
            "workflow_dependency_invalid",
        }:
            raise RuntimeError(str(exc)) from None
        raise RuntimeError("workflow_registry_unavailable") from None
    except Exception:
        raise RuntimeError("workflow_registry_unavailable") from None


async def validate_selected_workflow(
    capability: CapabilitySpec,
    *,
    registry: CapabilityRegistryPort,
    definitions: Mapping[str, WorkflowDefinition],
    descriptors: Mapping[str, CapabilitySpec],
) -> None:
    try:
        current = await registry.get(capability.capability_id)
        if current != capability:
            raise ValueError("selected snapshot changed")
        await _validate_entry(
            capability,
            registry=registry,
            definitions=definitions,
            descriptors=descriptors,
        )
    except Exception:
        raise VersionBindingMismatchError("Workflow activation contract is unavailable") from None
