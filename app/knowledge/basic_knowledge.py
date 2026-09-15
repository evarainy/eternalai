"""Small code-defined knowledge plus Registry-derived capability descriptions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Sequence, TypeAlias

from app.ports.capability_registry import CapabilitySpec

if TYPE_CHECKING:
    from app.knowledge.capability_selection import CapabilityCandidateSet

KnowledgeCategory: TypeAlias = Literal[
    "enterprise_term",
    "mock_system",
    "oa_system",
    "policy_template",
]

_REDACTED = "[REDACTED]"
_MAX_GUIDANCE_CAPABILITIES = 5
_MAX_CAPABILITY_ID_LENGTH = 96
_SAFE_CAPABILITY_ID = re.compile(r"[A-Za-z0-9._-]+")
_SENSITIVE_MARKER = re.compile(
    r"(?:bearer|token|credential|secret|password|passwd|auth|authorization|"
    r"cookie|session)",
    re.IGNORECASE,
)
_SENSITIVE_LOCATION_OR_IDENTITY = (
    re.compile(r"(?:https?|ftp)://\S+", re.IGNORECASE),
    re.compile(r"\\\\[^\s\\]+\\\S+"),
    re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)"),
    re.compile(
        r"(?:负责人|联系人|姓名|owner)\s*[:=：]",
        re.IGNORECASE,
    ),
)


@dataclass(frozen=True, slots=True)
class KnowledgeItem:
    """One immutable static fact selected only by deterministic keywords."""

    category: KnowledgeCategory
    keywords: tuple[str, ...]
    content: str


ENTERPRISE_TERM_ITEMS = (
    KnowledgeItem(
        category="enterprise_term",
        keywords=("报销单", "费用报销"),
        content="企业术语：报销单是 OA 中用于费用报销申请与审批的流程单据。",
    ),
    KnowledgeItem(
        category="enterprise_term",
        keywords=("待办", "待审批"),
        content="企业术语：待办是等待当前用户处理的流程事项，不代表已经完成。",
    ),
)

MOCK_SYSTEM_ITEMS = (
    KnowledgeItem(
        category="mock_system",
        keywords=("mock", "模拟", "oa", "u8", "海康", "ivms"),
        content=(
            "Mock 系统说明：OA、U8 与 Hikvision iVMS 的当前能力只返回合成数据，"
            "不连接或写入真实业务系统。"
        ),
    ),
)

LIVE_SYSTEM_ITEMS = (
    KnowledgeItem(
        category="oa_system",
        keywords=("oa",),
        content="OA 系统说明：当前 OA 读取能力使用 live 模式，请求配置的 OA 业务系统。",
    ),
)

REPLAY_SYSTEM_ITEMS = (
    KnowledgeItem(
        category="oa_system",
        keywords=("oa", "replay", "回放"),
        content=(
            "OA 系统说明：当前 OA 读取能力使用 replay 模式，"
            "从本地合同包回放响应，不代表实时业务数据。"
        ),
    ),
)

POLICY_TEMPLATE_ITEMS = (
    KnowledgeItem(
        category="policy_template",
        keywords=("报销", "审批", "制度"),
        content=(
            "制度模板：费用报销按提交、主管审核、财务复核与归档描述；"
            "该模板只提供事实参考，不授权执行。"
        ),
    ),
    KnowledgeItem(
        category="policy_template",
        keywords=("设备", "巡检", "制度"),
        content=("制度模板：设备巡检按登记、复核与归档描述；该模板只提供事实参考，不授权执行。"),
    ),
)

_STATIC_ITEMS = ENTERPRISE_TERM_ITEMS + MOCK_SYSTEM_ITEMS + POLICY_TEMPLATE_ITEMS


@dataclass(frozen=True, slots=True)
class BasicKnowledge:
    """Build deterministic request context without owning mutable knowledge state."""

    static_items: tuple[KnowledgeItem, ...] = _STATIC_ITEMS

    def context_items(
        self,
        message: str,
        capabilities: Sequence[CapabilitySpec],
    ) -> tuple[str, ...]:
        """Return only matching code-defined facts for the bounded text path."""
        normalized_message = _normalize_for_match(message)
        return tuple(
            sanitize_knowledge_text(item.content)
            for item in self.static_items
            if any(_keyword_matches(keyword, normalized_message) for keyword in item.keywords)
        )

    def select_capability_candidates(
        self,
        message: str,
        capabilities: Sequence[CapabilitySpec],
    ) -> CapabilityCandidateSet:
        """Delegate to the single safe projection and deterministic Top-K selector."""
        from app.knowledge.capability_selection import select_capability_candidates

        return select_capability_candidates(message, capabilities)

    def no_capability_guidance(
        self,
        capabilities: Sequence[CapabilitySpec],
    ) -> tuple[str, str]:
        """Briefly tell the user what is unavailable and what is available."""
        active = sorted(
            (item for item in capabilities if item.status == "active"),
            key=lambda item: _safe_capability_id(item.capability_id),
        )
        if active:
            summaries = [
                _active_capability_summary(item) for item in active[:_MAX_GUIDANCE_CAPABILITIES]
            ]
            suffix = "" if len(active) <= _MAX_GUIDANCE_CAPABILITIES else " 等"
            overview = f"当前可用能力：{'、'.join(summaries)}{suffix}。"
            fallback_overview = f"Available capabilities: {', '.join(summaries)}{suffix}."
        else:
            overview = "当前没有可用能力。"
            fallback_overview = "No capabilities are currently available."
        return (
            f"暂未接入该能力。{overview}",
            f"No capability found. {fallback_overview}",
        )


def contains_sensitive_property_key(value: str) -> bool:
    """Apply credential word boundaries to keys, retaining location checks."""
    if any(pattern.search(value) for pattern in _SENSITIVE_LOCATION_OR_IDENTITY):
        return True
    # Exact business-contract exception, never a prefix or whole-schema exemption.
    if value == "authoritative_count":
        return False
    segmented = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", value)
    segmented = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", segmented)
    return any(
        _SENSITIVE_MARKER.fullmatch(part) is not None
        or part.casefold() in {"sessionid", "accesstoken", "refreshtoken"}
        for part in re.split(r"[_.-]+", segmented)
    )


def sanitize_knowledge_text(value: str) -> str:
    """Catch obvious static-knowledge author mistakes, not arbitrary secrets."""
    normalized = " ".join(value.replace("\r", "\n").split())
    if _SENSITIVE_MARKER.search(normalized) or any(
        pattern.search(normalized) for pattern in _SENSITIVE_LOCATION_OR_IDENTITY
    ):
        return _REDACTED
    return normalized.strip()


def _active_capability_summary(capability: CapabilitySpec) -> str:
    capability_id = _safe_capability_id(capability.capability_id)
    return {
        "oa.list_pending_workflows": "OA 待办",
        "oa.list_system_messages": "OA 系统消息",
    }.get(capability_id, capability_id)


def _safe_capability_id(value: str) -> str:
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > _MAX_CAPABILITY_ID_LENGTH
        or _SAFE_CAPABILITY_ID.fullmatch(normalized) is None
        or _SENSITIVE_MARKER.search(normalized) is not None
    ):
        return _REDACTED
    return normalized


def _normalize_for_match(value: str) -> str:
    return " ".join(value.replace("\r", "\n").split()).casefold()


def _keyword_matches(keyword: str, normalized_message: str) -> bool:
    normalized_keyword = _normalize_for_match(keyword)
    if normalized_keyword.isascii() and any(
        character.isalnum() for character in normalized_keyword
    ):
        return bool(
            re.search(
                rf"(?<![a-z0-9]){re.escape(normalized_keyword)}(?![a-z0-9])",
                normalized_message,
            )
        )
    return normalized_keyword in normalized_message


__all__ = (
    "BasicKnowledge",
    "ENTERPRISE_TERM_ITEMS",
    "KnowledgeItem",
    "MOCK_SYSTEM_ITEMS",
    "POLICY_TEMPLATE_ITEMS",
    "sanitize_knowledge_text",
)
