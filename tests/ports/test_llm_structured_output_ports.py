"""Contract tests for LLM provider and structured output ports."""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import Any, get_args, get_origin, get_type_hints

import pytest
from pydantic import BaseModel, ValidationError

from app.ports.llm_provider import (
    LLMCompletionResponse,
    LLMMessage,
    LLMMessageRole,
    LLMProviderErrorCode,
    LLMProviderPort,
)
from app.ports.structured_output import (
    StructuredOutputError,
    StructuredOutputErrorCode,
    StructuredOutputPort,
    StructuredOutputResult,
)


class _MockLLMProviderPort:
    async def complete(
        self,
        messages: list[LLMMessage],
        model: str,
        response_format: dict[str, Any] | None = None,
    ) -> LLMCompletionResponse:
        return LLMCompletionResponse(
            content="mock response",
            model_used=model,
            trace_metadata={"mock": True},
        )

    async def chat(
        self,
        messages: list[LLMMessage],
        model: str,
        response_format: dict[str, Any] | None = None,
    ) -> LLMCompletionResponse:
        return LLMCompletionResponse(
            content="mock chat response",
            model_used=model,
            trace_metadata={"mock": True},
        )


class _MockStructuredOutputPort:
    async def parse_to_schema(
        self,
        raw_response: str,
        schema_type: type[BaseModel],
        trace_metadata: dict[str, Any] | None = None,
    ) -> StructuredOutputResult:
        import json

        try:
            data = json.loads(raw_response)
            parsed = schema_type.model_validate(data)
            return StructuredOutputResult(
                parsed=parsed,
                trace_metadata=trace_metadata or {},
                raw_response=raw_response,
            )
        except Exception as exc:
            return StructuredOutputResult(
                error=StructuredOutputError(
                    error_code="parse_error",
                    error_message=str(exc),
                ),
                raw_response=raw_response,
            )


def test_llm_message_role_literal_values() -> None:
    assert get_args(LLMMessageRole) == ("system", "user", "assistant")


def test_llm_message_role_accepts_all_valid_values() -> None:
    for role in ("system", "user", "assistant"):
        msg = LLMMessage(role=role, content="test content")
        assert msg.role == role


def test_llm_message_role_rejects_invalid_value() -> None:
    with pytest.raises(ValidationError) as exc_info:
        LLMMessage(role="tool", content="test")
    assert "role" in str(exc_info.value)


def test_llm_provider_error_code_literal_values() -> None:
    assert get_args(LLMProviderErrorCode) == (
        "timeout",
        "rate_limited",
        "context_length_exceeded",
        "provider_error",
        "invalid_request",
        "model_not_available",
    )


def test_llm_provider_error_code_accepts_all_valid_values() -> None:
    for code in get_args(LLMProviderErrorCode):
        resp = LLMCompletionResponse(error_code=code, error_message="test")
        assert resp.error_code == code


def test_llm_provider_error_code_rejects_invalid_value() -> None:
    with pytest.raises(ValidationError) as exc_info:
        LLMCompletionResponse(error_code="unknown_error")
    assert "error_code" in str(exc_info.value)


def test_llm_message_has_extra_forbid_config() -> None:
    with pytest.raises(ValidationError):
        LLMMessage(role="user", content="test", extra_field="x")


def test_llm_message_requires_role_and_content() -> None:
    with pytest.raises(ValidationError) as exc_info:
        LLMMessage()
    error_text = str(exc_info.value)
    assert "role" in error_text
    assert "content" in error_text


def test_llm_message_content_accepts_arbitrary_string() -> None:
    msg = LLMMessage(
        role="user",
        content="arbitrary-content-sentinel-xyz-123 special chars !@#",
    )
    assert msg.content == "arbitrary-content-sentinel-xyz-123 special chars !@#"


def test_llm_message_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        LLMMessage(role="user", content="test", injected="x")


def test_llm_completion_response_has_extra_forbid_config() -> None:
    with pytest.raises(ValidationError):
        LLMCompletionResponse(extra_field="x")


def test_llm_completion_response_defaults_all_optional_fields() -> None:
    resp = LLMCompletionResponse()
    assert resp.content is None
    assert resp.error_code is None
    assert resp.error_message is None
    assert resp.trace_metadata == {}
    assert resp.model_used is None
    assert resp.usage_tokens is None


