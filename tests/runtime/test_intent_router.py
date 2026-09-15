"""Intent Router boundary and validation behavior."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.infra.llm.json_structured_output import JSONStructuredOutputProvider
from app.infra.llm.mock_llm.mock_llm_provider import MockLLMProvider
from app.knowledge import BasicKnowledge
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
from app.runtime.models import CapabilityRef, IntentOutput, MatchedIntent
from tests.runtime.registry_fakes import active_capability

_ROUTER_CAPABILITIES = (active_capability("oa.list_pending_workflows"),)


def _candidate_payload(messages: list[Any]) -> dict[str, Any]:
    segments = [
        message.content
        for message in messages
        if message.role == "system" and '{"capability_candidates":' in message.content
    ]
    assert len(segments) == 1
    return json.loads(segments[0].split("\n", maxsplit=1)[1])["capability_candidates"]


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
            content='{"match":"capability","capability_id":"pending-workflows"}',
            trace_metadata={"provider_request_id": "request-1"},
        ),
    )
    structured_output = RecordingStructuredOutput(
        StructuredOutputResult(
            parsed={
                "match": "capability",
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
            capabilities=(active_capability("pending-workflows"),),
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
        "system",
        "user",
    ]
    assert "semantic_system_knowledge" in llm_call["messages"][1].content
    assert "企业术语：待办" in llm_call["messages"][1].content
    assert "查 OA\n待办" not in llm_call["messages"][1].content
    assert "capability_candidates" not in llm_call["messages"][1].content
    candidates = _candidate_payload(llm_call["messages"])
    assert [item["capability_id"] for item in candidates["items"]] == ["pending-workflows"]
    assert "查 OA\n待办" not in llm_call["messages"][2].content
    assert llm_call["messages"][-1].content == "查 OA\n待办"
    assert structured_output.calls == [
        {
            "raw_response": '{"match":"capability","capability_id":"pending-workflows"}',
            "schema_type": IntentOutput,
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
        StructuredOutputResult(
            parsed=MatchedIntent(match="capability", capability_id="must-not-run")
        )
    )
    router = IntentRouter(llm_provider, structured_output, "qwen-test")

    result = asyncio.run(router.parse("request", capabilities=_ROUTER_CAPABILITIES))

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

    result = asyncio.run(router.parse("raw-json", capabilities=_ROUTER_CAPABILITIES))

    assert result.capability_ref is None
    assert result.failure_reason == (
        "schema_invalid" if error_code == "validation_error" else "structured_output_error"
    )
    assert result.structured_output_error_code == error_code
    assert canary not in repr(result)
    assert len(structured_output.calls) == 1
    assert structured_output.calls[0]["schema_type"] is IntentOutput


@pytest.mark.parametrize(
    "parsed",
    [
        {"match": "capability", "capability_id": "", "arguments": {}},
        {"match": "capability", "capability_id": "   ", "arguments": {}},
        {"match": "capability", "capability_id": "intent", "target_system": "unknown"},
    ],
)
def test_router_classifies_invalid_pydantic_results_without_raw_content(
    parsed: dict[str, Any],
) -> None:
    llm_provider = MockLLMProvider()
    structured_output = RecordingStructuredOutput(StructuredOutputResult(parsed=parsed))
    router = IntentRouter(llm_provider, structured_output, "qwen-test")

    result = asyncio.run(router.parse("raw-json", capabilities=_ROUTER_CAPABILITIES))

    assert result.capability_ref is None
    assert result.failure_reason == "schema_invalid"
    assert result.structured_output_error_code == "validation_error"
    assert repr(parsed) not in repr(result)


def test_router_rejects_blank_input_without_calling_either_boundary() -> None:
    llm_provider = MockLLMProvider()
    structured_output = RecordingStructuredOutput(
        StructuredOutputResult(
            parsed=MatchedIntent(match="capability", capability_id="must-not-run")
        )
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
        LLMCompletionResponse(content='{"match":"capability","capability_id":"oa.previous.query"}'),
    )
    structured_output = RecordingStructuredOutput(
        StructuredOutputResult(
            parsed=MatchedIntent(match="capability", capability_id="oa.previous.query")
        )
    )
    router = IntentRouter(llm_provider, structured_output, "qwen-test")

    result = asyncio.run(
        router.parse(
            "repeat",
            capabilities=(active_capability("oa.previous.query"),),
            memory_summaries=(SessionMemorySummary(capability_id="oa.previous.query"),),
        )
    )

    assert result.capability_ref == CapabilityRef(capability_id="oa.previous.query")
    messages = llm_provider.calls[0]["messages"]
    assert [message.role for message in messages] == ["system", "system", "system", "user"]
    assert '{"capability_candidates":' in messages[1].content
    assert messages[2].content.endswith(
        '{"session_memory":[{"capability_id":"oa.previous.query","terminal_status":"completed"}]}'
    )
    assert "repeat" not in messages[1].content
    assert "repeat" not in messages[2].content


def test_router_truncates_knowledge_to_exact_item_and_length_limits() -> None:
    bounded = _bound_generated_knowledge(
        tuple(
            f"item-{index}:" + (str(index) * (MAX_KNOWLEDGE_ITEM_LENGTH + 20))
            for index in range(10)
        )
    )

    assert MAX_KNOWLEDGE_ITEMS == 8
    assert MAX_KNOWLEDGE_ITEM_LENGTH == 240
    assert len(bounded) == 8
    assert all(len(item) == MAX_KNOWLEDGE_ITEM_LENGTH for item in bounded)
    assert bounded[0].startswith("item-0:")
    assert bounded[7].startswith("item-7:")
    assert all("item-8:" not in item and "item-9:" not in item for item in bounded)


def test_router_preserves_explicit_empty_knowledge_and_capability_contracts() -> None:
    llm_provider = MockLLMProvider()
    router = IntentRouter(
        llm_provider,
        JSONStructuredOutputProvider(),
        "qwen-test",
        semantic_knowledge=BasicKnowledge(static_items=()),
    )

    asyncio.run(router.parse("OA mock", capabilities=(active_capability("oa.safe.query"),)))

    messages = llm_provider.calls[0]["messages"]
    assert [message.role for message in messages] == ["system", "system", "user"]
    assert "semantic_system_knowledge" not in messages[1].content
    assert "Mock 系统说明" not in messages[1].content
    candidates = _candidate_payload(messages)
    assert [item["capability_id"] for item in candidates["items"]] == ["oa.safe.query"]


def test_router_injects_at_most_eight_registry_derived_capabilities() -> None:
    llm_provider = MockLLMProvider()
    structured_output = RecordingStructuredOutput(
        StructuredOutputResult(parsed=MatchedIntent(match="capability", capability_id="oa.item-9"))
    )
    router = IntentRouter(llm_provider, structured_output, "qwen-test")
    capabilities = tuple(active_capability(f"oa.item-{index}") for index in range(10))

    result = asyncio.run(router.parse("请执行 oa.item-9", capabilities=capabilities))

    assert result.capability_ref == CapabilityRef(capability_id="oa.item-9")
    messages = llm_provider.calls[0]["messages"]
    assert [message.role for message in messages] == ["system", "system", "system", "user"]
    assert "semantic_system_knowledge" in messages[1].content
    candidates = _candidate_payload(messages)
    injected = [item["capability_id"] for item in candidates["items"]]
    assert 1 <= len(injected) <= 8
    assert injected[0] == "oa.item-9"
    selection = result.candidate_selection
    assert selection is not None
    assert selection.outcome == "ready"
    assert selection.visible_count == 10
    assert selection.selected_count == len(injected)
    assert candidates["omitted_count"] == selection.omitted_count == 10 - len(injected)
    assert candidates["coverage_complete"] is False
    assert candidates["truncated_by"] == list(selection.truncated_by)
    assert selection.payload_bytes == len(messages[2].content.split("\n", maxsplit=1)[1])


@pytest.mark.parametrize(
    ("message", "capability_count", "expected_outcome"),
    [
        ("完全无关的请求", 9, "low_confidence"),
        ("oa item", 9, "ambiguous"),
    ],
)
def test_router_non_ready_candidate_selection_never_calls_model(
    message: str,
    capability_count: int,
    expected_outcome: str,
) -> None:
    llm_provider = MockLLMProvider()
    structured_output = RecordingStructuredOutput(StructuredOutputResult())
    router = IntentRouter(llm_provider, structured_output, "qwen-test")
    capabilities = tuple(active_capability(f"oa.item-{index}") for index in range(capability_count))

    result = asyncio.run(router.parse(message, capabilities=capabilities))
    empty = asyncio.run(router.parse(message, capabilities=()))

    assert result.candidate_selection is not None
    assert result.candidate_selection.outcome == expected_outcome
    assert result.failure_reason is None and result.match is None
    assert empty.candidate_selection is not None
    assert empty.candidate_selection.outcome == "empty"
    assert llm_provider.calls == []
    assert structured_output.calls == []


def test_candidate_budget_preserves_message_and_memory() -> None:
    long_message = "查询 OA 待办 " + ("超长原始请求内容" * 400)
    long_key = "k" * 3800
    fitting = active_capability("oa.fitting.query")
    oversized = active_capability("oa.oversized.query").model_copy(
        update={
            "input_schema": {
                "type": "object",
                "properties": {long_key: {"type": "string"}},
            }
        }
    )
    memory = (SessionMemorySummary(capability_id="oa.previous.query"),)
    llm_provider = MockLLMProvider()
    router = IntentRouter(
        llm_provider,
        RecordingStructuredOutput(
            StructuredOutputResult(
                parsed=MatchedIntent(match="capability", capability_id="oa.fitting.query")
            )
        ),
        "qwen-test",
    )

    ready = asyncio.run(
        router.parse(long_message, capabilities=(fitting,), memory_summaries=memory)
    )
    rejected = asyncio.run(
        router.parse(long_message, capabilities=(oversized,), memory_summaries=memory)
    )

    assert ready.candidate_selection is not None
    assert ready.candidate_selection.outcome == "ready"
    assert len(llm_provider.calls) == 1
    messages = llm_provider.calls[0]["messages"]
    assert messages[-1].content == long_message.strip()
    assert any("session_memory" in message.content for message in messages)
    segment = next(m.content for m in messages if '{"capability_candidates":' in m.content)
    sent_payload = segment.split("\n", maxsplit=1)[1]
    assert sent_payload == ready.candidate_selection.payload_json
    assert len(sent_payload.encode("utf-8")) == ready.candidate_selection.payload_bytes
    assert rejected.candidate_selection is not None
    assert rejected.candidate_selection.outcome == "over_budget"
    assert len(llm_provider.calls) == 1


def test_router_keeps_knowledge_and_memory_in_independent_system_messages() -> None:
    llm_provider = MockLLMProvider()
    structured_output = RecordingStructuredOutput(
        StructuredOutputResult(
            parsed=MatchedIntent(match="capability", capability_id="oa.safe.query")
        )
    )
    router = IntentRouter(llm_provider, structured_output, "qwen-test")

    asyncio.run(
        router.parse(
            "待办 repeat",
            capabilities=(active_capability("oa.safe.query"),),
            memory_summaries=(SessionMemorySummary(capability_id="oa.previous.query"),),
        )
    )

    messages = llm_provider.calls[0]["messages"]
    assert [message.role for message in messages] == [
        "system",
        "system",
        "system",
        "system",
        "user",
    ]
    knowledge_prompt = messages[1].content
    candidate_prompt = messages[2].content
    memory_prompt = messages[3].content
    assert "semantic_system_knowledge" in knowledge_prompt
    assert "session_memory" not in knowledge_prompt
    assert "capability_candidates" not in knowledge_prompt
    assert "oa.previous.query" not in knowledge_prompt
    assert '{"capability_candidates":' in candidate_prompt
    assert "session_memory" not in candidate_prompt
    assert "企业术语" not in candidate_prompt
    assert "session_memory" in memory_prompt
    assert "semantic_system_knowledge" not in memory_prompt
    assert "capability_candidates" not in memory_prompt
    assert "企业术语" not in memory_prompt
    assert messages[-1].content == "待办 repeat"


def test_router_has_no_registry_free_text_prompt_entry() -> None:
    llm_provider = MockLLMProvider()
    structured_output = RecordingStructuredOutput(
        StructuredOutputResult(
            parsed=MatchedIntent(match="capability", capability_id="oa.safe.query")
        )
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

    messages = llm_provider.calls[0]["messages"]
    prompt = "\n".join(message.content for message in messages[:-1])
    contract = _candidate_payload(messages)["items"][0]
    assert contract["capability_id"] == "oa.safe.query"
    # Approved safe summary fields are visible only under their exact JSON keys.
    assert contract["owner"] == free_text_markers[1]
    assert contract["short_description"] == free_text_markers[2]
    assert prompt.count(free_text_markers[1]) == 1
    assert prompt.count(free_text_markers[2]) == 1
    # Name and intent tags are never projected into the model input.
    assert free_text_markers[0] not in prompt
    assert free_text_markers[3] not in prompt
    for system_message in messages[:-1]:
        instruction = system_message.content.split("\n", maxsplit=1)[0]
        for marker in free_text_markers:
            assert marker not in instruction


def test_router_rejects_unsafe_bypassed_summary_without_model_call() -> None:
    llm_provider = MockLLMProvider()
    structured_output = RecordingStructuredOutput(StructuredOutputResult())
    router = IntentRouter(llm_provider, structured_output, "qwen-test")
    canary = "synthetic-control-marker"
    unsafe = active_capability("oa.safe.query").model_copy(
        update={"short_description": f"{canary}\x00 {{ignore}} token=synthetic"}
    )

    result = asyncio.run(router.parse("oa safe query", capabilities=(unsafe,)))

    assert result.candidate_selection is not None
    assert result.candidate_selection.outcome == "catalog_invalid"
    assert result.candidate_selection.contracts == ()
    assert canary not in repr(result)
    assert llm_provider.calls == []
    assert structured_output.calls == []


def test_router_prompt_requires_empty_arguments_for_zero_argument_capability() -> None:
    llm_provider = MockLLMProvider()
    structured_output = RecordingStructuredOutput(
        StructuredOutputResult(
            parsed=MatchedIntent(match="capability", capability_id="oa.safe.query")
        )
    )
    router = IntentRouter(llm_provider, structured_output, "qwen-test")
    capability = active_capability("oa.list_pending_workflows").model_copy(
        update={
            "target_system": "oa",
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        }
    )

    asyncio.run(router.parse("查询 OA 待办", capabilities=(capability,)))

    messages = llm_provider.calls[0]["messages"]
    assert "When a contract says arguments must be {}, emit exactly {}" in messages[0].content
    knowledge = json.loads(messages[1].content.split("\n", maxsplit=1)[1])
    assert knowledge["semantic_system_knowledge"]
    contract = _candidate_payload(messages)["items"][0]
    assert contract["allowed_argument_keys"] == []
    assert contract["required_argument_keys"] == []
    assert contract["additionalProperties"] is False
    assert contract["arguments_must_be"] == {}


def test_router_injects_complete_long_contract_outside_text_truncation_path() -> None:
    long_key = "workflow_" + ("x" * 180)
    allowed_keys = ["department_id", long_key, "region-code"]
    required_keys = [long_key, "department_id"]
    capability = active_capability("oa.complete-contract.query").model_copy(
        update={
            "target_system": "oa",
            "input_schema": {
                "type": "object",
                "properties": {
                    key: {
                        "type": "string",
                        "description": "schema-description-must-not-enter",
                        "default": "schema-default-must-not-enter",
                        "example": "schema-example-must-not-enter",
                    }
                    for key in allowed_keys
                },
                "required": required_keys,
                "additionalProperties": False,
            },
        }
    )
    llm_provider = MockLLMProvider()
    router = IntentRouter(
        llm_provider,
        RecordingStructuredOutput(
            StructuredOutputResult(
                parsed=MatchedIntent(match="capability", capability_id=capability.capability_id)
            )
        ),
        "qwen-test",
    )

    asyncio.run(router.parse("request", capabilities=(capability,)))

    messages = llm_provider.calls[0]["messages"]
    prompt = "\n".join(message.content for message in messages)
    contract = _candidate_payload(messages)["items"][0]
    assert all("semantic_system_knowledge" not in message.content for message in messages)
    assert len(json.dumps(contract, ensure_ascii=True)) > MAX_KNOWLEDGE_ITEM_LENGTH
    assert contract["capability_id"] == capability.capability_id
    assert contract["capability_type"] == "query"
    assert contract["target_system"] == "oa"
    assert contract["allowed_argument_keys"] == sorted(allowed_keys)
    assert contract["required_argument_keys"] == sorted(required_keys)
    assert contract["additionalProperties"] is False
    assert "schema-description-must-not-enter" not in prompt
    assert "schema-default-must-not-enter" not in prompt
    assert "schema-example-must-not-enter" not in prompt


@pytest.mark.parametrize("match", ["none", "capability"])
@pytest.mark.parametrize(
    "variant",
    [
        "upper",
        "title",
        "leading-space",
        "trailing-space",
        "surrounding-spaces",
        "surrounding-tabs",
        "surrounding-unicode-space",
        "internal-space",
        "internal-tab",
        "internal-unicode-space",
    ],
)
def test_router_rejects_nonliteral_match_values(match: str, variant: str) -> None:
    invalid_match = {
        "upper": match.upper(),
        "title": match.title(),
        "leading-space": f" {match}",
        "trailing-space": f"{match} ",
        "surrounding-spaces": f" {match} ",
        "surrounding-tabs": f"\t{match}\t",
        "surrounding-unicode-space": f"\u2003{match}\u00a0",
        "internal-space": f"{match[0]} {match[1:]}",
        "internal-tab": f"{match[0]}\t{match[1:]}",
        "internal-unicode-space": f"{match[0]}\u2003{match[1:]}",
    }[variant]
    payload = {"match": invalid_match}
    if match == "capability":
        payload["capability_id"] = "oa.list_pending_workflows"
    llm_provider = MockLLMProvider()
    llm_provider.register("request", LLMCompletionResponse(content=json.dumps(payload)))
    router = IntentRouter(llm_provider, JSONStructuredOutputProvider(), "qwen-test")

    result = asyncio.run(router.parse("request", capabilities=_ROUTER_CAPABILITIES))

    assert result.failure_reason == "schema_invalid"
    assert result.structured_output_error_code == "validation_error"
    assert result.match is None
    assert result.capability_ref is None


@pytest.mark.parametrize("match", ["none", "capability"])
@pytest.mark.parametrize(
    "json_form",
    ["compact", "external-whitespace", "unicode-key", "unicode-value", "unicode-both"],
)
def test_router_accepts_legal_match_json_forms(match: str, json_form: str) -> None:
    payload = {"match": match}
    if match == "capability":
        payload["capability_id"] = "oa.list_pending_workflows"
    raw_response = json.dumps(payload, separators=(",", ":"))
    if json_form == "external-whitespace":
        raw_response = " \t\r\n" + raw_response.replace(":", " \t:\r\n ") + "\r\n\t "
    if json_form in {"unicode-key", "unicode-both"}:
        raw_response = raw_response.replace('"match"', r'"\u006datch"')
    if json_form in {"unicode-value", "unicode-both"}:
        escaped_match = r"\u006eone" if match == "none" else r"\u0063apability"
        raw_response = raw_response.replace(f'"{match}"', f'"{escaped_match}"')
    llm_provider = MockLLMProvider()
    llm_provider.register("request", LLMCompletionResponse(content=raw_response))
    router = IntentRouter(llm_provider, JSONStructuredOutputProvider(), "qwen-test")

    result = asyncio.run(router.parse("request", capabilities=_ROUTER_CAPABILITIES))

    assert result.failure_reason is None
    assert result.structured_output_error_code is None
    assert result.match == match
    if match == "none":
        assert result.capability_ref is None
    else:
        assert result.capability_ref == CapabilityRef(capability_id="oa.list_pending_workflows")


def test_router_preserves_only_safe_pydantic_validation_diagnostics(caplog: Any) -> None:
    canary = "must-not-enter-trace-log-or-response"
    llm_provider = MockLLMProvider()
    llm_provider.register(
        "invalid intent",
        LLMCompletionResponse(
            content=json.dumps(
                {
                    "match": "capability",
                    "capability_id": "oa.safe.query",
                    "arguments": {"user": canary},
                    "target_system": "oa",
                    "capability_type": "query",
                    canary: "rejected-extra-value",
                }
            )
        ),
    )
    router = IntentRouter(
        llm_provider,
        JSONStructuredOutputProvider(),
        "qwen-test",
    )

    result = asyncio.run(router.parse("invalid intent", capabilities=_ROUTER_CAPABILITIES))

    assert result.capability_ref is None
    assert result.failure_reason == "schema_invalid"
    assert result.structured_output_error_code == "validation_error"
    assert result.validation_error_path == "$"
    assert result.validation_error_type == "extra_forbidden"
    assert result.argument_keys == ("user",)
    assert canary not in repr(result)
    assert canary not in caplog.text
