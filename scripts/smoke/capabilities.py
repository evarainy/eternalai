"""Canonical OA Registry rows used by smoke checks and controlled provisioning."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.infra.adapters.oa.capabilities import expected_oa_capabilities
from app.infra.workflow.catalog import (
    OVERVIEW_ID,
    is_canonical_overview,
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
    overview = by_id.get(OVERVIEW_ID)
    if overview is not None and not is_canonical_overview(overview):
        contract_mismatch += (OVERVIEW_ID,)
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
        and not is_canonical_overview(item)
    )
    resolved_knowledge = BasicKnowledge() if knowledge is None else knowledge
    visible_probe_count = 0
    overview_visible = True
    if overview is not None and overview.status == "active":
        overview_selection = resolved_knowledge.select_capability_candidates(
            "查看 OA 待办与系统消息概览", active,
        )
        overview_visible = overview_selection.outcome == "ready" and any(
            binding.capability_id == OVERVIEW_ID for binding in overview_selection.bindings
        )
    for probe, capability_id in probe_capability_pairs:
        selection = resolved_knowledge.select_capability_candidates(probe, active)
        if selection.outcome == "ready" and any(
            binding.capability_id == capability_id for binding in selection.bindings
        ):
            visible_probe_count += 1

    if missing:
        state = "missing"
    elif inactive:
        state = "inactive"
    elif contract_mismatch:
        state = "contract_mismatch"
    elif unexpected_active:
        state = "unexpected_active"
    elif visible_probe_count != len(context_probes) or not overview_visible:
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
