"""Deterministic LLMProviderPort adapter for tests and Golden Tasks."""

from __future__ import annotations

from typing import Any

from app.ports.llm_provider import LLMCompletionResponse, LLMMessage


class MockLLMProvider:
    """Return registered responses or pass through the final user message."""

    def __init__(self) -> None:
        self._responses: dict[str, LLMCompletionResponse] = {}
        self.calls: list[dict[str, Any]] = []

    def register(
        self,
        user_message: str,
        response: LLMCompletionResponse,
    ) -> None:
        self._responses[user_message] = response

    async def complete(
        self,
        messages: list[LLMMessage],
        model: str,
        response_format: dict[str, Any] | None = None,
    ) -> LLMCompletionResponse:
        return self._respond("complete", messages, model, response_format)

    async def chat(
        self,
        messages: list[LLMMessage],
        model: str,
        response_format: dict[str, Any] | None = None,
    ) -> LLMCompletionResponse:
        return self._respond("chat", messages, model, response_format)

    def _respond(
        self,
        method: str,
        messages: list[LLMMessage],
        model: str,
        response_format: dict[str, Any] | None,
    ) -> LLMCompletionResponse:
        user_message = next(
            (message.content for message in reversed(messages) if message.role == "user"),
            "",
        )
        self.calls.append(
            {
                "method": method,
                "messages": list(messages),
                "model": model,
                "response_format": response_format,
            }
        )
        return self._responses.get(
            user_message,
            LLMCompletionResponse(content=user_message, model_used=model),
        )


__all__ = ("MockLLMProvider",)
