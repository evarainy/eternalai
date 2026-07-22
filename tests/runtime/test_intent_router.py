"""Intent Router boundary and validation behavior."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.infra.llm.mock_llm.mock_llm_provider import MockLLMProvider
from app.memory import SessionMemorySummary
from app.ports.llm_provider import LLMCompletionResponse
from app.ports.structured_output import (
    StructuredOutputError,
    StructuredOutputResult,
)
from app.runtime.intent_router import (
    MAX_KNOWLEDGE_ITEM_LENGTH,
    MAX_KNOWLEDGE_ITEMS,
    IntentRouter,
)
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
            trace_metadata={
                "trace_id": "trace-1",
                "task_id": "task-1",
                "unapproved_context": "must-not-forward",
            },
        )
    )

    assert result.capability_ref == CapabilityRef(
        capability_id="pending-workflows",
        target_system="oa",
        capability_type="query",
    )
    assert result.failure_reason is None
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
                "trace_id": "trace-1",
                "task_id": "task-1",
            },
        }
    ]


@pytest.mark.parametrize(
    ("completion", "expected_reason"),
    [
        (LLMCompletionResponse(content=None), "empty_response"),
        (LLMCompletionResponse(content="   "), "empty_response"),
        (
            LLMCompletionResponse(error_code="provider_error", error_message="failed"),
            "provider_error",
        ),
    ],
)
def test_router_fails_closed_before_structured_output_on_llm_failure(
    completion: LLMCompletionResponse,
    expected_reason: str,
) -> None:
    llm_provider = MockLLMProvider()
    llm_provider.register("request", completion)
    structured_output = RecordingStructuredOutput(
        StructuredOutputResult(parsed=CapabilityRef(capability_id="must-not-run"))
    )
    router = IntentRouter(llm_provider, structured_output, "qwen-test")

    result = asyncio.run(router.parse("request"))

    assert result.capability_ref is None
    assert result.failure_reason == expected_reason
    assert len(llm_provider.calls) == 1
    assert structured_output.calls == []


@pytest.mark.parametrize(
    ("structured_result", "expected_reason"),
    [
        (
            StructuredOutputResult(
                error=StructuredOutputError(
                    error_code="validation_error",
                    error_message="invalid intent",
                )
            ),
            "structured_output_error",
        ),
        (
            StructuredOutputResult(parsed={"capability_id": "", "arguments": {}}),
            "schema_invalid",
        ),
        (
            StructuredOutputResult(parsed={"capability_id": "   ", "arguments": {}}),
            "schema_invalid",
        ),
        (
            StructuredOutputResult(
                parsed={"capability_id": "intent", "target_system": "unknown"}
            ),
            "schema_invalid",
        ),
    ],
)
def test_router_rejects_structured_output_errors_and_invalid_pydantic_results(
    structured_result: StructuredOutputResult,
    expected_reason: str,
) -> None:
    llm_provider = MockLLMProvider()
    structured_output = RecordingStructuredOutput(structured_result)
    router = IntentRouter(llm_provider, structured_output, "qwen-test")

    result = asyncio.run(router.parse("raw-json"))

    assert result.capability_ref is None
    assert result.failure_reason == expected_reason
    assert len(structured_output.calls) == 1
    assert structured_output.calls[0]["schema_type"] is CapabilityRef


def test_router_rejects_blank_input_without_calling_either_boundary() -> None:
    llm_provider = MockLLMProvider()
    structured_output = RecordingStructuredOutput(
        StructuredOutputResult(parsed=CapabilityRef(capability_id="must-not-run"))
    )
    router = IntentRouter(llm_provider, structured_output, "qwen-test")

    result = asyncio.run(router.parse(" \r\n "))

    assert result.capability_ref is None
    assert result.failure_reason == "blank_input"
    assert llm_provider.calls == []
    assert structured_output.calls == []


def test_router_rejects_empty_model_configuration() -> None:
    with pytest.raises(ValueError, match="model"):
        IntentRouter(
            MockLLMProvider(),
            RecordingStructuredOutput(StructuredOutputResult()),
            "   ",
        )


def test_router_adds_only_structured_success_summaries_when_memory_exists() -> None:
    llm_provider = MockLLMProvider()
    llm_provider.register(
        "repeat",
        LLMCompletionResponse(content='{"capability_id":"oa.previous.query"}'),
    )
    structured_output = RecordingStructuredOutput(
        StructuredOutputResult(parsed=CapabilityRef(capability_id="oa.previous.query"))
    )
    router = IntentRouter(llm_provider, structured_output, "qwen-test")

    result = asyncio.run(
        router.parse(
            "repeat",
            memory_summaries=(
                SessionMemorySummary(capability_id="oa.previous.query"),
            ),
        )
    )

    assert result.capability_ref == CapabilityRef(capability_id="oa.previous.query")
    messages = llm_provider.calls[0]["messages"]
    assert [message.role for message in messages] == ["system", "system", "user"]
    assert messages[1].content.endswith(
        '{"session_memory":[{"capability_id":"oa.previous.query",'
        '"terminal_status":"completed"}]}'
    )
    assert "repeat" not in messages[1].content


def test_router_truncates_knowledge_to_exact_item_and_length_limits() -> None:
    llm_provider = MockLLMProvider()
    structured_output = RecordingStructuredOutput(
        StructuredOutputResult(parsed=CapabilityRef(capability_id="oa.safe.query"))
    )
    router = IntentRouter(llm_provider, structured_output, "qwen-test")
    knowledge_items = tuple(
        f"item-{index}:" + (str(index) * 260)
        for index in range(10)
    )

    result = asyncio.run(
        router.parse("request", knowledge_items=knowledge_items)
    )

    assert result.capability_ref == CapabilityRef(capability_id="oa.safe.query")
    messages = llm_provider.calls[0]["messages"]
    assert [message.role for message in messages] == ["system", "system", "user"]
    payload = json.loads(messages[1].content.split("\n", maxsplit=1)[1])
    injected = payload["semantic_system_knowledge"]
    assert MAX_KNOWLEDGE_ITEMS == 8
    assert MAX_KNOWLEDGE_ITEM_LENGTH == 240
    assert len(injected) == 8
    assert all(len(item) == 240 for item in injected)
    assert injected[0].startswith("item-0:")
    assert injected[7].startswith("item-7:")
    assert all("item-8:" not in item and "item-9:" not in item for item in injected)


def test_router_keeps_knowledge_and_memory_in_independent_system_messages() -> None:
    llm_provider = MockLLMProvider()
    structured_output = RecordingStructuredOutput(
        StructuredOutputResult(parsed=CapabilityRef(capability_id="oa.safe.query"))
    )
    router = IntentRouter(llm_provider, structured_output, "qwen-test")

    asyncio.run(
        router.parse(
            "repeat",
            knowledge_items=("企业术语：待办是等待处理的流程事项。",),
            memory_summaries=(
                SessionMemorySummary(capability_id="oa.previous.query"),
            ),
        )
    )

    messages = llm_provider.calls[0]["messages"]
    assert [message.role for message in messages] == [
        "system",
        "system",
        "system",
        "user",
    ]
    knowledge_prompt = messages[1].content
    memory_prompt = messages[2].content
    assert "semantic_system_knowledge" in knowledge_prompt
    assert "session_memory" not in knowledge_prompt
    assert "oa.previous.query" not in knowledge_prompt
    assert "session_memory" in memory_prompt
    assert "semantic_system_knowledge" not in memory_prompt
    assert "企业术语" not in memory_prompt
    assert messages[-1].content == "repeat"


def test_router_sanitizes_knowledge_again_at_the_final_prompt_boundary() -> None:
    llm_provider = MockLLMProvider()
    structured_output = RecordingStructuredOutput(
        StructuredOutputResult(parsed=CapabilityRef(capability_id="oa.safe.query"))
    )
    router = IntentRouter(llm_provider, structured_output, "qwen-test")
    credential_value = "synthetic-router-credential"
    private_address = "https://192.168.1.8/internal"
    quoted_credential = "synthetic spaced router credential"
    quoted_bearer = "synthetic spaced bearer credential"

    asyncio.run(
        router.parse(
            "request",
            knowledge_items=(
                f"authorization={credential_value} endpoint={private_address} "
                f"password=\"{quoted_credential}\" "
                f"Bearer \"{quoted_bearer}\"",
            ),
        )
    )

    prompt = llm_provider.calls[0]["messages"][1].content
    assert credential_value not in prompt
    assert private_address not in prompt
    assert quoted_credential not in prompt
    assert quoted_bearer not in prompt
    assert prompt.count("[REDACTED]") == 4
