"""Code-owned success contracts for the six approved fixed-service reads.

Source: the owner-supplied six-read success field contract (2026-10-15) and its
execution addendum. The provider declares no outputSchema; each success is a
fixed, permission-scoped projection whose fields are all displayable. Nothing
here is derived from provider results, model output or registry rows.

Layout per tool: the root ``properties``/``$defs`` use only the subset the
shared projector supports and become the public schema; branch-specific
requirements live in a root ``allOf`` overlay that only validation sees.
Exact-path exceptions (whole displayable objects, the canonical JSON text and
the named raw scalar columns) are frozen in an ApprovedResponseProjection and
bound to the canonical public schema.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from dataclasses import field as dataclass_field
from functools import lru_cache
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence, get_args

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError, best_match

from app.mcp.contracts import OutputContract
from app.mcp.diagnostics import record_mcp_event
from app.mcp.models import McpFailure, ServiceConfig
from app.ports.response_projection_contract import (
    ARRAY_ITEM,
    ApprovedProjectionFailure,
    ApprovedProjectionPath,
    ApprovedResponseProjection,
    canonical_schema_json,
    named_scalar_schema,
)

SERVICE_CONFIG_ID = "lesscode-business-context-read"
QUERY_TOOLS = (
    "person_find",
    "talk_context_get",
    "talk_record_get",
    "talk_tasks_list",
    "clothing_options_get",
    "clothing_result_get",
)
CONTRACT_VERSION = "fixed-query-success-v1-20261015"
PROJECTION_POLICY_VERSION = "fixed-query-projection-v1-20261015"
APPROVAL_EVIDENCE = (
    "owner-supplied six-read success field contract 2026-10-15; "
    "execution addendum fixed raw-column compatibility 2026-10-15"
)

_HEX32 = "^[A-Fa-f0-9]{32}$"
_HEX64 = "^[A-Fa-f0-9]{64}$"
_DATE = "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"
_A = ARRAY_ITEM

# Raw fixed-column nullability as stated by the supplier.
_STATED_NULL, _STATED_NOT_NULL, _UNSTATED = "yes", "no", "unstated"
RawColumn = tuple[str, dict[str, Any] | None, str]  # None base: named scalar


def _string(**keywords: Any) -> dict[str, Any]:
    return {"type": "string", **keywords}


def _integer(**keywords: Any) -> dict[str, Any]:
    return {"type": "integer", **keywords}


def _boolean() -> dict[str, Any]:
    return {"type": "boolean"}


def _nullable(node: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [node, {"type": "null"}]}


def _date() -> dict[str, Any]:
    return _string(pattern=_DATE)


def _array(items: dict[str, Any], **keywords: Any) -> dict[str, Any]:
    return {"type": "array", "items": items, **keywords}


def _object(
    properties: dict[str, Any], required: list[str] | None = None
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties) if required is None else required,
        "additionalProperties": False,
    }


def _opaque() -> dict[str, Any]:
    # Displayable as a whole only at a frozen opaque path; never elsewhere.
    return {"type": "object"}


def _ref(name: str) -> dict[str, Any]:
    return {"$ref": f"#/$defs/{name}"}


def _raw_row(
    table: str, columns: tuple[RawColumn, ...]
) -> tuple[dict[str, Any], set[ApprovedProjectionPath], set[ApprovedProjectionPath]]:
    """One fixed raw row: every named column required, no other column."""
    properties: dict[str, Any] = {}
    scalars: set[ApprovedProjectionPath] = set()
    unstated: set[ApprovedProjectionPath] = set()
    for name, base, nullability in columns:
        if base is None:
            properties[name] = named_scalar_schema()
            scalars.add((table, _A, name))
        elif nullability == _STATED_NOT_NULL:
            properties[name] = base
        else:
            properties[name] = _nullable(base)
            if nullability == _UNSTATED:
                unstated.add((table, _A, name))
    return _object(properties), scalars, unstated


@dataclass(frozen=True)
class _QuerySpec:
    schema: dict[str, Any]
    opaque: frozenset[ApprovedProjectionPath] = frozenset()
    canonical: frozenset[ApprovedProjectionPath] = frozenset()
    scalars: frozenset[ApprovedProjectionPath] = frozenset()
    unstated: frozenset[ApprovedProjectionPath] = frozenset()


def _root(
    properties: dict[str, Any],
    *,
    required: list[str] | None = None,
    definitions: dict[str, Any] | None = None,
    overlay: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **_object(properties, required),
    }
    if definitions:
        schema["$defs"] = definitions
    if overlay:
        schema["allOf"] = overlay
    return schema


def _person_find() -> _QuerySpec:
    person = _object(
        {
            "personId": _string(),
            "number": _nullable(_string()),
            "name": _nullable(_string()),
            "jyId": _nullable(_string()),
            "jqId": _nullable(_string()),
        }
    )
    return _QuerySpec(
        _root(
            {
                "items": _array(person),
                "page": _integer(),
                "pageSize": _integer(),
                "hasMore": _boolean(),
            }
        )
    )


_TASK_LIST_FIELDS = (
    "tasks", "page", "pageSize", "total", "rules", "sourceReadiness",
    "configurationStatus", "schedulerEnabled", "timezone",
)
_TASK_OPAQUE: tuple[ApprovedProjectionPath, ...] = (
    ("tasks", _A, "ruleSnapshot"),
    ("rules", _A, "latestConfig"),
    ("rules", _A, "config"),
    ("rules", _A, "scheduledVersions", _A, "config"),
)


def _task_definitions() -> dict[str, Any]:
    return {
        "talkTask": _object(
            {
                "id": _string(pattern=_HEX32),
                "ruleId": _string(),
                "personId": _string(),
                "jyId": _nullable(_string()),
                "jqId": _nullable(_string()),
                "weekStart": _date(),
                "deadline": _string(),
                "required": _integer(),
                "valid": _integer(),
                "remaining": _integer(),
                "state": _string(),
                "version": _integer(),
                "assigneeId": _nullable(_string()),
                "prepared": _boolean(),
                "preparationArtifactId": _nullable(_string()),
                "everOverdue": _boolean(),
                "reason": _nullable(_string()),
                "ruleSnapshot": _nullable(_opaque()),
                "sourceIds": _array(_string()),
            }
        ),
        "talkRule": _object(
            {
                "id": _string(),
                "jyId": _string(),
                "jqId": _nullable(_string()),
                "version": _integer(),
                "latestConfig": _opaque(),
                "currentVersion": _nullable(_integer()),
                "config": _nullable(_opaque()),
                "currentStatus": _string(),
                "scheduledVersions": _array(
                    _object(
                        {"version": _integer(), "effectiveFrom": _date(), "config": _opaque()}
                    )
                ),
            }
        ),
        # FORBIDDEN and per-rule items merged; the overlay separates them.
        "sourceReadiness": _object(
            {
                "ruleId": _string(),
                "status": _string(
                    enum=["READY", "DATA_PENDING", "PENDING_EFFECTIVE", "FORBIDDEN"]
                ),
                "unknownDateRecordCount": _integer(),
                "reason": _nullable(_string()),
            },
            required=["status"],
        ),
    }


def _task_list_properties() -> dict[str, Any]:
    return {
        "tasks": _array(_ref("talkTask")),
        "page": _integer(),
        "pageSize": _integer(),
        "total": _integer(),
        "rules": _array(_ref("talkRule")),
        "sourceReadiness": _array(_ref("sourceReadiness")),
        "configurationStatus": _string(),
        "schedulerEnabled": _boolean(),
        "timezone": _string(const="Asia/Shanghai"),
    }


def _source_readiness_overlay() -> dict[str, Any]:
    forbidden = {"properties": {"status": {"const": "FORBIDDEN"}}}
    return {
        "items": {
            "if": forbidden,
            "then": {"maxProperties": 1},
            "else": {"required": ["ruleId", "unknownDateRecordCount", "reason"]},
        },
        "if": {"contains": forbidden},
        "then": {"maxItems": 1},
    }


def _talk_tasks_list() -> _QuerySpec:
    return _QuerySpec(
        _root(
            _task_list_properties(),
            definitions=_task_definitions(),
            overlay=[{"properties": {"sourceReadiness": _source_readiness_overlay()}}],
        ),
        opaque=frozenset(_TASK_OPAQUE),
    )


_TALK_SOURCES = ("jygz.gbthjy", "jygz.wxfth", "jygz.wgfth", "app.gbth")
_BACKGROUND_SOURCES = ("jygz.wxfrd", "jygz.wgfrd")
_DICTIONARY_KEYS = ("thsx", "thdd", "thxg", "sbt", "thnrzt")
_OPTION_STATUSES = ["TRUNCATED", "EMPTY", "AVAILABLE"]


def _records_follow_status() -> dict[str, Any]:
    return {
        "if": {"properties": {"status": {"const": "AVAILABLE"}}},
        "then": {"required": ["records"]},
        "else": {"not": {"required": ["records"]}},
    }


def _talk_source_overlay(source: str, statuses: tuple[str, ...]) -> dict[str, Any]:
    return {
        "properties": {"source": {"const": source}, "status": {"enum": list(statuses)}},
        "not": {"required": ["reason"]},
        **_records_follow_status(),
    }


def _talk_context_get() -> _QuerySpec:
    record = _object(
        {
            "source": _string(),
            "recordId": _string(),
            "recordedAt": _nullable(_date()),
            "text": _nullable(_string()),
            "truncated": _boolean(),
        }
    )
    background_record = _object(
        {
            "source": _string(),
            "recordId": _string(),
            "recordedAt": _nullable(_date()),
            "reasonField": _string(enum=["wxyy", "qtyy"]),
            "recognitionReasonCode": _nullable(_string()),
            "recordedReason": _nullable(_string()),
            "truncated": _boolean(),
        }
    )
    definitions = {
        **_task_definitions(),
        "dictionaryItem": _object({"code": _string(), "label": _nullable(_string())}),
        "dictionaryMetadata": _object(
            {
                "status": _string(enum=_OPTION_STATUSES),
                "hasMore": _boolean(),
                "limit": _integer(),
            }
        ),
    }
    properties = {
        "person": _object(
            {
                "personId": _string(),
                "source": _string(const="yzgl.dk.jbxx"),
                "recordId": _string(),
                "number": _nullable(_string()),
                "name": _nullable(_string()),
                "jyId": _nullable(_string()),
                "jqId": _nullable(_string()),
                "custodyStatus": _nullable(_string()),
                "educationCode": _nullable(_string()),
                "admissionDate": _nullable(_date()),
            }
        ),
        "purpose": _string(),
        "scenario": _string(),
        "sources": _array(
            _object(
                {
                    "source": _string(enum=list(_TALK_SOURCES)),
                    "status": _string(
                        enum=["AVAILABLE", "EMPTY", "FORBIDDEN", "UNAVAILABLE"]
                    ),
                    "records": _array(record, maxItems=10),
                    "reason": _string(),
                },
                required=["source", "status"],
            )
        ),
        "backgroundSources": _array(
            _object(
                {
                    "source": _string(enum=list(_BACKGROUND_SOURCES)),
                    "status": _string(enum=["AVAILABLE", "EMPTY", "FORBIDDEN"]),
                    "records": _array(background_record, maxItems=10),
                },
                required=["source", "status"],
            )
        ),
        "dictionaryOptions": _object(
            {
                **{
                    key: _array(_ref("dictionaryItem"), maxItems=100)
                    for key in _DICTIONARY_KEYS
                },
                "metadata": _object(
                    {key: _ref("dictionaryMetadata") for key in _DICTIONARY_KEYS}
                ),
            }
        ),
        "officerOptions": _object(
            {
                "options": _array(
                    _object(
                        {
                            "userId": _string(),
                            "name": _nullable(_string()),
                            "departmentName": _nullable(_string()),
                        }
                    ),
                    maxItems=100,
                ),
                "status": _string(enum=_OPTION_STATUSES),
                "limit": _integer(),
                "hasMore": _boolean(),
                "currentUserId": _string(),
            }
        ),
        "savedArtifacts": _array(
            _object(
                {
                    "artifactId": _string(pattern=_HEX32),
                    "kind": _string(enum=["TALK_PREPARATION", "TALK_DRAFT"]),
                    "clientId": _string(),
                    "personId": _string(),
                    "jyId": _nullable(_string()),
                    "jqId": _nullable(_string()),
                    "payload": _opaque(),
                    "canonicalPayload": _string(),
                    "payloadHash": _string(pattern=_HEX64),
                    "operation": _nullable(_string()),
                    "status": _string(enum=["DRAFT", "CONFIRMED", "DONE"]),
                    "expiresAt": _integer(),
                    "reviewPath": _string(),
                    "result": _nullable(_opaque()),
                    "reviewUrl": _string(),
                }
            ),
            maxItems=10,
        ),
        # Complete talk_tasks_list output, or {"status": "FORBIDDEN"} alone.
        "tasks": _object(
            {**_task_list_properties(), "status": _string(const="FORBIDDEN")},
            required=[],
        ),
    }
    overlay = [
        {
            "properties": {
                "sources": {
                    "minItems": len(_TALK_SOURCES),
                    "maxItems": len(_TALK_SOURCES),
                    "prefixItems": [
                        _talk_source_overlay("jygz.gbthjy", ("AVAILABLE", "EMPTY")),
                        _talk_source_overlay(
                            "jygz.wxfth", ("AVAILABLE", "EMPTY", "FORBIDDEN")
                        ),
                        _talk_source_overlay(
                            "jygz.wgfth", ("AVAILABLE", "EMPTY", "FORBIDDEN")
                        ),
                        {
                            "properties": {
                                "source": {"const": "app.gbth"},
                                "status": {"const": "UNAVAILABLE"},
                                "reason": {"const": "PERMISSION_MAPPING_UNCONFIGURED"},
                            },
                            "required": ["reason"],
                            "not": {"required": ["records"]},
                        },
                    ],
                },
                "backgroundSources": {
                    "minItems": len(_BACKGROUND_SOURCES),
                    "maxItems": len(_BACKGROUND_SOURCES),
                    "prefixItems": [
                        {"properties": {"source": {"const": source}}, **_records_follow_status()}
                        for source in _BACKGROUND_SOURCES
                    ],
                },
                "tasks": {
                    "if": {"required": ["status"]},
                    "then": {"maxProperties": 1},
                    "else": {
                        "required": list(_TASK_LIST_FIELDS),
                        "properties": {"sourceReadiness": _source_readiness_overlay()},
                    },
                },
            }
        }
    ]
    return _QuerySpec(
        _root(properties, definitions=definitions, overlay=overlay),
        opaque=frozenset(
            {
                ("savedArtifacts", _A, "payload"),
                ("savedArtifacts", _A, "result"),
                *(("tasks", *path) for path in _TASK_OPAQUE),
            }
        ),
        canonical=frozenset({("savedArtifacts", _A, "canonicalPayload")}),
    )


_GBTHJY_COLUMNS: tuple[RawColumn, ...] = (
    ("ID", _string(), _STATED_NOT_NULL),
    *(
        (name, _string(), _STATED_NULL)
        for name in (
            "JBXX_ID", "JY_ID", "JQ_ID", "TH_RQ", "THDD", "THMJ_ID", "THSX",
            "THXG", "THNR", "SBT", "QTDCYCS", "SFWC", "C_SCBZ",
        )
    ),
)
_ALLOCATION_COLUMNS: tuple[RawColumn, ...] = (
    ("TASK_ID", _string(), _UNSTATED),
    ("RULE_ID", _string(), _UNSTATED),
    ("WEEK_START", _string(), _UNSTATED),
    ("STATE", _string(), _UNSTATED),
    ("VALID_COUNT", _integer(), _UNSTATED),
    ("REMAINING_COUNT", _integer(), _UNSTATED),
    ("EVER_OVERDUE", _integer(enum=[0, 1]), _UNSTATED),
)
_PROVENANCE_FIELDS = (
    "artifactId", "actorId", "claimOwnerId", "taskId", "originalActualNotes",
    "submittedAt", "originalPersonId", "originalJyId", "originalJqId", "reviewUrl",
)


def _talk_record_get() -> _QuerySpec:
    record, _, _ = _raw_row("record", _GBTHJY_COLUMNS)
    allocation, scalars, unstated = _raw_row("taskAllocations", _ALLOCATION_COLUMNS)
    properties = {
        "source": _string(const="jygz.gbthjy"),
        "record": record,
        # AVAILABLE and FORBIDDEN provenance merged; the overlay separates them.
        "provenance": _array(
            _object(
                {
                    "artifactId": _string(pattern=_HEX32),
                    "actorId": _string(),
                    "claimOwnerId": _nullable(_string()),
                    "taskId": _nullable(_string()),
                    "originalActualNotes": _nullable(_string()),
                    "submittedAt": _string(),
                    "originalPersonId": _nullable(_string()),
                    "originalJyId": _nullable(_string()),
                    "originalJqId": _nullable(_string()),
                    "status": _string(enum=["AVAILABLE", "FORBIDDEN"]),
                    "reason": _string(),
                    "reviewUrl": _string(),
                },
                required=["status"],
            )
        ),
        "countingSource": _string(enum=["MCP_CONFIRMED_RECORD", "LEGACY_NOT_AUTO_COUNTED"]),
        "taskAllocations": _array(allocation),
        "taskAllocationsStatus": _string(enum=["AVAILABLE", "FORBIDDEN"]),
    }
    overlay: list[dict[str, Any]] = [
        {
            "properties": {
                "provenance": {
                    "maxItems": 1,
                    "items": {
                        "if": {"properties": {"status": {"const": "AVAILABLE"}}},
                        "then": {
                            "required": list(_PROVENANCE_FIELDS),
                            "not": {"required": ["reason"]},
                        },
                        "else": {
                            "properties": {
                                "reason": {"const": "ORIGINAL_PROVENANCE_SCOPE_DENIED"}
                            },
                            "required": ["reason"],
                            "maxProperties": 2,
                        },
                    },
                }
            }
        },
        # The supplier text allows an absent or empty list without task rights.
        {
            "if": {"properties": {"taskAllocationsStatus": {"const": "AVAILABLE"}}},
            "then": {"required": ["taskAllocations"]},
            "else": {"properties": {"taskAllocations": {"maxItems": 0}}},
        },
    ]
    return _QuerySpec(
        _root(
            properties,
            required=[key for key in properties if key != "taskAllocations"],
            overlay=overlay,
        ),
        scalars=frozenset(scalars),
        unstated=frozenset(unstated),
    )


_TEMPLATE_COLUMNS: tuple[RawColumn, ...] = (
    ("ID", _string(), _UNSTATED),
    ("NAME", _string(), _STATED_NULL),
    ("BATCH_MONTHS", _string(), _STATED_NULL),
)
# Addendum: these named columns carry any finite JSON scalar, type preserved.
_WAREHOUSE_COLUMNS: tuple[RawColumn, ...] = tuple(
    (name, None, _UNSTATED if name == "ID" else _STATED_NULL)
    for name in ("ID", "NAME", "TYPE", "UNIT_ID", "JY_ID", "JQ_ID")
)
_STANDARD_COLUMNS: tuple[RawColumn, ...] = (
    ("ID", None, _UNSTATED),
    ("NAME", None, _STATED_NULL),
    ("GENDER", None, _STATED_NULL),
    ("FIRST_MONTHS", None, _STATED_NULL),
    ("FIRST_QTY", _integer(), _UNSTATED),
    ("CYCLE_YEARS", None, _STATED_NULL),
    ("CYCLE_QTY", _integer(), _UNSTATED),
    ("MIN_HOLDING", None, _STATED_NULL),
    ("STATUS", None, _STATED_NULL),
)
_INVENTORY_COLUMNS: tuple[RawColumn, ...] = (
    ("WAREHOUSE_ID", _string(), _UNSTATED),
    ("CATEGORY_ID", _string(), _UNSTATED),
    ("AVAILABLE", _integer(), _UNSTATED),
)
_HISTORY_COLUMNS: tuple[RawColumn, ...] = (
    ("BATCH_MONTH", _integer(), _UNSTATED),
    ("STATUS", _string(), _UNSTATED),
    ("ROW_COUNT", _integer(), _UNSTATED),
    ("QUANTITY", _integer(), _STATED_NULL),
)


def _clothing_options_get() -> _QuerySpec:
    properties: dict[str, Any] = {}
    scalars: set[ApprovedProjectionPath] = set()
    unstated: set[ApprovedProjectionPath] = set()
    for table, columns in (
        ("templates", _TEMPLATE_COLUMNS),
        ("warehouses", _WAREHOUSE_COLUMNS),
        ("standards", _STANDARD_COLUMNS),
        ("inventory", _INVENTORY_COLUMNS),
        ("history", _HISTORY_COLUMNS),
    ):
        row, row_scalars, row_unstated = _raw_row(table, columns)
        properties[table] = _array(row)
        scalars |= row_scalars
        unstated |= row_unstated
    properties.update(
        {
            "sources": _object(
                {key: _string() for key in ("templates", "standards", "inventory", "history")}
            ),
            "mode": _string(const="CYCLE_ONLY"),
            "shortagePolicy": _string(const="REJECT_ENTIRE_PLAN"),
            "oldLedgerEffect": _string(),
        }
    )
    return _QuerySpec(
        _root(properties), scalars=frozenset(scalars), unstated=frozenset(unstated)
    )


_STOCK_OUT_COLUMNS: tuple[RawColumn, ...] = (
    ("ID", _string(), _UNSTATED),
    ("STATUS", _string(), _STATED_NULL),
    ("C_SCBZ", _string(), _STATED_NULL),
)


def _clothing_result_get() -> _QuerySpec:
    stock_out, scalars, unstated = _raw_row("currentStockOutStatuses", _STOCK_OUT_COLUMNS)
    return _QuerySpec(
        _root(
            {
                "receipt": _object(
                    {
                        "artifactId": _string(pattern=_HEX32),
                        "batchNo": _string(),
                        "rosterIds": _array(_string()),
                        "stockOutIds": _array(_string()),
                        "quantity": _integer(),
                        "submittedAt": _string(),
                        "submittedBy": _string(),
                        "physicalHandover": _string(const="UNCONFIRMED"),
                        "reviewUrl": _nullable(_string()),
                    }
                ),
                "currentStockOutStatuses": _array(stock_out),
            }
        ),
        scalars=frozenset(scalars),
        unstated=frozenset(unstated),
    )


_SPECS: dict[str, Callable[[], _QuerySpec]] = {
    "person_find": _person_find,
    "talk_context_get": _talk_context_get,
    "talk_record_get": _talk_record_get,
    "talk_tasks_list": _talk_tasks_list,
    "clothing_options_get": _clothing_options_get,
    "clothing_result_get": _clothing_result_get,
}


_DIAGNOSTIC_KEYWORDS = frozenset(
    {
        "type", "required", "enum", "const", "pattern", "additionalProperties",
        "minItems", "maxItems", "maxProperties", "not", "anyOf", "prefixItems",
    }
)


def _schema_property_names(node: Any, names: set[str]) -> set[str]:
    if isinstance(node, Mapping):
        properties = node.get("properties")
        if isinstance(properties, Mapping):
            names.update(key for key in properties if isinstance(key, str))
        for child in node.values():
            _schema_property_names(child, names)
    elif isinstance(node, list):
        for child in node:
            _schema_property_names(child, names)
    return names


def _schema_failure_label(schema: Mapping[str, Any], error: ValidationError) -> str:
    """Map a validation error to code-owned schema names; never instance data."""
    names = _schema_property_names(schema, set())
    tokens = list(error.absolute_schema_path)
    parts: list[str] = []
    overlay = bool(tokens) and tokens[0] == "allOf"
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "properties" and index + 1 < len(tokens):
            name = tokens[index + 1]
            if not isinstance(name, str) or name not in names:
                return "unknown_schema_node"
            parts.append(name)
            index += 2
            continue
        if token in {"items", "prefixItems"}:
            parts.append(_A)
            index += 2 if token == "prefixItems" else 1
            continue
        index += 1
    validator, required = error.validator, error.validator_value
    keyword = (
        validator
        if isinstance(validator, str) and validator in _DIAGNOSTIC_KEYWORDS
        else "other"
    )
    if (
        keyword == "required"
        and isinstance(error.instance, dict)
        and isinstance(required, list)
    ):
        missing = next(
            (
                name
                for name in required
                if isinstance(name, str) and name in names and name not in error.instance
            ),
            None,
        )
        if missing is not None:
            parts.append(missing)
    return _node_label(overlay, parts, keyword)


def _node_label(overlay: bool, parts: Sequence[str], keyword: str) -> str:
    node = ".".join(parts) or "root"
    return f"{'overlay:' if overlay else ''}{node}:{keyword}"


_LABEL_KEYWORDS = _DIAGNOSTIC_KEYWORDS | {"other"}
_SCHEMA_CHILD_KEYWORDS = ("not", "if", "then", "else", "contains", "additionalProperties")


def _schema_failure_labels(schema: Mapping[str, Any]) -> set[str]:
    """Every label ``_schema_failure_label`` can derive from one code schema."""
    definitions = schema.get("$defs")
    labels: set[str] = set()

    def visit(node: Any, parts: tuple[str, ...], overlay: bool, refs: frozenset[str]) -> None:
        if not isinstance(node, Mapping):
            return
        labels.update(_node_label(overlay, parts, keyword) for keyword in _LABEL_KEYWORDS)
        required = node.get("required")
        if isinstance(required, list):
            labels.update(
                _node_label(overlay, (*parts, name), "required")
                for name in required
                if isinstance(name, str)
            )
        properties = node.get("properties")
        if isinstance(properties, Mapping):
            for name, child in properties.items():
                if isinstance(name, str):
                    visit(child, (*parts, name), overlay, refs)
        visit(node.get("items"), (*parts, _A), overlay, refs)
        for keyword in ("prefixItems", "anyOf", "allOf"):
            branches = node.get(keyword)
            if not isinstance(branches, list):
                continue
            # Only the root allOf is a validation overlay in the label.
            branch_overlay = overlay or (keyword == "allOf" and node is schema)
            branch_parts = (*parts, _A) if keyword == "prefixItems" else parts
            for child in branches:
                visit(child, branch_parts, branch_overlay, refs)
        for keyword in _SCHEMA_CHILD_KEYWORDS:
            visit(node.get(keyword), parts, overlay, refs)
        reference = node.get("$ref")
        prefix = "#/$defs/"
        if (
            isinstance(reference, str)
            and reference.startswith(prefix)
            and reference not in refs
            and isinstance(definitions, Mapping)
        ):
            target = definitions.get(reference[len(prefix):])
            visit(target, parts, overlay, refs | {reference})

    visit(schema, (), False, frozenset())
    return labels


def _output_failure_reason(schema: Mapping[str, Any], result: dict[str, Any]) -> str:
    structured, blocks = result.get("structuredContent"), result.get("content")
    if (
        not isinstance(structured, dict)
        or not isinstance(blocks, list)
        or not blocks
        or not isinstance(blocks[0], dict)
        or blocks[0].get("type") != "text"
    ):
        return "envelope_shape"
    try:
        decoded = json.loads(blocks[0]["text"])
    except (KeyError, ValueError, TypeError, RecursionError):
        return "text_json_invalid"
    if decoded != structured:
        return "text_mismatch"
    try:
        error = best_match(Draft202012Validator(schema).iter_errors(structured))
    except RecursionError:
        error = None
    if error is None:
        return "schema_mismatch:unknown_schema_node"
    return f"schema_mismatch:{_schema_failure_label(schema, error)}"


_OUTPUT_FAILURE_REASONS = (
    "envelope_shape",
    "text_json_invalid",
    "text_mismatch",
    "unknown_schema_node",
    "schema_mismatch:unknown_schema_node",
)
# Stage -> fixed code used when a reason fails or falls outside the closed set.
_FAILURE_FALLBACKS = {
    "fixed_query_output": "unknown_schema_node",
    "fixed_query_projection": "projection_rejected:unclassified",
    "chat_projection": "projection_rejected:unclassified",
}
FIXED_QUERY_DIAGNOSTIC_STAGES = tuple(_FAILURE_FALLBACKS)


@lru_cache(maxsize=1)
def fixed_query_diagnostic_error_codes() -> Mapping[str, frozenset[str]]:
    """Closed ``error`` labels per fixed-query failure stage.

    Built only from the code factory schemas and the fixed projection reasons;
    never from provider results, instance keys/values or validator messages.
    """
    output = set(_OUTPUT_FAILURE_REASONS)
    for builder in _SPECS.values():
        output.update(
            f"schema_mismatch:{label}" for label in _schema_failure_labels(builder().schema)
        )
    projection = frozenset(
        {f"projection_rejected:{reason}" for reason in get_args(ApprovedProjectionFailure)}
        | {"projection_rejected:unclassified"}
    )
    return MappingProxyType(
        {
            "fixed_query_output": frozenset(output),
            "fixed_query_projection": projection,
            "chat_projection": projection,
        }
    )


def record_fixed_query_failure(
    stage: str,
    reason: Callable[[], str],
    *,
    profile: ServiceConfig | None = None,
    tool: str = "",
    task_id: str = "",
    trace_id: str = "",
    connection_id: str = "",
    started_at: float | None = None,
) -> None:
    """Emit one closed-set failure record; never replaces the caller's exception."""
    try:
        allowed = fixed_query_diagnostic_error_codes().get(stage)
        if allowed is None:
            return
        fallback = _FAILURE_FALLBACKS[stage]
        try:
            code = reason()
        except Exception:
            code = fallback
        record_mcp_event(
            stage,
            "failed",
            profile=profile,
            tool=tool if tool in QUERY_TOOLS else "",
            task_id=task_id,
            trace_id=trace_id,
            connection_id=connection_id,
            error_code=code if code in allowed else fallback,
            started_at=started_at,
        )
    except Exception:
        # Diagnostics are best effort; the business failure keeps propagating.
        return


