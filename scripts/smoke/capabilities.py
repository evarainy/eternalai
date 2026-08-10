"""Canonical OA Registry rows used by smoke checks and controlled provisioning."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from app.infra.adapters.oa.adapter import (
    ListPendingWorkflowsArguments,
    ListSystemMessagesArguments,
)
from app.infra.adapters.oa.contracts import (
    OAPendingWorkflowCollection,
    OASystemMessageCollection,
)
from app.ports.capability_registry import CapabilitySpec

REQUIRED_ACTIVE_OA_CAPABILITY_IDS = (
    "oa.list_pending_workflows",
    "oa.list_system_messages",
)
OA_CAPABILITY_CONTEXT_PROBES = (
    "查询我的待办",
    "查询我的系统消息",
)


def schema_digest(schema: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        schema,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


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
            input_schema_digest=schema_digest(pending_input),
            output_schema_digest=schema_digest(pending_output),
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
            input_schema_digest=schema_digest(system_input),
            output_schema_digest=schema_digest(system_output),
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


__all__ = (
    "OA_CAPABILITY_CONTEXT_PROBES",
    "REQUIRED_ACTIVE_OA_CAPABILITY_IDS",
    "expected_oa_capabilities",
    "schema_digest",
)
