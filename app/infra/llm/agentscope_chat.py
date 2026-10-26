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
import re
import secrets
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping, Sequence
from contextvars import ContextVar, Token
from dataclasses import dataclass
from time import monotonic
from typing import TYPE_CHECKING, Any, Literal, NoReturn, TypeAlias

from agentscope.agent import Agent, ContextConfig, InjectionConfig, ModelConfig, ReActConfig
from agentscope.credential import OpenAICredential
from agentscope.event import ToolResultEndEvent, ToolResultStartEvent
from agentscope.message import Msg, ToolCallBlock, ToolResultState
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
    "tool_call_budget_exceeded",
]

# Fixed request contract (PLAN v1.3 section 4.1.1); not environment-tunable.
MAX_OUTPUT_TOKENS = 2048
TEMPERATURE = 0.0
TOP_P = 1.0


@dataclass(frozen=True, slots=True)
class TurnLimits:
    """Per-turn bounds of one AgentScope reply; code-owned, not env-tunable."""

    model_call_timeout_seconds: float
    turn_timeout_seconds: float
    max_model_calls: int
    max_tool_calls_per_response: int
    max_tool_executions: int
    max_resets: int
    max_groups_per_reset: int
    max_tool_tokens: int


# M1 tool-group turn. The SDK adds one forced summary call at max_iters; the
# guard rejects it as over budget, so a turn makes at most five real calls.
M1_TURN_LIMITS = TurnLimits(
    model_call_timeout_seconds=60.0,
    turn_timeout_seconds=120.0,
    max_model_calls=5,
    max_tool_calls_per_response=2,
    max_tool_executions=4,
    max_resets=2,
    max_groups_per_reset=3,
    max_tool_tokens=12000,
)

# Decides whether an ERROR tool result is a discovery error (the model may go
# on) or a business error (tool use stops for the reply). Arguments: tool call
# id, tool name, result metadata and the agent state.
ToolErrorKind: TypeAlias = Literal["discovery", "business"]
ToolErrorClassifier: TypeAlias = Callable[
    [str, str, Mapping[str, Any], AgentState], ToolErrorKind
]

# Empirical bound on the SDK's bytes/4 input estimate (template overhead not
# counted), well below the measured 200000-token vLLM window minus 2048 output.
DEFAULT_CONTEXT_TOKEN_BUDGET = 65536
# Interruption fallback text written by the framework. It is not a business
# fact: callers must re-read business records instead of trusting it.
INTERRUPTION_MESSAGE = "本轮对话已中断，请以业务记录的实际状态为准。"

# vLLM accepts any bearer value when no API key is configured; this is a
# placeholder, not a credential.
_NO_AUTH_PLACEHOLDER = "EMPTY"
# The guard's wall-clock timeout fires first; the client timeout is a backstop.
_CLIENT_TIMEOUT_SECONDS = M1_TURN_LIMITS.model_call_timeout_seconds + 5.0
_DISABLE_THINKING_BODY = {"chat_template_kwargs": {"enable_thinking": False}}
_FORBIDDEN_TOOL_NAMES = frozenset({"generate_structured_output", "CompressContext"})
_STATE_KEY = "eternalai_model_guard"
_HALT_KEY = "eternalai_tool_halt"
_EXECUTION_KEY = "eternalai_tool_executions"
_SAFE_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")

# Absolute end of the current bounded turn (event-loop clock); tool-side waits
# such as task closes must fit inside it.
_TURN_DEADLINE: ContextVar[float | None] = ContextVar("eternalai_turn_deadline", default=None)
# One random id per turn, shared by every model, tool, stop and turn log line
# so a single turn can be followed across loggers; carries no request data.
_TURN_ID: ContextVar[str | None] = ContextVar("eternalai_turn_id", default=None)

# Same convention as uvicorn.error.mcp_stages: lines reach the server log through
# uvicorn's handler. Only codes, counts, timings and ids are ever logged here.
_MODEL_LOGGER = logging.getLogger("uvicorn.error.agentscope_model")


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
    limits: TurnLimits,
    extra_middlewares: Sequence[MiddlewareBase] = (),
    classify_tool_error: ToolErrorClassifier | None = None,
) -> Agent:
    """Create an agent with the fixed retry, compression and iteration bounds.

    ``extra_middlewares`` wrap the guard; index 0 is the outermost layer.
    """

    return Agent(
        name=name,
        system_prompt=system_prompt,
        model=model,
        toolkit=toolkit,
        middlewares=[
            *extra_middlewares,
            BoundedModelGuard(limits, classify_tool_error=classify_tool_error),
        ],
        state=state,
        model_config=ModelConfig(max_retries=0, fallback_model=None),
        context_config=ContextConfig(compression_tool_enabled=False),
        react_config=ReActConfig(
            max_iters=limits.max_model_calls,
            interruption_message=INTERRUPTION_MESSAGE,
            interruption_raise_cancelled_error=True,
        ),
        injection_config=InjectionConfig(inject_runtime_state=False),
    )


