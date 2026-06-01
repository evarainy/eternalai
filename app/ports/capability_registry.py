"""Capability registry interface contract."""

from __future__ import annotations

from typing import Any, Literal, Protocol, TypeAlias

from pydantic import BaseModel, Field

CapabilityType: TypeAlias = Literal["query", "action", "workflow", "mock"]
CapabilityRiskLevel: TypeAlias = Literal["low", "medium", "high"]
CapabilityStatus: TypeAlias = Literal["draft", "active", "disabled", "deprecated"]
CapabilityTargetSystem: TypeAlias = Literal["oa", "u8", "hikvision_ivms"]
CapabilityExecutionIdentity: TypeAlias = Literal[
    "user_delegated",
    "system_scope",
    "admin_approved_proxy",
]


class CapabilitySpec(BaseModel):
    capability_id: str
    name: str
    type: CapabilityType
    intent_tags: list[str] = Field(default_factory=list)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    input_schema_digest: str
    output_schema_digest: str
    risk_level: CapabilityRiskLevel
    owner: str
    version: str
    status: CapabilityStatus
    short_description: str
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
