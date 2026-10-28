"""Deterministic AgentScope chat model for Golden replay on the AgentScope path.

The model reads only the fixture's synthetic intent (derived from ``given`` and
``when``, never ``then_*``) and the tool results it actually receives:

1. ``match=none`` answers with one ``report_unsupported()`` call.
2. ``match=capability`` first activates the capability's group through
   ``reset_tools``, the group computed by the production ``ToolCatalog.group_of``;
   a capability outside the visible catalog has no group, so the model calls its
   tool name directly and production handles it as a missing tool.
3. The second response calls the tool with the intent's arguments unchanged; a
   write tool parks there. After a tool result the model answers with the
   ``message`` of the last tool result payload, or empty text.
4. A selector alias is called as-is; the model never re-selects a capability.
5. An invalid synthetic intent produces arguments that violate the schema.
6. The confirmation message of a second round belongs to the deterministic
   confirmation entry; the model raises if it is ever asked to answer it.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any
from uuid import uuid4

from agentscope.credential import OpenAICredential
from agentscope.message import Msg, TextBlock, ToolCallBlock, ToolResultBlock
from agentscope.model import ChatResponse, OpenAIChatModel
from pydantic import SecretStr

from app.infra.orchestration.agentscope_runtime.catalog import (
    SCOPE_EXCLUDED_PREFIX,
    VISIBLE_TYPES,
    build_tool_catalog,
)
from app.ports.capability_registry import CapabilitySpec

GOLDEN_MODEL_NAME = "golden-agentscope-model"
_RESET_TOOLS = "reset_tools"
_REPORT_UNSUPPORTED = "report_unsupported"


class GoldenModelContractError(AssertionError):
    """The replay asked the model for something the fixture contract forbids."""


class GoldenChatModel(OpenAIChatModel):
    """Answers each call from the synthetic intent and the tool results so far.

    The production model class keeps its formatter; only the network call is
    replaced, so the agent sees the same model surface as in production.
    """

    def __init__(
        self,
        *,
        intent: dict[str, Any] | None,
        group: str | None,
        confirmation_message: str | None,
    ) -> None:
        super().__init__(
            credential=OpenAICredential(
                api_key=SecretStr("EMPTY"), base_url="http://golden-model.invalid/v1"
            ),
            model=GOLDEN_MODEL_NAME,
            stream=False,
            max_retries=0,
        )
        self._intent = intent
        self._group = group
        self._confirmation_message = confirmation_message
        self.calls = 0

    async def _call_api(
        self,
        model_name: str,
        messages: list[Msg],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> ChatResponse:
        self.calls += 1
        user_text, calls, results = _current_turn(messages)
        if (
            self._confirmation_message is not None
            and user_text == self._confirmation_message
        ):
            raise GoldenModelContractError(
                "confirmation_message reached the model instead of the confirmation entry"
            )
        intent = self._intent
        if intent is None:
            # Rule 5: a schema-violating call; no current fixture reaches it.
            return _call(_RESET_TOOLS, "malformed synthetic intent")
        if intent.get("match") != "capability":
            if calls:
                return _text("")
            return _call(_REPORT_UNSUPPORTED, json.dumps({}))
        tool_name = _tool_name(str(intent.get("capability_id", "")))
        arguments = json.dumps(intent.get("arguments") or {}, ensure_ascii=False)
        if not calls:
            if self._group is None:
                return _call(tool_name, arguments)
            return _call(_RESET_TOOLS, json.dumps({self._group: True}))
        if calls[-1] == _RESET_TOOLS:
            return _call(tool_name, arguments)
        return _text(_result_message(results[-1]) if results else "")


def golden_model_factory(
    *,
    intent: dict[str, Any] | None,
    capability_specs: Sequence[CapabilitySpec],
    confirmation_message: str | None,
) -> GoldenChatModel:
    """One model per turn; the group comes from the production catalog."""

    group = None
    if intent is not None and intent.get("match") == "capability":
        group = _catalog_group(str(intent.get("capability_id", "")), capability_specs)
    return GoldenChatModel(
        intent=intent, group=group, confirmation_message=confirmation_message
    )


def _catalog_group(capability_id: str, specs: Sequence[CapabilitySpec]) -> str | None:
    # The pure part of visible_capabilities: active, typed, in scope. The
    # model cannot see the policy preview; Golden previews defer everything.
    visible = [
        spec
        for spec in specs
        if spec.status == "active"
        and spec.type in VISIBLE_TYPES
        and not spec.capability_id.startswith(SCOPE_EXCLUDED_PREFIX)
    ]
    return build_tool_catalog(visible).group_of(capability_id)


def _tool_name(capability_id: str) -> str:
    # The catalog's naming rule, applied verbatim to aliases as well.
    return capability_id.replace(".", "__")


def _current_turn(
    messages: Sequence[Msg],
) -> tuple[str | None, list[str], list[ToolResultBlock]]:
    start = max(
        (index for index, message in enumerate(messages) if message.role == "user"),
        default=-1,
    )
    user_text = None
    if start >= 0:
        user_text = "".join(
            block.text
            for block in messages[start].content
            if isinstance(block, TextBlock)
        )
    calls: list[str] = []
    results: list[ToolResultBlock] = []
    for message in messages[start + 1 :]:
        for block in message.content:
            if isinstance(block, ToolCallBlock):
                calls.append(block.name)
            elif isinstance(block, ToolResultBlock):
                results.append(block)
    return user_text, calls, results


def _result_message(result: ToolResultBlock) -> str:
    output = result.output
    text = (
        output
        if isinstance(output, str)
        else "".join(block.text for block in output if isinstance(block, TextBlock))
    )
    try:
        payload = json.loads(text)
    except ValueError:
        return ""
    message = payload.get("message") if isinstance(payload, dict) else None
    return message if isinstance(message, str) else ""


def _call(name: str, arguments: str) -> ChatResponse:
    return ChatResponse(
        content=[ToolCallBlock(id=f"golden-{uuid4().hex}", name=name, input=arguments)],
        is_last=True,
    )


def _text(text: str) -> ChatResponse:
    return ChatResponse(content=[TextBlock(text=text)], is_last=True)


__all__ = (
    "GOLDEN_MODEL_NAME",
    "GoldenChatModel",
    "GoldenModelContractError",
    "golden_model_factory",
)
