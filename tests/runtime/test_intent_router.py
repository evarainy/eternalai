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
    StructuredOutputErrorCode,
    StructuredOutputResult,
)
from app.runtime.intent_router import (
    MAX_KNOWLEDGE_ITEM_LENGTH,
    MAX_KNOWLEDGE_ITEMS,
    IntentRouter,
    _bound_generated_knowledge,
)
from app.runtime.models import CapabilityRef
from tests.runtime.registry_fakes import active_capability


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
    assert [message.role for message in llm_call["messages"]] == [
        "system",
        "system",
        "user",
    ]
    assert "semantic_system_knowledge" in llm_call["messages"][1].content
    assert "企业术语：待办" in llm_call["messages"][1].content
    assert "查 OA\n待办" not in llm_call["messages"][1].content
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
            LLMCompletionResponse(
                error_code="provider_error",
                error_message="sensitive-provider-detail",
            ),
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
    assert result.structured_output_error_code is None
    assert "sensitive-provider-detail" not in repr(result)
    assert len(llm_provider.calls) == 1
    assert structured_output.calls == []


@pytest.mark.parametrize(
    ("error_code",),
    [
        ("parse_error",),
        ("validation_error",),
        ("schema_error",),
    ],
)
def test_router_preserves_safe_structured_output_error_code_without_raw_content(
    error_code: StructuredOutputErrorCode,
) -> None:
    canary = "sensitive-structured-output-detail"
    llm_provider = MockLLMProvider()
    structured_output = RecordingStructuredOutput(
        StructuredOutputResult(
            error=StructuredOutputError(
                error_code=error_code,
                error_message=canary,
                raw_response=canary,
            ),
            raw_response=canary,
        )
    )
    router = IntentRouter(llm_provider, structured_output, "qwen-test")

    result = asyncio.run(router.parse("raw-json"))

    assert result.capability_ref is None
    assert result.failure_reason == "structured_output_error"
    assert result.structured_output_error_code == error_code
    assert canary not in repr(result)
    assert len(structured_output.calls) == 1
    assert structured_output.calls[0]["schema_type"] is CapabilityRef


@pytest.mark.parametrize(
    "parsed",
    [
        {"capability_id": "", "arguments": {}},
        {"capability_id": "   ", "arguments": {}},
        {"capability_id": "intent", "target_system": "unknown"},
    ],
)
def test_router_classifies_invalid_pydantic_results_without_raw_content(
    parsed: dict[str, Any],
) -> None:
    llm_provider = MockLLMProvider()
    structured_output = RecordingStructuredOutput(StructuredOutputResult(parsed=parsed))
    router = IntentRouter(llm_provider, structured_output, "qwen-test")

    result = asyncio.run(router.parse("raw-json"))

    assert result.capability_ref is None
    assert result.failure_reason == "schema_invalid"
    assert result.structured_output_error_code == "validation_error"
    assert repr(parsed) not in repr(result)


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
    bounded = _bound_generated_knowledge(
        tuple(f"item-{index}:" + (str(index) * 260) for index in range(10))
    )

    assert MAX_KNOWLEDGE_ITEMS == 8
    assert MAX_KNOWLEDGE_ITEM_LENGTH == 240
    assert len(bounded) == 8
    assert all(len(item) == 240 for item in bounded)
    assert bounded[0].startswith("item-0:")
    assert bounded[7].startswith("item-7:")
    assert all("item-8:" not in item and "item-9:" not in item for item in bounded)


def test_router_injects_at_most_eight_registry_derived_capabilities() -> None:
    llm_provider = MockLLMProvider()
    structured_output = RecordingStructuredOutput(
        StructuredOutputResult(parsed=CapabilityRef(capability_id="oa.safe.query"))
    )
    router = IntentRouter(llm_provider, structured_output, "qwen-test")
    capabilities = tuple(
        active_capability(f"oa.item-{index}") for index in range(10)
    )

    result = asyncio.run(router.parse("request", capabilities=capabilities))

    assert result.capability_ref == CapabilityRef(capability_id="oa.safe.query")
    messages = llm_provider.calls[0]["messages"]
    assert [message.role for message in messages] == ["system", "system", "user"]
    payload = json.loads(messages[1].content.split("\n", maxsplit=1)[1])
    injected = payload["semantic_system_knowledge"]
    assert len(injected) == 8
    assert "id=oa.item-0" in injected[0]
    assert "id=oa.item-7" in injected[7]
    assert all("oa.item-8" not in item and "oa.item-9" not in item for item in injected)


def test_router_keeps_knowledge_and_memory_in_independent_system_messages() -> None:
    llm_provider = MockLLMProvider()
    structured_output = RecordingStructuredOutput(
        StructuredOutputResult(parsed=CapabilityRef(capability_id="oa.safe.query"))
    )
    router = IntentRouter(llm_provider, structured_output, "qwen-test")

    asyncio.run(
        router.parse(
            "待办 repeat",
            capabilities=(active_capability("oa.safe.query"),),
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
    assert messages[-1].content == "待办 repeat"


def test_router_has_no_registry_free_text_prompt_entry() -> None:
    llm_provider = MockLLMProvider()
    structured_output = RecordingStructuredOutput(
        StructuredOutputResult(parsed=CapabilityRef(capability_id="oa.safe.query"))
    )
    router = IntentRouter(llm_provider, structured_output, "qwen-test")
    free_text_markers = (
        "unique-router-name-marker",
        "unique-router-owner-marker",
        "unique-router-description-marker",
        "unique-router-intent-marker",
    )
    capability = active_capability("oa.safe.query").model_copy(
        update={
            "name": free_text_markers[0],
            "owner": free_text_markers[1],
            "short_description": free_text_markers[2],
            "intent_tags": [free_text_markers[3]],
        }
    )

    asyncio.run(router.parse("request", capabilities=(capability,)))

    prompt = llm_provider.calls[0]["messages"][1].content
    assert "id=oa.safe.query" in prompt
    for marker in free_text_markers:
        assert marker not in prompt
