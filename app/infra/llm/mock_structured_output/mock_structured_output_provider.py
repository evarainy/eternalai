from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.ports.structured_output import (
    StructuredOutputError,
    StructuredOutputResult,
)


class MockStructuredOutputProvider:
    """Deterministic mock for StructuredOutputPort. No real LLM calls."""

    def __init__(self) -> None:
        self._registry: dict[tuple[str, type[BaseModel]], StructuredOutputResult] = {}
        self._malformed: set[tuple[str, type[BaseModel]]] = set()

    def register(
        self,
        raw_response_key: str,
        schema_type: type[BaseModel],
        parsed_result: BaseModel,
    ) -> None:
        """Register a deterministic response for a (key, schema_type) pair."""
        self._registry[(raw_response_key, schema_type)] = StructuredOutputResult(
            parsed=parsed_result,
            raw_response=raw_response_key,
        )

    def register_malformed(
        self,
        raw_response_key: str,
        schema_type: type[BaseModel],
    ) -> None:
        """Register a sentinel that will trigger parse failure."""
        self._malformed.add((raw_response_key, schema_type))

    async def parse_to_schema(
        self,
        raw_response: str,
        schema_type: type[BaseModel],
        trace_metadata: dict[str, Any] | None = None,
    ) -> StructuredOutputResult:
        key = (raw_response, schema_type)

        if key in self._malformed:
            return StructuredOutputResult(
                parsed=None,
                error=StructuredOutputError(
                    error_code="parse_error",
                    error_message=f"Simulated parse failure for key={raw_response!r}",
                    raw_response=raw_response,
                ),
                raw_response=raw_response,
                trace_metadata=trace_metadata or {},
            )

        if key in self._registry:
            result = self._registry[key]
            return StructuredOutputResult(
                parsed=result.parsed,
                error=None,
                raw_response=raw_response,
                trace_metadata=trace_metadata or {},
            )

        return StructuredOutputResult(
            parsed=None,
            error=StructuredOutputError(
                error_code="schema_error",
                error_message=(
                    f"No registered mock for raw_response={raw_response!r} "
                    f"with schema={schema_type.__name__}"
                ),
                raw_response=raw_response,
            ),
            raw_response=raw_response,
            trace_metadata=trace_metadata or {},
        )
