"""Runtime-local structured output schema."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CapabilityRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability_id: str
    arguments: dict[str, Any] = Field(default_factory=dict)
