"""Canonical OA read descriptors shared by production and provisioning."""

from app.infra.adapters.oa.adapter import (
    ListPendingWorkflowsArguments,
    ListSystemMessagesArguments,
)
from app.infra.adapters.oa.contracts import (
    OAPendingWorkflowCollection,
    OASystemMessageCollection,
)
from app.ports.capability_registry import CapabilitySpec
from app.ports.response_projection_contract import canonical_schema_digest


def expected_oa_capabilities() -> tuple[CapabilitySpec, CapabilitySpec]:
    pending_input = ListPendingWorkflowsArguments.model_json_schema()
    pending_output = OAPendingWorkflowCollection.model_json_schema()
    system_input = ListSystemMessagesArguments.model_json_schema()
    system_output = OASystemMessageCollection.model_json_schema()
    return (
        CapabilitySpec(
            capability_id="oa.list_pending_workflows",
            name="OA 待办事宜查询",
            type="query",
            intent_tags=["oa.pending_workflows", "oa.pending_approvals"],
            input_schema=pending_input,
            output_schema=pending_output,
            input_schema_digest=canonical_schema_digest(pending_input),
            output_schema_digest=canonical_schema_digest(pending_output),
            risk_level="low",
            owner="eternalai-platform",
            version="2.0.0",
            status="active",
            short_description="查询当前 OA 用户的待办事宜列表。",
            target_system="oa",
            execution_identity="user_delegated",
            binding_required=True,
            policy_digest=None,
        ),
        CapabilitySpec(
            capability_id="oa.list_system_messages",
            name="OA 系统消息查询",
            type="query",
            intent_tags=["oa.system_messages"],
            input_schema=system_input,
            output_schema=system_output,
            input_schema_digest=canonical_schema_digest(system_input),
            output_schema_digest=canonical_schema_digest(system_output),
            risk_level="low",
            owner="eternalai-platform",
            version="1.0.0",
            status="active",
            short_description="查询当前 OA 用户的系统消息列表。",
            target_system="oa",
            execution_identity="user_delegated",
            binding_required=True,
            policy_digest=None,
        ),
    )
