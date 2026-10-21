"""Fail-closed projection of capability results into ResponseEnvelope data."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Mapping, NoReturn

from app.infra.sdui.credential_markers import has_credential_marker
from app.ports.response_projection_contract import (
    ARRAY_ITEM,
    ApprovedProjectionFailure,
    ApprovedProjectionKind,
    ApprovedProjectionPath,
    ApprovedResponseProjection,
    ApprovedResponseProjectionError,
    ProjectionContractSnapshot,
    canonical_schema_digest,
    canonical_schema_json,
    named_scalar_schema,
)

_DROP = object()
_MAX_APPROVED_DEPTH = 64
_OPAQUE_OBJECT: dict[str, Any] = {"type": "object"}
_NULL: dict[str, Any] = {"type": "null"}


@dataclass
class _Approval:
    policy: ApprovedResponseProjection
    seen: set[ApprovedProjectionPath] = field(default_factory=set)

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


def schema_has_credential_property(schema: Mapping[str, Any]) -> bool:
    return _schema_has_credential_property(schema, seen=set())


def project_response_data(
    data: dict[str, Any] | None,
    schema: Mapping[str, Any] | None,
    *,
    approved: ApprovedResponseProjection | None = None,
) -> dict[str, Any] | None:
    if approved is not None:
        try:
            return project_approved_response_data(data, schema, approved)
        except ApprovedResponseProjectionError:
            return None
    if data is None or not isinstance(schema, Mapping) or not schema:
        return None
    root_schema = dict(schema)
    if schema_has_credential_property(root_schema):
        return None
    if not _schema_is_supported(root_schema, root_schema, ()):
        return None
    projected = _project(data, root_schema, root_schema, ())
    return projected if isinstance(projected, dict) else None


def project_approved_response_data(
    data: dict[str, Any] | None,
    schema: Mapping[str, Any] | None,
    approved: ApprovedResponseProjection,
) -> dict[str, Any]:
    """Exact projection under one code-owned policy; any rejection fails it whole.

    The policy only applies to the canonical schema it was frozen with. No
    value is silently dropped: an unexpected key, type or approved-path value
    raises with a fixed reason instead of returning a partial result.
    """
    if type(approved) is not ApprovedResponseProjection or not isinstance(schema, Mapping):
        _reject("policy_mismatch")
    root_schema = dict(schema)
    try:
        bound = bool(root_schema) and approved.matches_schema(root_schema)
    except (TypeError, ValueError, RecursionError):
        bound = False
    if not bound:
        _reject("policy_mismatch")
    approval = _Approval(approved)
    if (
        schema_has_credential_property(root_schema)
        or not _schema_is_supported(root_schema, root_schema, (), path=(), approval=approval)
        or approval.seen != approved.declared_paths()
    ):
        _reject("unsupported_schema")
    if data is None:
        _reject("unsupported_value")
    try:
        projected = _project(data, root_schema, root_schema, (), path=(), approval=approval)
    except RecursionError:
        _reject("depth_or_cycle")
    if not isinstance(projected, dict):
        _reject("unsupported_value")
    return projected


def _reject(reason: ApprovedProjectionFailure) -> NoReturn:
    raise ApprovedResponseProjectionError(reason)


def _approved_node_supported(schema: Mapping[str, Any], kind: ApprovedProjectionKind) -> bool:
    if kind == "canonical_json":
        return dict(schema) == {"type": "string"}
    if kind == "named_scalar":
        return dict(schema) == named_scalar_schema()
    branches = schema.get("anyOf")
    if branches is None:
        return dict(schema) == _OPAQUE_OBJECT
    return (
        set(schema) == {"anyOf"}
        and isinstance(branches, list)
        and len(branches) == 2
        and _OPAQUE_OBJECT in branches
        and _NULL in branches
    )


def _is_nullable_scalar(schema: Mapping[str, Any]) -> bool:
    branches = schema.get("anyOf")
    return bool(
        set(schema) == {"anyOf"}
        and isinstance(branches, list)
        and len(branches) == 2
        and _NULL in branches
        and any(
            isinstance(branch, Mapping)
            and branch.get("type") in {"string", "integer", "number", "boolean"}
            for branch in branches
        )
    )


def _approved_json_value(value: Any, depth: int) -> Any:
    """Copy one finite JSON value; reject credential keys and other values."""
    if depth > _MAX_APPROVED_DEPTH:
        _reject("depth_or_cycle")
    if isinstance(value, dict):
        copied: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                _reject("unsupported_value")
            if has_credential_marker(key):
                _reject("credential_key")
            copied[key] = _approved_json_value(item, depth + 1)
        return copied
    if isinstance(value, list):
        return [_approved_json_value(item, depth + 1) for item in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            _reject("nonfinite_number")
        return value
    _reject("unsupported_value")


def _approved_opaque(value: Any, schema: Mapping[str, Any]) -> Any:
    if value is None:
        if "anyOf" in schema:
            return None
        _reject("unsupported_value")
    if not isinstance(value, dict):
        _reject("unsupported_value")
    return _approved_json_value(value, 0)


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in pairs:
        if key in result:
            _reject("invalid_json")
        result[key] = item
    return result


def _reject_json_constant(_: str) -> NoReturn:
    _reject("nonfinite_number")


def _finite_json_float(text: str) -> float:
    number = float(text)
    if not math.isfinite(number):
        _reject("nonfinite_number")
    return number


def _approved_canonical_json(value: Any) -> str:
    """Keep the original text only when it strictly decodes to a safe object."""
    if not isinstance(value, str):
        _reject("unsupported_value")
    try:
        decoded = json.loads(
            value,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
            parse_float=_finite_json_float,
        )
    except ApprovedResponseProjectionError:
        raise
    except RecursionError:
        _reject("depth_or_cycle")
    except (ValueError, TypeError):
        _reject("invalid_json")
    if not isinstance(decoded, dict):
        _reject("invalid_json")
    _approved_json_value(decoded, 0)
    return value


def _approved_named_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            _reject("nonfinite_number")
        return value
    _reject("unsupported_value")


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
    *,
    path: ApprovedProjectionPath = (),
    approval: _Approval | None = None,
) -> bool:
    """Validate the entire schema subset before any value-dependent projection."""

    if approval is not None:
        kind = approval.policy.kind_at(path)
        if kind is not None:
            approval.seen.add(path)
            return _approved_node_supported(schema, kind)
        # Checked once at the column node; its anyOf branches share the path.
        if path in approval.policy.raw_nullable_paths and path not in approval.seen:
            approval.seen.add(path)
            if not _is_nullable_scalar(schema):
                return False

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
                path=path,
                approval=approval,
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
                and _schema_is_supported(
                    branch, root_schema, ref_stack, path=path, approval=approval
                )
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
        if approval is not None and (
            additional_schema is not None or ARRAY_ITEM in raw_properties
        ):
            return False
        if any(
            not isinstance(raw_key, str)
            or not isinstance(subschema, Mapping)
            or not subschema
            or not _schema_is_supported(
                subschema, root_schema, ref_stack, path=(*path, raw_key), approval=approval
            )
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
            and _schema_is_supported(
                items, root_schema, ref_stack, path=(*path, ARRAY_ITEM), approval=approval
            )
        )
    return schema_type in {"string", "boolean", "null", "integer", "number"}


def _project(
    value: Any,
    schema: Mapping[str, Any],
    root_schema: Mapping[str, Any],
    ref_stack: tuple[str, ...],
    *,
    path: ApprovedProjectionPath = (),
    approval: _Approval | None = None,
) -> Any:
    if approval is not None:
        kind = approval.policy.kind_at(path)
        if kind == "opaque_object":
            return _approved_opaque(value, schema)
        if kind == "canonical_json":
            return _approved_canonical_json(value)
        if kind == "named_scalar":
            return _approved_named_scalar(value)

    if not schema or any(key not in _ALLOWED_KEYWORDS for key in schema):
        return _DROP

    reference = schema.get("$ref")
    if reference is not None:
        return _project_reference(
            value, schema, root_schema, ref_stack, path=path, approval=approval
        )

    any_of = schema.get("anyOf")
    if any_of is not None:
        return _project_nullable(
            value, schema, root_schema, ref_stack, path=path, approval=approval
        )

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
        return _project_object(
            value, schema, root_schema, ref_stack, path=path, approval=approval
        )
    if schema_type == "array":
        return _project_array(
            value, schema, root_schema, ref_stack, path=path, approval=approval
        )
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
    *,
    path: ApprovedProjectionPath = (),
    approval: _Approval | None = None,
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
    return _project(
        value, target, root_schema, (*ref_stack, reference), path=path, approval=approval
    )


def _project_nullable(
    value: Any,
    schema: Mapping[str, Any],
    root_schema: Mapping[str, Any],
    ref_stack: tuple[str, ...],
    *,
    path: ApprovedProjectionPath = (),
    approval: _Approval | None = None,
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
        return _project(
            value, null_branches[0], root_schema, ref_stack, path=path, approval=approval
        )
    return _project(value, non_null, root_schema, ref_stack, path=path, approval=approval)


def _project_object(
    value: Any,
    schema: Mapping[str, Any],
    root_schema: Mapping[str, Any],
    ref_stack: tuple[str, ...],
    *,
    path: ApprovedProjectionPath = (),
    approval: _Approval | None = None,
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
            if approval is not None:
                _reject("unsupported_value")
            continue
        subschema = properties.get(raw_key)
        if subschema is None:
            if approval is not None:
                _reject("unsupported_value")
            if additional_schema is None or has_credential_marker(raw_key):
                continue
            subschema = additional_schema
        if not isinstance(subschema, Mapping) or not subschema:
            continue
        item = _project(
            raw_item, subschema, root_schema, ref_stack,
            path=(*path, raw_key), approval=approval,
        )
        if item is not _DROP:
            projected[raw_key] = item
        elif approval is not None:
            _reject("unsupported_value")
    return projected


def _project_array(
    value: Any,
    schema: Mapping[str, Any],
    root_schema: Mapping[str, Any],
    ref_stack: tuple[str, ...],
    *,
    path: ApprovedProjectionPath = (),
    approval: _Approval | None = None,
) -> Any:
    if not isinstance(value, list):
        return _DROP
    items = schema.get("items")
    if not isinstance(items, Mapping) or not items:
        return _DROP
    projected: list[Any] = []
    for raw_item in value:
        item = _project(
            raw_item, items, root_schema, ref_stack,
            path=(*path, ARRAY_ITEM), approval=approval,
        )
        if item is not _DROP:
            projected.append(item)
        elif approval is not None:
            _reject("unsupported_value")
    return projected


__all__ = (
    "ProjectionContractSnapshot",
    "canonical_schema_digest",
    "canonical_schema_json",
    "project_approved_response_data",
    "project_response_data",
    "schema_has_credential_property",
)
