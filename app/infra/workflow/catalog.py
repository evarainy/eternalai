"""Canonical descriptor for the single read-only production Workflow."""

from pydantic import BaseModel, ConfigDict

from app.infra.adapters.oa.contracts import (
    OAPendingWorkflowCollection,
    OASystemMessageCollection,
)
from app.ports.capability_registry import CapabilitySpec
from app.ports.response_projection_contract import canonical_schema_digest

OVERVIEW_ID = "oa.read_overview"


class OAReadOverviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class OAReadOverviewOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    pending: OAPendingWorkflowCollection
    messages: OASystemMessageCollection


def production_workflow_capabilities() -> tuple[CapabilitySpec, ...]:
    input_schema = OAReadOverviewInput.model_json_schema()
    output_schema = OAReadOverviewOutput.model_json_schema()
    return (
        CapabilitySpec(
            capability_id=OVERVIEW_ID,
            name="OA 待办与系统消息概览",
            type="workflow",
            intent_tags=[OVERVIEW_ID],
            input_schema=input_schema,
            output_schema=output_schema,
            input_schema_digest=canonical_schema_digest(input_schema),
            output_schema_digest=canonical_schema_digest(output_schema),
            risk_level="low",
            owner="eternalai-platform",
            version="1.0.0",
            status="active",
            short_description=(
                "一次查看当前 OA 用户的待办事宜和系统消息，只读，不提交审批或办理事项。"
            ),
            target_system="oa",
            execution_identity="user_delegated",
            binding_required=True,
            automation_level="full",
            policy_digest=None,
            displayable_argument_fields=[],
            handles_work_objects=[],
        ),
    )


def is_canonical_overview(capability: CapabilitySpec) -> bool:
    """Only active or explicitly disabled canonical rows are recognized."""
    canonical = production_workflow_capabilities()[0]
    return capability.status in {"active", "disabled"} and capability == canonical.model_copy(
        update={"status": capability.status},
        deep=True,
    )
