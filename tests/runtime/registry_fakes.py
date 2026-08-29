"""Deterministic Capability Registry fakes shared by Runtime tests."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from app.ports.capability_registry import CapabilitySpec

VALID_RUNTIME_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"result": {"type": "string"}},
}


def schema_digest(schema: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        schema,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def active_capability(
    capability_id: str,
    *,
    output_schema: dict[str, Any] | None = None,
) -> CapabilitySpec:
    output_schema = output_schema or VALID_RUNTIME_OUTPUT_SCHEMA
    return CapabilitySpec(
        capability_id=capability_id,
        name=capability_id,
        type="query",
        input_schema_digest=f"input-{capability_id}",
        output_schema=output_schema,
        output_schema_digest=schema_digest(output_schema),
        risk_level="low",
        owner="runtime-test",
        version="1.0.0",
        status="active",
        short_description=capability_id,
        target_system=None,
        execution_identity="user_delegated",
        binding_required=False,
    )


class StaticCapabilityRegistry:
    def __init__(self, *capabilities: str | CapabilitySpec) -> None:
        specs = [
            capability
            if isinstance(capability, CapabilitySpec)
            else active_capability(capability)
            for capability in capabilities
        ]
        self._capabilities = {
            capability.capability_id: capability for capability in specs
        }

    async def get(self, capability_id: str) -> CapabilitySpec | None:
        return self._capabilities.get(capability_id)

    async def list(
        self,
        target_system: str | None = None,
        type: str | None = None,
        status: str | None = None,
    ) -> list[CapabilitySpec]:
        capabilities = list(self._capabilities.values())
        if target_system is not None:
            capabilities = [
                capability
                for capability in capabilities
                if capability.target_system == target_system
            ]
        if type is not None:
            capabilities = [
                capability for capability in capabilities if capability.type == type
            ]
        if status is not None:
            capabilities = [
                capability
                for capability in capabilities
                if capability.status == status
            ]
        return capabilities
