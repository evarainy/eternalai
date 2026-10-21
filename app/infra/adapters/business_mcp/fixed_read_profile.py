"""One explicitly approved account and business_context_get profile.

Successful fields follow the approved supplier guide, SHA256
735c67a0b1090f98f8f294b7fb979a361ee5b0ae769d090d9231b4e8eca181ea.
Identity binding requires either an approved expected subject or an explicit
OAuth-owner policy; neither mode infers approval evidence.
"""

from __future__ import annotations

import json
import ssl
from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime
from hashlib import sha256
from threading import Lock
from typing import Any, Literal

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.infra.adapters.business_mcp.catalog import catalog
from app.infra.adapters.business_mcp.fixed_query_contracts import (
    QUERY_TOOLS as QUERY_TOOLS,
)
from app.infra.adapters.business_mcp.fixed_query_contracts import (
    SERVICE_CONFIG_ID as SERVICE_CONFIG_ID,
)
from app.infra.adapters.business_mcp.fixed_query_contracts import (
    approved_fixed_query_projection,
)
from app.infra.mcp.driver import McpDriver
from app.infra.sdui.response_projection import (
    project_approved_response_data,
    project_response_data,
    schema_has_credential_property,
)
from app.mcp.contracts import (
    OutputContract,
    input_digest,
    project_public_result_schema,
    safety_digest,
)
from app.mcp.diagnostics import record_mcp_event
from app.mcp.models import McpFailure, ServiceConfig, ToolBinding, digest
from app.ports.auth import (
    AuthenticatedSessionContext,
    SessionRevocationStorePort,
    authenticated_session,
)
from app.ports.capability_registry import CapabilityRegistryPort, CapabilitySpec
from app.ports.mcp_store import AuthorizationTransaction, IdentityAssertionPort, McpStorePort
from app.ports.request_context import RequestOrgContext
from app.ports.response_projection_contract import (
    ARRAY_ITEM,
    ApprovedProjectionPath,
    ApprovedResponseProjection,
)

TOOL = "business_context_get"
RESOURCE = "https://34.64.8.101/mcp"


def fixed_read_display_fields() -> dict[str, str]:
    return {"timezone": "业务时区", "capabilities": "平台可用工具（仅展示）"}


def business_context_success_schema() -> dict[str, Any]:
    """Actual supplier types; additional private fields never become public."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "userId": {"type": "string", "pattern": "^[0-9]+$"},
            "displayName": {"type": "string"},
            "clientId": {"type": "string", "minLength": 1},
            "adapterClientId": {"type": "string", "const": "controlled-agent"},
            "resource": {"type": "string", "const": RESOURCE},
            "timezone": {"type": "string", "const": "Asia/Shanghai"},
            "capabilities": {
                "type": "array",
                "items": {"type": "string"},
                "contains": {"const": TOOL},
            },
            "organization": {
                "type": "object",
                "properties": {
                    "departmentId": {"type": ["string", "null"]},
                    "departmentName": {"type": ["string", "null"]},
                    "departmentType": {"type": ["string", "null"]},
                    "allOrganizations": {"type": "boolean"},
                    "jyId": {"type": ["string", "null"]},
                    "jqPrefix": {"type": ["string", "null"]},
                },
                "required": [
                    "departmentId",
                    "departmentName",
                    "departmentType",
                    "allOrganizations",
                    "jyId",
                    "jqPrefix",
                ],
            },
        },
        "required": [
            "userId",
            "displayName",
            "clientId",
            "adapterClientId",
            "resource",
            "timezone",
            "capabilities",
            "organization",
        ],
    }


def _fixed_read_display_schema(public_schema: dict[str, Any]) -> dict[str, Any]:
    """Drop one validator-only keyword from this profile's display schema."""
    public_schema["anyOf"][0]["properties"].get("capabilities", {}).pop("contains", None)
    return public_schema


class _FixedReadOutputContract(OutputContract):
    def validate(self, result: dict[str, Any]) -> dict[str, Any]:
        try:
            return super().validate(result)
        except McpFailure as exc:
            if exc.code != "mcp_output_invalid":
                raise
            stage = "identity_output_envelope"
            data, blocks = result.get("structuredContent"), result.get("content")
            if isinstance(data, dict) and isinstance(blocks, list) and blocks:
                block = blocks[0]
                stage = "identity_output_content"
                if isinstance(block, dict) and block.get("type") == "text":
                    stage = "identity_output_text"
                    try:
                        matching = json.loads(block["text"]) == data
                    except (KeyError, ValueError, TypeError, RecursionError):
                        matching = False
                    if matching:
                        stage = "identity_output_schema"
                        error = next(Draft202012Validator(self.schema).iter_errors(data), None)
                        if error is not None:
                            fields = {
                                "userId",
                                "displayName",
                                "clientId",
                                "adapterClientId",
                                "resource",
                                "timezone",
                                "capabilities",
                                "organization",
                                "departmentId",
                                "departmentName",
                                "departmentType",
                                "allOrganizations",
                                "jyId",
                                "jqPrefix",
                            }
                            field = next(
                                (
                                    p.lower()
                                    for p in reversed(tuple(error.absolute_schema_path))
                                    if p in fields
                                ),
                                "root",
                            )
                            keyword = (
                                error.validator
                                if error.validator
                                in {
                                    "required",
                                    "type",
                                    "const",
                                    "pattern",
                                    "minLength",
                                    "contains",
                                }
                                else "other"
                            )
                            if keyword == "required" and isinstance(error.instance, dict):
                                field = next(
                                    (
                                        p.lower()
                                        for p in error.validator_value
                                        if p in fields and p not in error.instance
                                    ),
                                    field,
                                )
                            stage += f"_{field}_{keyword.lower()}"
                            if field == "departmentid" and keyword == "type":
                                actual = error.instance
                                kind = {
                                    str: "string",
                                    bool: "boolean",
                                    dict: "object",
                                    list: "array",
                                }.get(type(actual), "other")
                                stage += f"_{kind}"
                                if isinstance(actual, str):
                                    stage += (
                                        "_digits"
                                        if actual and actual.isascii() and actual.isdecimal()
                                        else "_other"
                                    )
            record_mcp_event(stage, "failed", tool=TOOL, error_code="mcp_output_invalid")
            raise

    def public_result_schema(self) -> dict[str, Any]:
        return _fixed_read_display_schema(super().public_result_schema())


