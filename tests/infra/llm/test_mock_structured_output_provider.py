from __future__ import annotations

import asyncio
import inspect
from typing import Any, get_args

import pytest
from pydantic import BaseModel, ValidationError

from app.ports.structured_output import (
    StructuredOutputError,
    StructuredOutputErrorCode,
    StructuredOutputResult,
)


class SampleOutput(BaseModel):
    intent: str
    value: int


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_mock_provider_satisfies_structured_output_port() -> None:
    from app.infra.llm.mock_structured_output.mock_structured_output_provider import (
        MockStructuredOutputProvider,
    )

    provider = MockStructuredOutputProvider()

    assert hasattr(provider, "parse_to_schema")
    assert inspect.iscoroutinefunction(provider.parse_to_schema)
    sig = inspect.signature(provider.parse_to_schema)
    assert "raw_response" in sig.parameters
    assert "schema_type" in sig.parameters
    assert "trace_metadata" in sig.parameters
    # Verify return annotation matches StructuredOutputResult
    import typing

    hints = typing.get_type_hints(provider.parse_to_schema)
    assert hints.get("return") is StructuredOutputResult

    # Verify parameter count and order match Protocol exactly
    # Protocol: parse_to_schema(self, raw_response, schema_type, trace_metadata)
    params = list(sig.parameters.keys())
    assert params == ["raw_response", "schema_type", "trace_metadata"]


@pytest.mark.anyio
async def test_registered_input_returns_expected_result() -> None:
    from app.infra.llm.mock_structured_output.mock_structured_output_provider import (
        MockStructuredOutputProvider,
    )

    expected = SampleOutput(intent="query_pending", value=42)
    provider = MockStructuredOutputProvider()
    provider.register("RAW_QUERY_PENDING", SampleOutput, expected)
    trace_metadata: dict[str, Any] = {"case": "registered"}

    result = await provider.parse_to_schema(
        raw_response="RAW_QUERY_PENDING",
        schema_type=SampleOutput,
        trace_metadata=trace_metadata,
    )

    assert isinstance(result, StructuredOutputResult)
    assert result.error is None
    assert result.parsed is not None
    assert isinstance(result.parsed, SampleOutput)
    assert result.parsed.intent == "query_pending"
    assert result.parsed.value == 42
    assert result.trace_metadata == trace_metadata


@pytest.mark.anyio
async def test_registered_input_is_deterministic() -> None:
    """Same input always returns same result (deterministic)."""
    from app.infra.llm.mock_structured_output.mock_structured_output_provider import (
        MockStructuredOutputProvider,
    )

    expected = SampleOutput(intent="stable", value=1)
    provider = MockStructuredOutputProvider()
    provider.register("STABLE_INPUT", SampleOutput, expected)

    await asyncio.sleep(0)
    r1 = await provider.parse_to_schema("STABLE_INPUT", SampleOutput)
    r2 = await provider.parse_to_schema("STABLE_INPUT", SampleOutput)

    assert r1.parsed == r2.parsed


@pytest.mark.anyio
async def test_unknown_input_returns_error_not_first_registered() -> None:
    from app.infra.llm.mock_structured_output.mock_structured_output_provider import (
        MockStructuredOutputProvider,
    )

    provider = MockStructuredOutputProvider()
    provider.register("KNOWN_INPUT", SampleOutput, SampleOutput(intent="known", value=1))

    result = await provider.parse_to_schema(
        raw_response="UNKNOWN_INPUT_XYZ",
        schema_type=SampleOutput,
    )

    assert isinstance(result, StructuredOutputResult)
    assert result.error is not None
    assert result.parsed is None
    assert result.error.error_code == "schema_error"
    # result.parsed is already asserted None above - no registered value leaked


