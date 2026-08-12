"""Value-free parent/child contract for the full-chain smoke check."""

from __future__ import annotations

from typing import Final, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

from scripts.smoke.capabilities import REQUIRED_ACTIVE_OA_CAPABILITY_IDS
from scripts.smoke.trace_contract import REQUIRED_TRACE_EVENTS

FULL_CHAIN_SCHEMA_VERSION: Final = "p2.smoke.full-chain.v1"
FullChainFailureCode: TypeAlias = Literal[
    "composition_build_failed",
    "authentication_failed",
    "runtime_request_failed",
    "envelope_invalid",
    "trace_incomplete",
    "probe_argv_invalid",
    "unknown_error",
]
FULL_CHAIN_FAILURE_CODES = frozenset(
    {
        "composition_build_failed",
        "authentication_failed",
        "runtime_request_failed",
        "envelope_invalid",
        "trace_incomplete",
        "probe_argv_invalid",
        "unknown_error",
    }
)


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
        return (
            self.failure_code(
                expected_capability_ids=expected_capability_ids
            )
            is None
        )

    def failure_code(
        self,
        *,
        expected_capability_ids: tuple[str, ...] = (
            REQUIRED_ACTIVE_OA_CAPABILITY_IDS
        ),
    ) -> FullChainFailureCode | None:
        if (
            self.schema_version != FULL_CHAIN_SCHEMA_VERSION
            or self.required_trace_event_count != len(REQUIRED_TRACE_EVENTS)
            or tuple(item.capability_id for item in self.capabilities)
            != expected_capability_ids
        ):
            return "unknown_error"
        for item in self.capabilities:
            if (
                item.successful_envelope is not True
                or item.normalized_data is not True
            ):
                return "envelope_invalid"
            if (
                item.selected_capability is not True
                or item.trace_events_complete is not True
                or item.observed_trace_event_count < len(REQUIRED_TRACE_EVENTS)
            ):
                return "trace_incomplete"
        return None


class FullChainFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    error_code: FullChainFailureCode
    schema_version: Literal["p2.smoke.full-chain.v1"]


__all__ = (
    "FULL_CHAIN_FAILURE_CODES",
    "FULL_CHAIN_SCHEMA_VERSION",
    "CapabilityFullChainOutcome",
    "FullChainFailure",
    "FullChainFailureCode",
    "FullChainOutcome",
)
