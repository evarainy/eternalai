"""Deterministic MockLLMProvider behavior."""

from __future__ import annotations

import asyncio

from app.infra.llm.mock_llm.mock_llm_provider import MockLLMProvider
from app.ports.llm_provider import LLMCompletionResponse, LLMMessage


def test_unregistered_message_is_passed_through_and_call_is_recorded() -> None:
    provider = MockLLMProvider()
    messages = [LLMMessage(role="user", content="normalized request")]

    result = asyncio.run(
        provider.complete(
            messages,
            "qwen-test",
            response_format={"type": "json_object"},
        )
    )

    assert result.content == "normalized request"
    assert result.model_used == "qwen-test"
    assert provider.calls == [
        {
            "method": "complete",
            "messages": messages,
            "model": "qwen-test",
            "response_format": {"type": "json_object"},
        }
    ]


def test_registered_response_is_returned_for_chat_without_fallback() -> None:
    provider = MockLLMProvider()
    expected = LLMCompletionResponse(
        error_code="provider_error",
        error_message="synthetic failure",
    )
    provider.register("request", expected)

    result = asyncio.run(
        provider.chat(
            [LLMMessage(role="user", content="request")],
            "qwen-test",
        )
    )

    assert result is expected
    assert provider.calls[0]["method"] == "chat"
    assert provider.calls[0]["response_format"] is None
