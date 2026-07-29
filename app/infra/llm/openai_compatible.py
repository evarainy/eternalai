"""OpenAI-compatible vLLM adapter using only the Python standard library."""

from __future__ import annotations

import asyncio
import json
import socket
from http.client import HTTPResponse
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import OpenerDirector, Request, build_opener

from app.ports.llm_provider import (
    LLMCompletionResponse,
    LLMMessage,
    LLMProviderErrorCode,
)

_MAX_RESPONSE_BYTES = 2_097_152
_GENERIC_ERROR_MESSAGES: dict[LLMProviderErrorCode, str] = {
    "timeout": "LLM request timed out.",
    "rate_limited": "LLM provider rate limit reached.",
    "context_length_exceeded": "LLM context length was exceeded.",
    "provider_error": "LLM provider request failed.",
    "invalid_request": "LLM request was rejected.",
    "model_not_available": "LLM model is not available.",
}


class OpenAICompatibleLLMProvider:
    """Single-pass raw JSON client for a vLLM OpenAI-compatible endpoint."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        max_tokens: int,
        temperature: float,
        top_p: float,
        top_k: int,
        enable_thinking: bool,
        opener: OpenerDirector | None = None,
    ) -> None:
        normalized_base_url = base_url.rstrip("/")
        if not normalized_base_url.startswith(("http://", "https://")):
            raise ValueError("LLM base URL must use HTTP or HTTPS")
        if timeout_seconds <= 0 or max_tokens <= 0 or top_k <= 0:
            raise ValueError("LLM timeout and token/sampling limits must be positive")
        if not 0.0 <= temperature <= 2.0 or not 0.0 < top_p <= 1.0:
            raise ValueError("LLM sampling parameters are outside the allowed range")
        self._base_url = normalized_base_url
        self._timeout_seconds = timeout_seconds
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._top_p = top_p
        self._top_k = top_k
        self._enable_thinking = enable_thinking
        self._opener = opener or build_opener()

    async def complete(
        self,
        messages: list[LLMMessage],
        model: str,
        response_format: dict[str, Any] | None = None,
    ) -> LLMCompletionResponse:
        return await self._request_completion(messages, model, response_format)

    async def chat(
        self,
        messages: list[LLMMessage],
        model: str,
        response_format: dict[str, Any] | None = None,
    ) -> LLMCompletionResponse:
        return await self._request_completion(messages, model, response_format)

    async def check_health(
        self,
        expected_model: str,
        *,
        timeout_seconds: float,
    ) -> bool:
        """Confirm the endpoint is reachable and advertises the configured model."""

        if timeout_seconds <= 0:
            return False
        request = Request(
            f"{self._base_url}/models",
            headers={
                "Accept": "application/json",
                "User-Agent": "EternalAI-vLLM-Health/1",
            },
            method="GET",
        )
        try:
            payload = await asyncio.to_thread(
                self._open_json,
                request,
                timeout_seconds,
            )
            models = payload.get("data")
            if not isinstance(models, list):
                return False
            return any(
                isinstance(item, dict) and item.get("id") == expected_model
                for item in models
            )
        except Exception:
            return False

    async def _request_completion(
        self,
        messages: list[LLMMessage],
        model: str,
        response_format: dict[str, Any] | None,
    ) -> LLMCompletionResponse:
        normalized_model = model.strip()
        if not normalized_model:
            return _failure("invalid_request")
        payload: dict[str, Any] = {
            "model": normalized_model,
            "messages": [message.model_dump(mode="json") for message in messages],
            "temperature": self._temperature,
            "top_p": self._top_p,
            "top_k": self._top_k,
            "max_tokens": self._max_tokens,
        }
        if response_format is not None:
            payload["response_format"] = response_format
        if not self._enable_thinking:
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        request = Request(
            f"{self._base_url}/chat/completions",
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "EternalAI-vLLM/1",
            },
            method="POST",
        )
        try:
            response_payload = await asyncio.to_thread(
                self._open_json,
                request,
                self._timeout_seconds,
            )
            return _completion_response(response_payload)
        except HTTPError as exc:
            return _failure(_error_code_for_http_status(exc.code))
        except (TimeoutError, socket.timeout):
            return _failure("timeout")
        except URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                return _failure("timeout")
            return _failure("provider_error")
        except (UnicodeError, ValueError, json.JSONDecodeError, OSError):
            return _failure("provider_error")

    def _open_json(self, request: Request, timeout_seconds: float) -> dict[str, Any]:
        with self._opener.open(request, timeout=timeout_seconds) as response:
            return _read_json_object(response)


def _read_json_object(response: HTTPResponse) -> dict[str, Any]:
    status_code = int(response.getcode())
    if status_code < 200 or status_code >= 300:
        raise ValueError("LLM response status is not successful")
    raw = response.read(_MAX_RESPONSE_BYTES + 1)
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise ValueError("LLM response exceeds the size limit")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("LLM response must be a JSON object")
    return {str(key): value for key, value in payload.items()}


def _completion_response(payload: dict[str, Any]) -> LLMCompletionResponse:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return _failure("provider_error")
    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        return _failure("provider_error")
    message = first_choice.get("message")
    if not isinstance(message, dict):
        return _failure("provider_error")
    content = message.get("content")
    if not isinstance(content, str):
        return _failure("provider_error")
    model_used = payload.get("model")
    usage = payload.get("usage")
    total_tokens = usage.get("total_tokens") if isinstance(usage, dict) else None
    return LLMCompletionResponse(
        content=content,
        model_used=model_used if isinstance(model_used, str) else None,
        usage_tokens=total_tokens if isinstance(total_tokens, int) else None,
    )


def _failure(error_code: LLMProviderErrorCode) -> LLMCompletionResponse:
    return LLMCompletionResponse(
        error_code=error_code,
        error_message=_GENERIC_ERROR_MESSAGES[error_code],
    )


def _error_code_for_http_status(status_code: int) -> LLMProviderErrorCode:
    if status_code == 429:
        return "rate_limited"
    if status_code in {404, 410}:
        return "model_not_available"
    if status_code in {400, 413, 422}:
        return "invalid_request"
    return "provider_error"


__all__ = ("OpenAICompatibleLLMProvider",)
