"""Internal OA read models and structural Contract Pack fingerprinting."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
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
PENDING_WORKFLOW_DERIVATION_WARNING: Final = (
    "Structure was derived from a real system-message capture after both OA "
    "message-center categories were verified to share the same field set; "
    "the pending category was not captured directly."
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
_SAFE_WIRE_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Both message-center capabilities read the same ``getMsgList`` endpoint and
# return the same record shape; only the category id differs. Keeping one map
# is what stops the two capabilities from drifting apart again.
_LIVE_MESSAGE_CENTER_FIELD_NAMES: Final[Mapping[str, str]] = {
    "messageid": "message_id",
    "title": "title",
    "context": "content",
    "name": "source_name",
    "time": "occurred_at",
    "bizstate": "business_state",
    "link": "link",
    "linkmobileurl": "mobile_link",
}
# OA navigation hints, not business data; every captured real record carries
# them as empty strings. Enumerated key by key rather than exempted as a class,
# so adding or removing one always shows up in a diff. If a later capture shows
# them carrying values, revisit this list instead of leaving them ignored.
_LIVE_MESSAGE_CENTER_IGNORED_WIRE_FIELDS: Final[frozenset[str]] = frozenset(
    {"gomethod", "gomethodpc", "showimage"}
)
_PENDING_WORKFLOW_STRUCTURAL_SCHEMA_EXEMPLAR = {
    "workflows": [
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


class OAMessageCenterRecord(BaseModel):
    """One normalized, credential-free OA message-center record.

    Both ``oa.list_system_messages`` and ``oa.list_pending_workflows`` read the
    same endpoint and return this shape; only the category id differs.
    """

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
        # "//host/path" is protocol-relative, i.e. off-origin, and would become a
        # cross-origin redirect once rendered as an href. The Live normalizer
        # already rejects it; the model must too, or the Replay path lets it in.
        if value is not None and (
            not value.strip()
            or not value.startswith("/")
            or value.startswith("//")
        ):
            raise ValueError("system-message links must be null or relative paths")
        return value


# Name kept for the system-message call sites that predate the shared record.
OASystemMessage = OAMessageCenterRecord


class OASystemMessageCollection(BaseModel):
    """Bounded result for ``oa.list_system_messages`` with explicit completeness."""

    model_config = ConfigDict(extra="forbid", strict=True)

    messages: list[OAMessageCenterRecord]
    returned_count: int
    is_complete: bool

    @model_validator(mode="after")
    def _validate_returned_count(self) -> OASystemMessageCollection:
        if self.returned_count != len(self.messages):
            raise ValueError("returned_count must match the message collection")
        return self


class OAPendingWorkflowCollection(BaseModel):
    """Bounded result for ``oa.list_pending_workflows``.

    The pending category of the OA message center. ``returned_count`` and
    ``is_complete`` are what keep a silently truncated page from reading as a
    complete answer.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    workflows: list[OAMessageCenterRecord]
    returned_count: int
    is_complete: bool

    @model_validator(mode="after")
    def _validate_returned_count(self) -> OAPendingWorkflowCollection:
        if self.returned_count != len(self.workflows):
            raise ValueError("returned_count must match the workflow collection")
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
        "derived_from_sibling_capture",
    ]
    source_warning: Literal[
        "Source was sanitized externally; leakage assertions are not evidence "
        "of EternalAI sanitizer verification.",
        "Structure was derived from a real system-message capture after both OA "
        "message-center categories were verified to share the same field set; "
        "the pending category was not captured directly.",
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
        expected_warning = None
        if self.source_kind == "externally_sanitized_capture":
            expected_warning = EXTERNAL_SANITIZATION_WARNING
        elif self.source_kind == "derived_from_sibling_capture":
            expected_warning = PENDING_WORKFLOW_DERIVATION_WARNING
        if self.source_warning != expected_warning:
            raise ValueError("capture source requires its fixed assurance warning")
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
    changed_expected: tuple[OAStructuralNode, ...]
    changed: tuple[OAStructuralNode, ...]

    @field_validator("expected_sha256", "actual_sha256")
    @classmethod
    def _validate_report_sha256(cls, value: str) -> str:
        if _STRUCTURAL_SHA256_PATTERN.fullmatch(value) is None:
            raise ValueError("drift report SHA-256 is invalid")
        return value

    @model_validator(mode="after")
    def _validate_changed_pairs(self) -> OAStructuralDriftReport:
        expected_paths = tuple(node.path for node in self.changed_expected)
        actual_paths = tuple(node.path for node in self.changed)
        if expected_paths != actual_paths:
            raise ValueError("changed structural nodes must have matching paths")
        return self


def build_structural_fingerprint(payload: Any) -> dict[str, Any]:
    """Fingerprint JSON structure without including values or array lengths."""

    return _build_structural_fingerprint(
        payload,
        include_contract_exemplar=True,
    )


def build_contract_drift_baseline_fingerprint(payload: Any) -> dict[str, Any]:
    """Baseline for comparing live structure against a Contract Pack sample.

    Excludes the contract exemplar on purpose: the exemplar declares what the
    contract permits, while drift asks whether reality still matches what was
    recorded. Mixing the two reports a change on every exemplar-only nullable.
    """

    return _build_structural_fingerprint(
        payload,
        include_contract_exemplar=False,
    )


def build_live_pending_workflows_fingerprint(
    records: list[Any],
) -> dict[str, Any]:
    """Fingerprint one Live aggregate from value-free normalized wire structure."""

    field_names = _live_record_field_names(
        records,
        normalized_field_names=_LIVE_MESSAGE_CENTER_FIELD_NAMES,
        raw_path="$.workflows[]",
    )
    projected_records = [
        _project_live_message_center_record(
            record,
            field_names=field_names,
            raw_path="$.workflows[]",
        )
        for record in records
    ]
    return _build_structural_fingerprint(
        {
            "workflows": projected_records,
            "returned_count": 0,
            "is_complete": False,
        },
        include_contract_exemplar=False,
    )


def build_live_system_messages_fingerprint(
    records: list[Any],
) -> dict[str, Any]:
    """Fingerprint one bounded Live system-message aggregate without wire values."""

    field_names = _live_record_field_names(
        records,
        normalized_field_names=_LIVE_MESSAGE_CENTER_FIELD_NAMES,
        raw_path="$.messages[]",
    )
    projected_records = [
        _project_live_message_center_record(
            record,
            field_names=field_names,
            raw_path="$.messages[]",
        )
        for record in records
    ]
    return _build_structural_fingerprint(
        {
            "messages": projected_records,
            "returned_count": 0,
            "is_complete": False,
        },
        include_contract_exemplar=False,
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


def _project_live_message_center_record(
    record: Any,
    *,
    field_names: Mapping[str, str],
    raw_path: str,
) -> Any:
    if not isinstance(record, Mapping):
        return _project_json_structure(
            record,
            raw_path=raw_path,
            expose_safe_wire_names=True,
        )
    return _project_json_mapping(
        record,
        field_names=field_names,
        ignored_fields=_LIVE_MESSAGE_CENTER_IGNORED_WIRE_FIELDS,
        raw_path=raw_path,
        expose_safe_wire_names=True,
    )


def _live_record_field_names(
    records: list[Any],
    *,
    normalized_field_names: Mapping[str, str],
    raw_path: str,
) -> dict[str, str]:
    observed_fields: set[str] = set()
    for record in records:
        if isinstance(record, Mapping):
            observed_fields.update(
                key for key in record if isinstance(key, str)
            )
    field_names = {
        key: normalized_field_names[key]
        for key in sorted(observed_fields & normalized_field_names.keys())
    }
    unknown_fields = sorted(observed_fields - normalized_field_names.keys())
    field_names.update(
        _wire_field_names(
            unknown_fields,
            raw_path=raw_path,
            reserved_names=frozenset(normalized_field_names.values()),
        )
    )
    return field_names


def _wire_field_names(
    keys: list[str],
    *,
    raw_path: str,
    reserved_names: frozenset[str],
) -> dict[str, str]:
    aliases: dict[str, str] = {}
    allocated = set(reserved_names)
    for key in sorted(keys):
        candidate = (
            f"wire_{key}"
            if _SAFE_WIRE_IDENTIFIER_PATTERN.fullmatch(key)
            else _anonymous_field_name(_raw_mapping_child_path(raw_path, key))
        )
        if candidate in allocated:
            candidate = _anonymous_field_name(
                _raw_mapping_child_path(raw_path, key)
            )
        if candidate in allocated:
            raise ValueError("wire structural field collision")
        aliases[key] = candidate
        allocated.add(candidate)
    return aliases


def _project_json_structure(
    value: Any,
    *,
    raw_path: str,
    expose_safe_wire_names: bool,
) -> Any:
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
            raw_path=raw_path,
            expose_safe_wire_names=expose_safe_wire_names,
        )
    if isinstance(value, list):
        return [
            _project_json_structure(
                item,
                raw_path=f"{raw_path}[]",
                expose_safe_wire_names=expose_safe_wire_names,
            )
            for item in value
        ]
    raise TypeError("Live OA payload must contain JSON-compatible values")


def _project_json_mapping(
    value: Mapping[Any, Any],
    *,
    field_names: Mapping[str, str],
    ignored_fields: frozenset[str] | set[str],
    raw_path: str,
    expose_safe_wire_names: bool,
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
    safe_unknown_names = (
        _wire_field_names(
            unknown_keys,
            raw_path=raw_path,
            reserved_names=frozenset(field_names.values()),
        )
        if expose_safe_wire_names
        else _anonymous_field_names(unknown_keys, raw_path=raw_path)
    )
    projected: dict[str, Any] = {}
    for key in string_keys:
        safe_key = (
            field_names[key]
            if key in field_names
            else safe_unknown_names[key]
        )
        projected[safe_key] = _project_json_structure(
            value[key],
            raw_path=_raw_mapping_child_path(raw_path, key),
            expose_safe_wire_names=expose_safe_wire_names,
        )
    return projected


def _anonymous_field_names(
    keys: list[str],
    *,
    raw_path: str,
) -> dict[str, str]:
    aliases = {
        key: _anonymous_field_name(_raw_mapping_child_path(raw_path, key))
        for key in sorted(keys)
    }
    if len(set(aliases.values())) != len(aliases):
        raise ValueError("anonymous structural field collision")
    return aliases


def _anonymous_field_name(raw_path: str) -> str:
    digest = hashlib.sha256(raw_path.encode("utf-8")).hexdigest()
    return f"unknown_field_{digest}"


def _raw_mapping_child_path(raw_path: str, key: str) -> str:
    encoded_key = json.dumps(
        key,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"{raw_path}[{encoded_key}]"


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
    changed_paths = tuple(
        path
        for path in sorted(expected_nodes.keys() & actual_nodes.keys())
        if actual_nodes[path] != expected_nodes[path]
    )
    changed_expected = tuple(expected_nodes[path] for path in changed_paths)
    changed = tuple(actual_nodes[path] for path in changed_paths)
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
        changed_expected=changed_expected,
        changed=changed,
    )


def normalize_pending_workflow_records(
    records: list[Any],
    *,
    record_limit: int,
    is_complete: bool,
    link_normalizer: Callable[[str], str] | None = None,
) -> OAPendingWorkflowCollection:
    """Whitelist and normalize one bounded complete Live pending aggregate."""

    normalized = _normalize_message_center_records(
        records,
        record_limit=record_limit,
        link_normalizer=link_normalizer,
    )
    try:
        return OAPendingWorkflowCollection.model_validate(
            {
                "workflows": normalized,
                "returned_count": len(normalized),
                "is_complete": is_complete,
            },
            strict=True,
        )
    except ValidationError:
        raise ValueError("normalized OA workflow collection is invalid") from None


def normalize_system_message_records(
    records: list[Any],
    *,
    record_limit: int,
    is_complete: bool,
    link_normalizer: Callable[[str], str] | None = None,
) -> OASystemMessageCollection:
    """Whitelist and normalize one bounded complete Live message aggregate."""

    normalized = _normalize_message_center_records(
        records,
        record_limit=record_limit,
        link_normalizer=link_normalizer,
    )
    try:
        return OASystemMessageCollection.model_validate(
            {
                "messages": normalized,
                "returned_count": len(normalized),
                "is_complete": is_complete,
            },
            strict=True,
        )
    except ValidationError:
        raise ValueError(
            "normalized OA system-message collection is invalid"
        ) from None


def _normalize_message_center_records(
    records: list[Any],
    *,
    record_limit: int,
    link_normalizer: Callable[[str], str] | None,
) -> list[dict[str, Any]]:
    """Whitelist the shared message-center record shape for both capabilities."""

    if record_limit <= 0 or len(records) > record_limit:
        raise ValueError("OA system-message aggregate exceeds the record limit")
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
    return normalized


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
    "PENDING_WORKFLOW_DERIVATION_WARNING",
    "OAContractPackProfile",
    "OAMessageCenterRecord",
    "OAPendingWorkflowCollection",
    "OASystemMessage",
    "OASystemMessageCollection",
    "OAStructuralDriftReport",
    "OAStructuralFingerprint",
    "OAStructuralNode",
    "STRUCTURAL_FINGERPRINT_ALGORITHM",
    "build_contract_drift_baseline_fingerprint",
    "build_live_pending_workflows_fingerprint",
    "build_live_system_messages_fingerprint",
    "build_structural_fingerprint",
    "compare_structural_fingerprints",
    "normalize_pending_workflow_records",
    "normalize_system_message_records",
)
