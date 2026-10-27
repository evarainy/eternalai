"""Visible read catalog of one M1 turn, as native tools in AgentScope tool groups.

Visibility copies the legacy Runtime rule (an unsafe id, a policy error or an
unexpected verdict makes the whole catalog invalid); only query capabilities
are kept and there is no lexical pre-selection. Grouping is a pure function of
the visible definitions so offline evaluation can call it directly.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

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
MAX_GROUP_DESCRIPTION_CHARS = 300
MAX_GROUP_INSTRUCTIONS_CHARS = 1200
RESERVED_TOOL_NAMES = frozenset({"reset_tools", "report_unsupported"})
_TOOL_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")

GROUP_BY_TARGET: dict[str | None, str] = {
    "oa": "oa_query",
    "business_platform": "business_query",
    "u8": "u8_query",
    "hikvision_ivms": "ivms_query",
    None: "general_query",
}
# Second-level split inside one primary group, registered once the real
# catalog is exported: {group: ((subgroup, (capability suffix, ...)), ...)}.
SUBGROUPS: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {}
_GROUP_PURPOSES: dict[str, str] = {
    "oa_query": "查询 OA 办公系统中当前用户本人的数据。",
    "business_query": "查询业务平台中的业务数据（如人员信息）。",
    "u8_query": "查询 U8 系统中的业务数据。",
    "ivms_query": "查询海康 iVMS 平台中的数据。",
    "general_query": "查询其他系统中的数据。",
}
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
if sum(len(text) for text in GROUP_INSTRUCTIONS.values()) > MAX_GROUP_INSTRUCTIONS_CHARS:
    raise RuntimeError("group_instructions_over_budget")


def _rules_digest() -> str:
    canonical = canonical_json(
        {
            "map": {str(key) if key else "none": value for key, value in GROUP_BY_TARGET.items()},
            "subgroups": {key: [list(item) for item in value] for key, value in SUBGROUPS.items()},
        }
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


GROUP_RULES = (
    f"groups-v1;gmax={G_MAX};desc={MAX_GROUP_DESCRIPTION_CHARS};map={_rules_digest()}"
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

    @property
    def incomplete(self) -> bool:
        return bool(self.excluded)

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


async def visible_capabilities(
    registry: CapabilityRegistryPort,
    policy: CapabilityCandidatePolicyPort,
    *,
    ai_user_id: str,
    tenant_id: str,
    channel: RequestChannel,
    request_id: str | None = None,
) -> VisibleCatalog | None:
    """This request's policy-previewed active query set, or None if invalid."""

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
    return VisibleCatalog(
        capabilities=tuple(item for item in visible if item.type == "query"),
        active_count=len(active),
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
        group = GROUP_BY_TARGET.get(spec.target_system)
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
            )
        )
    names = [tool.name for tool in candidates]
    tools: list[CatalogTool] = []
    for tool in candidates:
        if tool.name in RESERVED_TOOL_NAMES or names.count(tool.name) > 1:
            _count(excluded, "tool_name_collision")
        else:
            tools.append(tool)

    spec_names = {spec.capability_id: spec.name for spec in specs}
    groups: list[CatalogGroup] = []
    grouped: list[CatalogTool] = []
    for primary in dict.fromkeys(GROUP_BY_TARGET.values()):
        members = [tool for tool in tools if tool.group == primary]
        for group_name, chunk in _split(primary, members, spec_names, excluded):
            renamed = [_with_group(tool, group_name) for tool in chunk]
            grouped.extend(renamed)
            groups.append(
                CatalogGroup(
                    name=group_name,
                    description=_group_description(primary, chunk, spec_names),
                    instructions=_group_instructions(chunk),
                    tool_names=tuple(tool.name for tool in renamed),
                )
            )
    return ToolCatalog(
        tools=tuple(grouped),
        groups=tuple(groups),
        visible_count=len(specs),
        excluded=tuple(sorted(excluded.items())),
    )


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
    size = min(G_MAX, len(fitting))
    while True:
        chunks = [fitting[index : index + size] for index in range(0, len(fitting), size)]
        if all(
            len(_group_description(primary, chunk, spec_names)) <= MAX_GROUP_DESCRIPTION_CHARS
            for chunk in chunks
        ):
            break
        size -= 1
    if len(chunks) == 1:
        return [(primary, chunks[0])]
    return [(f"{primary}_other_{index}", chunk) for index, chunk in enumerate(chunks, start=1)]


def _with_group(tool: CatalogTool, group: str) -> CatalogTool:
    return CatalogTool(
        name=tool.name,
        capability_id=tool.capability_id,
        group=group,
        description=tool.description,
        input_schema=tool.input_schema,
        schema_digest=tool.schema_digest,
        binding=tool.binding,
    )


def _group_description(
    primary: str, members: Sequence[CatalogTool], spec_names: dict[str, str]
) -> str:
    return (
        _GROUP_PURPOSES[primary]
        + "包含："
        + "、".join(spec_names[tool.capability_id] for tool in members)
    )


def _group_instructions(members: Sequence[CatalogTool]) -> str | None:
    texts = [
        text
        for suffix, text in GROUP_INSTRUCTIONS.items()
        if any(tool.capability_id.rsplit(".", 1)[-1] == suffix for tool in members)
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
        text += "。必填参数：" + "、".join(hinted)
        text += "；缺少时先向用户询问，不要猜测。"
    return text


def _count(excluded: dict[str, int], reason: str) -> None:
    excluded[reason] = excluded.get(reason, 0) + 1


__all__ = (
    "G_MAX",
    "GROUP_BY_TARGET",
    "GROUP_INSTRUCTIONS",
    "GROUP_RULES",
    "MAX_GROUP_DESCRIPTION_CHARS",
    "RESERVED_TOOL_NAMES",
    "SUBGROUPS",
    "TOOL_GROUP_PROTOCOL_VERSION",
    "CatalogGroup",
    "CatalogTool",
    "ToolCatalog",
    "VisibleCatalog",
    "build_tool_catalog",
    "visible_capabilities",
)
