"""Internal OA read models and structural Contract Pack fingerprinting."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, Final, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationError,
    field_validator,
    model_validator,
)

STRUCTURAL_FINGERPRINT_ALGORITHM: Final[
    Literal["eternalai-structural-v1"]
] = "eternalai-structural-v1"
EXTERNAL_SANITIZATION_WARNING: Final = (
    "Source was sanitized externally; leakage assertions are not evidence "
    "of EternalAI sanitizer verification."
)
_PROFILE_VERSION_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_STRUCTURAL_PATH_PATTERN = re.compile(
    r"^\$(?:(?:\.[A-Za-z_][A-Za-z0-9_]*)|\[\])*$"
)
_STRUCTURAL_JSON_TYPES = frozenset(
    {"array", "boolean", "integer", "null", "number", "object", "string"}
)
_STRUCTURAL_ARRAY_SHAPE_PATTERN = re.compile(r"^[a-z:<>|]+$")
_STRUCTURAL_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_LIVE_PENDING_WORKFLOW_FIELD_NAMES: Final[Mapping[str, str]] = {
    "workflowId": "workflow_id",
    "title": "title",
    "status": "status",
    "applicant": "applicant",
    "currentStep": "current_step",
    "approver": "approver",
    "createdAt": "created_at",
    "expired": "expired",
}
_LIVE_SYSTEM_MESSAGE_FIELD_NAMES: Final[Mapping[str, str]] = {
    "messageid": "message_id",
    "title": "title",
    "context": "content",
    "name": "source_name",
    "time": "occurred_at",
    "bizstate": "business_state",
    "link": "link",
    "linkmobileurl": "mobile_link",
}
_LIVE_SYSTEM_MESSAGE_IGNORED_FIELDS: Final = frozenset(
    {"gomethod", "gomethodpc", "showimage"}
)
_PENDING_WORKFLOW_STRUCTURAL_SCHEMA_EXEMPLAR = {
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
_SYSTEM_MESSAGE_STRUCTURAL_SCHEMA_EXEMPLAR = {
    "messages": [
        {
            "message_id": "",
            "title": "",
            "content": "",
            "source_name": "",
            "occurred_at": "",
            "business_state": "",
            "link": "",
            "mobile_link": "",
        },
        {
            "message_id": "",
            "title": "",
            "content": "",
            "source_name": "",
            "occurred_at": "",
            "business_state": "",
            "link": None,
            "mobile_link": None,
        },
    ],
    "returned_count": 0,
    "is_complete": False,
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


class OASystemMessage(BaseModel):
    """Normalized, credential-free system-message data returned by OA."""

    model_config = ConfigDict(extra="forbid", strict=True)

    message_id: str
    title: str
    content: str
    source_name: str
    occurred_at: str
    business_state: str
    link: str | None
    mobile_link: str | None

    @field_validator(
        "message_id",
        "title",
        "content",
        "source_name",
        "occurred_at",
        "business_state",
    )
    @classmethod
    def _require_non_empty_string(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("system-message text fields must not be empty")
        return value

    @field_validator("link", "mobile_link")
    @classmethod
    def _validate_optional_link(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or not value.startswith("/")):
            raise ValueError("system-message links must be null or relative paths")
        return value


class OASystemMessageCollection(BaseModel):
    """Bounded result for ``oa.list_system_messages`` with explicit completeness."""

    model_config = ConfigDict(extra="forbid", strict=True)

    messages: list[OASystemMessage]
    returned_count: int
    is_complete: bool

    @model_validator(mode="after")
    def _validate_returned_count(self) -> OASystemMessageCollection:
        if self.returned_count != len(self.messages):
            raise ValueError("returned_count must match the message collection")
        return self


class OAContractPackProfile(BaseModel):
    """Metadata required to bind a Replay provider to one immutable pack."""

    model_config = ConfigDict(extra="forbid", strict=True)

    profile_version: str
    capability_id: Literal[
        "oa.list_pending_workflows",
        "oa.list_system_messages",
    ]
    source_kind: Literal[
        "synthetic",
        "sanitized_capture",
        "externally_sanitized_capture",
    ]
    source_warning: Literal[
        "Source was sanitized externally; leakage assertions are not evidence "
        "of EternalAI sanitizer verification."
    ] | None = None
    sanitizer_version: Literal["1", "2"]
    sample_file: Literal["sample.json"]
    fingerprint_file: Literal["fingerprint.json"]

    @field_validator("profile_version")
    @classmethod
    def _validate_profile_version(cls, value: str) -> str:
        if _PROFILE_VERSION_PATTERN.fullmatch(value) is None:
            raise ValueError("profile_version must use safe lowercase path characters")
        return value

    @model_validator(mode="after")
    def _validate_source_warning(self) -> OAContractPackProfile:
        is_external = self.source_kind == "externally_sanitized_capture"
        if is_external != (self.source_warning == EXTERNAL_SANITIZATION_WARNING):
            raise ValueError(
                "externally sanitized sources require the fixed assurance warning"
            )
        return self


class OAStructuralNode(BaseModel):
    """One value-free node in an OA normalized structural fingerprint."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    path: str
    json_type: str
    nullable: bool
    array_shape: str | None

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        if _STRUCTURAL_PATH_PATTERN.fullmatch(value) is None:
            raise ValueError("structural path is invalid")
        return value

    @field_validator("json_type")
    @classmethod
    def _validate_json_type(cls, value: str) -> str:
        members = value.split("|")
        if (
            not members
            or members != sorted(set(members))
            or any(member not in _STRUCTURAL_JSON_TYPES for member in members)
        ):
            raise ValueError("structural JSON type is invalid")
        return value

    @field_validator("array_shape")
    @classmethod
    def _validate_array_shape(cls, value: str | None) -> str | None:
        if (
            value is not None
            and _STRUCTURAL_ARRAY_SHAPE_PATTERN.fullmatch(value) is None
        ):
            raise ValueError("structural array shape is invalid")
        return value


