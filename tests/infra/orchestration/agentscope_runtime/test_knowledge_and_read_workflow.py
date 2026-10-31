"""M2 S5: bounded knowledge in the system prompt; the overview workflow is a read."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import uuid4

from agentscope.message import Msg, TextBlock
from agentscope.model import ChatResponse

from app.composition import build_agentscope_runtime_stack
from app.infra.human_gate.in_memory import InMemoryHumanGate
from app.infra.llm.mock_llm.mock_llm_provider import MockLLMProvider
from app.infra.llm.mock_structured_output.mock_structured_output_provider import (
    MockStructuredOutputProvider,
)
from app.infra.orchestration.agentscope_runtime.catalog import (
    READ_WORKFLOW_IDS,
    build_tool_catalog,
)
from app.infra.workflow.catalog import OVERVIEW_ID
from app.knowledge import BasicKnowledge
from app.knowledge.bounded import KNOWLEDGE_SYSTEM_PROMPT
from app.ports.auth import authenticated_session
from app.ports.runtime import chat_client_request_id
from tests.infra.orchestration.agentscope_runtime.test_confirmation_regression import (
    LOGIN,
    Gateway,
    Model,
    _call,
    _settle,
    _spec,
)
from tests.runtime.test_runtime_user_action import RecordingTrace
from tests.runtime.test_runtime_workflow import Registry, SessionStore, TaskStore

PENDING = "oa.list_pending_workflows"
OTHER_WORKFLOW = "oa.workflow.leave_request"


def test_overview_workflow_is_a_read_tool_and_other_workflows_stay_writes() -> None:
    assert READ_WORKFLOW_IDS == {OVERVIEW_ID}
    catalog = build_tool_catalog(
        [_spec(OVERVIEW_ID, "workflow", "概览"), _spec(OTHER_WORKFLOW, "workflow", "请假")]
    )
    tools = {tool.capability_id: tool for tool in catalog.tools}
    assert (tools[OVERVIEW_ID].kind, tools[OVERVIEW_ID].group) == ("read", "oa_query")
    assert tools[OVERVIEW_ID].binding.capability_type == "workflow"
    assert (tools[OTHER_WORKFLOW].kind, tools[OTHER_WORKFLOW].group) == ("write", "oa_action")


class _CapturingModel(Model):
    """Opens the read group on the first call and answers text on the next."""

    def __init__(self, group: str, prompts: list[str]) -> None:
        super().__init__(group, [])
        self.prompts = prompts

    async def _call_api(
        self,
        model_name: str,
        messages: list[Msg],
        tools: Any = None,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> ChatResponse:
        self.prompts.append(messages[0].get_text_content() or "")
        if len(self.prompts) == 1:
            return _call("reset_tools", {self._group: True})
        return ChatResponse(content=[TextBlock(text="好的。")], is_last=True)


def test_knowledge_enters_the_system_prompt_once_and_stays_equal_within_a_turn() -> None:
    prompts: list[str] = []
    registry = Registry(_spec(PENDING, "query", "待办"))
    group = build_tool_catalog(list(registry.items.values())).group_of(PENDING)
    assert group is not None
    runtime = build_agentscope_runtime_stack(
        task_store=TaskStore(),
        session_store=SessionStore(),
        capability_registry=registry,
        gateway=Gateway(),
        trace_port=RecordingTrace(),
        llm_provider=MockLLMProvider(),
        structured_output=MockStructuredOutputProvider(),
        intent_model="c-suite",
        model_factory=lambda: _CapturingModel(group, prompts),
        semantic_knowledge=BasicKnowledge(),
        human_gate_port=InMemoryHumanGate(),
    )

    async def run(message: str) -> None:
        login_token = authenticated_session.set(LOGIN)
        request_token = chat_client_request_id.set(str(uuid4()))
        try:
            await runtime.handle_user_message("web", LOGIN.principal, "session-k", message, {})
        finally:
            chat_client_request_id.reset(request_token)
            authenticated_session.reset(login_token)
        await _settle()

    asyncio.run(run("帮我看看待办"))
    assert len(prompts) == 2
    sections = [
        prompt.partition("<knowledge>")[2].partition("</knowledge>")[0] for prompt in prompts
    ]
    assert sections[0] == sections[1] != ""
    header, _, payload = sections[0].strip().partition("\n")
    assert header == KNOWLEDGE_SYSTEM_PROMPT
    items = json.loads(payload)["semantic_system_knowledge"]
    assert any("待办是等待当前用户处理" in item for item in items)
    assert all(prompt.rstrip().endswith("</knowledge>") for prompt in prompts)

    prompts.clear()
    asyncio.run(run("你好"))
    assert prompts and all("<knowledge>" not in prompt for prompt in prompts)
