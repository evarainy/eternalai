from __future__ import annotations

import asyncio
import json
from typing import Any, cast
from urllib.error import HTTPError
from urllib.request import OpenerDirector, Request

from app.infra.llm.openai_compatible import OpenAICompatibleLLMProvider
from app.ports.llm_provider import LLMMessage


class FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._raw = json.dumps(payload).encode("utf-8")
        self._status_code = status_code

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def getcode(self) -> int:
        return self._status_code

    def read(self, _limit: int) -> bytes:
        return self._raw


class RecordingOpener:
    def __init__(
        self,
        response: FakeResponse | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.calls: list[tuple[Request, float]] = []

    def open(self, request: Request, timeout: float) -> FakeResponse:
        self.calls.append((request, timeout))
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def _provider(opener: RecordingOpener) -> OpenAICompatibleLLMProvider:
    return OpenAICompatibleLLMProvider(
        base_url="http://vllm.invalid:8000/v1",
        timeout_seconds=120,
        max_tokens=2048,
        temperature=0.6,
        top_p=0.95,
        top_k=20,
        enable_thinking=False,
        opener=cast(OpenerDirector, opener),
    )


def test_vllm_adapter_sends_raw_json_parameters_and_parses_response() -> None:
    opener = RecordingOpener(
        FakeResponse(
            {
                "model": "qwen3.5-27b",
                "choices": [
                    {
                        "message": {
                            "content": '{"capability_id":"oa.list_pending_workflows"}'
                        }
                    }
                ],
                "usage": {"total_tokens": 41},
            }
        )
    )
    provider = _provider(opener)

    result = asyncio.run(
        provider.complete(
            [LLMMessage(role="user", content="查询我的待办")],
            "qwen3.5-27b",
            {"type": "json_object"},
        )
    )

    assert result.error_code is None
    assert result.content == '{"capability_id":"oa.list_pending_workflows"}'
    assert result.model_used == "qwen3.5-27b"
    assert result.usage_tokens == 41
    request, timeout = opener.calls[0]
    assert timeout == 120
    assert request.full_url == "http://vllm.invalid:8000/v1/chat/completions"
    payload = json.loads(cast(bytes, request.data))
    assert payload == {
        "model": "qwen3.5-27b",
        "messages": [{"role": "user", "content": "查询我的待办"}],
        "temperature": 0.6,
        "top_p": 0.95,
        "top_k": 20,
        "max_tokens": 2048,
        "response_format": {"type": "json_object"},
        "chat_template_kwargs": {"enable_thinking": False},
    }
    assert request.get_header("Authorization") is None


def test_vllm_adapter_maps_http_errors_without_leaking_provider_body() -> None:
    provider = _provider(
        RecordingOpener(
            error=HTTPError(
                url="http://vllm.invalid/v1/chat/completions",
                code=429,
                msg="secret-provider-detail",
                hdrs=None,
                fp=None,
            )
        )
    )

    result = asyncio.run(
        provider.complete(
            [LLMMessage(role="user", content="hello")],
            "qwen3.5-27b",
        )
    )

    assert result.error_code == "rate_limited"
    assert result.error_message == "LLM provider rate limit reached."
    assert "secret-provider-detail" not in repr(result)


def test_vllm_health_requires_configured_model() -> None:
    opener = RecordingOpener(
        FakeResponse({"data": [{"id": "qwen3.5-27b"}, {"id": "glm-4.7"}]})
    )

    assert (
        asyncio.run(
            _provider(opener).check_health(
                "qwen3.5-27b",
                timeout_seconds=5,
            )
        )
        is True
    )
    assert opener.calls[0][0].full_url == "http://vllm.invalid:8000/v1/models"
    assert opener.calls[0][1] == 5
