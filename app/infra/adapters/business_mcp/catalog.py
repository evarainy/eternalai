"""Code-owned capability manifest; inspecting this module never publishes it."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from app.mcp.contracts import (
    INPUT_SCHEMAS,
    WRITE_TOOLS,
    OutputContract,
    input_digest,
    safety_digest,
)
from app.mcp.models import ServiceConfig, ToolBinding, digest
from app.ports.capability_registry import CapabilitySpec
from app.workflow.models import (
    GovernedWorkflowDefinition,
    GovernedWorkflowPolicy,
    WorkflowInputRef,
    WorkflowStep,
)

VERSION = "1.0.0"
POLICY_VERSION = "mcp-governed-v1"
OPERATION_OUTPUT: dict[str, Any] = {
    "type": "object",
    "properties": {"operation_id": {"type": "string"}, "state": {"type": "string"}},
    "required": ["operation_id", "state"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class GovernedManifest:
    service_config_id: str
    outer_id: str
    leaf_id: str
    tool: str
    version: str = VERSION
    retry_limit: int = 0
    risk: str = "high"


def manifest(profile: ServiceConfig) -> tuple[GovernedManifest, ...]:
    return tuple(
        GovernedManifest(
            profile.service_config_id,
            f"business.{profile.service_config_id}.{tool}",
            f"business.{profile.service_config_id}.internal.{tool}",
            tool,
        )
        for tool in sorted(WRITE_TOOLS)
    )


def workflow_definitions(profile: ServiceConfig) -> dict[str, GovernedWorkflowDefinition]:
    return {
        item.outer_id: GovernedWorkflowDefinition(
            workflow_id=item.outer_id,
            version=item.version,
            steps=(
                WorkflowStep(
                    step_id="governed_write",
                    capability_id=item.leaf_id,
                    input_mapping={
                        key: WorkflowInputRef(source="workflow_input", key=key)
                        for key in INPUT_SCHEMAS[item.tool]["properties"]
                    },
                ),
            ),
            policy=GovernedWorkflowPolicy(
                service_config_id=item.service_config_id,
                outer_capability_id=item.outer_id,
                leaf_capability_id=item.leaf_id,
                remote_tool=item.tool,
                version=item.version,
                recovery_capability_id=(
                    f"business.{item.service_config_id}."
                    + {
                        "clothing_plan_submit": "clothing_result_get",
                        "talk_record_submit": "talk_record_get",
                        "talk_task_claim": "talk_tasks_list",
                    }[item.tool]
                )
                if item.tool in {"clothing_plan_submit", "talk_record_submit", "talk_task_claim"}
                else None,
            ),
        )
        for item in manifest(profile)
    }


def catalog(
    profile: ServiceConfig,
    contracts: dict[str, OutputContract],
) -> tuple[list[CapabilitySpec], list[ToolBinding]]:
    capabilities, bindings = [], []
    for tool, schema in INPUT_SCHEMAS.items():
        policy = contracts.get(tool)
        business_output = dict(policy.schema) if policy else {"not": {}}
        outer_id = f"business.{profile.service_config_id}.{tool}"
        for internal in [False, True] if tool in WRITE_TOOLS else [False]:
            capability_id = (
                f"business.{profile.service_config_id}.internal.{tool}" if internal else outer_id
            )
            outer = tool in WRITE_TOOLS and not internal
            output = OPERATION_OUTPUT if outer else business_output
            capabilities.append(
                CapabilitySpec(
                    capability_id=capability_id,
                    name=tool,
                    type="workflow" if outer else "action" if internal else "query",
                    intent_tags=[] if internal else [tool],
                    input_schema=deepcopy(schema),
                    output_schema=deepcopy(output),
                    input_schema_digest=input_digest(tool),
                    output_schema_digest=digest(output),
                    risk_level="high" if tool in WRITE_TOOLS else "low",
                    owner="business-platform",
                    version=VERSION,
                    status="active" if policy else "disabled",
                    short_description="Internal governed step" if internal else tool,
                    target_system="business_platform",
                    execution_identity="user_delegated",
                    binding_required=True,
                    policy_digest=digest([POLICY_VERSION, profile.service_config_id, tool]),
                    automation_level="assisted" if tool in WRITE_TOOLS else "full",
                    displayable_argument_fields=[
                        field
                        for field in schema["properties"]
                        if outer
                        and field
                        in {
                            "artifactId",
                            "taskId",
                            "personId",
                            "jyId",
                            "jqId",
                            "templateId",
                            "warehouseId",
                            "year",
                            "batchMonth",
                            "distDate",
                            "durationMinutes",
                            "occurredAt",
                            "locationCode",
                            "talkTypeCode",
                            "effectCode",
                        }
                    ],
                )
            )
            bindings.append(
                ToolBinding(
                    capability_id=capability_id,
                    capability_version=VERSION,
                    service_config_id=profile.service_config_id,
                    remote_tool=tool,
                    input_digest=input_digest(tool),
                    safety_digest=safety_digest(tool),
                    output_contract_version=policy.version if policy else "unconfirmed",
                    policy_version=POLICY_VERSION,
                    internal_only=internal,
                )
            )
    return capabilities, bindings