class OAStructuralFingerprint(BaseModel):
    """Validated ``eternalai-structural-v1`` fingerprint payload."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    algorithm: Literal["eternalai-structural-v1"]
    nodes: list[OAStructuralNode]
    sha256: str

    @field_validator("sha256")
    @classmethod
    def _validate_sha256(cls, value: str) -> str:
        if _STRUCTURAL_SHA256_PATTERN.fullmatch(value) is None:
            raise ValueError("structural SHA-256 is invalid")
        return value

    @field_validator("nodes")
    @classmethod
    def _validate_unique_sorted_paths(
        cls,
        value: list[OAStructuralNode],
    ) -> list[OAStructuralNode]:
        paths = [node.path for node in value]
        if paths != sorted(set(paths)):
            raise ValueError("structural nodes must have unique sorted paths")
        return value


class OAStructuralDriftReport(BaseModel):
    """Safe Live drift result containing structural metadata only."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    matches: bool
    algorithm: Literal["eternalai-structural-v1"]
    expected_sha256: str
    actual_sha256: str
    added: tuple[OAStructuralNode, ...]
    removed: tuple[OAStructuralNode, ...]
    changed: tuple[OAStructuralNode, ...]

    @field_validator("expected_sha256", "actual_sha256")
    @classmethod
    def _validate_report_sha256(cls, value: str) -> str:
        if _STRUCTURAL_SHA256_PATTERN.fullmatch(value) is None:
            raise ValueError("drift report SHA-256 is invalid")
        return value


def build_structural_fingerprint(payload: Any) -> dict[str, Any]:
    """Fingerprint JSON structure without including values or array lengths."""

    return _build_structural_fingerprint(
        payload,
        include_contract_exemplar=True,
    )


def build_live_pending_workflows_fingerprint(
    records: list[Any],
) -> dict[str, Any]:
    """Fingerprint one Live aggregate from value-free normalized wire structure."""

    common_wire_fields = set(_LIVE_PENDING_WORKFLOW_FIELD_NAMES)
    for record in records:
        if not isinstance(record, Mapping):
            common_wire_fields.clear()
            break
        common_wire_fields.intersection_update(
            key for key in record if isinstance(key, str)
        )
    projected_records = [
        _project_live_workflow_record(
            record,
            common_wire_fields=common_wire_fields,
        )
        for record in records
    ]
    return _build_structural_fingerprint(
        {"workflows": projected_records},
        include_contract_exemplar=False,
    )


def build_live_system_messages_fingerprint(
    records: list[Any],
) -> dict[str, Any]:
    """Fingerprint one bounded Live system-message page without wire values."""

    common_wire_fields = set(_LIVE_SYSTEM_MESSAGE_FIELD_NAMES)
    for record in records:
        if not isinstance(record, Mapping):
            common_wire_fields.clear()
            break
        common_wire_fields.intersection_update(
            key for key in record if isinstance(key, str)
        )
    projected_records = [
        _project_live_system_message_record(
            record,
            common_wire_fields=common_wire_fields,
        )
        for record in records
    ]
    return _build_structural_fingerprint(
        {
            "messages": projected_records,
            "returned_count": 0,
            "is_complete": False,
        },
        include_contract_exemplar=True,
    )