def new_turn_deadline(limits: TurnLimits) -> float:
    """Absolute end (event-loop clock) of a turn that starts now."""

    return asyncio.get_running_loop().time() + limits.turn_timeout_seconds


async def run_bounded_turn(
    agent: Agent, inputs: Msg | list[Msg], *, deadline: float
) -> Msg:
    """Run one user turn within the fixed wall-clock budget.

    ``deadline`` is an absolute event-loop time set when the caller's turn
    began, so tool preparation done before this call counts against the same
    budget. Never pass a structured schema: the forced structured-output path
    is out of contract for this seam.
    """

    started = monotonic()
    token = _TURN_DEADLINE.set(deadline)
    turn_token = _TURN_ID.set(secrets.token_hex(8)) if _TURN_ID.get() is None else None
    try:
        async with asyncio.timeout_at(deadline):
            return await agent.reply(inputs)
    except TimeoutError:
        _log_turn_failure("turn_timeout", started)
        raise AgentScopeModelError("turn_timeout") from None
    except AgentScopeModelError as exc:
        _log_turn_failure(exc.code, started)
        raise
    finally:
        _TURN_DEADLINE.reset(token)
        if turn_token is not None:
            _TURN_ID.reset(turn_token)


def bind_turn_id() -> tuple[str, Token[str | None]]:
    """Start a turn log id for the caller's whole turn; reset with the token."""

    turn_id = secrets.token_hex(8)
    return turn_id, _TURN_ID.set(turn_id)


def reset_turn_id(token: Token[str | None]) -> None:
    _TURN_ID.reset(token)


def current_turn_id() -> str:
    """Log id of the current turn, or "none" outside a turn."""

    return _TURN_ID.get() or "none"


def turn_seconds_remaining() -> float | None:
    """Seconds left in the current bounded turn, or None outside a turn."""

    deadline = _TURN_DEADLINE.get()
    if deadline is None:
        return None
    return max(0.0, deadline - asyncio.get_running_loop().time())


def model_call_count(state: AgentState) -> int:
    """Model calls the guard admitted or rejected in the current reply."""

    record = state.middle_context.get(_STATE_KEY)
    count = record.get(state.reply_id, 0) if isinstance(record, dict) else 0
    return int(count)


def tool_execution_count(state: AgentState) -> int:
    """Tool executions claimed in the current reply."""

    record = state.middle_context.get(_EXECUTION_KEY)
    count = record.get(state.reply_id, 0) if isinstance(record, dict) else 0
    return int(count)


def halt_tool_use(state: AgentState, reason: str) -> None:
    """Stop further business tool execution for the rest of this reply."""

    if tool_halt_reason(state) is None:
        state.middle_context[_HALT_KEY] = {state.reply_id: reason}
        _MODEL_LOGGER.info("agentscope_tool_halt reason=%s turn=%s", reason, current_turn_id())


def tool_halt_reason(state: AgentState) -> str | None:
    """Return the first tool error code that stopped tool use in this reply."""

    record = state.middle_context.get(_HALT_KEY)
    reason = record.get(state.reply_id) if isinstance(record, dict) else None
    return reason if isinstance(reason, str) else None


def reserve_tool_execution(state: AgentState, limits: TurnLimits) -> bool:
    """Claim one real tool execution for this reply within the turn limit."""

    reply_id = state.reply_id
    record = state.middle_context.get(_EXECUTION_KEY)
    count = record.get(reply_id, 0) if isinstance(record, dict) else 0
    if count >= limits.max_tool_executions:
        return False
    state.middle_context[_EXECUTION_KEY] = {reply_id: count + 1}
    return True


