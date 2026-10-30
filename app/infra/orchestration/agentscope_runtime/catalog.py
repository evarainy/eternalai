"""Visible catalog of one M1 turn, as native tools in AgentScope tool groups.

Visibility copies the legacy Runtime rule (an unsafe id, a policy error or an
unexpected verdict makes the whole catalog invalid). Query capabilities become
read tools and workflow capabilities become write-proposal tools in their own
write groups (except READ_WORKFLOW_IDS, which are read tools); action and mock
capabilities are never visible, and browser
capabilities are excluded silently (counted only). There is no lexical
pre-selection. Grouping is a pure function of the visible definitions so
offline evaluation can call it directly. The one registered browser
capability of a trusted browser request is added by the runtime as an
external tool in its own group (M2 §3.3).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal
from uuid import uuid4

from pydantic import ValidationError

from app.infra.workflow.catalog import OVERVIEW_ID
from app.knowledge.capability_selection import (
    CandidateBinding,
    ToolDefinitionRejected,
    canonical_json,
    capability_fingerprint,
    is_safe_capability_id,
    project_tool_input_schema,
    validate_tool_text,
)
from app.ports.capability_registry import (
    CAPABILITY_NAME_MAX_LENGTH,
    CAPABILITY_SHORT_DESCRIPTION_MAX_LENGTH,
    CapabilityRegistryPort,
    CapabilitySpec,
)
from app.ports.policy_guard import CapabilityCandidatePolicyPort
from app.ports.request_context import RequestChannel, RequestOrgContext

TOOL_GROUP_PROTOCOL_VERSION = "agentscope-tool-groups-v1"
G_MAX = 10
MAX_GROUP_DESCRIPTION_CHARS = 800
MAX_GROUP_INSTRUCTIONS_CHARS = 1200
# A member appears in its group description as "name: summary", the summary
# being the first sentence of its short description when it is this short.
DISCOVERY_SUMMARY_MAX_CHARS = 50
RESERVED_TOOL_NAMES = frozenset({"reset_tools", "report_unsupported", "ask_user"})
_TOOL_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")

GROUP_BY_TARGET: dict[str | None, str] = {
    "oa": "oa_query",
    "business_platform": "business_query",
    "u8": "u8_query",
    "hikvision_ivms": "ivms_query",
    None: "general_query",
}
# Workflow capabilities only; a write tool never joins a read group.
WRITE_GROUP_BY_TARGET: dict[str | None, str] = {
    "oa": "oa_action",
    "business_platform": "business_action",
    "u8": "u8_action",
    "hikvision_ivms": "ivms_action",
    None: "general_action",
}
VISIBLE_TYPES = frozenset({"query", "workflow"})
# Workflows whose leaves are all queries: a read tool in its read group.
READ_WORKFLOW_IDS = frozenset({OVERVIEW_ID})
# Out of this path's scope: excluded before grouping and only counted.
SCOPE_EXCLUDED_PREFIX = "browser."
# Group of the registered browser tool; only that one tool, never activated
# together with a write proposal (each must be called alone).
BROWSER_GROUP = "oa_browser"
BROWSER_GROUP_PURPOSE = "在 OA 网页中由浏览器代为处理已登记的事项，不会保存或提交。"
ToolKind = Literal["read", "write", "browser"]
# Second-level split inside one primary group, applied only when the primary
# group does not fit in one chunk: {group: ((subgroup, (capability suffix,
# ...)), ...)}. Subgroup names must not contain "_other_".
SUBGROUPS: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {}
# Family key: the capability id without its last dot segment, plus the part of
# that segment before its first underscore ("oa.seal_cur_list" -> "oa.seal").
# Chunking keeps a family together unless the family alone breaks a limit.
FAMILY_KEY_RULE = "namespace+stem"
_GROUP_PURPOSES: dict[str, str] = {
    "oa_query": "查询 OA 办公系统中当前用户本人的数据。",
    "business_query": "查询业务平台中的业务数据和平台信息。",
    "u8_query": "查询 U8 系统中的业务数据。",
    "ivms_query": "查询海康 iVMS 平台中的数据。",
    "general_query": "查询其他系统中的数据。",
    "oa_action": "在 OA 中发起需确认的流程。调用后只生成确认卡片，用户确认后才执行。",
    "business_action": "在业务平台中发起需确认的流程。调用后只生成确认卡片，用户确认后才执行。",
    "u8_action": "在 U8 系统中发起需确认的流程。调用后只生成确认卡片，用户确认后才执行。",
    "ivms_action": "在海康 iVMS 平台中发起需确认的流程。调用后只生成确认卡片，用户确认后才执行。",
    "general_action": "在其他系统中发起需确认的流程。调用后只生成确认卡片，用户确认后才执行。",
}
# Attached to every write group and shown only while that group is active.
WRITE_GROUP_INSTRUCTIONS = (
    "写入类工具一次只调用一个，并且要单独调用，不要与查询或其他工具放在同一次回复里；"
    "调用后本轮结束并展示确认卡片，不要声称已经提交。"
)
# Usage rules by capability suffix, attached to the group holding that
# capability and shown only while the group is active.
GROUP_INSTRUCTIONS: dict[str, str] = {
    "person_find": (
        "按姓名查人员时，用户没有指定页码或每页条数就不要填写 page、pageSize，"
        "用户指定了就按原值填写；不要为了只取一个结果缩小每页条数。"
        "查到多个匹配的人时，列出来请用户指定是哪一位，不要替用户选择。"
    ),
    "clothing_options_get": (
        "查询被服选项只需要 jyId；jqId 是可选的监区 ID，"
        "用户没有给出时不要填写，也不要向用户索要。"
    ),
}
if (
    sum(len(text) for text in GROUP_INSTRUCTIONS.values()) + len(WRITE_GROUP_INSTRUCTIONS)
    > MAX_GROUP_INSTRUCTIONS_CHARS
):
    raise RuntimeError("group_instructions_over_budget")


def _rules_digest() -> str:
    canonical = canonical_json(
        {
            "map": {str(key) if key else "none": value for key, value in GROUP_BY_TARGET.items()},
            "write_map": {
                str(key) if key else "none": value for key, value in WRITE_GROUP_BY_TARGET.items()
            },
            "subgroups": {key: [list(item) for item in value] for key, value in SUBGROUPS.items()},
        }
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


GROUP_RULES = (
    f"groups-v4;gmax={G_MAX};desc={MAX_GROUP_DESCRIPTION_CHARS};"
    f"summary=first-sentence<={DISCOVERY_SUMMARY_MAX_CHARS};"
    f"family={FAMILY_KEY_RULE};map={_rules_digest()}"
)


@dataclass(frozen=True, slots=True)
class CatalogTool:
    name: str
    capability_id: str
    group: str
    description: str
    input_schema: dict[str, Any]
    schema_digest: str
    binding: CandidateBinding
    kind: ToolKind = "read"


@dataclass(frozen=True, slots=True)
class CatalogGroup:
    name: str
    description: str
    instructions: str | None
    tool_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ToolCatalog:
    tools: tuple[CatalogTool, ...]
    groups: tuple[CatalogGroup, ...]
    visible_count: int
    # (reason_code, count), sorted by reason; never capability ids.
    excluded: tuple[tuple[str, int], ...]
    # Members listed by name only because their summary is too long; logged,
    # not a catalog gap.
    summary_omitted: int = 0

    @property
    def incomplete(self) -> bool:
        return bool(self.excluded)

    def group_of(self, capability_id: str) -> str | None:
        """Group holding the tool of ``capability_id``; None when it is not a tool."""

        return next(
            (tool.group for tool in self.tools if tool.capability_id == capability_id), None
        )

    def trace_attributes(self) -> dict[str, Any]:
        """Value-free catalog statistics; the control tool is never counted."""

        return {
            "protocol_version": TOOL_GROUP_PROTOCOL_VERSION,
            "visible_count": self.visible_count,
            "registered_count": len(self.tools),
            "excluded_count": self.visible_count - len(self.tools),
            "group_count": len(self.groups),
            "coverage_complete": not self.incomplete,
        }


@dataclass(frozen=True, slots=True)
class VisibleCatalog:
    capabilities: tuple[CapabilitySpec, ...]
    active_count: int
    # Visible capabilities outside this path's scope (browser.*); counted only.
    scope_excluded: int = 0
    # The requested browser capability when it is active and policy-visible,
    # before the type and prefix filters; the runtime decides on exposure.
    browser: CapabilitySpec | None = None


async def visible_capabilities(
    registry: CapabilityRegistryPort,
    policy: CapabilityCandidatePolicyPort,
    *,
    ai_user_id: str,
    tenant_id: str,
    channel: RequestChannel,
    request_id: str | None = None,
    browser_capability_id: str | None = None,
) -> VisibleCatalog | None:
    """This request's policy-previewed active query/workflow set, or None if invalid."""

    # Same rule as the legacy Runtime's _visible_capabilities.
    try:
        snapshot = await registry.list(status="active")
    except ValidationError:
        return None
    active = [item for item in snapshot if item.status == "active"]
    if any(not is_safe_capability_id(item.capability_id) for item in active):
        return None
    request_context = RequestOrgContext(
        request_id=request_id or str(uuid4()), channel=channel, tenant_id=tenant_id
    )
    visible: list[CapabilitySpec] = []
    for item in sorted(active, key=lambda capability: capability.capability_id):
        try:
            verdict = await policy.preview_capability(
                ai_user_id=ai_user_id,
                capability_id=item.capability_id,
                request_context=request_context.model_copy(deep=True),
            )
        except Exception:
            return None
        if verdict == "exclude":
            continue
        if verdict != "defer":
            return None
        visible.append(item.model_copy(deep=True))
    typed = [item for item in visible if item.type in VISIBLE_TYPES]
    in_scope = tuple(
        item for item in typed if not item.capability_id.startswith(SCOPE_EXCLUDED_PREFIX)
    )
    browser = next(
        (item for item in visible if item.capability_id == browser_capability_id), None
    )
    return VisibleCatalog(
        capabilities=in_scope,
        active_count=len(active),
        scope_excluded=len(typed) - len(in_scope),
        browser=browser,
    )


