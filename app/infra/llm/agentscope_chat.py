"""Bounded AgentScope native tool-calling seam for the internal vLLM endpoint.

AgentScope types stay inside infra. The fixed request shape is non-thinking,
temperature 0, top_p 1, at most 2048 output tokens and explicit auto tool
choice. Model and client retries, fallback models, context compression and
forced structured output are disabled; budget overruns are rejected with a
symbolic error instead of silently switching protocol.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import AsyncGenerator, Awaitable, Callable
from time import monotonic
from typing import TYPE_CHECKING, Any, Literal, NoReturn, TypeAlias

from agentscope.agent import Agent, ContextConfig, InjectionConfig, ModelConfig, ReActConfig
from agentscope.credential import OpenAICredential
from agentscope.message import Msg, ToolCallBlock
from agentscope.middleware import MiddlewareBase
from agentscope.model import ChatResponse, FinishedReason, OpenAIChatModel
from agentscope.state import AgentState
from agentscope.tool import ToolChoice, Toolkit
from pydantic import SecretStr

if TYPE_CHECKING:
    from agentscope.model import ChatModelBase

AgentScopeErrorCode: TypeAlias = Literal[
    "timeout",
    "turn_timeout",
    "rate_limited",
    "context_length_exceeded",
    "provider_error",
    "invalid_request",
    "model_not_available",
    "model_call_budget_exceeded",
    "forbidden_request_shape",
]

# Fixed M0 request contract (PLAN v1.3 section 4.1.1); not environment-tunable.
MODEL_CALL_TIMEOUT_SECONDS = 30.0
TURN_TIMEOUT_SECONDS = 120.0
MAX_MODEL_CALLS_PER_TURN = 4
MAX_OUTPUT_TOKENS = 2048
TEMPERATURE = 0.0
TOP_P = 1.0
# Upper bound of the estimated model input; larger inputs are rejected.
DEFAULT_CONTEXT_TOKEN_BUDGET = 32768
# Interruption fallback text written by the framework. It is not a business
# fact: callers must re-read business records instead of trusting it.
INTERRUPTION_MESSAGE = "本轮对话已中断，请以业务记录的实际状态为准。"

# vLLM accepts any bearer value when no API key is configured; this is a
# placeholder, not a credential.
_NO_AUTH_PLACEHOLDER = "EMPTY"
# The guard's wall-clock timeout fires first; the client timeout is a backstop.
_CLIENT_TIMEOUT_SECONDS = MODEL_CALL_TIMEOUT_SECONDS + 5.0
_DISABLE_THINKING_BODY = {"chat_template_kwargs": {"enable_thinking": False}}
_FORBIDDEN_TOOL_NAMES = frozenset({"generate_structured_output", "CompressContext"})
_STATE_KEY = "eternalai_model_guard"

_MODEL_LOGGER = logging.getLogger("eternalai.agentscope_model")
_MODEL_LOGGER.addHandler(logging.NullHandler())
_MODEL_LOGGER.propagate = False


class AgentScopeModelError(Exception):
    """Symbolic failure of a bounded model call or agent turn."""

    def __init__(self, code: AgentScopeErrorCode) -> None:
        super().__init__(code)
        self.code: AgentScopeErrorCode = code


def build_chat_model(
    *,
    base_url: str,
    model: str,
    context_token_budget: int = DEFAULT_CONTEXT_TOKEN_BUDGET,
) -> OpenAIChatModel:
    """Build the single-attempt OpenAI-compatible chat model for vLLM."""

    normalized_base_url = base_url.rstrip("/")
    if not normalized_base_url.startswith(("http://", "https://")):
        raise ValueError("LLM base URL must use HTTP or HTTPS")
    if not model.strip():
        raise ValueError("LLM model must be configured")
    if context_token_budget <= 0:
        raise ValueError("Context token budget must be positive")
    return OpenAIChatModel(
        credential=OpenAICredential(
            api_key=SecretStr(_NO_AUTH_PLACEHOLDER),
            base_url=normalized_base_url,
        ),
        model=model.strip(),
        parameters=OpenAIChatModel.Parameters(
            max_tokens=MAX_OUTPUT_TOKENS,
            temperature=TEMPERATURE,
            top_p=TOP_P,
        ),
        stream=False,
        max_retries=0,
        context_size=context_token_budget,
        client_kwargs={
            "timeout": _CLIENT_TIMEOUT_SECONDS,
            "max_retries": 0,
        },
        extra_body=_DISABLE_THINKING_BODY,
    )


def create_bounded_agent(
    *,
    name: str,
    system_prompt: str,
    model: ChatModelBase,
    toolkit: Toolkit | None = None,
    state: AgentState | None = None,
) -> Agent:
    """Create an agent with the fixed retry, compression and iteration bounds."""

    return Agent(
        name=name,
        system_prompt=system_prompt,
        model=model,
        toolkit=toolkit,
        middlewares=[BoundedModelGuard()],
        state=state,
        model_config=ModelConfig(max_retries=0, fallback_model=None),
        context_config=ContextConfig(compression_tool_enabled=False),
        react_config=ReActConfig(
            max_iters=MAX_MODEL_CALLS_PER_TURN,
            interruption_message=INTERRUPTION_MESSAGE,
            interruption_raise_cancelled_error=True,
        ),
        injection_config=InjectionConfig(inject_runtime_state=False),
    )


async def run_bounded_turn(agent: Agent, inputs: Msg | list[Msg]) -> Msg:
    """Run one user turn within the fixed wall-clock budget.

    Never pass a structured schema: the forced structured-output path is out of
    contract for this seam.
    """

    started = monotonic()
    try:
        async with asyncio.timeout(TURN_TIMEOUT_SECONDS):
            return await agent.reply(inputs)
    except TimeoutError:
        _log_turn_failure("turn_timeout", started)
        raise AgentScopeModelError("turn_timeout") from None
    except AgentScopeModelError as exc:
        _log_turn_failure(exc.code, started)
        raise


class BoundedModelGuard(MiddlewareBase):
    """Enforce per-turn call budget, request shape and per-call timeout."""

    async def on_compress_context(
        self,
        agent: Agent,
        input_kwargs: dict[str, Any],
        next_handler: Callable[..., Awaitable[None]],
    ) -> None:
        # Compression would summarise through generate_structured_output.
        # It is skipped; the budget check below rejects oversized input.
        return None

    async def on_model_call(
        self,
        agent: Agent,
        input_kwargs: dict[str, Any],
        next_handler: Callable[..., Awaitable[ChatResponse | AsyncGenerator[ChatResponse, None]]],
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        call_index = _next_call_index(agent)
        tools: list[dict[str, Any]] = input_kwargs.get("tools") or []
        tool_choice: ToolChoice | None = input_kwargs.get("tool_choice")
        started = monotonic()
        if call_index > MAX_MODEL_CALLS_PER_TURN:
            _reject("model_call_budget_exceeded", call_index, started)
        if any(_tool_name(tool) in _FORBIDDEN_TOOL_NAMES for tool in tools):
            _reject("forbidden_request_shape", call_index, started)
        if tool_choice is not None and tool_choice.mode != "auto":
            _reject("forbidden_request_shape", call_index, started)
        if tools:
            input_kwargs = {**input_kwargs, "tool_choice": ToolChoice(mode="auto")}
        model: ChatModelBase = input_kwargs["current_model"]
        estimated = await model.count_tokens(input_kwargs["messages"], tools or None)
        if estimated > model.context_size:
            _reject("context_length_exceeded", call_index, started)
        try:
            async with asyncio.timeout(MODEL_CALL_TIMEOUT_SECONDS):
                response = await next_handler(**input_kwargs)
        except TimeoutError:
            _reject("timeout", call_index, started)
        except Exception as exc:
            _reject(_provider_error_code(exc), call_index, started, type(exc).__name__)
        if not isinstance(response, ChatResponse):
            # stream=False is fixed; a generator means the contract drifted.
            _reject("provider_error", call_index, started)
        if response.finished_reason == FinishedReason.INTERRUPTED:
            # The SDK turns cancellation into an INTERRUPTED response; when the
            # wall clock is spent, that is this guard's timeout.
            if monotonic() - started >= MODEL_CALL_TIMEOUT_SECONDS:
                _reject("timeout", call_index, started)
        _log_model_call(call_index, started, response)
        return response


def _next_call_index(agent: Agent) -> int:
    reply_id = agent.state.reply_id
    record = agent.state.middle_context.get(_STATE_KEY)
    count = record.get(reply_id, 0) if isinstance(record, dict) else 0
    agent.state.middle_context[_STATE_KEY] = {reply_id: count + 1}
    return int(count) + 1


def _tool_name(tool: dict[str, Any]) -> str | None:
    function = tool.get("function")
    name = function.get("name") if isinstance(function, dict) else None
    return name if isinstance(name, str) else None


def _provider_error_code(error: Exception) -> AgentScopeErrorCode:
    # Same HTTP status mapping as the raw JSON provider; transport errors
    # without a status are provider errors.
    status = getattr(error, "status_code", None)
    if status == 429:
        return "rate_limited"
    if status in {404, 410}:
        return "model_not_available"
    if status in {400, 413, 422}:
        return "invalid_request"
    return "provider_error"


def _reject(
    code: AgentScopeErrorCode,
    call_index: int,
    started: float,
    error_class: str = "none",
) -> NoReturn:
    # error_class is a code-owned class name, never the provider message.
    _MODEL_LOGGER.info(
        "agentscope_model_call result=rejected error=%s error_class=%s call_index=%s "
        "elapsed_ms=%s trace=%s",
        code, error_class, call_index, round((monotonic() - started) * 1000),
        secrets.token_hex(16),
    )
    raise AgentScopeModelError(code)


def _log_model_call(call_index: int, started: float, response: ChatResponse) -> None:
    # Only counts, enum values and timings; never message or argument text.
    content = response.content or []
    tool_calls = sum(1 for block in content if isinstance(block, ToolCallBlock))
    usage = response.usage
    _MODEL_LOGGER.info(
        "agentscope_model_call result=ok call_index=%s elapsed_ms=%s finished=%s "
        "tool_calls=%s blocks=%s input_tokens=%s output_tokens=%s trace=%s",
        call_index, round((monotonic() - started) * 1000), str(response.finished_reason),
        tool_calls, len(content),
        usage.input_tokens if usage is not None else "unknown",
        usage.output_tokens if usage is not None else "unknown",
        secrets.token_hex(16),
    )


def _log_turn_failure(code: AgentScopeErrorCode, started: float) -> None:
    _MODEL_LOGGER.info(
        "agentscope_turn result=failed error=%s elapsed_ms=%s trace=%s",
        code, round((monotonic() - started) * 1000), secrets.token_hex(16),
    )


__all__ = (
    "AgentScopeErrorCode",
    "AgentScopeModelError",
    "BoundedModelGuard",
    "build_chat_model",
    "create_bounded_agent",
    "run_bounded_turn",
)