def test_llm_completion_response_supports_error_type() -> None:
    resp = LLMCompletionResponse(
        error_code="timeout",
        error_message="Connection timed out after 30s",
    )
    assert resp.error_code == "timeout"
    assert resp.error_message == "Connection timed out after 30s"


def test_llm_completion_response_trace_metadata_is_open_dict() -> None:
    resp = LLMCompletionResponse(
        trace_metadata={
            "request_id": "req-1",
            "latency_ms": 42,
            "nested": {"model": "qwen"},
        },
    )
    assert resp.trace_metadata["request_id"] == "req-1"
    assert resp.trace_metadata["nested"]["model"] == "qwen"


def test_llm_completion_response_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        LLMCompletionResponse(unknown_field="x")


def test_llm_completion_response_accepts_all_error_code_values() -> None:
    for code in get_args(LLMProviderErrorCode):
        resp = LLMCompletionResponse(error_code=code)
        assert resp.error_code == code


def test_llm_completion_response_rejects_invalid_error_code() -> None:
    with pytest.raises(ValidationError):
        LLMCompletionResponse(error_code="not_a_valid_code")


def test_llm_completion_response_defines_no_plaintext_credential_slots() -> None:
    forbidden = {
        "password",
        "token",
        "cookie",
        "sessionid",
        "access_token",
        "refresh_token",
        "api_key",
        "private_key",
        "secret",
    }
    assert forbidden.isdisjoint(LLMCompletionResponse.model_fields.keys())


def test_llm_completion_response_content_accepts_arbitrary_string() -> None:
    resp = LLMCompletionResponse(
        content="arbitrary-sentinel-content-xyz-123 freeform text !@#",
    )
    assert resp.content == "arbitrary-sentinel-content-xyz-123 freeform text !@#"


def test_llm_completion_response_error_message_accepts_arbitrary_string() -> None:
    resp = LLMCompletionResponse(
        error_message="arbitrary-sentinel-error-msg-xyz-123 freeform text !@#",
    )
    assert resp.error_message == "arbitrary-sentinel-error-msg-xyz-123 freeform text !@#"


def test_llm_completion_response_model_used_accepts_arbitrary_string() -> None:
    resp = LLMCompletionResponse(
        model_used="arbitrary-model-name-sentinel-xyz-qwen3-72b-custom",
    )
    assert resp.model_used == "arbitrary-model-name-sentinel-xyz-qwen3-72b-custom"


def test_llm_provider_port_supports_complete_or_chat_abstraction() -> None:
    assert (
        "complete" in LLMProviderPort.__protocol_attrs__
        or "chat" in LLMProviderPort.__protocol_attrs__
    )


def test_llm_provider_port_defines_complete_and_chat() -> None:
    assert "complete" in LLMProviderPort.__protocol_attrs__
    assert "chat" in LLMProviderPort.__protocol_attrs__


def test_complete_is_coroutine_function() -> None:
    assert inspect.iscoroutinefunction(LLMProviderPort.complete)


def test_complete_signature() -> None:
    sig = inspect.signature(LLMProviderPort.complete)
    assert list(sig.parameters) == ["self", "messages", "model", "response_format"]
    hints = get_type_hints(LLMProviderPort.complete)
    assert hints["model"] is str
    assert hints["return"] is LLMCompletionResponse
    response_format_hint = hints["response_format"]
    import types as _types

    assert get_origin(response_format_hint) is _types.UnionType or str(
        response_format_hint,
    ) in (
        "dict[str, typing.Any] | None",
        "typing.Optional[dict[str, typing.Any]]",
        "dict[str, Any] | None",
    )


def test_chat_is_coroutine_function() -> None:
    assert inspect.iscoroutinefunction(LLMProviderPort.chat)


def test_chat_signature() -> None:
    sig = inspect.signature(LLMProviderPort.chat)
    assert list(sig.parameters) == ["self", "messages", "model", "response_format"]
    hints = get_type_hints(LLMProviderPort.chat)
    assert hints["model"] is str
    assert hints["return"] is LLMCompletionResponse


def test_structured_output_error_code_literal_values() -> None:
    assert get_args(StructuredOutputErrorCode) == (
        "parse_error",
        "validation_error",
        "schema_error",
        "empty_response",
    )