def build_tool_catalog(specs: Sequence[CapabilitySpec]) -> ToolCatalog:
    """Project, name and group the visible definitions; no AgentScope types."""

    excluded: dict[str, int] = {}
    candidates: list[CatalogTool] = []
    for spec in sorted(specs, key=lambda item: item.capability_id):
        name = spec.capability_id.replace(".", "__")
        if _TOOL_NAME.fullmatch(name) is None:
            _count(excluded, "tool_name_invalid")
            continue
        kind: ToolKind = (
            "write"
            if spec.type == "workflow" and spec.capability_id not in READ_WORKFLOW_IDS
            else "read"
        )
        group = (WRITE_GROUP_BY_TARGET if kind == "write" else GROUP_BY_TARGET).get(
            spec.target_system
        )
        if group is None:
            _count(excluded, "group_unmapped")
            continue
        try:
            validate_tool_text(spec.name, max_chars=CAPABILITY_NAME_MAX_LENGTH)
            validate_tool_text(
                spec.short_description, max_chars=CAPABILITY_SHORT_DESCRIPTION_MAX_LENGTH
            )
            schema = project_tool_input_schema(spec.input_schema)
        except ToolDefinitionRejected as rejected:
            _count(excluded, rejected.reason)
            continue
        candidates.append(
            CatalogTool(
                name=name,
                capability_id=spec.capability_id,
                group=group,
                description=_description(spec, schema),
                input_schema=schema,
                schema_digest=hashlib.sha256(
                    canonical_json(schema).encode("utf-8")
                ).hexdigest(),
                binding=CandidateBinding(
                    capability_id=spec.capability_id,
                    version=spec.version,
                    fingerprint=capability_fingerprint(spec),
                    capability_type=spec.type,
                    target_system=spec.target_system,
                    intent_tags=tuple(spec.intent_tags),
                ),
                kind=kind,
            )
        )
    names = [tool.name for tool in candidates]
    tools: list[CatalogTool] = []
    for tool in candidates:
        if tool.name in RESERVED_TOOL_NAMES or names.count(tool.name) > 1:
            _count(excluded, "tool_name_collision")
        else:
            tools.append(tool)

    # Each member's entry in its group description: "name: summary" or name.
    summaries = {spec.capability_id: discovery_summary(spec) for spec in specs}
    spec_names = {
        spec.capability_id: (
            spec.name if summaries[spec.capability_id] is None
            else f"{spec.name}：{summaries[spec.capability_id]}"
        )
        for spec in specs
    }
    groups: list[CatalogGroup] = []
    grouped: list[CatalogTool] = []
    primaries = (*GROUP_BY_TARGET.values(), *WRITE_GROUP_BY_TARGET.values())
    for primary in dict.fromkeys(primaries):
        members = [tool for tool in tools if tool.group == primary]
        for group_name, chunk in _split(primary, members, spec_names, excluded):
            renamed = [_with_group(tool, group_name) for tool in chunk]
            grouped.extend(renamed)
            groups.append(
                CatalogGroup(
                    name=group_name,
                    description=_group_description(primary, chunk, spec_names),
                    instructions=_group_instructions(primary, chunk),
                    tool_names=tuple(tool.name for tool in renamed),
                )
            )
    return ToolCatalog(
        tools=tuple(grouped),
        groups=tuple(groups),
        visible_count=len(specs),
        excluded=tuple(sorted(excluded.items())),
        summary_omitted=sum(1 for tool in grouped if summaries[tool.capability_id] is None),
    )


