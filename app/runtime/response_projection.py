"""Fail-closed projection of capability results into ResponseEnvelope data."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from app.infra.sdui.credential_markers import has_credential_marker
from app.ports.capability_registry import CapabilitySpec

_DROP = object()

_ANNOTATION_KEYWORDS = frozenset(
    {
        "title",
        "description",
        "default",
        "examples",
        "deprecated",
        "readOnly",
        "writeOnly",
        "$comment",
        "$id",
        "$schema",
    }
)
_IGNORED_VALIDATION_KEYWORDS = frozenset(
    {
        "required",
        "const",
        "enum",
        "format",
        "pattern",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minItems",
        "maxItems",
        "uniqueItems",
        "minProperties",
        "maxProperties",
    }
)
_SUPPORTED_STRUCTURE_KEYWORDS = frozenset(
    {
        "$defs",
        "$ref",
        "type",
        "properties",
        "items",
        "additionalProperties",
        "anyOf",
    }
)
_ALLOWED_KEYWORDS = (
    _ANNOTATION_KEYWORDS | _IGNORED_VALIDATION_KEYWORDS | _SUPPORTED_STRUCTURE_KEYWORDS
)


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


def schema_has_credential_property(schema: Mapping[str, Any]) -> bool:
    return _schema_has_credential_property(schema, seen=set())


def project_response_data(
    data: dict[str, Any] | None,
    schema: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if data is None or not isinstance(schema, Mapping) or not schema:
        return None
    root_schema = dict(schema)
    if schema_has_credential_property(root_schema):
        return None
    if not _schema_is_supported(root_schema, root_schema, ()):
        return None
    projected = _project(data, root_schema, root_schema, ())
    return projected if isinstance(projected, dict) else None


def _schema_has_credential_property(
    schema: Mapping[str, Any],
    *,
    seen: set[int],
) -> bool:
    identity = id(schema)
    if identity in seen:
        return False
    seen.add(identity)

    properties = schema.get("properties")
    if isinstance(properties, Mapping):
        for raw_name, subschema in properties.items():
            if not isinstance(raw_name, str) or has_credential_marker(raw_name):
                return True
            if isinstance(subschema, Mapping) and _schema_has_credential_property(
                subschema, seen=seen
            ):
                return True

    for keyword in ("$defs",):
        children = schema.get(keyword)
        if isinstance(children, Mapping):
            for child in children.values():
                if isinstance(child, Mapping) and _schema_has_credential_property(child, seen=seen):
                    return True

    for keyword in ("items", "additionalProperties"):
        child = schema.get(keyword)
        if isinstance(child, Mapping) and _schema_has_credential_property(child, seen=seen):
            return True

    branches = schema.get("anyOf")
    if isinstance(branches, list):
        for branch in branches:
            if isinstance(branch, Mapping) and _schema_has_credential_property(branch, seen=seen):
                return True
    return False


def _schema_is_supported(
    schema: Mapping[str, Any],
    root_schema: Mapping[str, Any],
    ref_stack: tuple[str, ...],
) -> bool:
    """Validate the entire schema subset before any value-dependent projection."""

    if not schema or any(key not in _ALLOWED_KEYWORDS for key in schema):
        return False

    definitions = schema.get("$defs")
    if definitions is not None and not isinstance(definitions, Mapping):
        return False

    reference = schema.get("$ref")
    if reference is not None:
        if not isinstance(reference, str):
            return False
        siblings = set(schema) - {"$ref"}
        if any(key not in _ANNOTATION_KEYWORDS for key in siblings):
            return False
        prefix = "#/$defs/"
        name = reference.removeprefix(prefix)
        if not reference.startswith(prefix) or not name or "/" in name or reference in ref_stack:
            return False
        root_definitions = root_schema.get("$defs")
        if not isinstance(root_definitions, Mapping):
            return False
        target = root_definitions.get(name)
        return bool(
            isinstance(target, Mapping)
            and target
            and _schema_is_supported(
                target,
                root_schema,
                (*ref_stack, reference),
            )
        )

    any_of = schema.get("anyOf")
    if any_of is not None:
        structure_siblings = (set(schema) & _SUPPORTED_STRUCTURE_KEYWORDS) - {
            "anyOf",
            "$defs",
        }
        if structure_siblings or not isinstance(any_of, list) or len(any_of) != 2:
            return False
        null_branches = [
            branch
            for branch in any_of
            if isinstance(branch, Mapping) and branch.get("type") == "null"
        ]
        non_null_branches = [branch for branch in any_of if branch not in null_branches]
        return bool(
            len(null_branches) == 1
            and len(non_null_branches) == 1
            and all(
                isinstance(branch, Mapping)
                and branch
                and _schema_is_supported(branch, root_schema, ref_stack)
                for branch in (*null_branches, *non_null_branches)
            )
        )

    structure_keys = set(schema) & _SUPPORTED_STRUCTURE_KEYWORDS
    structure_keys.discard("$defs")
    if (
        structure_keys != {"type"}
        and not (
            schema.get("type") == "object"
            and structure_keys <= {"type", "properties", "additionalProperties"}
        )
        and not (schema.get("type") == "array" and structure_keys <= {"type", "items"})
    ):
        return False

    schema_type = schema.get("type")
    if schema_type == "object":
        raw_properties = schema.get("properties", {})
        if not isinstance(raw_properties, Mapping):
            return False
        additional = schema.get("additionalProperties")
        if additional not in (None, True, False) and not isinstance(additional, Mapping):
            return False
        additional_schema = additional if isinstance(additional, Mapping) and additional else None
        if not raw_properties and additional_schema is None:
            return False
        if any(
            not isinstance(raw_key, str)
            or not isinstance(subschema, Mapping)
            or not subschema
            or not _schema_is_supported(subschema, root_schema, ref_stack)
            for raw_key, subschema in raw_properties.items()
        ):
            return False
        return bool(
            additional_schema is None
            or _schema_is_supported(additional_schema, root_schema, ref_stack)
        )
    if schema_type == "array":
        items = schema.get("items")
        return bool(
            isinstance(items, Mapping)
            and items
            and _schema_is_supported(items, root_schema, ref_stack)
        )
    return schema_type in {"string", "boolean", "null", "integer", "number"}


def _project(
    value: Any,
    schema: Mapping[str, Any],
    root_schema: Mapping[str, Any],
    ref_stack: tuple[str, ...],
) -> Any:
    if not schema or any(key not in _ALLOWED_KEYWORDS for key in schema):
        return _DROP

    reference = schema.get("$ref")
    if reference is not None:
        return _project_reference(value, schema, root_schema, ref_stack)

    any_of = schema.get("anyOf")
    if any_of is not None:
        return _project_nullable(value, schema, root_schema, ref_stack)

    structure_keys = set(schema) & _SUPPORTED_STRUCTURE_KEYWORDS
    if "$defs" in structure_keys:
        structure_keys.remove("$defs")
    if (
        structure_keys != {"type"}
        and not (
            schema.get("type") == "object"
            and structure_keys <= {"type", "properties", "additionalProperties"}
        )
        and not (schema.get("type") == "array" and structure_keys <= {"type", "items"})
    ):
        return _DROP

    schema_type = schema.get("type")
    if schema_type == "object":
        return _project_object(value, schema, root_schema, ref_stack)
    if schema_type == "array":
        return _project_array(value, schema, root_schema, ref_stack)
    if schema_type == "string":
        return value if isinstance(value, str) else _DROP
    if schema_type == "boolean":
        return value if isinstance(value, bool) else _DROP
    if schema_type == "null":
        return None if value is None else _DROP
    if schema_type == "integer":
        return value if isinstance(value, int) and not isinstance(value, bool) else _DROP
    if schema_type == "number":
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else _DROP
    return _DROP


def _project_reference(
    value: Any,
    schema: Mapping[str, Any],
    root_schema: Mapping[str, Any],
    ref_stack: tuple[str, ...],
) -> Any:
    reference = schema.get("$ref")
    if not isinstance(reference, str):
        return _DROP
    siblings = set(schema) - {"$ref"}
    if any(key not in _ANNOTATION_KEYWORDS for key in siblings):
        return _DROP
    prefix = "#/$defs/"
    name = reference.removeprefix(prefix)
    if not reference.startswith(prefix) or not name or "/" in name or reference in ref_stack:
        return _DROP
    definitions = root_schema.get("$defs")
    if not isinstance(definitions, Mapping):
        return _DROP
    target = definitions.get(name)
    if not isinstance(target, Mapping) or not target:
        return _DROP
    return _project(value, target, root_schema, (*ref_stack, reference))


def _project_nullable(
    value: Any,
    schema: Mapping[str, Any],
    root_schema: Mapping[str, Any],
    ref_stack: tuple[str, ...],
) -> Any:
    structure_siblings = (set(schema) & _SUPPORTED_STRUCTURE_KEYWORDS) - {"anyOf", "$defs"}
    if structure_siblings:
        return _DROP
    branches = schema.get("anyOf")
    if not isinstance(branches, list) or len(branches) != 2:
        return _DROP
    null_branches = [
        branch
        for branch in branches
        if isinstance(branch, Mapping) and branch.get("type") == "null"
    ]
    non_null_branches = [branch for branch in branches if branch not in null_branches]
    if len(null_branches) != 1 or len(non_null_branches) != 1:
        return _DROP
    non_null = non_null_branches[0]
    if not isinstance(non_null, Mapping) or not non_null:
        return _DROP
    if value is None:
        return _project(value, null_branches[0], root_schema, ref_stack)
    return _project(value, non_null, root_schema, ref_stack)


def _project_object(
    value: Any,
    schema: Mapping[str, Any],
    root_schema: Mapping[str, Any],
    ref_stack: tuple[str, ...],
) -> Any:
    if not isinstance(value, dict):
        return _DROP
    raw_properties = schema.get("properties", {})
    if not isinstance(raw_properties, Mapping):
        return _DROP
    properties = dict(raw_properties)
    additional = schema.get("additionalProperties")
    if additional not in (None, True, False) and not isinstance(additional, Mapping):
        return _DROP
    additional_schema = additional if isinstance(additional, Mapping) and additional else None
    if not properties and additional_schema is None:
        return _DROP

    projected: dict[str, Any] = {}
    for raw_key, raw_item in value.items():
        if not isinstance(raw_key, str):
            continue
        subschema = properties.get(raw_key)
        if subschema is None:
            if additional_schema is None or has_credential_marker(raw_key):
                continue
            subschema = additional_schema
        if not isinstance(subschema, Mapping) or not subschema:
            continue
        item = _project(raw_item, subschema, root_schema, ref_stack)
        if item is not _DROP:
            projected[raw_key] = item
    return projected


def _project_array(
    value: Any,
    schema: Mapping[str, Any],
    root_schema: Mapping[str, Any],
    ref_stack: tuple[str, ...],
) -> Any:
    if not isinstance(value, list):
        return _DROP
    items = schema.get("items")
    if not isinstance(items, Mapping) or not items:
        return _DROP
    projected: list[Any] = []
    for raw_item in value:
        item = _project(raw_item, items, root_schema, ref_stack)
        if item is not _DROP:
            projected.append(item)
    return projected


__all__ = (
    "ProjectionContractSnapshot",
    "canonical_schema_digest",
    "canonical_schema_json",
    "project_response_data",
    "schema_has_credential_property",
)
