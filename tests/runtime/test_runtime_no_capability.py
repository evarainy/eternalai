"""Explicit no-match routing through the real JSON parser and Runtime."""

from __future__ import annotations

import asyncio
import json

import pytest

from app.infra.llm.json_structured_output import JSONStructuredOutputProvider
from app.infra.llm.mock_llm.mock_llm_provider import MockLLMProvider
from app.knowledge import BasicKnowledge
from app.knowledge.capability_selection import select_capability_candidates
from app.ports.llm_provider import LLMCompletionResponse
from app.runtime.intent_router import IntentRouter
from app.runtime.models import IntentOutput
from tests.runtime.test_runtime_capability_selection import _capability, _run_runtime


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"capability_id": "oa.list_pending_workflows"},
        {"match": "capability"},
        {"match": "capability", "capability_id": None},
        {"match": "capability", "capability_id": "   "},
        {"match": None},
        {"match": "unknown"},
        {"match": "none", "capability_id": "oa.list_pending_workflows"},
        {"match": "none", "capability_id": None},
        {"match": "none", "arguments": {}},
    ],
)
def test_invalid_decision_is_schema_invalid_and_never_no_match(payload: dict) -> None:
    raw = json.dumps(payload)
    router = IntentRouter(MockLLMProvider(), JSONStructuredOutputProvider(), "test")
    result = asyncio.run(
        router.parse(raw, capabilities=(_capability("oa.list_pending_workflows"),))
    )
    assert result.match is None
    assert result.capability_ref is None
    assert result.failure_reason == "schema_invalid"
    assert result.structured_output_error_code == "validation_error"

    envelope, store, trace, gateway, registry = _run_runtime(
        "oa.list_pending_workflows",
        [_capability("oa.list_pending_workflows")],
        llm_completion=LLMCompletionResponse(content=raw),
        structured_output_override=JSONStructuredOutputProvider(),
    )
    assert envelope.status == "failed"
    assert store.status_updates[-1][1:] == ("failed", "internal_error")
    assert gateway.calls == []
    assert registry.get_calls == []
    assert all(step["event_type"] != "no_capability_found" for step in trace.steps)
    parsed = next(step for step in trace.steps if step["event_type"] == "intent_parsed")
    assert parsed["status"] == "failed"
    assert parsed["attributes"]["reason"] == "schema_invalid"
    assert parsed["attributes"]["structured_output_error_code"] == "validation_error"


def test_explicit_no_match_is_successful_parse_and_no_capability_terminal() -> None:
    raw = '{"match":"none"}'
    router = IntentRouter(MockLLMProvider(), JSONStructuredOutputProvider(), "test")
    result = asyncio.run(
        router.parse(raw, capabilities=(_capability("oa.list_pending_workflows"),))
    )
    assert result.match == "none"
    assert result.failure_reason is None
    assert result.structured_output_error_code is None
    assert result.capability_ref is None

    envelope, store, trace, gateway, registry = _run_runtime(
        "oa.list_pending_workflows",
        [_capability("oa.list_pending_workflows")],
        llm_completion=LLMCompletionResponse(content=raw),
        structured_output_override=JSONStructuredOutputProvider(),
    )
    assert envelope.status == "no_capability_found"
    assert store.status_updates[-1][1:] == ("no_capability_found", "capability_not_found")
    assert envelope.data is None
    assert gateway.calls == []
    assert registry.get_calls == []
    parsed = next(step for step in trace.steps if step["event_type"] == "intent_parsed")
    assert parsed["status"] == "ok"
    assert parsed["attributes"] == {
        "result": "valid",
        "match": "none",
        **select_capability_candidates(
            "select oa.list_pending_workflows",
            (_capability("oa.list_pending_workflows"),),
        ).trace_attributes(),
    }
    terminal = next(step for step in trace.steps if step["event_type"] == "no_capability_found")
    assert terminal["attributes"]["reason"] == "no_matching_capability"
    assert terminal["error_code"] == "capability_not_found"
    assert all(step["event_type"] != "intent_parse_failed" for step in trace.steps)