def with_browser_tool(
    catalog: ToolCatalog,
    spec: CapabilitySpec,
    instructions: str,
    purpose: str = BROWSER_GROUP_PURPOSE,
    usage: str | None = None,
    *,
    collect_missing: bool = False,
) -> tuple[ToolCatalog, CatalogTool | None]:
    """Add the registered browser capability as the only tool of BROWSER_GROUP.

    ``purpose`` opens the group description the model selects groups by. A
    ``usage`` text replaces the required-argument hint of the tool
    description: the runtime, not ask_user, collects missing arguments.

    A definition that cannot be projected is not registered and marks the
    catalog incomplete, like any other rejected definition.
    """

    name = spec.capability_id.replace(".", "__")
    try:
        if _TOOL_NAME.fullmatch(name) is None or name in {tool.name for tool in catalog.tools}:
            raise ToolDefinitionRejected("tool_name_invalid")
        validate_tool_text(spec.name, max_chars=CAPABILITY_NAME_MAX_LENGTH)
        validate_tool_text(
            spec.short_description, max_chars=CAPABILITY_SHORT_DESCRIPTION_MAX_LENGTH
        )
        schema = project_tool_input_schema(spec.input_schema)
    except ToolDefinitionRejected as rejected:
        excluded = dict(catalog.excluded)
        _count(excluded, rejected.reason)
        return replace(
            catalog, visible_count=catalog.visible_count + 1,
            excluded=tuple(sorted(excluded.items())),
        ), None
    if collect_missing:
        # Empty string placeholders are missing collection fields, not completed
        # form values. required is already omitted by the common projection.
        # Keep types, upper bounds and the original published schema unchanged.
        for field in schema["properties"].values():
            field.pop("minLength", None)
    summary = discovery_summary(spec)
    tool = CatalogTool(
        name=name,
        capability_id=spec.capability_id,
        group=BROWSER_GROUP,
        description=(
            _description(spec, schema)
            if usage is None
            else f"{usage}{spec.name}：{spec.short_description}"
        ),
        input_schema=schema,
        schema_digest=hashlib.sha256(canonical_json(schema).encode("utf-8")).hexdigest(),
        binding=CandidateBinding(
            capability_id=spec.capability_id,
            version=spec.version,
            fingerprint=capability_fingerprint(spec),
            capability_type=spec.type,
            target_system=spec.target_system,
            intent_tags=tuple(spec.intent_tags),
        ),
        kind="browser",
    )
    group = CatalogGroup(
        name=BROWSER_GROUP,
        description=(
            purpose + "包含：" + (spec.name if summary is None else f"{spec.name}：{summary}")
        ),
        instructions=instructions,
        tool_names=(name,),
    )
    return replace(
        catalog,
        tools=(*catalog.tools, tool),
        groups=(*catalog.groups, group),
        visible_count=catalog.visible_count + 1,
        summary_omitted=catalog.summary_omitted + (summary is None),
    ), tool


