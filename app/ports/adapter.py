"""Adapter interface contract."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, Literal, Mapping, Protocol, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.ports.capability_gateway import ErrorCode

AdapterStatus: TypeAlias = Literal["success", "error", "timeout", "permission_denied"]

AdapterFailureStage: TypeAlias = Literal[
    "argument_validation",
    "credential_read",
    "provider_transport",
    "normalization",
    "unknown",
]

MockErrorMode: TypeAlias = Literal[
    "timeout",
    "permission_denied",
    "malformed_json",
    "empty_response",
    "http_500",
    "missing_required_field",
]

MOCK_ERROR_MODE_TO_ERROR_CODE: Mapping[MockErrorMode, ErrorCode] = MappingProxyType(
    {
        "timeout": "adapter_timeout",
        "permission_denied": "upstream_permission_denied",
        "malformed_json": "adapter_payload_invalid",
        "empty_response": "adapter_empty_response",
        "http_500": "adapter_http_500",
        "missing_required_field": "adapter_missing_required_field",
    }
)


class AdapterTraceMetadata(BaseModel):
    """Bounded adapter diagnostics safe for persistent Trace attributes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    argument_keys: tuple[str, ...] = ()
    failure_stage: AdapterFailureStage | None = None

    @field_validator("argument_keys")
    @classmethod
    def _require_sorted_unique_keys(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("argument_keys must be sorted and unique")
        return value


class AdapterResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: AdapterStatus
    data: dict[str, Any] | None = None
    error_code: ErrorCode | None = None
    raw_payload_ref: str | None = None
    trace_metadata: AdapterTraceMetadata | None = Field(
        default=None,
        exclude=True,
        repr=False,
    )


class AdapterPort(Protocol):
    async def execute(
        self,
        capability_id: str,
        arguments: dict[str, Any],
        execution_context: dict[str, Any],
    ) -> AdapterResult: ...