@dataclass(frozen=True)
class FixedQueryOutputContract(OutputContract):
    """Local OutputContract carrying one tool's frozen projection rules."""

    tool: str = ""
    service_config_id: str = SERVICE_CONFIG_ID
    projection: ApprovedResponseProjection | None = dataclass_field(default=None, repr=False)

    def validate(self, result: dict[str, Any]) -> dict[str, Any]:
        try:
            return super().validate(result)
        except McpFailure as exc:
            if exc.code == "mcp_output_invalid":
                record_fixed_query_failure(
                    "fixed_query_output",
                    lambda: _output_failure_reason(self.schema, result),
                    tool=self.tool,
                )
            raise


def fixed_query_output_contract(tool: str) -> FixedQueryOutputContract:
    """Build one approved read contract from code; never from configuration."""
    builder = _SPECS.get(tool)
    if builder is None:
        raise McpFailure("mcp_output_contract_invalid")
    spec = builder()
    try:
        Draft202012Validator.check_schema(spec.schema)
    except SchemaError:
        raise McpFailure("mcp_output_contract_invalid") from None
    fields = tuple(sorted(spec.schema["properties"]))
    contract = FixedQueryOutputContract(
        version=CONTRACT_VERSION,
        schema=spec.schema,
        model_fields=fields,
        ui_fields=fields,
        persistence_fields=fields,
        approval_evidence=APPROVAL_EVIDENCE,
        synthetic=False,
        tool=tool,
        service_config_id=SERVICE_CONFIG_ID,
    )
    public_schema = contract.public_result_schema()
    # Validation-only overlays must never reach the public projection schema.
    if "allOf" in public_schema["anyOf"][0]:
        raise McpFailure("mcp_output_contract_invalid")
    try:
        projection = ApprovedResponseProjection(
            policy_version=PROJECTION_POLICY_VERSION,
            output_schema_json=canonical_schema_json(public_schema),
            opaque_object_paths=spec.opaque,
            canonical_json_paths=spec.canonical,
            named_scalar_paths=spec.scalars,
            raw_nullable_paths=spec.unstated,
        )
    except ValueError:
        raise McpFailure("mcp_output_contract_invalid") from None
    return replace(contract, projection=projection)