@pytest.mark.anyio
async def test_unknown_input_error_code_is_valid_literal() -> None:
    """The error_code returned for unknown input must be a valid StructuredOutputErrorCode."""
    from app.infra.llm.mock_structured_output.mock_structured_output_provider import (
        MockStructuredOutputProvider,
    )

    provider = MockStructuredOutputProvider()

    result = await provider.parse_to_schema("TOTALLY_UNKNOWN", SampleOutput)

    assert result.error is not None
    valid_codes = get_args(StructuredOutputErrorCode)
    assert result.error.error_code in valid_codes


@pytest.mark.anyio
async def test_malformed_model_output_returns_error_not_raises() -> None:
    """Malformed path returns StructuredOutputResult with error; no uncaught exception."""
    from app.infra.llm.mock_structured_output.mock_structured_output_provider import (
        MockStructuredOutputProvider,
    )

    provider = MockStructuredOutputProvider()
    provider.register_malformed("MALFORMED_SENTINEL", SampleOutput)

    result = await provider.parse_to_schema(
        raw_response="MALFORMED_SENTINEL",
        schema_type=SampleOutput,
    )

    assert isinstance(result, StructuredOutputResult)
    assert result.error is not None
    assert result.parsed is None
    assert result.error.error_code in ("parse_error", "validation_error")


@pytest.mark.anyio
async def test_malformed_model_output_no_exception_propagation() -> None:
    """Calling with malformed sentinel must not raise any exception."""
    from app.infra.llm.mock_structured_output.mock_structured_output_provider import (
        MockStructuredOutputProvider,
    )

    provider = MockStructuredOutputProvider()
    provider.register_malformed("MALFORMED_SENTINEL2", SampleOutput)

    try:
        result = await provider.parse_to_schema("MALFORMED_SENTINEL2", SampleOutput)
        assert result.error is not None
    except Exception as exc:  # pragma: no cover - failure branch reported by pytest.
        pytest.fail(f"parse_to_schema must not raise, but got: {exc}")


@pytest.mark.parametrize(
    "code",
    ["parse_error", "validation_error", "schema_error", "empty_response"],
)
def test_all_structured_output_error_codes_constructible(
    code: StructuredOutputErrorCode,
) -> None:
    """Every allowed StructuredOutputErrorCode value must be constructible."""
    err = StructuredOutputError(
        error_code=code,
        error_message=f"test for {code}",
    )

    assert err.error_code == code


def test_invalid_structured_output_error_code_raises_validation_error() -> None:
    """Invalid error_code must raise ValidationError - guards against future drift."""
    with pytest.raises(ValidationError):
        StructuredOutputError(
            error_code="no_capability_found",
            error_message="this should fail",
        )


def test_another_invalid_error_code_raises() -> None:
    with pytest.raises(ValidationError):
        StructuredOutputError(
            error_code="structured_output_failed",
            error_message="this should fail",
        )


def test_structured_output_error_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        StructuredOutputError(
            error_code="parse_error",
            error_message="err",
            unexpected_field="bad",
        )


def test_structured_output_result_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        StructuredOutputResult(
            parsed=None,
            unexpected_field="bad",
        )


def test_structured_output_error_raw_response_accepts_arbitrary_string() -> None:
    """raw_response on StructuredOutputError is open str - test arbitrary value."""
    err = StructuredOutputError(
        error_code="parse_error",
        error_message="err",
        raw_response="arbitrary-raw-value-for-lock-test-xyzzy",
    )

    assert err.raw_response == "arbitrary-raw-value-for-lock-test-xyzzy"


def test_structured_output_result_raw_response_accepts_arbitrary_string() -> None:
    """raw_response on StructuredOutputResult is open str - test arbitrary value."""
    result = StructuredOutputResult(
        parsed=None,
        raw_response="arbitrary-raw-value-for-lock-test-abcde",
    )

    assert result.raw_response == "arbitrary-raw-value-for-lock-test-abcde"


# SDK import scan verified separately: no openai/instructor/pydantic_ai/dashscope/vllm
# imports in app/infra/llm/mock_structured_output/mock_structured_output_provider.py.
