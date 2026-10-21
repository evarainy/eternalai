"""M0 counting turn: one bounded AgentScope reply over the read-only bridge.

The result is symbolic only. Tool arguments, tool results and the final reply
never leave this module as text; argument values are compared in-process with
the caller-supplied expectation and only the boolean verdict is returned.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from time import monotonic
from typing import Any

from agentscope.message import ToolCallBlock, ToolResultBlock, UserMsg

from app.infra.llm.agentscope_chat import (
    AgentScopeModelError,
    build_chat_model,
    create_bounded_agent,
    model_call_count,
    run_bounded_turn,
    tool_halt_reason,
)
from app.infra.orchestration.agentscope_read_tools import (
    M0_READ_CAPABILITY_IDS,
    AgentScopeReadDependencies,
    ReadToolScope,
    build_read_toolkit,
)
from app.ports.auth import AuthenticatedSessionContext

# Fixed M0 system prompt; part of the frozen sample definition and of the
# prompt version binding, so any change requires re-freezing the samples.
M0_SYSTEM_PROMPT = (
    "你是企业内部业务助手。只能通过提供的只读工具查询业务数据，不能执行任何写入或提交。"
    "参数不足、对象不唯一或范围不明确时，先向用户提问澄清，不要猜测参数。"
    "用户要求的能力不在工具中时，直接说明当前不支持。不要编造工具结果。"
)

ExpectedCall = tuple[str, dict[str, Any]]
M0TurnRunner = Callable[
    [AuthenticatedSessionContext, str, str, Sequence[ExpectedCall]],
    Awaitable[dict[str, Any]],
]
_TOOL_NAMES = {
    capability_id.replace(".", "__"): capability_id for capability_id in M0_READ_CAPABILITY_IDS
}


def build_m0_turn_runner(
    deps: AgentScopeReadDependencies,
    *,
    base_url: str,
    model: str,
) -> M0TurnRunner:
    async def run(
        session: AuthenticatedSessionContext,
        session_id: str,
        message: str,
        expected: Sequence[ExpectedCall],
    ) -> dict[str, Any]:
        started = monotonic()
        scope = ReadToolScope.from_session(session, bound_session_id=session_id)
        kit = await build_read_toolkit(
            scope, deps, message, model=model, system_prompt=M0_SYSTEM_PROMPT
        )
        agent = create_bounded_agent(
            name="eternalai_m0",
            system_prompt=M0_SYSTEM_PROMPT,
            model=build_chat_model(base_url=base_url, model=model),
            toolkit=kit.toolkit,
        )
        outcome = "completed"
        final_text = ""
        try:
            final = await run_bounded_turn(agent, UserMsg(name="user", content=message))
            final_text = final.get_text_content() or ""
        except AgentScopeModelError as exc:
            outcome = exc.code
        return {
            "outcome": outcome,
            "elapsed_ms": round((monotonic() - started) * 1000),
            "model_calls": model_call_count(agent.state),
            "candidate_outcome": kit.selection.outcome,
            "registered_tools": len(kit.exposed_schema_digests),
            "tool_calls": _tool_calls(agent.state.context, expected),
            "tool_executions": [
                {"capability": item.capability_id, "status": item.status,
                 "error_code": item.error_code}
                for item in kit.executions
            ],
            "halt_reason": tool_halt_reason(agent.state),
            "final_chars": len(final_text),
            "final_has_question": "?" in final_text or "？" in final_text,
        }

    return run


def _tool_calls(context: Sequence[Any], expected: Sequence[ExpectedCall]) -> list[dict[str, Any]]:
    results: dict[str, ToolResultBlock] = {}
    calls: list[ToolCallBlock] = []
    for message in context:
        for block in message.get_content_blocks():
            if isinstance(block, ToolCallBlock):
                calls.append(block)
            elif isinstance(block, ToolResultBlock):
                results[block.id] = block
    summary: list[dict[str, Any]] = []
    for index, call in enumerate(calls):
        try:
            arguments = json.loads(call.input or "{}")
        except ValueError:
            arguments = None
        result = results.get(call.id)
        matches: bool | None = None
        if index < len(expected):
            expected_name, expected_arguments = expected[index]
            matches = call.name == expected_name and arguments == expected_arguments
        summary.append(
            {
                "tool": _TOOL_NAMES.get(call.name, "unregistered"),
                "parsed": isinstance(arguments, dict),
                "argument_keys": _safe_keys(arguments),
                "matches_expected": matches,
                "result_state": str(result.state) if result is not None else "none",
                "result_error_code": _safe_code(result.metadata.get("error_code"))
                if result is not None else None,
            }
        )
    return summary


def _safe_keys(arguments: object) -> list[str]:
    # Schema field names only; anything else the model invented is masked.
    if not isinstance(arguments, dict):
        return []
    return sorted(
        key if key.isascii() and key.isidentifier() and len(key) <= 64 else "other"
        for key in arguments
    )


def _safe_code(value: object) -> str | None:
    return value if isinstance(value, str) and value.isascii() and len(value) <= 64 else None


__all__ = ("M0_SYSTEM_PROMPT", "M0TurnRunner", "build_m0_turn_runner")
