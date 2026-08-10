"""Capability registry interface contract."""

from __future__ import annotations

import re
import unicodedata
from typing import Annotated, Any, Literal, Protocol, TypeAlias

from pydantic import BaseModel, BeforeValidator, Field, StringConstraints

CapabilityType: TypeAlias = Literal["query", "action", "workflow", "mock"]
CapabilityRiskLevel: TypeAlias = Literal["low", "medium", "high"]
CapabilityStatus: TypeAlias = Literal["draft", "active", "disabled", "deprecated"]
CapabilityTargetSystem: TypeAlias = Literal["oa", "u8", "hikvision_ivms"]
CapabilityExecutionIdentity: TypeAlias = Literal[
    "user_delegated",
    "system_scope",
    "admin_approved_proxy",
]

CAPABILITY_NAME_MAX_LENGTH = 120
CAPABILITY_OWNER_MAX_LENGTH = 120
CAPABILITY_SHORT_DESCRIPTION_MAX_LENGTH = 500
CAPABILITY_INTENT_TAG_MAX_LENGTH = 64
CAPABILITY_INTENT_TAGS_MAX_ITEMS = 32

_PROMPT_STRUCTURAL_CHARACTERS = frozenset("\\`|<>{}[]")
_INTENT_TAG_PATTERN = r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$"
_INTENT_TAG_PATTERN_RE = re.compile(_INTENT_TAG_PATTERN, flags=re.ASCII)


def _normalize_prompt_safe_text(value: object, *, field_name: str) -> object:
    if not isinstance(value, str):
        return value
    normalized = unicodedata.normalize("NFKC", value)
    if not normalized.isprintable():
        raise ValueError(f"{field_name} must contain only printable characters")
    if any(
        character != " " and unicodedata.category(character).startswith("Z")
        for character in normalized
    ):
        raise ValueError(f"{field_name} must use ASCII space separators")
    if any(character in _PROMPT_STRUCTURAL_CHARACTERS for character in normalized):
        raise ValueError(f"{field_name} contains a reserved prompt delimiter")
    return normalized.strip()


def _normalize_capability_name(value: object) -> object:
    return _normalize_prompt_safe_text(value, field_name="name")


def _normalize_capability_owner(value: object) -> object:
    return _normalize_prompt_safe_text(value, field_name="owner")


def _normalize_capability_short_description(value: object) -> object:
    return _normalize_prompt_safe_text(value, field_name="short_description")


def _normalize_intent_tag(value: object) -> object:
    if not isinstance(value, str):
        return value
    normalized = unicodedata.normalize("NFKC", value)
    if not normalized.isprintable():
        raise ValueError("intent_tags must contain only printable characters")
    if not normalized.isascii():
        raise ValueError("intent_tags must use the ASCII tag character set")
    normalized = normalized.strip().lower()
    if not _INTENT_TAG_PATTERN_RE.fullmatch(normalized):
        raise ValueError("intent_tags must use ASCII slug form")
    return normalized


CapabilityName: TypeAlias = Annotated[
    str,
    StringConstraints(min_length=1, max_length=CAPABILITY_NAME_MAX_LENGTH),
    BeforeValidator(_normalize_capability_name),
]
CapabilityOwner: TypeAlias = Annotated[
    str,
    StringConstraints(min_length=1, max_length=CAPABILITY_OWNER_MAX_LENGTH),
    BeforeValidator(_normalize_capability_owner),
]
CapabilityShortDescription: TypeAlias = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=CAPABILITY_SHORT_DESCRIPTION_MAX_LENGTH,
    ),
    BeforeValidator(_normalize_capability_short_description),
]
CapabilityIntentTag: TypeAlias = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=CAPABILITY_INTENT_TAG_MAX_LENGTH,
        pattern=_INTENT_TAG_PATTERN,
    ),
    BeforeValidator(_normalize_intent_tag),
]
CapabilityIntentTags: TypeAlias = Annotated[
    list[CapabilityIntentTag],
    Field(max_length=CAPABILITY_INTENT_TAGS_MAX_ITEMS),
]


class CapabilitySpec(BaseModel):
    capability_id: str
    name: CapabilityName
    type: CapabilityType
    intent_tags: CapabilityIntentTags = Field(default_factory=list)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    input_schema_digest: str
    output_schema_digest: str
    risk_level: CapabilityRiskLevel
    owner: CapabilityOwner
    version: str
    status: CapabilityStatus
    short_description: CapabilityShortDescription
    target_system: CapabilityTargetSystem | None = None
    execution_identity: CapabilityExecutionIdentity
    binding_required: bool
    policy_digest: str | None = None


class CapabilityRegistryPort(Protocol):
    async def create(self, capability: CapabilitySpec) -> CapabilitySpec: ...

    async def get(self, capability_id: str) -> CapabilitySpec | None: ...

    async def list(
        self,
        target_system: str | None = None,
        type: str | None = None,
        status: str | None = None,
    ) -> list[CapabilitySpec]: ...

    async def update(self, capability_id: str, patch: dict[str, Any]) -> CapabilitySpec: ...

    async def disable(self, capability_id: str) -> CapabilitySpec: ...