def test_matching_decision_keeps_executable_reference_and_gateway_routing() -> None:
    raw = '{"match":"capability","capability_id":"oa.list_pending_workflows"}'
    envelope, store, trace, gateway, _ = _run_runtime(
        "oa.list_pending_workflows",
        [_capability("oa.list_pending_workflows")],
        llm_completion=LLMCompletionResponse(content=raw),
        structured_output_override=JSONStructuredOutputProvider(),
    )
    assert envelope.status == "completed"
    assert store.status_updates[-1][1:] == ("completed", None)
    assert gateway.calls[0]["capability_id"] == "oa.list_pending_workflows"
    assert all(step["event_type"] != "no_capability_found" for step in trace.steps)


def test_output_schema_requires_explicit_discriminator_and_conditional_id() -> None:
    schema = IntentOutput.model_json_schema()
    assert schema["discriminator"]["propertyName"] == "match"
    assert set(schema["discriminator"]["mapping"]) == {"capability", "none"}
    assert set(schema["$defs"]["MatchedIntent"]["required"]) == {"match", "capability_id"}
    assert schema["$defs"]["UnmatchedIntent"]["required"] == ["match"]
    assert schema["$defs"]["UnmatchedIntent"]["additionalProperties"] is False


def test_user_guidance_is_short_and_lists_only_available_oa_operations() -> None:
    capabilities = [
        _capability("oa.list_pending_workflows"),
        _capability("oa.list_system_messages"),
    ]
    message, fallback = BasicKnowledge().no_capability_guidance(capabilities)
    assert message == "暂未接入该能力。当前可用能力：OA 待办、OA 系统消息。"
    assert len(message) < 60
    for text in (message, fallback):
        assert "Admin Lite" not in text
        assert "Registry" not in text
        assert "配置" not in text
    empty, _ = BasicKnowledge().no_capability_guidance([])
    assert empty == "暂未接入该能力。当前没有可用能力。"


def test_partial_candidate_none_is_not_global_no_match() -> None:
    target = _capability("oa.target")
    others = [_capability(f"oa.other-{index}") for index in range(8)]
    none_raw = '{"match":"none"}'
    select_raw = '{"match":"capability","capability_id":"oa.target"}'

    complete, complete_store, complete_trace, _, _ = _run_runtime(
        "oa.target",
        [target],
        llm_completion=LLMCompletionResponse(content=none_raw),
        structured_output_override=JSONStructuredOutputProvider(),
    )
    partial, partial_store, partial_trace, partial_gateway, _ = _run_runtime(
        "oa.target",
        [target, *others],
        llm_completion=LLMCompletionResponse(content=none_raw),
        structured_output_override=JSONStructuredOutputProvider(),
    )
    success, success_store, _success_trace, success_gateway, _ = _run_runtime(
        "oa.target",
        [target, *others],
        llm_completion=LLMCompletionResponse(content=select_raw),
        structured_output_override=JSONStructuredOutputProvider(),
    )
    full, _full_store, _full_trace, _full_gateway, _ = _run_runtime(
        "oa.target",
        [target, others[0]],
        llm_completion=LLMCompletionResponse(content=select_raw),
        structured_output_override=JSONStructuredOutputProvider(),
    )

    assert complete.status == "no_capability_found"
    assert complete_store.status_updates[-1][1:] == ("no_capability_found", "capability_not_found")
    assert any(step["event_type"] == "no_capability_found" for step in complete_trace.steps)
    assert partial.status == "failed"
    assert partial_store.status_updates[-1][1:] == (
        "failed",
        "capability_candidates_low_confidence",
    )
    assert partial.message == "当前请求尚未定位到足够明确的能力，请补充目标系统和具体操作。"
    assert partial.fallback_text == "Please specify the target system and operation."
    assert "暂未接入" not in partial.message
    assert partial_gateway.calls == []
    assert all(step["event_type"] != "no_capability_found" for step in partial_trace.steps)
    partial_intent = next(
        step for step in partial_trace.steps if step["event_type"] == "intent_parsed"
    )
    assert partial_intent["attributes"]["coverage_complete"] is False
    assert partial_intent["attributes"]["reason"] == "low_confidence"
    notice = "本次仅在相关性最高的部分能力中选择；若目标不符，请补充具体操作。"
    assert success.status == "completed"
    assert success_store.status_updates[-1][1:] == ("completed", None)
    assert success_gateway.calls[0]["capability_id"] == "oa.target"
    assert success.message.endswith(notice)
    assert success.message.count(notice) == 1
    assert success.fallback_text.endswith(
        "This selection considered only the most relevant capabilities; specify the "
        "operation if the result does not match your goal."
    )
    assert full.status == "completed"
    assert notice not in full.message
