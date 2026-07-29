"""Pydantic-backed raw JSON structured-output adapter."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ValidationError

from app.ports.structured_output import (
    StructuredOutputError,
    StructuredOutputErrorCode,
    StructuredOutputResult,
)


class JSONStructuredOutputProvider:
    """Validate one raw JSON response without retry or provider coupling."""

    async def parse_to_schema(
        self,
        raw_response: str,
        schema_type: type[BaseModel],
        trace_metadata: dict[str, Any] | None = None,
    ) -> StructuredOutputResult:
        metadata = dict(trace_metadata or {})
        if not raw_response.strip():
            return _failure("empty_response", "Structured output was empty.", metadata)
        try:
            payload = json.loads(raw_response)
        except json.JSONDecodeError:
            return _failure("parse_error", "Structured output was not valid JSON.", metadata)
        try:
            parsed = schema_type.model_validate(payload)
        except ValidationError:
            return _failure(
                "validation_error",
                "Structured output did not match the required schema.",
                metadata,
            )
        except (TypeError, ValueError):
            return _failure(
                "schema_error",
                "Structured output schema validation failed.",
                metadata,
            )
        return StructuredOutputResult(
            parsed=parsed,
            trace_metadata=metadata,
        )


def _failure(
    error_code: StructuredOutputErrorCode,
    error_message: str,
    trace_metadata: dict[str, Any],
) -> StructuredOutputResult:
    return StructuredOutputResult(
        error=StructuredOutputError(
            error_code=error_code,
            error_message=error_message,
        ),
        trace_metadata=trace_metadata,
    )


__all__ = ("JSONStructuredOutputProvider",)
