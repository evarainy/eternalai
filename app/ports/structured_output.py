"""Structured output port contract."""

from __future__ import annotations

from typing import Any, Literal, Protocol, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

StructuredOutputErrorCode: TypeAlias = Literal[
    "parse_error",
    "validation_error",
    "schema_error",
    "empty_response",
]


class StructuredOutputError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error_code: StructuredOutputErrorCode
    error_message: str
    raw_response: str | None = None


class StructuredOutputResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parsed: Any | None = None
    error: StructuredOutputError | None = None
    trace_metadata: dict[str, Any] = Field(default_factory=dict)
    raw_response: str | None = None


class StructuredOutputPort(Protocol):
    async def parse_to_schema(
        self,
        raw_response: str,
        schema_type: type[BaseModel],
        trace_metadata: dict[str, Any] | None = None,
    ) -> StructuredOutputResult: ...
