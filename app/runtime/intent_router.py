"""Intent parsing through the frozen LLM and structured-output boundaries."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.ports.llm_provider import LLMMessage, LLMProviderPort
from app.ports.structured_output import StructuredOutputPort
from app.runtime.models import CapabilityRef

JSON_OBJECT_RESPONSE_FORMAT: dict[str, str] = {"type": "json_object"}

_INTENT_SYSTEM_PROMPT = (
    "Normalize the user request into one JSON object with keys capability_id, "
    "arguments, target_system, and capability_type. capability_id must be a stable "
    "intent tag or exact capability id. Use null for unknown optional constraints."
)


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
    ) -> CapabilityRef | None:
        normalized_message = _normalize_user_message(message)
        if not normalized_message:
            return None

        completion = await self._llm_provider.complete(
            messages=[
                LLMMessage(role="system", content=_INTENT_SYSTEM_PROMPT),
                LLMMessage(role="user", content=normalized_message),
            ],
            model=self._model,
            response_format=dict(JSON_OBJECT_RESPONSE_FORMAT),
        )
        if completion.error_code is not None or completion.content is None:
            return None

        raw_response = completion.content.strip()
        if not raw_response:
            return None

        parser_metadata = dict(completion.trace_metadata)
        parser_metadata.update(trace_metadata or {})
        result = await self._structured_output.parse_to_schema(
            raw_response,
            CapabilityRef,
            trace_metadata=parser_metadata,
        )
        if result.error is not None or result.parsed is None:
            return None
        try:
            return CapabilityRef.model_validate(result.parsed)
        except ValidationError:
            return None


def _normalize_user_message(message: str) -> str:
    return message.replace("\r\n", "\n").replace("\r", "\n").strip()


__all__ = ("IntentRouter", "JSON_OBJECT_RESPONSE_FORMAT")