def test_structured_output_error_code_accepts_all_valid_values() -> None:
    for code in get_args(StructuredOutputErrorCode):
        err = StructuredOutputError(error_code=code, error_message="test")
        assert err.error_code == code


def test_structured_output_error_code_rejects_invalid_value() -> None:
    with pytest.raises(ValidationError):
        StructuredOutputError(error_code="unknown", error_message="test")


def test_structured_output_error_has_extra_forbid_config() -> None:
    with pytest.raises(ValidationError):
        StructuredOutputError(
            error_code="parse_error",
            error_message="t",
            extra="x",
        )


def test_structured_output_error_requires_error_code_and_message() -> None:
    with pytest.raises(ValidationError) as exc_info:
        StructuredOutputError()
    assert "error_code" in str(exc_info.value)
    assert "error_message" in str(exc_info.value)


def test_structured_output_error_raw_response_defaults_to_none() -> None:
    err = StructuredOutputError(error_code="parse_error", error_message="test")
    assert err.raw_response is None


def test_structured_output_error_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        StructuredOutputError(error_code="parse_error", error_message="t", injected="x")


def test_structured_output_error_error_message_accepts_arbitrary_string() -> None:
    err = StructuredOutputError(
        error_code="parse_error",
        error_message="arbitrary-sentinel-error-freeform xyz !@# 123",
    )
    assert err.error_message == "arbitrary-sentinel-error-freeform xyz !@# 123"


def test_structured_output_error_raw_response_accepts_arbitrary_string() -> None:
    err = StructuredOutputError(
        error_code="validation_error",
        error_message="test",
        raw_response='{"arbitrary_key": "arbitrary_value_sentinel_xyz_123"}',
    )
    assert err.raw_response == '{"arbitrary_key": "arbitrary_value_sentinel_xyz_123"}'


def test_structured_output_result_has_extra_forbid_config() -> None:
    with pytest.raises(ValidationError):
        StructuredOutputResult(extra_field="x")


def test_structured_output_result_defaults_all_optional_fields() -> None:
    result = StructuredOutputResult()
    assert result.parsed is None
    assert result.error is None
    assert result.trace_metadata == {}
    assert result.raw_response is None


def test_structured_output_result_supports_parsed_pydantic_instance() -> None:
    class _TestModel(BaseModel):
        name: str
        value: int

    instance = _TestModel(name="test", value=42)
    result = StructuredOutputResult(parsed=instance)
    assert isinstance(result.parsed, _TestModel)
    assert result.parsed.name == "test"


def test_structured_output_result_supports_error_field() -> None:
    err = StructuredOutputError(error_code="parse_error", error_message="invalid JSON")
    result = StructuredOutputResult(error=err)
    assert result.error is not None
    assert result.error.error_code == "parse_error"


def test_structured_output_result_raw_response_accepts_arbitrary_string() -> None:
    result = StructuredOutputResult(
        raw_response='{"arbitrary_key":"arbitrary_sentinel_value_xyz_123","nested":true}',
    )
    assert (
        result.raw_response
        == '{"arbitrary_key":"arbitrary_sentinel_value_xyz_123","nested":true}'
    )


def test_structured_output_result_trace_metadata_is_open_dict() -> None:
    result = StructuredOutputResult(
        trace_metadata={"request_id": "r1", "model": "qwen-72b", "latency": 42},
    )
    assert result.trace_metadata["request_id"] == "r1"


def test_structured_output_result_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        StructuredOutputResult(unknown_field="x")


def test_structured_output_result_parsed_accepts_non_model_value() -> None:
    result_dict = StructuredOutputResult(
        parsed={"capability_id": "oa_leave", "confidence": 0.95},
    )
    assert result_dict.parsed == {"capability_id": "oa_leave", "confidence": 0.95}
    result_list = StructuredOutputResult(parsed=["item1", "item2"])
    assert result_list.parsed == ["item1", "item2"]


def test_structured_output_port_defines_parse_to_schema() -> None:
    assert "parse_to_schema" in StructuredOutputPort.__protocol_attrs__


def test_parse_to_schema_is_coroutine_function() -> None:
    assert inspect.iscoroutinefunction(StructuredOutputPort.parse_to_schema)