def _split(
    primary: str,
    members: list[CatalogTool],
    spec_names: dict[str, str],
    excluded: dict[str, int],
) -> list[tuple[str, list[CatalogTool]]]:
    # Members are already sorted by capability id. A member whose own
    # description cannot fit is excluded; names are never truncated.
    fitting: list[CatalogTool] = []
    for tool in members:
        if len(_group_description(primary, [tool], spec_names)) > MAX_GROUP_DESCRIPTION_CHARS:
            _count(excluded, "group_description_too_long")
        else:
            fitting.append(tool)
    if not fitting:
        return []
    if _fits(primary, fitting, spec_names):
        return [(primary, fitting)]
    # Registered subgroups first, in table order; the rest become other chunks.
    named: list[tuple[str, list[CatalogTool]]] = []
    rest = fitting
    for subgroup, suffixes in SUBGROUPS.get(primary, ()):
        chosen = [tool for tool in rest if _suffix(tool) in suffixes]
        rest = [tool for tool in rest if _suffix(tool) not in suffixes]
        chunks = _pack(primary, chosen, spec_names)
        if len(chunks) == 1:
            named.append((subgroup, chunks[0]))
        else:
            named.extend(
                (f"{subgroup}_{index}", chunk) for index, chunk in enumerate(chunks, start=1)
            )
    named.extend(
        (f"{primary}_other_{index}", chunk)
        for index, chunk in enumerate(_pack(primary, rest, spec_names), start=1)
    )
    return named


