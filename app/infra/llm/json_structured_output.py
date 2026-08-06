"""Pydantic-backed raw JSON structured-output adapter."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, ValidationError

from app.ports.structured_output import (
    StructuredOutputError,
    StructuredOutputErrorCode,
    StructuredOutputResult,
)

_SAFE_ERROR_TYPE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_SAFE_ARGUMENT_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}")
_SAFE_CAPABILITY_REF_PATHS = frozenset(
    {"capability_id", "arguments", "target_system", "capability_type"}
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
        except ValidationError as exc:
            metadata.update(_validation_diagnostics(exc, payload))
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


def _validation_diagnostics(
    error: ValidationError,
    payload: Any,
) -> dict[str, Any]:
    errors = error.errors(
        include_url=False,
        include_context=False,
        include_input=False,
    )
    location = errors[0]["loc"] if errors else ()
    first_segment = location[0] if location else None
    error_path = (
        f"$.{first_segment}"
        if isinstance(first_segment, str)
        and first_segment in _SAFE_CAPABILITY_REF_PATHS
        else "$"
    )
    raw_error_type = errors[0]["type"] if errors else None
    error_type = (
        raw_error_type
        if isinstance(raw_error_type, str)
        and _SAFE_ERROR_TYPE.fullmatch(raw_error_type) is not None
        else "validation_error"
    )
    arguments = payload.get("arguments") if isinstance(payload, dict) else None
    return {
        "error_path": error_path,
        "error_type": error_type,
        "argument_keys": _bounded_argument_keys(arguments),
    }


def _bounded_argument_keys(arguments: Any) -> list[str]:
    if not isinstance(arguments, dict):
        return []
    keys = sorted(key for key in arguments if isinstance(key, str))
    return [
        key if _SAFE_ARGUMENT_KEY.fullmatch(key) is not None else "[REDACTED]"
        for key in keys[:32]
    ]


__all__ = ("JSONStructuredOutputProvider",)
