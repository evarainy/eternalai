"""Provider input snapshot and explicitly approved output-policy insertion points.

Inputs derive verbatim from tools-list.full.json in the authorized 2026-09-29
contract archive. Output policies are NOT supplied by that archive.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator, FormatChecker

from app.mcp.models import McpFailure, digest

WRITE_TOOLS = frozenset(
    {
        "clothing_plan_preview",
        "clothing_plan_submit",
        "talk_preparation_save",
        "talk_record_draft_save",
        "talk_record_submit",
        "talk_task_claim",
    }
)
SUBMIT_TOOLS = frozenset({"clothing_plan_submit", "talk_record_submit"})
NON_IDEMPOTENT_TOOLS = frozenset(
    {
        "clothing_plan_preview",
        "talk_preparation_save",
        "talk_record_draft_save",
    }
)


def validate_input(tool: str, arguments: dict[str, Any], *, now: datetime) -> None:
    schema = INPUT_SCHEMAS.get(tool)
    if schema is None:
        raise McpFailure("mcp_tool_unregistered")
    if any(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(arguments)):
        raise McpFailure("mcp_input_invalid")
    # JSON Schema integer accepts 1.0; the provider's strict integer contract does not.
    for key, value in arguments.items():
        if schema.get("properties", {}).get(key, {}).get("type") == "integer":
            if type(value) is not int:
                raise McpFailure("mcp_input_invalid")
    for key in ("personIds", "officerIds", "mandatoryTopicCodes"):
        items = arguments.get(key)
        if items is not None and len(items) != len(set(items)):
            raise McpFailure("mcp_input_invalid")
    if tool == "clothing_plan_preview":
        when = date.fromisoformat(arguments["distDate"])
        if (when.year, when.month) != (arguments["year"], arguments["batchMonth"]):
            raise McpFailure("mcp_input_invalid")
    if tool == "talk_record_draft_save":
        when = datetime.fromisoformat(arguments["occurredAt"].replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        if when > now:
            raise McpFailure("mcp_input_invalid")
        if arguments["talkTypeCode"] == "11" and not arguments.get("mandatoryTopicCodes"):
            raise McpFailure("mcp_input_invalid")
        for key, limit in BYTE_LIMITS.items():
            value = arguments.get(key)
            if value is not None:
                text = ",".join(value) if isinstance(value, list) else value
                if len(text.encode("utf-8")) > limit:
                    raise McpFailure("mcp_input_invalid")


@dataclass(frozen=True)
class OutputContract:
    """Trusted code/config policy, never accepted from a provider or model.

    No default schema or allow-all projection. Synthetic policies must only be
    passed to isolated tests; production loading calls require_production().
    """

    version: str
    schema: Mapping[str, Any]
    model_fields: tuple[str, ...]
    ui_fields: tuple[str, ...]
    persistence_fields: tuple[str, ...]
    approval_evidence: str
    synthetic: bool = True
    postcondition: Callable[[dict[str, Any]], bool] = field(repr=False, default=lambda _: False)
    external_confirmation: Callable[[dict[str, Any]], str | None] = field(
        repr=False,
        default=lambda _: None,
    )

    def require_production(self) -> None:
        if self.synthetic or not self.approval_evidence.strip():
            raise McpFailure("mcp_output_contract_unapproved")

    def validate(self, result: dict[str, Any]) -> dict[str, Any]:
        if result.get("isError", False) is not False:
            raise McpFailure("mcp_tool_rejected")
        structured = result.get("structuredContent")
        content = result.get("content")
        if not isinstance(structured, dict) or not isinstance(content, list) or not content:
            raise McpFailure("mcp_output_invalid")
        text = content[0]
        if not isinstance(text, dict) or text.get("type") != "text":
            raise McpFailure("mcp_output_invalid")
        try:
            matching = json.loads(text["text"]) == structured
        except (KeyError, ValueError, TypeError, RecursionError):
            matching = False
        if not matching or any(Draft202012Validator(self.schema).iter_errors(structured)):
            raise McpFailure("mcp_output_invalid")
        return structured

    def project(self, validated: dict[str, Any], purpose: str) -> dict[str, Any]:
        fields = {
            "model": self.model_fields,
            "ui": self.ui_fields,
            "persistence": self.persistence_fields,
        }[purpose]
        return {key: validated[key] for key in fields if key in validated}

    def public_result(self, validated: dict[str, Any]) -> dict[str, Any] | None:
        fields = set(self.model_fields) & set(self.ui_fields) & set(self.persistence_fields)
        result = {key: validated[key] for key in sorted(fields) if key in validated}
        return result or None

    def public_result_schema(self) -> dict[str, Any]:
        from copy import deepcopy

        fields = set(self.model_fields) & set(self.ui_fields) & set(self.persistence_fields)
        properties = self.schema.get("properties", {})
        if not fields:
            return {"type": "null"}
        if not fields <= properties.keys():
            raise McpFailure("mcp_output_contract_invalid")
        result_schema = {
            "type": "object",
            "properties": {key: deepcopy(properties[key]) for key in sorted(fields)},
            "required": [key for key in self.schema.get("required", []) if key in fields],
            "additionalProperties": False,
        }
        # Preserve only definitions reachable from approved fields. Local refs
        # resolve at the schema root, including when this schema is embedded.
        definitions = self.schema.get("$defs", {})
        selected: dict[str, Any] = {}

        def collect(value: Any) -> None:
            if isinstance(value, Mapping):
                reference = value.get("$ref")
                if reference is not None:
                    prefix = "#/$defs/"
                    if not isinstance(reference, str) or not reference.startswith(prefix):
                        raise McpFailure("mcp_output_contract_invalid")
                    name = reference[len(prefix) :]
                    if not name or "/" in name or name not in definitions:
                        raise McpFailure("mcp_output_contract_invalid")
                    if name not in selected:
                        selected[name] = deepcopy(definitions[name])
                        collect(selected[name])
                # Walk schema positions only. Property names and annotation or
                # validation data (examples/default/enum/const) are not schemas.
                for keyword in ("properties", "patternProperties", "dependentSchemas"):
                    children = value.get(keyword)
                    if isinstance(children, Mapping):
                        for child in children.values():
                            collect(child)
                for keyword in (
                    "items", "additionalProperties", "contains", "propertyNames",
                    "unevaluatedProperties", "unevaluatedItems", "not", "if", "then", "else",
                ):
                    collect(value.get(keyword))
                for keyword in ("anyOf", "allOf", "oneOf", "prefixItems"):
                    children = value.get(keyword)
                    if isinstance(children, list):
                        for child in children:
                            collect(child)

        collect(result_schema)
        output: dict[str, Any] = {"anyOf": [result_schema, {"type": "null"}]}
        if selected:
            output["$defs"] = selected
        return output


def input_digest(tool: str) -> str:
    return digest(INPUT_SCHEMAS[tool])


# Byte ceilings are confirmed by the provider's authorized guide, independent
# of JSON Schema's character-count maxLength. Populated from that contract.
BYTE_LIMITS: dict[str, int] = {
    "officerIds": 50,
    "mandatoryTopicCodes": 50,
    "content": 2000,
    "followUp": 2000,
    "locationCode": 50,
    "talkTypeCode": 50,
    "effectCode": 50,
}

# Verbatim schema snapshot follows; generation/provenance receipt is outside Git.
INPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    "business_context_get": {
        "type": "object",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "properties": {},
        "additionalProperties": False,
    },
    "person_find": {
        "type": "object",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "properties": {
            "query": {"type": "string", "minLength": 1, "maxLength": 100, "pattern": "\\S"},
            "page": {"default": 1, "type": "integer", "minimum": 1, "maximum": 1000},
            "pageSize": {"default": 20, "type": "integer", "minimum": 1, "maximum": 20},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    "talk_context_get": {
        "type": "object",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "properties": {
            "personId": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,50}$"},
            "purpose": {"type": "string", "minLength": 1, "maxLength": 200, "pattern": "\\S"},
            "scenario": {"type": "string", "minLength": 1, "maxLength": 100, "pattern": "\\S"},
            "limit": {"default": 5, "type": "integer", "minimum": 1, "maximum": 10},
        },
        "required": ["personId", "purpose", "scenario"],
        "additionalProperties": False,
    },
    "clothing_options_get": {
        "type": "object",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "properties": {
            "jyId": {"type": "string", "minLength": 1, "maxLength": 50, "pattern": "\\S"},
            "jqId": {"type": "string", "minLength": 1, "maxLength": 50, "pattern": "\\S"},
        },
        "required": ["jyId"],
        "additionalProperties": False,
    },
    "clothing_plan_preview": {
        "type": "object",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "properties": {
            "jyId": {"type": "string", "minLength": 1, "maxLength": 50, "pattern": "\\S"},
            "jqId": {"type": "string", "minLength": 1, "maxLength": 50, "pattern": "\\S"},
            "templateId": {"type": "string", "minLength": 1, "maxLength": 50, "pattern": "\\S"},
            "warehouseId": {"type": "string", "minLength": 1, "maxLength": 50, "pattern": "\\S"},
            "year": {"type": "integer", "minimum": 2000, "maximum": 2200},
            "batchMonth": {"type": "integer", "minimum": 1, "maximum": 12},
            "distDate": {
                "type": "string",
                "format": "date",
                "pattern": (
                    "^(?:(?:\\d\\d[2468][048]|\\d\\d[13579][26]|\\d\\d0[48]|[02468"
                    "][048]00|[13579][26]00)-02-29|\\d{4}-(?:(?:0[13578]|1[02"
                    "])-(?:0[1-9]|[12]\\d|3[01])|(?:0[469]|11)-(?:0[1-9]|[12]"
                    "\\d|30)|(?:02)-(?:0[1-9]|1\\d|2[0-8])))$"
                ),
            },
            "personIds": {
                "description": "可选人员子集，不得重复；省略按后台授权范围计算",
                "minItems": 1,
                "maxItems": 10000,
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": 50, "pattern": "\\S"},
            },
        },
        "required": ["jyId", "templateId", "warehouseId", "year", "batchMonth", "distDate"],
        "additionalProperties": False,
    },
    "clothing_plan_submit": {
        "type": "object",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "properties": {
            "artifactId": {
                "type": "string",
                "pattern": "^[a-f0-9]{32}$",
                "description": "后台返回的不可变草稿 ID，32 位小写十六进制",
            },
            "payloadHash": {
                "type": "string",
                "pattern": "^[a-f0-9]{64}$",
                "description": "该草稿的 SHA-256，不得自行重写内容或更换哈希",
            },
        },
        "required": ["artifactId", "payloadHash"],
        "additionalProperties": False,
    },
    "clothing_result_get": {
        "type": "object",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "properties": {
            "artifactId": {
                "type": "string",
                "pattern": "^[a-f0-9]{32}$",
                "description": "后台返回的不可变草稿 ID，32 位小写十六进制",
            }
        },
        "required": ["artifactId"],
        "additionalProperties": False,
    },
    "talk_preparation_save": {
        "type": "object",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "properties": {
            "personId": {"type": "string", "minLength": 1, "maxLength": 50, "pattern": "\\S"},
            "taskId": {
                "type": "string",
                "pattern": "^[a-f0-9]{32}$",
                "description": "后台返回的不可变草稿 ID，32 位小写十六进制",
            },
            "purpose": {"type": "string", "minLength": 1, "maxLength": 200, "pattern": "\\S"},
            "scenario": {"type": "string", "minLength": 1, "maxLength": 100, "pattern": "\\S"},
            "durationMinutes": {"type": "integer", "minimum": 1, "maximum": 480},
            "outline": {"type": "string", "minLength": 1, "maxLength": 16000, "pattern": "\\S"},
            "sourceRefs": {
                "maxItems": 30,
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "source": {
                            "type": "string",
                            "enum": [
                                "yzgl.dk.jbxx",
                                "jygz.gbthjy",
                                "jygz.wxfrd",
                                "jygz.wgfrd",
                                "jygz.wxfth",
                                "jygz.wgfth",
                            ],
                        },
                        "recordId": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 50,
                            "pattern": "\\S",
                        },
                    },
                    "required": ["source", "recordId"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["personId", "purpose", "scenario", "outline"],
        "additionalProperties": False,
    },
    "talk_record_draft_save": {
        "type": "object",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "properties": {
            "personId": {"type": "string", "minLength": 1, "maxLength": 50, "pattern": "\\S"},
            "taskId": {
                "type": "string",
                "pattern": "^[a-f0-9]{32}$",
                "description": "后台返回的不可变草稿 ID，32 位小写十六进制",
            },
            "occurredAt": {
                "type": "string",
                "maxLength": 40,
                "pattern": (
                    "^(?:(?:\\d\\d[2468][048]|\\d\\d[13579][26]|\\d\\d0[48]|[02468"
                    "][048]00|[13579][26]00)-02-29|\\d{4}-(?:(?:0[13578]|1[02"
                    "])-(?:0[1-9]|[12]\\d|3[01])|(?:0[469]|11)-(?:0[1-9]|[12]"
                    "\\d|30)|(?:02)-(?:0[1-9]|1\\d|2[0-8])))T(?:(?:[01]\\d|2[0-"
                    "3]):[0-5]\\d:[0-5]\\d(?:\\.\\d+)?(?:Z|([+-](?:[01]\\d|2[0-3]"
                    "):[0-5]\\d))|(?:[01]\\d|2[0-3]):[0-5]\\d(?::[0-5]\\d(?:\\.\\d"
                    "+)?)?)$"
                ),
            },
            "locationCode": {"type": "string", "minLength": 1, "maxLength": 50, "pattern": "\\S"},
            "officerIds": {
                "minItems": 1,
                "maxItems": 10,
                "type": "array",
                "items": {"type": "string", "pattern": "^\\d{1,19}$"},
            },
            "talkTypeCode": {"type": "string", "minLength": 1, "maxLength": 50, "pattern": "\\S"},
            "effectCode": {"type": "string", "minLength": 1, "maxLength": 50, "pattern": "\\S"},
            "content": {"type": "string", "minLength": 1, "maxLength": 2000, "pattern": "\\S"},
            "actualNotes": {"type": "string", "minLength": 1, "maxLength": 16000, "pattern": "\\S"},
            "mandatoryTopicCodes": {
                "minItems": 1,
                "maxItems": 30,
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": 20, "pattern": "\\S"},
            },
            "followUp": {"type": "string", "minLength": 1, "maxLength": 2000, "pattern": "\\S"},
        },
        "required": [
            "personId",
            "occurredAt",
            "locationCode",
            "officerIds",
            "talkTypeCode",
            "effectCode",
            "content",
            "actualNotes",
        ],
        "additionalProperties": False,
    },
    "talk_record_submit": {
        "type": "object",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "properties": {
            "artifactId": {
                "type": "string",
                "pattern": "^[a-f0-9]{32}$",
                "description": "后台返回的不可变草稿 ID，32 位小写十六进制",
            },
            "payloadHash": {
                "type": "string",
                "pattern": "^[a-f0-9]{64}$",
                "description": "该草稿的 SHA-256，不得自行重写内容或更换哈希",
            },
        },
        "required": ["artifactId", "payloadHash"],
        "additionalProperties": False,
    },
    "talk_record_get": {
        "type": "object",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "properties": {
            "recordId": {"type": "string", "minLength": 1, "maxLength": 50, "pattern": "\\S"}
        },
        "required": ["recordId"],
        "additionalProperties": False,
    },
    "talk_tasks_list": {
        "type": "object",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "properties": {
            "view": {"default": "pool", "type": "string", "enum": ["pool", "mine", "all"]},
            "page": {"default": 1, "type": "integer", "minimum": 1, "maximum": 100000},
            "pageSize": {"default": 20, "type": "integer", "minimum": 1, "maximum": 100},
            "personId": {"type": "string", "minLength": 1, "maxLength": 50, "pattern": "\\S"},
        },
        "additionalProperties": False,
    },
    "talk_task_claim": {
        "type": "object",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "properties": {
            "taskId": {
                "type": "string",
                "pattern": "^[a-f0-9]{32}$",
                "description": "后台返回的不可变草稿 ID，32 位小写十六进制",
            },
            "version": {"type": "integer", "minimum": 1, "maximum": 2147483647},
        },
        "required": ["taskId", "version"],
        "additionalProperties": False,
    },
}


# Exact annotations from the authorized tools-list.full.json snapshot.
SAFETY_ANNOTATIONS: dict[str, dict[str, bool]] = {
    "business_context_get": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "person_find": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "talk_context_get": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "clothing_options_get": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "clothing_plan_preview": {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "clothing_plan_submit": {
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "clothing_result_get": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "talk_preparation_save": {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "talk_record_draft_save": {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "talk_record_submit": {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "talk_record_get": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "talk_tasks_list": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "talk_task_claim": {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
}


def safety_digest(tool: str) -> str:
    return digest(SAFETY_ANNOTATIONS[tool])
