"""Immutable response projection contract shared across execution ports."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal, Mapping

from app.ports.capability_registry import CapabilitySpec

# Internal path segment for "every array item"; never a schema property name.
ARRAY_ITEM = "[]"
NAMED_SCALAR_TYPES = ("boolean", "integer", "null", "number", "string")

ApprovedProjectionPath = tuple[str, ...]
ApprovedProjectionKind = Literal["opaque_object", "canonical_json", "named_scalar"]
ApprovedProjectionFailure = Literal[
    "policy_mismatch",
    "unsupported_schema",
    "unsupported_value",
    "credential_key",
    "invalid_json",
    "nonfinite_number",
    "depth_or_cycle",
]


def named_scalar_schema() -> dict[str, Any]:
    """The only schema node accepted at an approved named-scalar path."""
    return {"type": list(NAMED_SCALAR_TYPES)}


@dataclass(frozen=True, slots=True)
class ApprovedResponseProjection:
    """Code-owned exact-path exceptions bound to one canonical public schema.

    Never derived from provider results, model output or registry rows. Every
    path is a schema position; ARRAY_ITEM stands for any array element.
    """

    policy_version: str
    output_schema_json: str
    opaque_object_paths: frozenset[ApprovedProjectionPath] = frozenset()
    canonical_json_paths: frozenset[ApprovedProjectionPath] = frozenset()
    named_scalar_paths: frozenset[ApprovedProjectionPath] = frozenset()
    # Raw fixed columns whose nullability the supplier left unstated.
    raw_nullable_paths: frozenset[ApprovedProjectionPath] = frozenset()

    def __post_init__(self) -> None:
        if not self.policy_version or not self.output_schema_json:
            raise ValueError("approved_projection_invalid")
        seen: set[ApprovedProjectionPath] = set()
        for group in (
            self.opaque_object_paths,
            self.canonical_json_paths,
            self.named_scalar_paths,
            self.raw_nullable_paths,
        ):
            if not isinstance(group, frozenset):
                raise ValueError("approved_projection_invalid")
            for path in group:
                if (
                    not isinstance(path, tuple)
                    or not path
                    or path[0] == ARRAY_ITEM
                    or any(not isinstance(part, str) or not part for part in path)
                    or path in seen
                ):
                    raise ValueError("approved_projection_invalid")
                seen.add(path)

    def kind_at(self, path: ApprovedProjectionPath) -> ApprovedProjectionKind | None:
        if path in self.opaque_object_paths:
            return "opaque_object"
        if path in self.canonical_json_paths:
            return "canonical_json"
        if path in self.named_scalar_paths:
            return "named_scalar"
        return None

    def declared_paths(self) -> frozenset[ApprovedProjectionPath]:
        return (
            self.opaque_object_paths
            | self.canonical_json_paths
            | self.named_scalar_paths
            | self.raw_nullable_paths
        )

    def matches_schema(self, schema: Mapping[str, Any]) -> bool:
        return canonical_schema_json(schema) == self.output_schema_json

    def digest_material(self) -> dict[str, Any]:
        """Stable, value-free summary for policy digests."""
        return {
            "policy_version": self.policy_version,
            "output_schema_digest": hashlib.sha256(
                self.output_schema_json.encode("utf-8")
            ).hexdigest(),
            "opaque_object_paths": sorted(list(path) for path in self.opaque_object_paths),
            "canonical_json_paths": sorted(list(path) for path in self.canonical_json_paths),
            "named_scalar_paths": sorted(list(path) for path in self.named_scalar_paths),
            "named_scalar_types": list(NAMED_SCALAR_TYPES),
            "raw_nullable_paths": sorted(list(path) for path in self.raw_nullable_paths),
        }


class ApprovedResponseProjectionError(Exception):
    """Local approved-read projection rejection; carries one fixed reason only."""

    def __init__(self, reason: ApprovedProjectionFailure) -> None:
        super().__init__(reason)
        self.reason: ApprovedProjectionFailure = reason


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
    "ARRAY_ITEM",
    "NAMED_SCALAR_TYPES",
    "ApprovedProjectionFailure",
    "ApprovedProjectionKind",
    "ApprovedProjectionPath",
    "ApprovedResponseProjection",
    "ApprovedResponseProjectionError",
    "ProjectionContractSnapshot",
    "canonical_schema_digest",
    "canonical_schema_json",
    "named_scalar_schema",
)