def _pack(
    primary: str, members: list[CatalogTool], spec_names: dict[str, str]
) -> list[list[CatalogTool]]:
    # Whole families are packed in family-key order into the current chunk
    # while it stays within both limits. A family that breaks a limit on its
    # own is cut into chunks of its own; every member fits alone already.
    families: dict[str, list[CatalogTool]] = {}
    for tool in sorted(members, key=lambda item: (_family_key(item), item.capability_id)):
        families.setdefault(_family_key(tool), []).append(tool)
    chunks: list[list[CatalogTool]] = []
    current: list[CatalogTool] = []
    for family in families.values():
        if _fits(primary, current + family, spec_names):
            current = current + family
            continue
        if current:
            chunks.append(current)
            current = []
        if _fits(primary, family, spec_names):
            current = family
            continue
        piece: list[CatalogTool] = []
        for tool in family:
            if piece and not _fits(primary, piece + [tool], spec_names):
                chunks.append(piece)
                piece = []
            piece.append(tool)
        chunks.append(piece)
    if current:
        chunks.append(current)
    return chunks


def _fits(primary: str, members: Sequence[CatalogTool], spec_names: dict[str, str]) -> bool:
    return (
        len(members) <= G_MAX
        and len(_group_description(primary, members, spec_names)) <= MAX_GROUP_DESCRIPTION_CHARS
    )


def _suffix(tool: CatalogTool) -> str:
    return tool.capability_id.rsplit(".", 1)[-1]


def _family_key(tool: CatalogTool) -> str:
    namespace, _, last = tool.capability_id.rpartition(".")
    return f"{namespace}.{last.split('_', 1)[0]}"


def _with_group(tool: CatalogTool, group: str) -> CatalogTool:
    return CatalogTool(
        name=tool.name,
        capability_id=tool.capability_id,
        group=group,
        description=tool.description,
        input_schema=tool.input_schema,
        schema_digest=tool.schema_digest,
        binding=tool.binding,
        kind=tool.kind,
    )


def discovery_summary(spec: CapabilitySpec) -> str | None:
    """First sentence of the short description, or None when it is too long."""

    summary = spec.short_description.split("。", 1)[0]
    return summary if len(summary) <= DISCOVERY_SUMMARY_MAX_CHARS else None


def _group_description(
    primary: str, members: Sequence[CatalogTool], spec_names: dict[str, str]
) -> str:
    return (
        _GROUP_PURPOSES[primary]
        + "包含："
        + "；".join(spec_names[tool.capability_id] for tool in members)
    )


def _group_instructions(primary: str, members: Sequence[CatalogTool]) -> str | None:
    texts = [WRITE_GROUP_INSTRUCTIONS] if primary in WRITE_GROUP_BY_TARGET.values() else []
    texts += [
        text
        for suffix, text in GROUP_INSTRUCTIONS.items()
        if any(_suffix(tool) == suffix for tool in members)
    ]
    return "\n".join(texts) if texts else None


def _description(spec: CapabilitySpec, schema: dict[str, Any]) -> str:
    # Same wording as the M0 bridge; required keys are hinted only when they
    # passed the property-key checks of the projected schema.
    text = f"{spec.name}：{spec.short_description}"
    properties = schema.get("properties", {})
    required = spec.input_schema.get("required")
    hinted = (
        [key for key in required if isinstance(key, str) and key in properties]
        if isinstance(required, list)
        else []
    )
    if hinted:
        text = text.removesuffix("。") + "。必填参数：" + "、".join(hinted)
        text += "；缺少时调用 ask_user 询问，不要猜测。"
    return text


def _count(excluded: dict[str, int], reason: str) -> None:
    excluded[reason] = excluded.get(reason, 0) + 1


__all__ = (
    "DISCOVERY_SUMMARY_MAX_CHARS",
    "FAMILY_KEY_RULE",
    "G_MAX",
    "GROUP_BY_TARGET",
    "GROUP_INSTRUCTIONS",
    "GROUP_RULES",
    "MAX_GROUP_DESCRIPTION_CHARS",
    "RESERVED_TOOL_NAMES",
    "SCOPE_EXCLUDED_PREFIX",
    "SUBGROUPS",
    "TOOL_GROUP_PROTOCOL_VERSION",
    "VISIBLE_TYPES",
    "BROWSER_GROUP",
    "BROWSER_GROUP_PURPOSE",
    "WRITE_GROUP_BY_TARGET",
    "WRITE_GROUP_INSTRUCTIONS",
    "CatalogGroup",
    "CatalogTool",
    "ToolCatalog",
    "ToolKind",
    "VisibleCatalog",
    "build_tool_catalog",
    "discovery_summary",
    "visible_capabilities",
    "with_browser_tool",
)