def _build_structural_fingerprint(
    payload: Any,
    *,
    include_contract_exemplar: bool,
) -> dict[str, Any]:
    observations: dict[str, _StructuralObservation] = {}
    if include_contract_exemplar:
        _observe_structure(_contract_exemplar(payload), "$", observations)
    _observe_structure(payload, "$", observations)
    nodes = [
        {
            "path": path,
            "json_type": (
                "|".join(sorted(observation.json_types))
                if observation.json_types
                else "null"
            ),
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


def _contract_exemplar(payload: Any) -> Mapping[str, Any]:
    if isinstance(payload, Mapping) and "messages" in payload:
        return _SYSTEM_MESSAGE_STRUCTURAL_SCHEMA_EXEMPLAR
    return _PENDING_WORKFLOW_STRUCTURAL_SCHEMA_EXEMPLAR


def _project_live_workflow_record(
    record: Any,
    *,
    common_wire_fields: set[str],
) -> Any:
    if not isinstance(record, Mapping):
        return _project_json_structure(record)
    common_field_names = {
        key: normalized_name
        for key, normalized_name in _LIVE_PENDING_WORKFLOW_FIELD_NAMES.items()
        if key in common_wire_fields
    }
    return _project_json_mapping(
        record,
        field_names=common_field_names,
        ignored_fields=(
            frozenset(_LIVE_PENDING_WORKFLOW_FIELD_NAMES)
            - common_wire_fields
        ),
    )


def _project_live_system_message_record(
    record: Any,
    *,
    common_wire_fields: set[str],
) -> Any:
    if not isinstance(record, Mapping):
        return _project_json_structure(record)
    common_field_names = {
        key: normalized_name
        for key, normalized_name in _LIVE_SYSTEM_MESSAGE_FIELD_NAMES.items()
        if key in common_wire_fields
    }
    return _project_json_mapping(
        record,
        field_names=common_field_names,
        ignored_fields=(
            (frozenset(_LIVE_SYSTEM_MESSAGE_FIELD_NAMES) - common_wire_fields)
            | _LIVE_SYSTEM_MESSAGE_IGNORED_FIELDS
        ),
    )


def _project_json_structure(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return False
    if isinstance(value, str):
        return ""
    if isinstance(value, int):
        return 0
    if isinstance(value, float):
        return 0.0
    if isinstance(value, Mapping):
        return _project_json_mapping(
            value,
            field_names={},
            ignored_fields=frozenset(),
        )
    if isinstance(value, list):
        return [_project_json_structure(item) for item in value]
    raise TypeError("Live OA payload must contain JSON-compatible values")


def _project_json_mapping(
    value: Mapping[Any, Any],
    *,
    field_names: Mapping[str, str],
    ignored_fields: frozenset[str] | set[str],
) -> dict[str, Any]:
    keys = list(value)
    if any(not isinstance(key, str) for key in keys):
        raise TypeError("Live OA payload object keys must be strings")
    string_keys = [
        key
        for key in keys
        if isinstance(key, str) and key not in ignored_fields
    ]
    unknown_keys = sorted(
        key for key in string_keys if key not in field_names
    )
    safe_unknown_names = {
        key: f"unknown_field_{index:03d}"
        for index, key in enumerate(unknown_keys, start=1)
    }
    projected: dict[str, Any] = {}
    for key in string_keys:
        safe_key = (
            field_names[key]
            if key in field_names
            else safe_unknown_names[key]
        )
        projected[safe_key] = _project_json_structure(value[key])
    return projected


def compare_structural_fingerprints(
    expected: Any,
    actual: Any,
) -> OAStructuralDriftReport:
    """Compare normalized fingerprints without exposing values or array lengths."""

    try:
        expected_fingerprint = OAStructuralFingerprint.model_validate(
            expected,
            strict=True,
        )
        actual_fingerprint = OAStructuralFingerprint.model_validate(
            actual,
            strict=True,
        )
    except ValidationError:
        raise ValueError("OA structural fingerprint is invalid") from None

    expected_nodes = {
        node.path: node for node in expected_fingerprint.nodes
    }
    actual_nodes = {
        node.path: node for node in actual_fingerprint.nodes
    }
    added = tuple(
        actual_nodes[path]
        for path in sorted(actual_nodes.keys() - expected_nodes.keys())
    )
    removed = tuple(
        expected_nodes[path]
        for path in sorted(expected_nodes.keys() - actual_nodes.keys())
    )
    changed = tuple(
        actual_nodes[path]
        for path in sorted(expected_nodes.keys() & actual_nodes.keys())
        if actual_nodes[path] != expected_nodes[path]
    )
    matches = (
        expected_fingerprint.algorithm == actual_fingerprint.algorithm
        and expected_fingerprint.sha256 == actual_fingerprint.sha256
        and not added
        and not removed
        and not changed
    )
    return OAStructuralDriftReport(
        matches=matches,
        algorithm=STRUCTURAL_FINGERPRINT_ALGORITHM,
        expected_sha256=expected_fingerprint.sha256,
        actual_sha256=actual_fingerprint.sha256,
        added=added,
        removed=removed,
        changed=changed,
    )


def normalize_pending_workflow_records(
    records: list[Any],
) -> OAPendingWorkflowCollection:
    """Whitelist and normalize one or more Live OA workflow record pages."""

    normalized: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, Mapping):
            raise ValueError("OA workflow record must be an object")
        normalized.append(
            {
                "workflow_id": _required_live_string(record, "workflowId"),
                "title": _required_live_string(record, "title"),
                "status": _required_live_pending_status(record),
                "applicant": _required_live_string(record, "applicant"),
                "current_step": _required_live_string(record, "currentStep"),
                "approver": _optional_live_string(record, "approver"),
                "created_at": _optional_live_string(record, "createdAt"),
                "expired": _required_live_boolean(record, "expired"),
            }
        )
    try:
        return OAPendingWorkflowCollection.model_validate(
            {"workflows": normalized},
            strict=True,
        )
    except ValidationError:
        raise ValueError("normalized OA workflow collection is invalid") from None


def normalize_system_message_records(
    records: list[Any],
    *,
    page_size: int,
    link_normalizer: Callable[[str], str] | None = None,
) -> OASystemMessageCollection:
    """Whitelist and normalize one bounded Live OA system-message page."""

    if page_size <= 0 or len(records) > page_size:
        raise ValueError("OA system-message page exceeds the record limit")
    normalized: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, Mapping):
            raise ValueError("OA system-message record must be an object")
        normalized.append(
            {
                "message_id": _required_live_system_message_string(
                    record,
                    "messageid",
                ),
                "title": _required_live_system_message_string(record, "title"),
                "content": _required_live_system_message_string(
                    record,
                    "context",
                ),
                "source_name": _required_live_system_message_string(
                    record,
                    "name",
                ),
                "occurred_at": _required_live_system_message_string(
                    record,
                    "time",
                ),
                "business_state": _required_live_system_message_string(
                    record,
                    "bizstate",
                ),
                "link": _optional_live_blankable_string(
                    record,
                    "link",
                    normalizer=link_normalizer,
                ),
                "mobile_link": _optional_live_blankable_string(
                    record,
                    "linkmobileurl",
                    normalizer=link_normalizer,
                ),
            }
        )
    try:
        return OASystemMessageCollection.model_validate(
            {
                "messages": normalized,
                "returned_count": len(normalized),
                "is_complete": len(normalized) < page_size,
            },
            strict=True,
        )
    except ValidationError:
        raise ValueError(
            "normalized OA system-message collection is invalid"
        ) from None


def _required_live_system_message_string(
    record: Mapping[str, Any],
    key: str,
) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("OA system-message required string is invalid")
    return value.strip()


def _optional_live_blankable_string(
    record: Mapping[str, Any],
    key: str,
    *,
    normalizer: Callable[[str], str] | None = None,
) -> str | None:
    value = record.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("OA system-message optional string is invalid")
    normalized = value.strip()
    return normalizer(normalized) if normalizer is not None else normalized


def _required_live_string(record: Mapping[str, Any], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("OA workflow required string is invalid")
    return value.strip()


def _optional_live_string(
    record: Mapping[str, Any],
    key: str,
) -> str | None:
    value = record.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("OA workflow optional string is invalid")
    return value.strip()


def _required_live_pending_status(record: Mapping[str, Any]) -> Literal["pending"]:
    if record.get("status") != "pending":
        raise ValueError("OA workflow status is invalid")
    return "pending"


def _required_live_boolean(record: Mapping[str, Any], key: str) -> bool:
    value = record.get(key)
    if not isinstance(value, bool):
        raise ValueError("OA workflow boolean is invalid")
    return value


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
    "EXTERNAL_SANITIZATION_WARNING",
    "OAContractPackProfile",
    "OAPendingWorkflow",
    "OAPendingWorkflowCollection",
    "OASystemMessage",
    "OASystemMessageCollection",
    "OAStructuralDriftReport",
    "OAStructuralFingerprint",
    "OAStructuralNode",
    "STRUCTURAL_FINGERPRINT_ALGORITHM",
    "build_live_pending_workflows_fingerprint",
    "build_live_system_messages_fingerprint",
    "build_structural_fingerprint",
    "compare_structural_fingerprints",
    "normalize_pending_workflow_records",
    "normalize_system_message_records",
)
