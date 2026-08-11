"""Value-free parent/child contract for the full-chain smoke check."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from scripts.smoke.capabilities import REQUIRED_ACTIVE_OA_CAPABILITY_IDS
from scripts.smoke.trace_contract import REQUIRED_TRACE_EVENTS

FULL_CHAIN_SCHEMA_VERSION = "p2.smoke.full-chain.v1"


class CapabilityFullChainOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    capability_id: str
    successful_envelope: bool
    normalized_data: bool
    selected_capability: bool
    trace_events_complete: bool
    observed_trace_event_count: int = Field(ge=0)


class FullChainOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: str
    required_trace_event_count: int = Field(ge=1)
    capabilities: tuple[CapabilityFullChainOutcome, ...]

    def passed(
        self,
        *,
        expected_capability_ids: tuple[str, ...] = (
            REQUIRED_ACTIVE_OA_CAPABILITY_IDS
        ),
    ) -> bool:
        if (
            self.schema_version != FULL_CHAIN_SCHEMA_VERSION
            or self.required_trace_event_count != len(REQUIRED_TRACE_EVENTS)
            or tuple(item.capability_id for item in self.capabilities)
            != expected_capability_ids
        ):
            return False
        return all(
            item.successful_envelope is True
            and item.normalized_data is True
            and item.selected_capability is True
            and item.trace_events_complete is True
            and item.observed_trace_event_count >= len(REQUIRED_TRACE_EVENTS)
            for item in self.capabilities
        )


__all__ = (
    "FULL_CHAIN_SCHEMA_VERSION",
    "CapabilityFullChainOutcome",
    "FullChainOutcome",
)