def test_parse_to_schema_signature() -> None:
    sig = inspect.signature(StructuredOutputPort.parse_to_schema)
    assert list(sig.parameters) == [
        "self",
        "raw_response",
        "schema_type",
        "trace_metadata",
    ]
    hints = get_type_hints(StructuredOutputPort.parse_to_schema)
    assert hints["raw_response"] is str
    assert hints["return"] is StructuredOutputResult


def test_concrete_mock_llm_provider_complete_returns_real_response() -> None:
    async def exercise() -> None:
        port = _MockLLMProviderPort()
        result = await port.complete(
            [LLMMessage(role="user", content="test")],
            model="qwen-72b",
        )
        assert isinstance(result, LLMCompletionResponse)

    asyncio.run(exercise())


def test_concrete_mock_llm_provider_chat_returns_real_response() -> None:
    async def exercise() -> None:
        port = _MockLLMProviderPort()
        result = await port.chat(
            [LLMMessage(role="user", content="hello")],
            model="qwen-72b",
        )
        assert isinstance(result, LLMCompletionResponse)

    asyncio.run(exercise())


def test_concrete_mock_llm_provider_accepts_all_message_roles() -> None:
    async def exercise() -> None:
        port = _MockLLMProviderPort()
        for role in ("system", "user", "assistant"):
            result = await port.complete(
                [LLMMessage(role=role, content="test content")],
                model="qwen-72b",
            )
            assert isinstance(result, LLMCompletionResponse)

    asyncio.run(exercise())


def test_concrete_mock_structured_output_parse_to_schema_returns_real_result() -> None:
    class _SimpleModel(BaseModel):
        name: str

    async def exercise() -> None:
        port = _MockStructuredOutputPort()
        result = await port.parse_to_schema('{"name":"test"}', _SimpleModel)
        assert isinstance(result, StructuredOutputResult)

    asyncio.run(exercise())


def test_concrete_mock_structured_output_parse_to_schema_with_trace_metadata() -> None:
    class _SimpleModel(BaseModel):
        name: str

    async def exercise() -> None:
        port = _MockStructuredOutputPort()
        result = await port.parse_to_schema(
            '{"name":"test"}',
            _SimpleModel,
            trace_metadata={"request_id": "r1", "model": "qwen-72b"},
        )
        assert result.trace_metadata["request_id"] == "r1"

    asyncio.run(exercise())


def test_concrete_mock_structured_output_parse_error_path() -> None:
    class _SimpleModel(BaseModel):
        name: str

    async def exercise() -> None:
        port = _MockStructuredOutputPort()
        result = await port.parse_to_schema("not-json", _SimpleModel)
        assert result.error is not None
        assert result.parsed is None

    asyncio.run(exercise())


def test_phase1_baseline_preserved_no_sdk_imports_in_llm_provider() -> None:
    source = Path("app/ports/llm_provider.py").read_text(encoding="utf-8")
    for term in ("openai", "instructor", "pydantic_ai", "dashscope", "vllm"):
        assert term not in source, f"Forbidden provider term: {term!r}"


def test_phase1_baseline_preserved_no_sdk_imports_in_structured_output() -> None:
    source = Path("app/ports/structured_output.py").read_text(encoding="utf-8")
    for term in ("openai", "instructor", "pydantic_ai", "dashscope", "vllm"):
        assert term not in source, f"Forbidden provider term: {term!r}"


def test_structured_output_port_supports_pydantic_model_validate_pattern() -> None:
    class _TestCapabilityRef(BaseModel):
        capability_id: str
        confidence: float

    class _PatternMockPort:
        async def parse_to_schema(
            self,
            raw_response: str,
            schema_type: type[BaseModel],
            trace_metadata: dict[str, Any] | None = None,
        ) -> StructuredOutputResult:
            import json

            data = json.loads(raw_response)
            parsed = schema_type.model_validate(data)
            return StructuredOutputResult(
                parsed=parsed,
                trace_metadata=trace_metadata or {},
            )

    async def exercise() -> None:
        port = _PatternMockPort()
        result = await port.parse_to_schema(
            '{"capability_id":"oa_leave","confidence":0.95}',
            _TestCapabilityRef,
        )
        assert isinstance(result.parsed, _TestCapabilityRef)
        assert result.parsed.capability_id == "oa_leave"
        assert result.parsed.confidence == 0.95

    asyncio.run(exercise())