class BoundedModelGuard(MiddlewareBase):
    """Enforce per-turn call budget, request shape and per-call timeout.

    A tool result in ERROR state also stops further tool execution for the
    rest of the reply, so rejected parameters are clarified with the user
    instead of being repaired and retried by the model. With a classifier,
    only results it classifies as business errors stop tool use; discovery
    errors (inactive or unexposed tools, rejected group resets) do not.
    """

    def __init__(
        self,
        limits: TurnLimits,
        *,
        classify_tool_error: ToolErrorClassifier | None = None,
    ) -> None:
        self._limits = limits
        self._classify = classify_tool_error

    async def on_reply(
        self,
        agent: Agent,
        input_kwargs: dict[str, Any],
        next_handler: Callable[..., AsyncGenerator[Any, None]],
    ) -> AsyncGenerator[Any, None]:
        # Events arrive one at a time; a sequential batch does not start the
        # next tool call until this generator pulls again.
        names: dict[str, str] = {}
        async for event in next_handler(**input_kwargs):
            if isinstance(event, ToolResultStartEvent):
                names[event.tool_call_id] = event.tool_call_name
            elif isinstance(event, ToolResultEndEvent) and event.state == ToolResultState.ERROR:
                self._on_tool_error(agent, event, names.get(event.tool_call_id, ""))
            yield event

    def _on_tool_error(self, agent: Agent, event: ToolResultEndEvent, name: str) -> None:
        code = event.metadata.get("error_code")
        safe_code = code if isinstance(code, str) and _SAFE_CODE.fullmatch(code) else None
        if self._classify is not None:
            kind = self._classify(event.tool_call_id, name, event.metadata, agent.state)
            if kind == "discovery":
                _MODEL_LOGGER.info(
                    "agentscope_tool_discovery_error code=%s turn=%s",
                    safe_code or "framework", current_turn_id(),
                )
                return
        halt_tool_use(agent.state, safe_code or "tool_input_rejected")

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
        limits = self._limits
        if call_index > limits.max_model_calls:
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
            async with asyncio.timeout(limits.model_call_timeout_seconds):
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
            if monotonic() - started >= limits.model_call_timeout_seconds:
                _reject("timeout", call_index, started)
        tool_calls = sum(1 for block in response.content if isinstance(block, ToolCallBlock))
        if tool_calls > limits.max_tool_calls_per_response:
            _reject("tool_call_budget_exceeded", call_index, started)
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
        "elapsed_ms=%s turn=%s",
        code, error_class, call_index, round((monotonic() - started) * 1000),
        current_turn_id(),
    )
    raise AgentScopeModelError(code)


def _log_model_call(call_index: int, started: float, response: ChatResponse) -> None:
    # Only counts, enum values and timings; never message or argument text.
    content = response.content or []
    tool_calls = sum(1 for block in content if isinstance(block, ToolCallBlock))
    usage = response.usage
    _MODEL_LOGGER.info(
        "agentscope_model_call result=ok call_index=%s elapsed_ms=%s finished=%s "
        "tool_calls=%s blocks=%s input_tokens=%s output_tokens=%s turn=%s",
        call_index, round((monotonic() - started) * 1000), str(response.finished_reason),
        tool_calls, len(content),
        usage.input_tokens if usage is not None else "unknown",
        usage.output_tokens if usage is not None else "unknown",
        current_turn_id(),
    )


def log_turn_end(
    outcome: str,
    started: float,
    *,
    model_calls: int,
    tool_calls: int,
    tool_executions: int,
    halt_reason: str | None,
) -> None:
    """One closing line per turn: outcome, counts and duration only."""

    _MODEL_LOGGER.info(
        "agentscope_turn_end outcome=%s model_calls=%s tool_calls=%s tool_executions=%s "
        "halt=%s elapsed_ms=%s turn=%s",
        outcome if _SAFE_CODE.fullmatch(outcome) else "other",
        model_calls, tool_calls, tool_executions,
        halt_reason if halt_reason and _SAFE_CODE.fullmatch(halt_reason) else "none",
        round((monotonic() - started) * 1000), current_turn_id(),
    )


def _log_turn_failure(code: AgentScopeErrorCode, started: float) -> None:
    _MODEL_LOGGER.info(
        "agentscope_turn result=failed error=%s elapsed_ms=%s turn=%s",
        code, round((monotonic() - started) * 1000), current_turn_id(),
    )


__all__ = (
    "M1_TURN_LIMITS",
    "AgentScopeErrorCode",
    "AgentScopeModelError",
    "BoundedModelGuard",
    "ToolErrorClassifier",
    "ToolErrorKind",
    "TurnLimits",
    "bind_turn_id",
    "build_chat_model",
    "current_turn_id",
    "halt_tool_use",
    "log_turn_end",
    "create_bounded_agent",
    "model_call_count",
    "new_turn_deadline",
    "reserve_tool_execution",
    "reset_turn_id",
    "run_bounded_turn",
    "tool_execution_count",
    "tool_halt_reason",
    "turn_seconds_remaining",
)