class FixedReadApproval(BaseModel):
    """Non-secret, owner-confirmed contract; never built from a model response."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    service_config_id: str
    service_config_version: int = Field(ge=1)
    tenant_id: str
    user_id: str | None = Field(default=None, min_length=1, repr=False)
    local_owner_source: Literal["fixed", "authenticated_session"] = "fixed"
    version: str = Field(min_length=1, max_length=100)
    approval_evidence: str = Field(min_length=1, repr=False)
    issuer_parameter_supported: bool
    subject_path: list[str] = Field(default_factory=lambda: ["userId"], repr=False)
    identity_binding_mode: Literal["expected_subject", "oauth_owner"] = "expected_subject"
    expected_subject: str | None = Field(default=None, pattern="^[0-9]+$", repr=False)
    success_schema: dict[str, Any] = Field(
        default_factory=business_context_success_schema, repr=False
    )
    # Labels and field names are approved configuration, not upstream captions.
    display_fields: dict[str, str] = Field(default_factory=fixed_read_display_fields)

    @model_validator(mode="after")
    def binding_mode_has_one_authority(self) -> FixedReadApproval:
        if (self.identity_binding_mode == "expected_subject") != (
            self.expected_subject is not None
        ) or (self.local_owner_source == "fixed") != (self.user_id is not None):
            raise ValueError("mcp_fixed_profile_invalid")
        if self.local_owner_source == "authenticated_session" and (
            self.identity_binding_mode != "oauth_owner"
            or self.service_config_id != SERVICE_CONFIG_ID
            or self.tenant_id != "default"
        ):
            raise ValueError("mcp_fixed_profile_invalid")
        return self


@dataclass(frozen=True)
class PreparedFixedReadDefinition:
    """Code-owned disabled metadata; carries no identity or approval evidence."""

    service: ServiceConfig
    success_schema: dict[str, Any]
    display_fields: dict[str, str]
    capability: CapabilitySpec
    binding: ToolBinding


@dataclass(frozen=True)
class ApprovedQueryRead:
    tool: str
    output_contract: OutputContract
    capability: CapabilitySpec
    binding: ToolBinding
    # Frozen code-owned rules; None for contracts without exception rights.
    projection: ApprovedResponseProjection | None = None


_QUERY_PRESENTATION = {
    "person_find": ("查找人员", "按用户提供的条件查询已授权人员", "person_lookup"),
    "talk_context_get": ("查询谈话上下文", "查询已授权人员的谈话上下文", "talk_context"),
    "talk_record_get": ("查询谈话记录", "查询已授权谈话记录", "talk_record"),
    "talk_tasks_list": ("查询谈话任务", "查询已授权谈话任务列表", "talk_tasks"),
    "clothing_options_get": ("查询被服选项", "查询已授权被服选项", "clothing_options"),
    "clothing_result_get": ("查询被服结果", "查询已授权被服结果", "clothing_result"),
}

# Display labels only translate names: every approved field is shown, unknown
# keys keep their original name. Paths use "." for objects and "[]" for items;
# a path entry wins over a bare field-name entry.
_QUERY_COMMON_LABELS = {
    "personId": "人员ID",
    "number": "人员编号",
    "name": "姓名",
    "jyId": "监狱ID",
    "jqId": "监区ID",
    "page": "页码",
    "pageSize": "每页条数",
    "hasMore": "是否还有结果",
    "source": "来源",
    "recordId": "记录ID",
    "status": "状态",
    "reason": "原因",
    "artifactId": "单据ID",
    "submittedAt": "提交时间",
    "reviewUrl": "查看链接",
    "ruleId": "规则ID",
    "effectiveFrom": "生效日期",
    "limit": "条数上限",
}
_TALK_TASKS_FIELD_LABELS = {
    "weekStart": "本周开始日期",
    "deadline": "截止时间",
    "required": "要求次数",
    "valid": "已计次数",
    "remaining": "剩余次数",
    "state": "状态",
    "version": "版本",
    "assigneeId": "领取人ID",
    "prepared": "是否已保存准备提纲",
    "preparationArtifactId": "准备单据ID",
    "everOverdue": "是否曾逾期",
    "ruleSnapshot": "规则快照",
    "ruleVersion": "规则版本",
    "classification": "分类",
    "minimum": "最低次数",
    "talkTypeCodes": "谈话类型代码",
    "eligibleOfficerIds": "可领取民警ID",
    "allowSharedRecord": "是否允许共用记录",
    "effectiveTo": "失效日期",
    "enabled": "是否启用",
    "lifecyclePolicy": "生命周期策略",
    "sourceIds": "来源ID",
    "total": "总数",
    "rules": "规则",
    "latestConfig": "最新配置",
    "currentVersion": "当前版本",
    "currentStatus": "当前状态",
    "scheduledVersions": "计划生效版本",
    "sourceReadiness": "来源就绪情况",
    "unknownDateRecordCount": "日期不明记录数",
    "configurationStatus": "配置状态",
    "schedulerEnabled": "是否启用自动调度",
    "timezone": "时区",
}
# Relative to a talk_tasks_list result; talk_context_get nests it under "tasks".
_TALK_TASKS_PATH_LABELS = {
    "tasks": "任务列表",
    "tasks[].id": "任务ID",
    "tasks[].reason": "不适用原因",
    "rules[].id": "规则ID",
    "rules[].version": "最新版本",
    "rules[].config": "当前配置",
    "rules[].scheduledVersions[].config": "配置",
}
_QUERY_STATUS_LABELS = {
    "AVAILABLE": "可用",
    "EMPTY": "无记录",
    "FORBIDDEN": "无权限",
    "UNAVAILABLE": "暂不可用",
    "TRUNCATED": "部分结果",
    "DRAFT": "草稿",
    "CONFIRMED": "已确认",
    "DONE": "已完成",
    "PENDING": "待处理",
    "PENDING_EFFECTIVE": "待生效",
}
_TALK_TASKS_ENUMS: dict[str, Mapping[str, str]] = {
    "sourceReadiness[].status": _QUERY_STATUS_LABELS,
    "rules[].currentStatus": _QUERY_STATUS_LABELS,
    "configurationStatus": {"CONFIGURED": "已配置", "UNCONFIGURED": "未配置"},
}
_DICTIONARY_KEYS = ("thsx", "thdd", "thxg", "sbt", "thnrzt")


def _nested_paths(prefix: str, table: Mapping[str, Any]) -> dict[str, Any]:
    return {f"{prefix}.{path}": value for path, value in table.items()}


_QUERY_LABELS: dict[str, dict[str, str]] = {
    "person_find": {**_QUERY_COMMON_LABELS, "items": "匹配人员"},
    "talk_context_get": {
        **_QUERY_COMMON_LABELS,
        **_TALK_TASKS_FIELD_LABELS,
        **_nested_paths("tasks", _TALK_TASKS_PATH_LABELS),
        "person": "人员信息",
        "custodyStatus": "在押状态",
        "educationCode": "文化程度码",
        "admissionDate": "入监日期",
        "purpose": "目的",
        "scenario": "场景",
        "sources": "资料来源",
        "backgroundSources": "背景资料来源",
        "records": "记录",
        "recordedAt": "记录日期",
        "text": "谈话文本",
        "truncated": "是否截断",
        "reasonField": "理由字段",
        "recognitionReasonCode": "认定原因码",
        "recordedReason": "认定理由",
        "dictionaryOptions": "字典选项",
        "thsx": "谈话事项",
        "thdd": "谈话地点",
        "thxg": "谈话效果",
        "sbt": "备注",
        "thnrzt": "谈话内容主题",
        "code": "代码",
        "label": "名称",
        "metadata": "字典状态",
        "officerOptions": "民警选项",
        "officerOptions.options": "民警列表",
        "userId": "民警用户ID",
        "departmentName": "部门名称",
        "currentUserId": "当前用户ID",
        "savedArtifacts": "已保存单据",
        "kind": "单据类型",
        "clientId": "客户端ID",
        "payload": "单据参数",
        "canonicalPayload": "规范参数文本",
        "payloadHash": "参数摘要",
        "operation": "操作",
        "expiresAt": "过期时间",
        "reviewPath": "查看路径",
        "result": "结果",
        "tasks": "谈话任务",
    },
    "talk_record_get": {
        **_QUERY_COMMON_LABELS,
        "record": "谈话记录",
        "record.ID": "记录ID",
        "JBXX_ID": "人员ID",
        "JY_ID": "监狱ID",
        "JQ_ID": "监区ID",
        "TH_RQ": "谈话日期",
        "THDD": "谈话地点",
        "THMJ_ID": "谈话民警ID",
        "THSX": "谈话事项",
        "THXG": "谈话效果",
        "THNR": "谈话内容",
        "SBT": "备注",
        "QTDCYCS": "其他次数",
        "SFWC": "完成标志",
        "C_SCBZ": "删除标志",
        "provenance": "提交溯源",
        "actorId": "操作人ID",
        "claimOwnerId": "领取人ID",
        "taskId": "任务ID",
        "originalActualNotes": "原始纪要",
        "originalPersonId": "原人员ID",
        "originalJyId": "原监狱ID",
        "originalJqId": "原监区ID",
        "countingSource": "计次来源",
        "taskAllocations": "任务计次分配",
        "TASK_ID": "任务ID",
        "RULE_ID": "规则ID",
        "WEEK_START": "周开始时间",
        "STATE": "状态",
        "VALID_COUNT": "已计次数",
        "REMAINING_COUNT": "剩余次数",
        "EVER_OVERDUE": "是否曾逾期",
        "taskAllocationsStatus": "任务计次分配状态",
    },
    "talk_tasks_list": {
        **_QUERY_COMMON_LABELS,
        **_TALK_TASKS_FIELD_LABELS,
        **_TALK_TASKS_PATH_LABELS,
    },
    "clothing_options_get": {
        **_QUERY_COMMON_LABELS,
        "templates": "模板",
        "templates[].ID": "模板ID",
        "NAME": "名称",
        "BATCH_MONTHS": "发放月份",
        "warehouses": "仓库",
        "warehouses[].ID": "仓库ID",
        "TYPE": "类型",
        "UNIT_ID": "单位ID",
        "JY_ID": "监狱ID",
        "JQ_ID": "监区ID",
        "standards": "发放标准",
        "standards[].ID": "标准ID",
        "GENDER": "性别",
        "FIRST_MONTHS": "首次月份",
        "FIRST_QTY": "首次数量",
        "CYCLE_YEARS": "周期年数",
        "CYCLE_QTY": "周期数量",
        "MIN_HOLDING": "最低持有量",
        "STATUS": "状态",
        "inventory": "库存",
        "WAREHOUSE_ID": "仓库ID",
        "CATEGORY_ID": "类别ID",
        "AVAILABLE": "可用数量",
        "history": "历史发放",
        "BATCH_MONTH": "发放月份",
        "ROW_COUNT": "记录数",
        "QUANTITY": "数量",
        "sources": "来源",
        "mode": "发放模式",
        "shortagePolicy": "缺货处理",
        "oldLedgerEffect": "旧台账说明",
    },
    "clothing_result_get": {
        **_QUERY_COMMON_LABELS,
        "receipt": "回执",
        "batchNo": "发放批次号",
        "rosterIds": "发放名册ID",
        "stockOutIds": "出库单ID",
        "quantity": "发放总件数",
        "submittedBy": "提交人ID",
        "physicalHandover": "实物交接",
        "currentStockOutStatuses": "当前出库状态",
        "currentStockOutStatuses[].ID": "出库单ID",
        "STATUS": "状态",
        "C_SCBZ": "删除标志",
    },
}
# Known codes are translated only at these exact paths; raw STATUS/STATE,
# free text and unknown codes keep their original value.
_QUERY_ENUMS: dict[str, dict[str, Mapping[str, str]]] = {
    "talk_context_get": {
        "sources[].status": _QUERY_STATUS_LABELS,
        "backgroundSources[].status": _QUERY_STATUS_LABELS,
        **{
            f"{base}.{key}.status": _QUERY_STATUS_LABELS
            for base in ("metadata", "dictionaryOptions.metadata")
            for key in _DICTIONARY_KEYS
        },
        "officerOptions.status": _QUERY_STATUS_LABELS,
        "savedArtifacts[].status": _QUERY_STATUS_LABELS,
        "tasks.status": _QUERY_STATUS_LABELS,
        **_nested_paths("tasks", _TALK_TASKS_ENUMS),
    },
    "talk_record_get": {
        "provenance[].status": _QUERY_STATUS_LABELS,
        "taskAllocationsStatus": _QUERY_STATUS_LABELS,
        "countingSource": {
            "MCP_CONFIRMED_RECORD": "由MCP确认记录计次",
            "LEGACY_NOT_AUTO_COUNTED": "历史记录不自动计次",
        },
    },
    "talk_tasks_list": _TALK_TASKS_ENUMS,
    "clothing_options_get": {
        "mode": {"CYCLE_ONLY": "按周期发放"},
        "shortagePolicy": {"REJECT_ENTIRE_PLAN": "整单拒绝"},
    },
    "clothing_result_get": {"receipt.physicalHandover": {"UNCONFIRMED": "未确认"}},
}
_QUERY_EMPTY_LISTS: dict[str, dict[str, str]] = {
    "person_find": {"items": "本页未返回匹配人员"},
    "talk_context_get": {"tasks.tasks": "本页未返回任务", "savedArtifacts": "本次未返回已保存单据"},
    "talk_tasks_list": {"tasks": "本页未返回任务"},
}


def _query_scalar(tool: str, path: str, value: Any, indent: str) -> str:
    if value is None:
        return "未提供"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, str):
        known = _QUERY_ENUMS.get(tool, {}).get(path)
        if known is not None and value in known:
            return known[value]
        return value.replace("\n", "\n" + indent) if value else "（空）"
    # Numbers keep their exact text, including 0 and 0.0.
    return str(value)


def _query_value(tool: str, path: str, value: Any, depth: int) -> tuple[str, list[str]]:
    """Return the text after a label and the indented lines below it."""
    pad = "  " * (depth + 1)
    if isinstance(value, Mapping):
        if not value:
            return "未返回任何字段", []
        labels = _QUERY_LABELS.get(tool, {})
        lines: list[str] = []
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            label = labels.get(child_path) or labels.get(str(key)) or str(key)
            head, rest = _query_value(tool, child_path, child, depth + 1)
            lines.append(f"{pad}{label}：{head}")
            lines += rest
        return "", lines
    if isinstance(value, list):
        if not value:
            return _QUERY_EMPTY_LISTS.get(tool, {}).get(path, "本次未返回记录"), []
        lines = []
        for number, item in enumerate(value, 1):
            head, rest = _query_value(tool, f"{path}[]", item, depth + 1)
            if not head and rest:
                # An object's first field shares its item number line.
                head, rest = rest[0].lstrip(" "), rest[1:]
            lines.append(f"{pad}{number}. {head}")
            lines += rest
        return f"本次返回 {len(value)} 项", lines
    return _query_scalar(tool, path, value, pad + "  "), []


def _approved_query_public_schema(
    contract: OutputContract,
    projection: ApprovedResponseProjection | None = None,
) -> dict[str, Any]:
    """Require named public fields at every depth before a read can be exposed.

    Only the exact paths frozen in a factory projection stop this expansion;
    their node shapes are checked by the shared projector itself.
    """
    contract.require_production()
    try:
        Draft202012Validator.check_schema(contract.schema)
        public_schema = contract.public_result_schema()
        Draft202012Validator.check_schema(public_schema)
        if schema_has_credential_property(public_schema):
            raise McpFailure("mcp_output_contract_invalid")
        # This also checks that the existing recursive projector supports every
        # structural keyword, including references and nullable branches.
        if project_response_data({}, public_schema, approved=projection) is None:
            raise McpFailure("mcp_output_contract_invalid")
        definitions = public_schema.get("$defs", {})

        def named_fields_only(
            node: Mapping[str, Any],
            references: frozenset[str],
            path: ApprovedProjectionPath,
        ) -> None:
            if projection is not None and projection.kind_at(path) is not None:
                return
            reference = node.get("$ref")
            if reference is not None:
                prefix = "#/$defs/"
                if not isinstance(reference, str) or not reference.startswith(prefix):
                    raise McpFailure("mcp_output_contract_invalid")
                name = reference[len(prefix) :]
                target = definitions.get(name)
                if name in references or not isinstance(target, Mapping):
                    raise McpFailure("mcp_output_contract_invalid")
                named_fields_only(target, references | {name}, path)
                return
            branches = node.get("anyOf")
            if branches is not None:
                if not isinstance(branches, list):
                    raise McpFailure("mcp_output_contract_invalid")
                for branch in branches:
                    named_fields_only(branch, references, path)
                return
            kind = node.get("type")
            if kind == "object":
                properties = node.get("properties")
                if (
                    not isinstance(properties, Mapping)
                    or not properties
                    or node.get("additionalProperties") not in (None, False)
                ):
                    raise McpFailure("mcp_output_contract_invalid")
                for name, child in properties.items():
                    named_fields_only(child, references, (*path, name))
            elif kind == "array":
                named_fields_only(node["items"], references, (*path, ARRAY_ITEM))
            elif kind not in {"string", "boolean", "null", "integer", "number"}:
                raise McpFailure("mcp_output_contract_invalid")

        named_fields_only(public_schema, frozenset(), ())
        return public_schema
    except McpFailure:
        raise
    except (KeyError, TypeError, ValueError, RecursionError):
        raise McpFailure("mcp_output_contract_invalid") from None


def _approved_query_reads(
    service: ServiceConfig,
    approval: FixedReadApproval,
    contracts: Mapping[str, OutputContract],
) -> dict[str, ApprovedQueryRead]:
    if set(contracts) - set(QUERY_TOOLS):
        raise McpFailure("mcp_output_contract_invalid")
    approved: dict[str, ApprovedQueryRead] = {}
    for tool in QUERY_TOOLS:
        contract = contracts.get(tool)
        if contract is None:
            continue
        if not isinstance(contract, OutputContract):
            raise McpFailure("mcp_output_contract_invalid")
        projection = approved_fixed_query_projection(service.service_config_id, tool, contract)
        public_schema = _approved_query_public_schema(contract, projection)
        capabilities, bindings = catalog(service, {tool: contract})
        capability_id = f"business.{service.service_config_id}.{tool}"
        prepared = next(item for item in capabilities if item.capability_id == capability_id)
        binding = next(item for item in bindings if item.capability_id == capability_id)
        name, description, intent = _QUERY_PRESENTATION[tool]
        policy: list[Any] = [
            approval.version,
            approval.approval_evidence,
            contract.version,
            contract.approval_evidence,
            service.service_config_id,
            tool,
        ]
        if projection is not None:
            # Any change to validation, public schema or a frozen path rule
            # yields a different candidate policy.
            policy.append(
                {
                    "validation_schema_digest": digest(dict(contract.schema)),
                    "public_schema_digest": digest(public_schema),
                    "projection": projection.digest_material(),
                }
            )
        capability = CapabilitySpec.model_validate(
            {
                **prepared.model_dump(),
                "name": name,
                "short_description": description,
                "intent_tags": [tool, intent],
                "output_schema": public_schema,
                "output_schema_digest": digest(public_schema),
                "policy_digest": digest(policy),
                "status": "disabled",
            }
        )
        approved[tool] = ApprovedQueryRead(tool, contract, capability, binding, projection)
    return approved


@dataclass(frozen=True, repr=False)
class FixedReadLocalOwner:
    """Local identity obtained only from the verified HTTP session context."""

    tenant_id: str
    user_id: str
    fingerprint: bytes = dataclass_field(repr=False)
    expires_at: datetime

    @classmethod
    def from_session(cls, session: AuthenticatedSessionContext) -> FixedReadLocalOwner:
        if session.expires_at <= datetime.now(UTC) or len(session.fingerprint) != 32:
            raise McpFailure("mcp_service_unavailable")
        return cls(
            tenant_id=session.principal.org_ctx.tenant_id,
            user_id=session.principal.ai_user_id,
            fingerprint=session.fingerprint,
            expires_at=session.expires_at,
        )


def prepare_fixed_read_definition(service: ServiceConfig) -> PreparedFixedReadDefinition:
    if (
        service.protocol_version != "2025-11-25"
        or service.endpoint != RESOURCE
        or service.resource != RESOURCE
    ):
        raise McpFailure("mcp_fixed_profile_invalid")
    schema = business_context_success_schema()
    try:
        Draft202012Validator.check_schema(schema)
    except Exception:
        raise McpFailure("mcp_output_contract_invalid") from None
    display_fields = fixed_read_display_fields()
    public_schema = _fixed_read_display_schema(
        project_public_result_schema(schema, set(display_fields))
    )
    if schema_has_credential_property(public_schema):
        raise McpFailure("mcp_output_contract_invalid")
    capabilities, bindings = catalog(service, {})
    capability_id = f"business.{service.service_config_id}.{TOOL}"
    capability = next(c for c in capabilities if c.capability_id == capability_id)
    binding = next(b for b in bindings if b.capability_id == capability_id)
    capability = CapabilitySpec.model_validate(
        {
            **capability.model_dump(),
            "name": "查询当前业务信息",
            "short_description": "读取本人已授权业务平台的当前业务信息",
            "intent_tags": ["business_context", "current_business", "business_context_get"],
            "output_schema": public_schema,
            "output_schema_digest": digest(public_schema),
            "status": "disabled",
        }
    )
    return PreparedFixedReadDefinition(service, schema, display_fields, capability, binding)


@dataclass
class FixedReadProfile(IdentityAssertionPort):
    service: ServiceConfig
    approval: FixedReadApproval
    output_contract: OutputContract
    capability: CapabilitySpec
    binding: ToolBinding
    approved_reads: Mapping[str, ApprovedQueryRead] = dataclass_field(default_factory=dict)
    # The existing port declares writable policy attributes, not properties.
    # Keep them out of constructor/config input and initialize from approval.
    version: str = dataclass_field(init=False)
    approved: bool = dataclass_field(init=False, default=True)
    tls_context: ssl.SSLContext | None = dataclass_field(init=False, default=None, repr=False)
    _bound_owner: FixedReadLocalOwner | None = dataclass_field(
        init=False, default=None, repr=False, compare=False
    )
    _owner_ready: bool = dataclass_field(init=False, default=False, repr=False, compare=False)
    _owner_lock: Lock = dataclass_field(init=False, default_factory=Lock, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.version = self.approval.version

    def permits_authorization(
        self, service: ServiceConfig, session: AuthenticatedSessionContext
    ) -> bool:
        """Check the approved or once-bound owner before OAuth config or network work."""
        try:
            owner = FixedReadLocalOwner.from_session(session)
        except McpFailure:
            return False
        return service == self.service and self.permits(
            tenant_id=owner.tenant_id,
            user_id=owner.user_id,
            service_config_id=service.service_config_id,
            require_ready=False,
        )

    async def bind_authorization_owner(
        self,
        service_config_id: str,
        session: AuthenticatedSessionContext,
        revocations: SessionRevocationStorePort,
    ) -> None:
        """A single explicit Apps authorization may select the local owner once."""
        if self.approval.local_owner_source != "authenticated_session":
            return
        if not self.service.enabled or service_config_id != self.service.service_config_id:
            raise McpFailure("mcp_service_unavailable")
        owner = FixedReadLocalOwner.from_session(session)
        if (
            owner.tenant_id != self.approval.tenant_id
            or not owner.user_id
            or await revocations.is_revoked(owner.fingerprint)
        ):
            raise McpFailure("mcp_service_unavailable")
        with self._owner_lock:
            if self._bound_owner is None:
                self._bound_owner = owner
                record_mcp_event("fixed_owner_bound", "success", profile=self.service, tool=TOOL)
            elif self._bound_owner != owner:
                raise McpFailure("mcp_service_unavailable")
            self._owner_ready = False

    async def complete_authorization_owner(
        self, session: AuthenticatedSessionContext, store: McpStorePort
    ) -> None:
        """Release the local read only after the matching grant is ACTIVE."""
        if self.approval.local_owner_source != "authenticated_session":
            return
        owner = FixedReadLocalOwner.from_session(session)
        if self._bound_owner != owner:
            raise McpFailure("mcp_authorization_invalid")
        connection = await store.connection(
            tenant_id=owner.tenant_id,
            user_id=owner.user_id,
            service_config_id=self.service.service_config_id,
        )
        if (
            connection is None
            or connection.state != "ACTIVE"
            or connection.service_config_version != self.service.service_config_version
            or connection.login_session_fingerprint != owner.fingerprint.hex()
            or connection.identity_policy_version != self.version
            or not connection.identity_evidence
            or connection.expires_at is None
            or connection.expires_at <= datetime.now(UTC)
        ):
            raise McpFailure("mcp_authorization_invalid")
        with self._owner_lock:
            if self._bound_owner != owner:
                raise McpFailure("mcp_authorization_invalid")
            self._owner_ready = True

    def permits(
        self,
        *,
        tenant_id: str,
        user_id: str,
        service_config_id: str,
        tool: str = TOOL,
        require_ready: bool = True,
    ) -> bool:
        if (
            not self.service.enabled
            or tenant_id != self.approval.tenant_id
            or service_config_id != self.service.service_config_id
            or (tool != TOOL and tool not in self.approved_reads)
        ):
            return False
        if self.approval.local_owner_source == "fixed":
            return user_id == self.approval.user_id
        owner = self._bound_owner
        session = authenticated_session.get()
        now = datetime.now(UTC)
        return (
            owner is not None
            and session is not None
            and (not require_ready or self._owner_ready)
            and owner.tenant_id == tenant_id
            and owner.user_id == user_id == session.principal.ai_user_id
            and session.principal.org_ctx.tenant_id == tenant_id
            and owner.fingerprint == session.fingerprint
            and owner.expires_at > now
            and session.expires_at > now
        )

    def tool_for_capability(self, capability_id: str) -> str | None:
        if capability_id == self.capability.capability_id:
            return TOOL
        for tool, read in self.approved_reads.items():
            if capability_id == read.capability.capability_id:
                return tool
        return None

    def permits_candidate(
        self,
        *,
        ai_user_id: str,
        capability_id: str,
        request_context: RequestOrgContext,
    ) -> bool:
        tool = self.tool_for_capability(capability_id)
        allowed = tool is not None and self.permits(
            tenant_id=request_context.tenant_id,
            user_id=ai_user_id,
            service_config_id=self.service.service_config_id,
            tool=tool,
            require_ready=True,
        )
        if not allowed:
            owner = self._bound_owner
            session = authenticated_session.get()
            now = datetime.now(UTC)
            if tool is None:
                reason = "mcp_candidate_capability_mismatch"
            elif not self.service.enabled or request_context.tenant_id != self.approval.tenant_id:
                reason = "mcp_candidate_config_denied"
            elif self.approval.local_owner_source == "fixed":
                reason = "mcp_candidate_user_mismatch"
            elif owner is None:
                reason = "mcp_candidate_owner_missing"
            elif session is None:
                reason = "mcp_candidate_session_missing"
            elif not self._owner_ready:
                reason = "mcp_candidate_owner_not_ready"
            elif (
                owner.tenant_id != request_context.tenant_id
                or owner.user_id != ai_user_id
                or ai_user_id != session.principal.ai_user_id
                or session.principal.org_ctx.tenant_id != request_context.tenant_id
            ):
                reason = "mcp_candidate_owner_scope_mismatch"
            elif owner.fingerprint != session.fingerprint:
                reason = "mcp_candidate_session_mismatch"
            elif owner.expires_at <= now:
                reason = "mcp_candidate_owner_expired"
            elif session.expires_at <= now:
                reason = "mcp_candidate_session_expired"
            else:
                reason = "mcp_candidate_denied"
            record_mcp_event(
                "candidate_visibility",
                "failed",
                profile=self.service,
                tool=tool or TOOL,
                error_code=reason,
            )
        return allowed

    async def verify(
        self,
        *,
        tenant_id: str,
        user_id: str,
        service_config_id: str,
        token_metadata: dict[str, Any],
    ) -> str | None:
        subject = token_metadata.get("verified_subject")
        connection_id = token_metadata.get("probe_connection_id")
        binding_epoch = token_metadata.get("probe_binding_epoch")
        fingerprint = token_metadata.get("probe_login_session_fingerprint")
        registration_id = token_metadata.get("registration_id")
        client_id = token_metadata.get("probe_client_id")
        if (
            not self.permits(
                tenant_id=tenant_id,
                user_id=user_id,
                service_config_id=service_config_id,
                require_ready=False,
            )
            or type(subject) is not str
            or not subject
            or not subject.isascii()
            or not subject.isdigit()
            or (
                self.approval.identity_binding_mode == "expected_subject"
                and subject != self.approval.expected_subject
            )
            or token_metadata.get("scope") != "mcp:business"
            or token_metadata.get("issuer") != self.service.issuer
            or token_metadata.get("identity_probe_version") != self.version
            or token_metadata.get("service_config_version") != self.service.service_config_version
            or type(connection_id) is not str
            or not connection_id
            or type(binding_epoch) is not int
            or binding_epoch < 1
            or type(fingerprint) is not str
            or len(fingerprint) != 64
            or any(char not in "0123456789abcdef" for char in fingerprint)
            or type(registration_id) is not str
            or not registration_id
            or registration_id != token_metadata.get("probe_registration_id")
            or type(client_id) is not str
            or not client_id
        ):
            return None
        return digest(
            [
                self.version,
                self.approval.approval_evidence,
                self.approval.identity_binding_mode,
                tenant_id,
                user_id,
                service_config_id,
                self.service.service_config_version,
                connection_id,
                binding_epoch,
                fingerprint,
                registration_id,
                client_id,
                subject,
            ]
        )

    async def probe_authorization(
        self,
        service: ServiceConfig,
        transaction: AuthorizationTransaction,
        token: str,
        revalidate: Callable[[], Awaitable[None]],
    ) -> dict[str, Any]:
        """Fixed pre-grant read inside the formal OAuth completion flow only."""
        if (
            service != self.service
            or transaction.service_config_version != service.service_config_version
            or not self.permits(
                tenant_id=transaction.owner.tenant_id,
                user_id=transaction.owner.user_id,
                service_config_id=transaction.owner.service_config_id,
                require_ready=False,
            )
        ):
            raise McpFailure("mcp_identity_probe_denied")

        async def guarded_token() -> str:
            await revalidate()  # Every POST, including initialize and tools/list.
            return token

        record_mcp_event(
            "identity_probe",
            "started",
            profile=service,
            tool=TOOL,
            connection_id=transaction.owner.connection_id,
        )
        result = await McpDriver._exchange(
            service,
            guarded_token,
            TOOL,
            {},
            input_digest=input_digest(TOOL),
            safety_digest=safety_digest(TOOL),
            connection_id=transaction.owner.connection_id,
            tls_context=self.tls_context,
        )
        record_mcp_event("identity_probe_received", "success", profile=service, tool=TOOL)
        validated = self.output_contract.validate(result)
        # The deployed service derives this opaque ID from the OAuth client.
        expected_client_id = (
            "oauth_"
            + sha256(
                (validated["adapterClientId"] + "\n" + transaction.client_id).encode("utf-8")
            ).hexdigest()
        )
        if validated["clientId"] != expected_client_id:
            raise McpFailure("mcp_identity_probe_denied")
        subject = validated["userId"]
        record_mcp_event("identity_probe_validated", "success", profile=service, tool=TOOL)
        # Subject mismatch keeps the encrypted grant pending; it never activates
        # a connection and never uses this response as a later Chat answer.
        await revalidate()
        record_mcp_event(
            "identity_probe",
            "success",
            profile=service,
            tool=TOOL,
            connection_id=transaction.owner.connection_id,
            outbound_count=1,
        )
        return {
            "verified_subject": subject,
            "identity_probe_version": self.version,
            "service_config_version": service.service_config_version,
            "probe_connection_id": transaction.owner.connection_id,
            "probe_binding_epoch": transaction.binding_epoch,
            "probe_login_session_fingerprint": transaction.login_session_fingerprint,
            "probe_registration_id": transaction.registration_id,
            "probe_client_id": transaction.client_id,
        }

    def format_result(self, data: dict[str, Any]) -> str:
        # Called only after the approved recursive projection. No provider text
        # or hidden subject fields are consumed by the formatter.
        values = []
        for field, label in self.approval.display_fields.items():
            if field not in data:
                continue
            value = data[field]
            if isinstance(value, (dict, list)):
                text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            else:
                text = (
                    "未提供"
                    if value is None
                    else "是"
                    if value is True
                    else "否"
                    if value is False
                    else str(value)
                )
            values.append(f"{label}：{text}")
        return "业务信息：" + "；".join(values) if values else "业务读取完成，未返回可展示字段。"

    def format_query_result(self, tool: str, data: dict[str, Any]) -> str:
        """Present only the already approved public projection, without status claims."""
        read = self.approved_reads.get(tool)
        if read is None:
            raise McpFailure("mcp_output_contract_invalid")
        if read.projection is not None:
            # Raises ApprovedResponseProjectionError; never a success text.
            public = project_approved_response_data(
                data, read.capability.output_schema, read.projection
            )
        else:
            maybe_public = project_response_data(data, read.capability.output_schema)
            if maybe_public is None:
                raise McpFailure("mcp_output_projection_invalid")
            public = maybe_public
        if not public:
            return "业务查询完成，未返回可展示字段。"
        # Depth -1 puts top-level fields flush left.
        _, lines = _query_value(tool, "", public, -1)
        return f"{read.capability.name}结果：\n" + "\n".join(lines)


def build_fixed_read_profile(
    service: ServiceConfig,
    approval: FixedReadApproval,
    *,
    prepared: PreparedFixedReadDefinition | None = None,
    read_contracts: Mapping[str, OutputContract] | None = None,
) -> FixedReadProfile:
    definition = prepared or prepare_fixed_read_definition(service)
    if (
        definition.service != service
        or (
            service.service_config_id,
            service.service_config_version,
            service.tenant_id,
        )
        != (
            approval.service_config_id,
            approval.service_config_version,
            approval.tenant_id,
        )
        or not approval.approval_evidence.strip()
        or not approval.display_fields
        or approval.subject_path != ["userId"]
        or not set(approval.display_fields) <= set(definition.display_fields)
        or approval.success_schema != definition.success_schema
    ):
        raise McpFailure("mcp_fixed_profile_invalid")
    # Root-field approval must never make the private identity branch public.
    # Displaying siblings inside that branch requires a separate, explicitly
    # approved nested projection and is outside this minimal profile.
    if approval.subject_path[0] in approval.display_fields:
        raise McpFailure("mcp_output_contract_invalid")
    schema = deepcopy(approval.success_schema)
    try:
        Draft202012Validator.check_schema(schema)
    except Exception:
        raise McpFailure("mcp_output_contract_invalid") from None
    fields = tuple(approval.display_fields)
    output = _FixedReadOutputContract(
        version=approval.version,
        schema=schema,
        model_fields=fields,
        ui_fields=fields,
        persistence_fields=fields,
        approval_evidence=approval.approval_evidence,
        synthetic=False,
    )
    output.require_production()
    public_schema = output.public_result_schema()
    if schema_has_credential_property(public_schema):
        raise McpFailure("mcp_output_contract_invalid")
    capability = CapabilitySpec.model_validate(
        {
            **definition.capability.model_dump(),
            "output_schema": public_schema,
            "output_schema_digest": digest(public_schema),
            "policy_digest": digest(
                [approval.version, approval.approval_evidence, service.service_config_id, TOOL]
            ),
            "status": "disabled",
        }
    )
    binding = ToolBinding.model_validate(
        {**definition.binding.model_dump(), "output_contract_version": output.version}
    )
    approved_reads = _approved_query_reads(service, approval, read_contracts or {})
    record_mcp_event("fixed_profile_loaded", "success", profile=service, tool=TOOL)
    return FixedReadProfile(service, approval, output, capability, binding, approved_reads)


def load_fixed_read_profile(
    services: dict[str, ServiceConfig],
    raw_approval: str | None,
    *,
    tls_contexts: Mapping[tuple[str, int, str], ssl.SSLContext] | None = None,
    prepared: PreparedFixedReadDefinition | None = None,
    read_contracts: Mapping[str, OutputContract] | None = None,
) -> FixedReadProfile | None:
    """Compose from non-secret approved config; absence leaves the profile off."""
    if raw_approval is None or not raw_approval.strip():
        return None
    try:
        approval = FixedReadApproval.model_validate_json(raw_approval)
        service = services[approval.service_config_id]
        matching_preparation = (
            prepared if prepared is not None and prepared.service == service else None
        )
        profile = build_fixed_read_profile(
            service,
            approval,
            prepared=matching_preparation,
            read_contracts=read_contracts,
        )
        if tls_contexts is not None:
            profile.tls_context = tls_contexts.get(
                (service.service_config_id, service.service_config_version, service.endpoint)
            )
        return profile
    except Exception:
        # Pydantic diagnostics can include supplied values; expose only a code.
        raise McpFailure("mcp_fixed_profile_invalid") from None


class FixedReadCapabilityRegistry:
    """Prepare only approved mappings behind existing AdminRegistryService commands.

    Compose this delegate only into the admin service, whose existing session
    and role checks run before create/enable/disable. This preserves the Admin
    API, its draft-first workflow and its audit records. Construction does no
    database work and never publishes anything on application startup.
    """

    def __init__(
        self,
        delegate: CapabilityRegistryPort,
        store: McpStorePort,
        profile: FixedReadProfile,
    ) -> None:
        self._delegate, self._store, self._profile = delegate, store, profile
        self._definitions = {
            profile.capability.capability_id: (TOOL, profile.capability, profile.binding),
            **{
                read.capability.capability_id: (tool, read.capability, read.binding)
                for tool, read in profile.approved_reads.items()
            },
        }
        self._protected_ids = {
            f"business.{profile.service.service_config_id}.{tool}" for tool in (TOOL, *QUERY_TOOLS)
        }

    def _validate(self, capability: CapabilitySpec, expected: CapabilitySpec) -> None:
        if (
            capability.status == "deprecated"
            or capability.model_copy(update={"status": "disabled"}) != expected
        ):
            raise McpFailure("mcp_capability_drift")

    async def create(self, capability: CapabilitySpec) -> CapabilitySpec:
        if capability.capability_id in self._protected_ids:
            definition = self._definitions.get(capability.capability_id)
            if definition is None:
                raise McpFailure("mcp_capability_unprepared")
            tool, expected, binding = definition
            self._validate(capability, expected)
            if capability.status not in {"draft", "disabled"}:
                raise McpFailure("mcp_capability_unprepared")
            await self._store.configure(self._profile.service)
            await self._store.bind_capability(binding)
            record_mcp_event(
                "read_capability_prepared", "success", profile=self._profile.service, tool=tool
            )
        return await self._delegate.create(capability)

    async def get(self, capability_id: str) -> CapabilitySpec | None:
        return await self._delegate.get(capability_id)

    async def list(
        self,
        target_system: str | None = None,
        type: str | None = None,
        status: str | None = None,
    ) -> list[CapabilitySpec]:
        return await self._delegate.list(target_system, type, status)

    async def update(self, capability_id: str, patch: dict[str, Any]) -> CapabilitySpec:
        if capability_id in self._protected_ids:
            definition = self._definitions.get(capability_id)
            if definition is None:
                raise McpFailure("mcp_capability_unprepared")
            _, expected, binding = definition
            current = await self._delegate.get(capability_id)
            if current is None:
                raise McpFailure("mcp_capability_unprepared")
            self._validate(
                CapabilitySpec.model_validate({**current.model_dump(), **patch}), expected
            )
            if patch.get("status") == "active":
                if not self._profile.service.enabled:
                    raise McpFailure("mcp_service_unavailable")
                await self._store.configure(self._profile.service)
                if await self._store.mapping(capability_id, current.version) != binding:
                    raise McpFailure("mcp_capability_unprepared")
        return await self._delegate.update(capability_id, patch)

    async def disable(self, capability_id: str) -> CapabilitySpec:
        return await self._delegate.disable(capability_id)
