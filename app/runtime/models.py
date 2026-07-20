"""Runtime-local structured output schema."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.ports.capability_registry import CapabilityTargetSystem, CapabilityType


class CapabilityRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability_id: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    target_system: CapabilityTargetSystem | None = None
    capability_type: CapabilityType | None = None

    @field_validator("capability_id", mode="before")
    @classmethod
    def normalize_capability_id(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value
