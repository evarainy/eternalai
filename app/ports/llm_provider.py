"""LLM provider port contract."""

from __future__ import annotations

from typing import Any, Literal, Protocol, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

LLMMessageRole: TypeAlias = Literal["system", "user", "assistant"]

LLMProviderErrorCode: TypeAlias = Literal[
    "timeout",
    "rate_limited",
    "context_length_exceeded",
    "provider_error",
    "invalid_request",
    "model_not_available",
]


class LLMMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: LLMMessageRole
    content: str


class LLMCompletionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str | None = None
    error_code: LLMProviderErrorCode | None = None
    error_message: str | None = None
    trace_metadata: dict[str, Any] = Field(default_factory=dict)
    model_used: str | None = None
    usage_tokens: int | None = None


class LLMProviderPort(Protocol):
    async def complete(
        self,
        messages: list[LLMMessage],
        model: str,
        response_format: dict[str, Any] | None = None,
    ) -> LLMCompletionResponse: ...

    async def chat(
        self,
        messages: list[LLMMessage],
        model: str,
        response_format: dict[str, Any] | None = None,
    ) -> LLMCompletionResponse: ...