def fixed_query_output_contracts() -> dict[tuple[str, str], FixedQueryOutputContract]:
    """Injection value for the existing ``mcp_contracts`` composition seam."""
    return {(SERVICE_CONFIG_ID, tool): fixed_query_output_contract(tool) for tool in QUERY_TOOLS}


@dataclass(frozen=True)
class _ExpectedContract:
    fields: tuple[str, ...]
    schema_json: str
    projection: ApprovedResponseProjection


@lru_cache(maxsize=None)
def _expected_contract(tool: str) -> _ExpectedContract:
    contract = fixed_query_output_contract(tool)
    assert contract.projection is not None
    return _ExpectedContract(
        contract.model_fields, canonical_schema_json(contract.schema), contract.projection
    )


def approved_fixed_query_projection(
    service_config_id: str, tool: str, contract: object
) -> ApprovedResponseProjection | None:
    """Return the code-owned rules only for an exact factory contract.

    Other contracts receive no exception rights. A fixed-query contract that
    drifted from its factory is rejected rather than downgraded.
    """
    if not isinstance(contract, FixedQueryOutputContract):
        return None
    if service_config_id != SERVICE_CONFIG_ID or tool not in QUERY_TOOLS:
        raise McpFailure("mcp_output_contract_invalid")
    expected = _expected_contract(tool)
    try:
        matching = (
            type(contract) is FixedQueryOutputContract
            and contract.tool == tool
            and contract.service_config_id == service_config_id
            and contract.version == CONTRACT_VERSION
            and contract.approval_evidence == APPROVAL_EVIDENCE
            and not contract.synthetic
            and contract.model_fields == expected.fields
            and contract.ui_fields == expected.fields
            and contract.persistence_fields == expected.fields
            and canonical_schema_json(contract.schema) == expected.schema_json
            and contract.projection == expected.projection
        )
    except (TypeError, ValueError, RecursionError):
        matching = False
    if not matching:
        raise McpFailure("mcp_output_contract_invalid")
    return expected.projection


__all__ = (
    "APPROVAL_EVIDENCE",
    "CONTRACT_VERSION",
    "FIXED_QUERY_DIAGNOSTIC_STAGES",
    "PROJECTION_POLICY_VERSION",
    "QUERY_TOOLS",
    "SERVICE_CONFIG_ID",
    "FixedQueryOutputContract",
    "approved_fixed_query_projection",
    "fixed_query_diagnostic_error_codes",
    "fixed_query_output_contract",
    "fixed_query_output_contracts",
    "record_fixed_query_failure",
)
