"""Canonical OA Registry rows used by smoke checks and controlled provisioning."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.infra.adapters.oa.adapter import (
    ListPendingWorkflowsArguments,
    ListSystemMessagesArguments,
)
from app.infra.adapters.oa.contracts import (
    OAPendingWorkflowCollection,
    OASystemMessageCollection,
)
from app.knowledge import BasicKnowledge
from app.ports.capability_registry import CapabilitySpec

REQUIRED_ACTIVE_OA_CAPABILITY_IDS = (
    "oa.list_pending_workflows",
    "oa.list_system_messages",
)
OA_CAPABILITY_CONTEXT_PROBES = (
    "查询我的待办",
    "查询我的系统消息",
)


@dataclass(frozen=True, slots=True)
class OARegistryClassification:
    state: str
    found_count: int
    valid_count: int
    unexpected_active_count: int
    active_total_count: int
    visible_probe_count: int
    missing_capability_ids: tuple[str, ...]
    inactive_capability_ids: tuple[str, ...]
    contract_mismatch_capability_ids: tuple[str, ...]
    unexpected_active_capability_ids: tuple[str, ...]


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


def classify_oa_registry(
    catalog: tuple[CapabilitySpec, ...],
    *,
    required_capability_ids: tuple[str, ...] = REQUIRED_ACTIVE_OA_CAPABILITY_IDS,
    context_probes: tuple[str, ...] = OA_CAPABILITY_CONTEXT_PROBES,
    knowledge: BasicKnowledge | None = None,
) -> OARegistryClassification:
    """Classify one OA Registry snapshot for deployment and smoke preflight."""

    if len(context_probes) != len(required_capability_ids):
        raise RuntimeError(
            "OA capability probes and required IDs must be one-to-one"
        )
    probe_capability_pairs = tuple(
        zip(context_probes, required_capability_ids, strict=True)
    )
    if not probe_capability_pairs:
        raise RuntimeError(
            "OA capability probe and required ID pairs must not be empty"
        )
    if any(
        not probe.strip() or not capability_id.strip()
        for probe, capability_id in probe_capability_pairs
    ):
        raise RuntimeError(
            "OA capability probes and required IDs must be non-empty"
        )
    if (
        len({probe for probe, _ in probe_capability_pairs})
        != len(probe_capability_pairs)
        or len({capability_id for _, capability_id in probe_capability_pairs})
        != len(probe_capability_pairs)
    ):
        raise RuntimeError(
            "OA capability probes and required IDs must be unique"
        )

    expected = {
        item.capability_id: item for item in expected_oa_capabilities()
    }
    by_id = {item.capability_id: item for item in catalog}
    found = tuple(
        by_id[capability_id]
        for capability_id in required_capability_ids
        if capability_id in by_id
    )
    missing = tuple(
        capability_id
        for capability_id in required_capability_ids
        if capability_id not in by_id
    )
    inactive = tuple(
        capability_id
        for capability_id in required_capability_ids
        if (
            (item := by_id.get(capability_id)) is not None
            and item.status != "active"
        )
    )
    contract_mismatch = tuple(
        capability_id
        for capability_id in required_capability_ids
        if (
            (item := by_id.get(capability_id)) is not None
            and item != expected.get(capability_id)
        )
    )
    valid = tuple(
        item
        for capability_id in required_capability_ids
        if (
            (item := by_id.get(capability_id)) is not None
            and item == expected.get(capability_id)
        )
    )
    active = tuple(item for item in catalog if item.status == "active")
    unexpected_active = tuple(
        item
        for item in active
        if item.target_system == "oa"
        and item.capability_id not in required_capability_ids
    )
    resolved_knowledge = BasicKnowledge() if knowledge is None else knowledge
    visible_contract_ids = {
        capability_id
        for contract in resolved_knowledge.capability_input_contracts(active)
        if isinstance((capability_id := contract.get("capability_id")), str)
    }
    visible_probe_count = sum(
        capability_id in visible_contract_ids
        for _, capability_id in probe_capability_pairs
    )

    if missing:
        state = "missing"
    elif inactive:
        state = "inactive"
    elif contract_mismatch:
        state = "contract_mismatch"
    elif unexpected_active:
        state = "unexpected_active"
    elif visible_probe_count != len(context_probes):
        state = "context_truncated"
    else:
        state = "passed"

    return OARegistryClassification(
        state=state,
        found_count=len(found),
        valid_count=len(valid),
        unexpected_active_count=len(unexpected_active),
        active_total_count=len(active),
        visible_probe_count=visible_probe_count,
        missing_capability_ids=missing,
        inactive_capability_ids=inactive,
        contract_mismatch_capability_ids=contract_mismatch,
        unexpected_active_capability_ids=tuple(
            item.capability_id for item in unexpected_active
        ),
    )


__all__ = (
    "OA_CAPABILITY_CONTEXT_PROBES",
    "OARegistryClassification",
    "REQUIRED_ACTIVE_OA_CAPABILITY_IDS",
    "classify_oa_registry",
    "expected_oa_capabilities",
    "schema_digest",
)
