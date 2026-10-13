"""OpenAI-compatible vLLM adapter using only the Python standard library."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import secrets
import socket
from collections import Counter
from http.client import HTTPResponse
from time import monotonic
from typing import Any, Literal, NamedTuple
from urllib.error import HTTPError, URLError
from urllib.request import OpenerDirector, Request, build_opener

from app.ports.llm_provider import (
    LLMCompletionResponse,
    LLMMessage,
    LLMProviderErrorCode,
)

_MAX_RESPONSE_BYTES = 2_097_152
_SYSTEM_MESSAGE_BOUNDARY = "\n\n--- SYSTEM MESSAGE BOUNDARY ---\n\n"
_FAILURE_LOGGER = logging.getLogger("eternalai.llm_failures")
_FAILURE_LOGGER.addHandler(logging.NullHandler())
_FAILURE_LOGGER.propagate = False
_SAFE_REQUEST_KEYS = (
    "model", "messages", "temperature", "top_p", "top_k", "max_tokens",
    "response_format", "chat_template_kwargs", "tools", "tool_choice",
)
_SAFE_MODEL_LABELS = frozenset({"glm-4.7", "qwen3.6-27b"})
_SAFE_PROVIDER_ERROR_VALUES = {
    "type": frozenset({"invalid_request_error", "bad_request", "BadRequestError"}),
    "code": frozenset({
        "context_length_exceeded", "model_not_found", "unsupported_parameter",
        "invalid_request", "bad_request",
    }),
    "param": frozenset({
        "model", "messages", "max_tokens", "temperature", "top_p", "top_k",
        "response_format", "chat_template_kwargs", "tools", "tool_choice",
    }),
}
_GENERIC_ERROR_MESSAGES: dict[LLMProviderErrorCode, str] = {
    "timeout": "LLM request timed out.",
    "rate_limited": "LLM provider rate limit reached.",
    "context_length_exceeded": "LLM context length was exceeded.",
    "provider_error": "LLM provider request failed.",
    "invalid_request": "LLM request was rejected.",
    "model_not_available": "LLM model is not available.",
}


class _SafeRequestFacts(NamedTuple):
    model: str
    parameters: str
    max_tokens: str
    temperature: str
    top_p: str
    top_k: str
    response_format: str
    thinking: str
    system_count: int
    user_count: int
    assistant_count: int


def _merge_leading_system_messages(messages: list[LLMMessage]) -> list[LLMMessage]:
    prefix_end = 0
    while prefix_end < len(messages) and messages[prefix_end].role == "system":
        prefix_end += 1
    if prefix_end < 2:
        return list(messages)
    merged = LLMMessage(
        role="system",
        content=_SYSTEM_MESSAGE_BOUNDARY.join(
            message.content for message in messages[:prefix_end]
        ),
    )
    return [merged, *messages[prefix_end:]]


def _safe_int(value: object) -> str:
    return str(value) if type(value) is int and 1 <= value <= 1_000_000 else "unknown"


def _safe_float(value: object, *, lower: float, upper: float) -> str:
    if type(value) not in (int, float) or not math.isfinite(value):
        return "unknown"
    return str(value) if lower <= value <= upper else "unknown"


def _safe_request_facts(payload: dict[str, Any]) -> _SafeRequestFacts:
    # The projection reads only fixed parameter names and message roles.
    model = payload.get("model")
    response_format = payload.get("response_format")
    thinking = payload.get("chat_template_kwargs")
    roles = Counter(
        item.get("role") for item in payload.get("messages", ())
        if type(item) is dict and item.get("role") in {"system", "user", "assistant"}
    )
    return _SafeRequestFacts(
        model=model if type(model) is str and model in _SAFE_MODEL_LABELS else "unknown",
        parameters=",".join(key for key in _SAFE_REQUEST_KEYS if key in payload),
        max_tokens=_safe_int(payload.get("max_tokens")),
        temperature=_safe_float(payload.get("temperature"), lower=0.0, upper=2.0),
        top_p=_safe_float(payload.get("top_p"), lower=0.0, upper=1.0),
        top_k=_safe_int(payload.get("top_k")),
        response_format=(
            "json_object" if type(response_format) is dict
            and response_format.get("type") == "json_object"
            else "absent" if "response_format" not in payload else "unknown"
        ),
        thinking=(
            "false" if type(thinking) is dict and thinking.get("enable_thinking") is False
            else "true" if type(thinking) is dict and thinking.get("enable_thinking") is True
            else "absent" if "chat_template_kwargs" not in payload else "unknown"
        ),
        system_count=roles["system"],
        user_count=roles["user"],
        assistant_count=roles["assistant"],
    )


def _safe_provider_error_headers(error: HTTPError) -> tuple[str, str, str]:
    # HTTPError response bodies remain unread: a read can wait for the socket timeout.
    headers = error.headers
    if headers is None:
        return ("unknown", "unknown", "unknown")
    result = []
    for field in ("type", "code", "param"):
        try:
            value = headers.get("X-Error-" + field.title())
        except (AttributeError, TypeError, ValueError):
            return ("unknown", "unknown", "unknown")
        result.append(
            value if type(value) is str and value in _SAFE_PROVIDER_ERROR_VALUES[field]
            else "unknown"
        )
    return (result[0], result[1], result[2])


def _record_failure(
    category: Literal[
        "http_status", "timeout", "network", "response_decode",
        "response_json", "response_invalid", "transport_os", "response_shape",
    ],
    *,
    status: int | None,
    error_code: LLMProviderErrorCode,
    started: float,
    facts: _SafeRequestFacts,
    provider_error: tuple[str, str, str] = ("unknown", "unknown", "unknown"),
) -> None:
    # Only code-owned labels and bounded HTTP status reach the dedicated sink.
    safe_status = status if type(status) is int and 100 <= status <= 599 else None
    _FAILURE_LOGGER.info(
        "llm_failure category=%s http_status=%s mapped_error=%s "
        "provider_error_type=%s provider_error_code=%s provider_error_param=%s "
        "model_label=%s parameters=%s max_tokens=%s temperature=%s top_p=%s "
        "top_k=%s response_format=%s thinking=%s roles_system=%s roles_user=%s "
        "roles_assistant=%s elapsed_ms=%s trace=%s",
        category, str(safe_status) if safe_status is not None else "null",
        error_code, *provider_error, *facts,
        round((monotonic() - started) * 1000),
        secrets.token_hex(16),
    )


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
            "messages": [
                message.model_dump(mode="json")
                for message in _merge_leading_system_messages(messages)
            ],
            "temperature": self._temperature,
            "top_p": self._top_p,
            "top_k": self._top_k,
            "max_tokens": self._max_tokens,
        }
        if response_format is not None:
            payload["response_format"] = response_format
        if not self._enable_thinking:
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        facts = _safe_request_facts(payload)
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
        started = monotonic()
        try:
            response_payload = await asyncio.to_thread(
                self._open_json,
                request,
                self._timeout_seconds,
            )
            completion = _completion_response(response_payload)
            if completion.error_code is not None:
                _record_failure(
                    "response_shape", status=None, error_code=completion.error_code,
                    started=started, facts=facts,
                )
            return completion
        except HTTPError as exc:
            error_code = _error_code_for_http_status(exc.code)
            _record_failure(
                "http_status", status=exc.code, error_code=error_code,
                started=started, facts=facts,
                provider_error=_safe_provider_error_headers(exc),
            )
            return _failure(error_code)
        except (TimeoutError, socket.timeout):
            _record_failure("timeout", status=None, error_code="timeout", started=started,
                            facts=facts)
            return _failure("timeout")
        except URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                _record_failure("timeout", status=None, error_code="timeout", started=started,
                                facts=facts)
                return _failure("timeout")
            _record_failure("network", status=None, error_code="provider_error", started=started,
                            facts=facts)
            return _failure("provider_error")
        except UnicodeError:
            _record_failure("response_decode", status=None, error_code="provider_error",
                            started=started, facts=facts)
            return _failure("provider_error")
        except json.JSONDecodeError:
            _record_failure("response_json", status=None, error_code="provider_error",
                            started=started, facts=facts)
            return _failure("provider_error")
        except ValueError:
            _record_failure("response_invalid", status=None, error_code="provider_error",
                            started=started, facts=facts)
            return _failure("provider_error")
        except OSError:
            _record_failure("transport_os", status=None, error_code="provider_error",
                            started=started, facts=facts)
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
