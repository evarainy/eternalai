"""Intent Router boundary and validation behavior."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.infra.llm.mock_llm.mock_llm_provider import MockLLMProvider
from app.ports.llm_provider import LLMCompletionResponse
from app.ports.structured_output import (
    StructuredOutputError,
    StructuredOutputResult,
)
from app.runtime.intent_router import IntentRouter
from app.runtime.models import CapabilityRef


class RecordingStructuredOutput:
    def __init__(self, result: StructuredOutputResult) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def parse_to_schema(
        self,
        raw_response: str,
        schema_type: type[Any],
        trace_metadata: dict[str, Any] | None = None,
    ) -> StructuredOutputResult:
        self.calls.append(
            {
                "raw_response": raw_response,
                "schema_type": schema_type,
                "trace_metadata": trace_metadata,
            }
        )
        return self.result


def test_router_normalizes_input_and_uses_both_frozen_boundaries() -> None:
    llm_provider = MockLLMProvider()
    llm_provider.register(
        "查 OA\n待办",
        LLMCompletionResponse(
            content='{"capability_id":"pending-workflows"}',
            trace_metadata={"provider_request_id": "request-1"},
        ),
    )
    structured_output = RecordingStructuredOutput(
        StructuredOutputResult(
            parsed={
                "capability_id": "pending-workflows",
                "arguments": {},
                "target_system": "oa",
                "capability_type": "query",
            }
        )
    )
    router = IntentRouter(llm_provider, structured_output, " qwen-test ")

    result = asyncio.run(
        router.parse(
            "  查 OA\r\n待办  ",
            trace_metadata={"trace_id": "trace-1"},
        )
    )

    assert result == CapabilityRef(
        capability_id="pending-workflows",
        target_system="oa",
        capability_type="query",
    )
    llm_call = llm_provider.calls[0]
    assert llm_call["method"] == "complete"
    assert llm_call["model"] == "qwen-test"
    assert llm_call["response_format"] == {"type": "json_object"}
    assert [message.role for message in llm_call["messages"]] == ["system", "user"]
    assert llm_call["messages"][-1].content == "查 OA\n待办"
    assert structured_output.calls == [
        {
            "raw_response": '{"capability_id":"pending-workflows"}',
            "schema_type": CapabilityRef,
            "trace_metadata": {
                "provider_request_id": "request-1",
                "trace_id": "trace-1",
            },
        }
    ]


@pytest.mark.parametrize(
    "completion",
    [
        LLMCompletionResponse(content=None),
        LLMCompletionResponse(content="   "),
        LLMCompletionResponse(error_code="provider_error", error_message="failed"),
    ],
)
def test_router_fails_closed_before_structured_output_on_llm_failure(
    completion: LLMCompletionResponse,
) -> None:
    llm_provider = MockLLMProvider()
    llm_provider.register("request", completion)
    structured_output = RecordingStructuredOutput(
        StructuredOutputResult(parsed=CapabilityRef(capability_id="must-not-run"))
    )
    router = IntentRouter(llm_provider, structured_output, "qwen-test")

    result = asyncio.run(router.parse("request"))

    assert result is None
    assert len(llm_provider.calls) == 1
    assert structured_output.calls == []


@pytest.mark.parametrize(
    "structured_result",
    [
        StructuredOutputResult(
            error=StructuredOutputError(
                error_code="validation_error",
                error_message="invalid intent",
            )
        ),
        StructuredOutputResult(parsed={"capability_id": "", "arguments": {}}),
        StructuredOutputResult(parsed={"capability_id": "   ", "arguments": {}}),
        StructuredOutputResult(parsed={"capability_id": "intent", "target_system": "unknown"}),
    ],
)
def test_router_rejects_structured_output_errors_and_invalid_pydantic_results(
    structured_result: StructuredOutputResult,
) -> None:
    llm_provider = MockLLMProvider()
    structured_output = RecordingStructuredOutput(structured_result)
    router = IntentRouter(llm_provider, structured_output, "qwen-test")

    result = asyncio.run(router.parse("raw-json"))

    assert result is None
    assert len(structured_output.calls) == 1
    assert structured_output.calls[0]["schema_type"] is CapabilityRef


def test_router_rejects_blank_input_without_calling_either_boundary() -> None:
    llm_provider = MockLLMProvider()
    structured_output = RecordingStructuredOutput(
        StructuredOutputResult(parsed=CapabilityRef(capability_id="must-not-run"))
    )
    router = IntentRouter(llm_provider, structured_output, "qwen-test")

    result = asyncio.run(router.parse(" \r\n "))

    assert result is None
    assert llm_provider.calls == []
    assert structured_output.calls == []


def test_router_rejects_empty_model_configuration() -> None:
    with pytest.raises(ValueError, match="model"):
        IntentRouter(
            MockLLMProvider(),
            RecordingStructuredOutput(StructuredOutputResult()),
            "   ",
        )
