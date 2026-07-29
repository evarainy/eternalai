"""Internal OA read models and structural Contract Pack fingerprinting."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

STRUCTURAL_FINGERPRINT_ALGORITHM = "eternalai-structural-v1"
_PROFILE_VERSION_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_STRUCTURAL_SCHEMA_EXEMPLAR = {
    "workflows": [
        {
            "workflow_id": "",
            "title": "",
            "status": "pending",
            "applicant": "",
            "current_step": "",
            "approver": "",
            "created_at": "2000-01-01T00:00:00+00:00",
            "expired": False,
        },
        {
            "workflow_id": "",
            "title": "",
            "status": "pending",
            "applicant": "",
            "current_step": "",
            "approver": None,
            "created_at": None,
            "expired": False,
        },
    ]
}


class OAPendingWorkflow(BaseModel):
    """Normalized, credential-free workflow data returned by the OA provider."""

    model_config = ConfigDict(extra="forbid", strict=True)

    workflow_id: str
    title: str
    status: Literal["pending"]
    applicant: str
    current_step: str
    approver: str | None
    created_at: str | None
    expired: bool

    @field_validator(
        "workflow_id",
        "title",
        "status",
        "applicant",
        "current_step",
    )
    @classmethod
    def _require_non_empty_string(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("workflow text fields must not be empty")
        return value

    @field_validator("approver")
    @classmethod
    def _validate_optional_approver(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("approver must be null or a non-empty string")
        return value

    @field_validator("created_at")
    @classmethod
    def _validate_optional_timestamp(cls, value: str | None) -> str | None:
        if value is not None:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                raise ValueError("created_at must include a timezone")
        return value


class OAPendingWorkflowCollection(BaseModel):
    """Normalized result for ``oa.list_pending_workflows``."""

    model_config = ConfigDict(extra="forbid", strict=True)

    workflows: list[OAPendingWorkflow]


class OAContractPackProfile(BaseModel):
    """Metadata required to bind a Replay provider to one immutable pack."""

    model_config = ConfigDict(extra="forbid", strict=True)

    profile_version: str
    capability_id: Literal["oa.list_pending_workflows"]
    source_kind: Literal["synthetic", "sanitized_capture"]
    sanitizer_version: Literal["1"]
    sample_file: Literal["sample.json"]
    fingerprint_file: Literal["fingerprint.json"]

    @field_validator("profile_version")
    @classmethod
    def _validate_profile_version(cls, value: str) -> str:
        if _PROFILE_VERSION_PATTERN.fullmatch(value) is None:
            raise ValueError("profile_version must use safe lowercase path characters")
        return value


def build_structural_fingerprint(payload: Any) -> dict[str, Any]:
    """Fingerprint JSON structure without including values or array lengths."""

    observations: dict[str, _StructuralObservation] = {}
    _observe_structure(_STRUCTURAL_SCHEMA_EXEMPLAR, "$", observations)
    _observe_structure(payload, "$", observations)
    nodes = [
        {
            "path": path,
            "json_type": "|".join(sorted(observation.json_types)),
            "nullable": observation.nullable,
            "array_shape": (
                "|".join(sorted(observation.array_shapes))
                if observation.array_shapes
                else None
            ),
        }
        for path, observation in sorted(observations.items())
    ]
    canonical = json.dumps(
        nodes,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "algorithm": STRUCTURAL_FINGERPRINT_ALGORITHM,
        "nodes": nodes,
        "sha256": hashlib.sha256(canonical).hexdigest(),
    }


class _StructuralObservation:
    def __init__(self) -> None:
        self.json_types: set[str] = set()
        self.nullable = False
        self.array_shapes: set[str] = set()


def _observe_structure(
    value: Any,
    path: str,
    observations: dict[str, _StructuralObservation],
) -> None:
    observation = observations.setdefault(path, _StructuralObservation())
    if value is None:
        observation.nullable = True
        return

    json_type = _json_type(value)
    observation.json_types.add(json_type)
    if isinstance(value, Mapping):
        for key in sorted(value):
            _observe_structure(value[key], f"{path}.{key}", observations)
        return
    if isinstance(value, list):
        if value:
            observation.array_shapes.add(_array_shape(value))
        for item in value:
            _observe_structure(item, f"{path}[]", observations)


def _array_shape(values: list[Any]) -> str:
    return "items:" + "|".join(sorted({_value_shape(value) for value in values}))


def _value_shape(value: Any) -> str:
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, list):
        return f"array<{_array_shape(value)}>" if value else "array<items:unknown>"
    return _json_type(value)


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, list):
        return "array"
    raise TypeError("Contract Pack payload must contain JSON-compatible values")


__all__ = (
    "OAContractPackProfile",
    "OAPendingWorkflow",
    "OAPendingWorkflowCollection",
    "STRUCTURAL_FINGERPRINT_ALGORITHM",
    "build_structural_fingerprint",
)
