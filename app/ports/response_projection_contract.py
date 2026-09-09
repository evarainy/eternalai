"""Immutable response projection contract shared across execution ports."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from app.ports.capability_registry import CapabilitySpec


@dataclass(frozen=True, slots=True)
class ProjectionContractSnapshot:
    capability_id: str
    capability_version: str
    output_schema_json: str
    declared_output_schema_digest: str

    @classmethod
    def from_capability(cls, capability: CapabilitySpec) -> ProjectionContractSnapshot:
        return cls(
            capability_id=capability.capability_id,
            capability_version=capability.version,
            output_schema_json=canonical_schema_json(capability.output_schema),
            declared_output_schema_digest=capability.output_schema_digest,
        )

    def load_output_schema(self) -> dict[str, Any]:
        loaded = json.loads(self.output_schema_json)
        return loaded if isinstance(loaded, dict) else {}

    def matches(self, capability: CapabilitySpec) -> bool:
        return (
            self.capability_id == capability.capability_id
            and self.capability_version == capability.version
            and self.output_schema_json == canonical_schema_json(capability.output_schema)
            and self.declared_output_schema_digest == capability.output_schema_digest
        )


def canonical_schema_json(schema: Mapping[str, Any]) -> str:
    return json.dumps(
        schema,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_schema_digest(schema: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_schema_json(schema).encode("utf-8")).hexdigest()


__all__ = (
    "ProjectionContractSnapshot",
    "canonical_schema_digest",
    "canonical_schema_json",
)
