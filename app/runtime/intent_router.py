"""Intent parsing through the frozen LLM and structured-output boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

from pydantic import ValidationError

from app.ports.llm_provider import LLMMessage, LLMProviderPort
from app.ports.structured_output import StructuredOutputPort
from app.runtime.models import CapabilityRef

JSON_OBJECT_RESPONSE_FORMAT: dict[str, str] = {"type": "json_object"}
IntentFailureReason: TypeAlias = Literal[
    "blank_input",
    "provider_error",
    "empty_response",
    "structured_output_error",
    "schema_invalid",
]

_INTENT_SYSTEM_PROMPT = (
    "Normalize the user request into one JSON object with keys capability_id, "
    "arguments, target_system, and capability_type. capability_id must be a stable "
    "intent tag or exact capability id. Use null for unknown optional constraints."
)


@dataclass(frozen=True)
class IntentParseResult:
    capability_ref: CapabilityRef | None = None
    failure_reason: IntentFailureReason | None = None


class IntentRouter:
    """Turn normalized user text into a validated runtime-local intent result."""

    def __init__(
        self,
        llm_provider: LLMProviderPort,
        structured_output: StructuredOutputPort,
        model: str,
    ) -> None:
        normalized_model = model.strip()
        if not normalized_model:
            raise ValueError("Intent model must not be empty")
        self._llm_provider = llm_provider
        self._structured_output = structured_output
        self._model = normalized_model

    async def parse(
        self,
        message: str,
        *,
        trace_metadata: dict[str, Any] | None = None,
    ) -> IntentParseResult:
        normalized_message = _normalize_user_message(message)
        if not normalized_message:
            return IntentParseResult(failure_reason="blank_input")

        completion = await self._llm_provider.complete(
            messages=[
                LLMMessage(role="system", content=_INTENT_SYSTEM_PROMPT),
                LLMMessage(role="user", content=normalized_message),
            ],
            model=self._model,
            response_format=dict(JSON_OBJECT_RESPONSE_FORMAT),
        )
        if completion.error_code is not None:
            return IntentParseResult(failure_reason="provider_error")
        if completion.content is None:
            return IntentParseResult(failure_reason="empty_response")

        raw_response = completion.content.strip()
        if not raw_response:
            return IntentParseResult(failure_reason="empty_response")

        caller_metadata = trace_metadata or {}
        parser_metadata = {
            key: caller_metadata[key]
            for key in ("trace_id", "task_id")
            if key in caller_metadata
        }
        result = await self._structured_output.parse_to_schema(
            raw_response,
            CapabilityRef,
            trace_metadata=parser_metadata,
        )
        if result.error is not None or result.parsed is None:
            return IntentParseResult(failure_reason="structured_output_error")
        try:
            capability_ref = CapabilityRef.model_validate(result.parsed)
        except ValidationError:
            return IntentParseResult(failure_reason="schema_invalid")
        return IntentParseResult(capability_ref=capability_ref)


def _normalize_user_message(message: str) -> str:
    return message.replace("\r\n", "\n").replace("\r", "\n").strip()


__all__ = (
    "IntentFailureReason",
    "IntentParseResult",
    "IntentRouter",
    "JSON_OBJECT_RESPONSE_FORMAT",
)
