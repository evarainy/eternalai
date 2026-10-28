"""C1-C6 confirmation regressions on the AgentScope runtime (M1B design §5).

Under test: the runtime built by the production ``build_agentscope_runtime_stack``,
its real ``RuntimeImpl`` coordinator, a real ``WorkflowEngine`` and the in-memory
HumanGate. The model is deterministic (it proposes on one message and answers
text otherwise); business systems are fakes and nothing is saved or submitted.
Governed MCP cards reuse the in-memory operation store of tests/mcp.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from time import monotonic
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from agentscope.agent import Agent
from agentscope.credential import OpenAICredential
from agentscope.event import UserInterruptEvent
from agentscope.message import Msg, TextBlock, ToolCallBlock
from agentscope.model import ChatResponse, OpenAIChatModel
from pydantic import SecretStr

import app.infra.llm.agentscope_chat as agentscope_chat
from app.api.v1.mcp import McpApiService, ResumeRequest
from app.composition import build_agentscope_runtime_stack
from app.contracts.sdui.models import ConfirmUserAction
from app.infra.human_gate.in_memory import InMemoryHumanGate
from app.infra.llm.mock_llm.mock_llm_provider import MockLLMProvider
from app.infra.llm.mock_structured_output.mock_structured_output_provider import (
    MockStructuredOutputProvider,
)
from app.infra.orchestration.agentscope_runtime.catalog import build_tool_catalog
from app.infra.orchestration.agentscope_runtime.runtime import AGENT_NAME
from app.mcp.connections import McpExecutionContextFactory
from app.mcp.models import McpFailure, digest
from app.mcp.operations import GovernedOperations
from app.ports.auth import (
    AuthenticatedSessionContext,
    Principal,
    PrincipalOrgContext,
    authenticated_session,
)
from app.ports.capability_gateway import ExecutionResult
from app.ports.capability_registry import CapabilitySpec
from app.ports.mcp import McpValidatedOutcome
from app.ports.mcp_store import Connection
from app.ports.response_envelope import ResponseEnvelope
from app.ports.runtime import RuntimeSessionBusyError, chat_client_request_id
from app.ports.workflow_store import WorkflowOperation
from app.workflow.engine import WorkflowEngine
from app.workflow.models import GovernedWorkflowPolicy, WorkflowDefinition, WorkflowStep
from tests.infra.mcp.test_transport import authorization
from tests.mcp.test_operations import RebindMcpStore, Store
from tests.runtime.registry_fakes import runtime_output_schema, schema_digest
from tests.runtime.test_runtime_user_action import RecordingTrace
from tests.runtime.test_runtime_workflow import Registry, SessionStore, TaskStore

SESSION = "session-c"
WORKFLOW = "oa.workflow.leave_request"
FIRST = ("oa.leave_request.preview", "oa.leave_request.execute")
SECOND = ("oa.leave_request.review", "oa.leave_request.review_execute")
START = "帮我发起两天请假"
QUESTION = "今天有什么安排"
_OBJECT = {"type": "object", "properties": {}, "additionalProperties": False}
_WRITE_INPUT = {
    "type": "object",
    "properties": {"days": {"type": "integer", "description": "请假天数"}},
    "required": ["days"],
    "additionalProperties": False,
}
_SUBJECT = "synthetic-subject"


def _spec(capability_id: str, kind: str, name: str) -> CapabilitySpec:
    output_schema = runtime_output_schema("registry_fakes.default")
    return CapabilitySpec(
        capability_id=capability_id,
        name=name,
        type=kind,
        input_schema=_WRITE_INPUT if kind == "workflow" else _OBJECT,
        input_schema_digest=f"input-{capability_id}",
        output_schema=output_schema,
        output_schema_digest=schema_digest(output_schema),
        # Workflow steps must be low-risk registered capabilities.
        risk_level="medium" if kind == "workflow" else "low",
        owner="test-confirmation-regression",
        version="1.0.0",
        status="active",
        short_description=f"{name}。",
        target_system="oa",
        execution_identity="user_delegated",
        binding_required=False,
    )


def _definition(two_steps: bool) -> WorkflowDefinition:
    steps = [WorkflowStep(step_id="first", capability_id=FIRST[0],
                          confirmed_capability_id=FIRST[1])]
    if two_steps:
        steps.append(WorkflowStep(step_id="second", capability_id=SECOND[0],
                                  confirmed_capability_id=SECOND[1]))
    return WorkflowDefinition(workflow_id=WORKFLOW, version="1.0.0", steps=tuple(steps))


def _specs(two_steps: bool) -> list[CapabilitySpec]:
    specs = [
        _spec(WORKFLOW, "workflow", "发起请假"),
        _spec(FIRST[0], "query", "请假预览"),
        _spec(FIRST[1], "action", "提交请假"),
    ]
    if two_steps:
        specs += [_spec(SECOND[0], "query", "请假复核"), _spec(SECOND[1], "action", "提交复核")]
    return specs


class Model(OpenAIChatModel):
    """Proposes the leave workflow on START; any other message gets plain text."""

    def __init__(self, group: str, log: list[str]) -> None:
        super().__init__(
            credential=OpenAICredential(api_key=SecretStr("EMPTY"),
                                        base_url="http://model.invalid/v1"),
            model="c-suite", stream=False, max_retries=0,
        )
        self._group, self._log = group, log

    async def _call_api(self, model_name: str, messages: list[Msg], tools: Any = None,
                        tool_choice: Any = None, **kwargs: Any) -> ChatResponse:
        start = max(i for i, message in enumerate(messages) if message.role == "user")
        text = "".join(b.text for b in messages[start].content if isinstance(b, TextBlock))
        calls = [b.name for m in messages[start + 1:] for b in m.content
                 if isinstance(b, ToolCallBlock)]
        self._log.append(text)
        if text == START and not calls:
            return _call("reset_tools", {self._group: True})
        if text == START and calls[-1] == "reset_tools":
            return _call(WORKFLOW.replace(".", "__"), {"days": 2})
        return ChatResponse(content=[TextBlock(text="好的。")], is_last=True)


def _call(name: str, arguments: dict[str, Any]) -> ChatResponse:
    block = ToolCallBlock(id=f"call-{uuid4().hex}", name=name, input=json.dumps(arguments))
    return ChatResponse(content=[block], is_last=True)


class Gateway:
    """Business systems: previews wait for confirmation, confirmed steps complete."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute_capability(self, task_id: str, session_id: str, ai_user_id: str,
                                 capability_id: str, arguments: dict[str, Any],
                                 request_context: Any, **kwargs: Any) -> ExecutionResult:
        self.calls.append(capability_id)
        if capability_id in (FIRST[0], SECOND[0]):
            return ExecutionResult(status="waiting_user", error_code="confirm_required",
                                   trace_id=request_context.request_id)
        return ExecutionResult(status="completed", data={"accepted": True},
                               trace_id=request_context.request_id)


class Engine(WorkflowEngine):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.resume_calls = 0
        self.entered = asyncio.Event()
        self.release: asyncio.Event | None = None

    async def resume(self, **kwargs: Any) -> Any:
        self.resume_calls += 1
        self.entered.set()
        if self.release is not None:
            await self.release.wait()
        return await super().resume(**kwargs)


class Gate(InMemoryHumanGate):
    def __init__(self) -> None:
        super().__init__()
        self.asserted: list[tuple[str, tuple[Any, ...]]] = []

    async def assert_task_bindings(self, task_id: str, bindings: Any, **kwargs: Any) -> None:
        if kwargs.get("exact"):  # The coordinator's check before recording a decision.
            self.asserted.append((task_id, tuple(bindings)))
        await super().assert_task_bindings(task_id, bindings, **kwargs)


@dataclass
class Stack:
    runtime: Any
    engine: Engine
    gate: Gate
    gateway: Gateway
    tasks: TaskStore
    trace: RecordingTrace
    registry: Registry
    model_log: list[str] = field(default_factory=list)

    @property
    def coordinator(self) -> Any:
        return self.runtime._deps.confirmations


def _stack(*, two_steps: bool = False, shared: Stack | None = None) -> Stack:
    """A runtime stack; ``shared`` simulates a restart of ``shared``'s process.

    Durable state survives (HumanGate, stores, the governed operation store);
    process memory (coordinator, engine checkpoints, AgentState) does not.
    """
    if shared is None:
        registry = Registry(*_specs(two_steps))
        tasks, trace, gateway, gate = TaskStore(), RecordingTrace(), Gateway(), Gate()
        model_log: list[str] = []
    else:
        registry, tasks, trace = shared.registry, shared.tasks, shared.trace
        gateway, gate, model_log = shared.gateway, shared.gate, shared.model_log
    engine = Engine(definitions={WORKFLOW: _definition(two_steps)},
                    capability_registry=registry, gateway=gateway,
                    task_store=tasks, trace_port=trace)
    if shared is not None:
        engine._governed = shared.engine._governed
    stack = Stack(None, engine, gate, gateway, tasks, trace, registry, model_log)
    visible = [spec for spec in stack.registry.items.values() if spec.type != "action"]
    group = build_tool_catalog(visible).group_of(WORKFLOW)
    assert group is not None
    stack.runtime = build_agentscope_runtime_stack(
        task_store=stack.tasks, session_store=SessionStore(),
        capability_registry=stack.registry, gateway=stack.gateway,
        trace_port=stack.trace, llm_provider=MockLLMProvider(),
        structured_output=MockStructuredOutputProvider(), intent_model="c-suite",
        model_factory=lambda: Model(group, stack.model_log),
        workflow_engine=stack.engine, human_gate_port=stack.gate,
    )
    return stack


def _principal(user_id: str = "synthetic-user") -> Principal:
    return Principal(ai_user_id=user_id, display_name="C suite", roles=("user",),
                     org_ctx=PrincipalOrgContext(tenant_id="default"))


def _login(fingerprint: bytes = b"login-a", user_id: str = "synthetic-user",
           hours: int = 1) -> AuthenticatedSessionContext:
    return AuthenticatedSessionContext(
        principal=_principal(user_id), fingerprint=fingerprint,
        expires_at=datetime.now(UTC) + timedelta(hours=hours),
    )


LOGIN = _login()


async def _say(stack: Stack, text: str, *, login: AuthenticatedSessionContext = LOGIN,
               request_id: str | None = None, session: str = SESSION) -> ResponseEnvelope:
    login_token = authenticated_session.set(login)
    request_token = chat_client_request_id.set(request_id or str(uuid4()))
    try:
        return await stack.runtime.handle_user_message(
            "web", login.principal, session, text, {}
        )
    finally:
        chat_client_request_id.reset(request_token)
        authenticated_session.reset(login_token)


async def _click(stack: Stack, response_id: str, *,
                 login: AuthenticatedSessionContext = LOGIN) -> ResponseEnvelope:
    token = authenticated_session.set(login)
    try:
        return await stack.runtime.handle_user_action(
            "web", login.principal, SESSION,
            ConfirmUserAction(action_type="confirm", response_id=response_id, confirmed=True),
        )
    finally:
        authenticated_session.reset(token)


def _outcome(envelope: ResponseEnvelope) -> str:
    assert envelope.data is not None
    return str(envelope.data["action_outcome"])


async def _settle() -> None:
    """Let every interrupt the turns started end, so sessions are released."""
    while agentscope_chat._PENDING_INTERRUPTS:
        await asyncio.gather(*list(agentscope_chat._PENDING_INTERRUPTS))
    await asyncio.sleep(0)


async def _propose(stack: Stack, login: AuthenticatedSessionContext = LOGIN) -> ResponseEnvelope:
    envelope = await _say(stack, START, login=login)
    assert envelope.status == "waiting_user"
    assert envelope.ui.component_type == "confirm_card"
    await _settle()
    return envelope


def _parked(stack: Stack) -> bool:
    return any(entry.state.has_awaiting_tool_calls(AGENT_NAME)
               for entry in stack.runtime._sessions._entries.values())


def _pending_tasks(stack: Stack) -> set[str]:
    return {pending.task_id for pending in stack.coordinator._pending_workflows.values()}


def _events(stack: Stack, task_id: str) -> list[str]:
    return [step["event_type"] for step in stack.trace.steps if step["task_id"] == task_id]


# --- C1 / C2: one execution per card ------------------------------------------


def test_c1_concurrent_card_clicks_resume_and_send_once() -> None:
    async def run() -> None:
        stack = _stack()
        card = await _propose(stack)
        stack.engine.release = asyncio.Event()
        winner = asyncio.create_task(_click(stack, card.response_id))
        await stack.engine.entered.wait()
        loser = await _click(stack, card.response_id)
        stack.engine.release.set()
        assert _outcome(await winner) == "accepted"
        assert _outcome(loser) == "action_already_claimed"
        assert stack.engine.resume_calls == 1
        assert stack.gateway.calls.count(FIRST[1]) == 1
        assert stack.tasks.records[card.task_id].status == "completed"

    asyncio.run(run())


@pytest.mark.parametrize("first", ["text", "card"])
def test_c2_text_and_card_confirmation_share_one_claim(first: str) -> None:
    async def run() -> None:
        stack = _stack()
        card = await _propose(stack)
        model_calls = len(stack.model_log)
        stack.engine.release = asyncio.Event()
        text_first = first == "text"
        winner = asyncio.create_task(
            _say(stack, "确认") if text_first else _click(stack, card.response_id)
        )
        await stack.engine.entered.wait()
        loser = await (_click(stack, card.response_id) if text_first else _say(stack, "确认"))
        stack.engine.release.set()
        won = await winner
        if text_first:
            assert won.status == "completed"
            assert _outcome(loser) == "action_already_claimed"
        else:
            assert _outcome(won) == "accepted"
            assert loser.status == "waiting_user" and "本次未执行" in loser.message
        assert stack.engine.resume_calls == 1
        assert stack.gateway.calls.count(FIRST[1]) == 1
        assert len(stack.model_log) == model_calls

    asyncio.run(run())


# --- C3: replay after completion and across gates ------------------------------


def test_c3_replay_after_completion_never_resumes_again() -> None:
    async def run() -> None:
        stack = _stack()
        card = await _propose(stack)
        assert _outcome(await _click(stack, card.response_id)) == "accepted"
        assert _outcome(await _click(stack, card.response_id)) == "action_already_claimed"
        model_calls = len(stack.model_log)
        replay = await _say(stack, f"确认 {card.response_id}")
        await _settle()
        # The completed card's reference falls through to an ordinary turn.
        assert len(stack.model_log) == model_calls + 1
        assert replay.task_id != card.task_id
        assert stack.engine.resume_calls == 1
        assert stack.gateway.calls.count(FIRST[1]) == 1

    asyncio.run(run())


def test_c3_receipt_pins_the_first_gate_and_second_card_keeps_proposal_bindings() -> None:
    async def run() -> None:
        stack = _stack(two_steps=True)
        card = await _propose(stack)
        manifest = await stack.gate.get_task_binding(card.task_id)
        assert manifest is not None
        request_id = str(uuid4())
        first = await _say(stack, "确认", request_id=request_id)
        assert first.status == "waiting_user" and first.ui.component_type == "confirm_card"
        assert first.response_id != card.response_id
        replay = await _say(stack, "确认", request_id=request_id)
        assert replay == first
        assert stack.engine.resume_calls == 1
        assert stack.gateway.calls.count(SECOND[1]) == 0
        assert _pending_tasks(stack) == {card.task_id}

        done = await _say(stack, "确认", request_id=str(uuid4()))
        assert done.status == "completed"
        assert stack.engine.resume_calls == 2
        assert stack.gateway.calls.count(SECOND[1]) == 1
        # Both confirmations assert the proposal-time M1 bindings, not the legacy intent.
        task_asserts = [bindings for task, bindings in stack.gate.asserted
                        if task == card.task_id]
        assert len(task_asserts) == 2
        legacy_intent = stack.coordinator._intent_version_binding
        for bindings in task_asserts:
            assert set(bindings) == set(manifest.bindings)
            assert legacy_intent not in bindings
        owner_key = ("default", SESSION, "synthetic-user")
        claimed = {key[3]: key[:3] for key in stack.coordinator._claimed_pending_confirmations}
        assert claimed[first.response_id] == owner_key

    asyncio.run(run())


# --- C4: expiry ---------------------------------------------------------------


def test_c4_expired_card_is_invalidated_and_cannot_approve_a_new_proposal() -> None:
    async def run() -> None:
        stack = _stack()
        card = await _propose(stack)
        stack.coordinator._monotonic_clock = lambda: monotonic() + 601
        expired = await _click(stack, card.response_id)
        assert _outcome(expired) == "confirmation_invalidated"
        assert "task_confirmation_invalidated" in _events(stack, card.task_id)
        assert stack.tasks.records[card.task_id].status == "confirmation_invalidated"
        assert card.task_id not in stack.engine._checkpoints
        assert not _parked(stack)

        fresh = await _propose(stack)
        again = await _click(stack, card.response_id)
        assert _outcome(again) == "confirmation_invalidated"
        assert stack.engine.resume_calls == 0 and FIRST[1] not in stack.gateway.calls
        assert _pending_tasks(stack) == {fresh.task_id}
        assert _outcome(await _click(stack, fresh.response_id)) == "accepted"

    asyncio.run(run())


# --- C5: a new question while a card waits -------------------------------------


def _interrupt_patch(monkeypatch: pytest.MonkeyPatch, behaviour: Callable[[], Any]) -> None:
    original = Agent.reply
    state = {"first": True}

    async def reply(self: Agent, inputs: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(inputs, UserInterruptEvent) and state["first"]:
            state["first"] = False
            await behaviour()
        return await original(self, inputs, *args, **kwargs)

    monkeypatch.setattr(Agent, "reply", reply)


async def _assert_card_untouched(stack: Stack, card: ResponseEnvelope) -> None:
    assert _pending_tasks(stack) == {card.task_id}
    assert stack.tasks.records[card.task_id].status == "waiting_user"
    assert stack.engine.resume_calls == 0 and FIRST[1] not in stack.gateway.calls


@pytest.mark.parametrize("interrupt", ["normal", "first_raises"])
def test_c5_new_question_completes_and_keeps_the_card(
    interrupt: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail() -> None:
        raise RuntimeError("synthetic interrupt failure")

    if interrupt == "first_raises":
        _interrupt_patch(monkeypatch, fail)

    async def run() -> None:
        stack = _stack()
        card = await _propose(stack)
        assert _parked(stack) is (interrupt == "first_raises")
        answer = await _say(stack, QUESTION)
        await _settle()
        assert answer.task_id != card.task_id and answer.status != "failed"
        assert stack.model_log[-1] == QUESTION
        assert not _parked(stack)
        # The parked reply is interrupted, not dropped: the earlier user turn stays.
        (entry,) = stack.runtime._sessions._entries.values()
        assert [
            "".join(b.text for b in m.content if isinstance(b, TextBlock))
            for m in entry.state.context if m.role == "user"
        ] == [START, QUESTION]
        await _assert_card_untouched(stack, card)

    asyncio.run(run())


def test_c5_blocked_interrupt_holds_the_session_then_releases_within_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agentscope_chat, "_INTERRUPT_TIMEOUT_SECONDS", 0.5)
    entered = asyncio.Event()

    async def block() -> None:
        entered.set()
        await asyncio.Event().wait()

    _interrupt_patch(monkeypatch, block)

    async def run() -> None:
        stack = _stack()
        proposal = asyncio.create_task(_say(stack, START))
        await entered.wait()
        started = monotonic()
        proposal.cancel()
        with pytest.raises(RuntimeSessionBusyError):
            await _say(stack, QUESTION)
        await _settle()
        assert monotonic() - started < 5
        answer = await _say(stack, QUESTION)
        await _settle()
        assert answer.status != "failed" and stack.model_log[-1] == QUESTION
        assert len(_pending_tasks(stack)) == 1
        assert stack.engine.resume_calls == 0 and FIRST[1] not in stack.gateway.calls

    asyncio.run(run())


# --- C6: governed MCP cards -----------------------------------------------------


@dataclass
class Governed:
    store: Store
    operations: Any
    mcp: RebindMcpStore
    sends: int = 0
    verified: int = 0


async def _govern(stack: Stack, card: ResponseEnvelope,
                  login: AuthenticatedSessionContext = LOGIN) -> Governed:
    """Attach a governed MCP operation to the chat card (as the lifecycle tests do)."""
    pending = next(iter(stack.coordinator._pending_workflows.values()))
    gate = await stack.gate.get_request(card.response_id)
    assert gate is not None and pending.gate_request_id == gate.request_id
    context = authorization().model_copy(update={
        "task_id": pending.task_id, "user_id": login.principal.ai_user_id,
        "tenant_id": "default", "chat_session_id": SESSION,
        "login_session_fingerprint": login.fingerprint.hex(),
    })
    op = WorkflowOperation(
        operation_id=uuid4().hex, context=context, outer_capability_id=WORKFLOW,
        outer_version="1.0.0", leaf_capability_id=context.capability_id, leaf_version="1",
        remote_tool="clothing_plan_preview", arguments={}, canonical_args_digest=digest({}),
        action_digest=pending.action_digest, gate_request_id=gate.request_id,
        expires_at=gate.expires_at, state="WAITING_LOCAL_CONFIRM",
        identity_evidence_digest=digest(_SUBJECT),
    )
    store = Store(op)
    store.gates = stack.gate
    mcp = RebindMcpStore(_connection(context, login.fingerprint, epoch=1))
    governed = Governed(store, None, mcp)

    async def send(task_id: str, session_id: str, user_id: str, capability_id: str,
                   arguments: dict[str, Any], request_context: Any, *,
                   workflow_authorization: Any) -> ExecutionResult:
        # The production Gateway verifies the write right before the remote send.
        await governed.operations.contexts.verify_write(
            store.op.context, workflow_authorization, arguments
        )
        governed.verified += 1
        await store.transition(store.op, state="SENDING")
        governed.sends += 1
        return ExecutionResult(
            status="completed", trace_id=request_context.request_id,
            mcp_outcome=McpValidatedOutcome(state="VERIFIED_SUCCESS", persistence={}),
        )

    governed.operations = GovernedOperations(
        store, McpExecutionContextFactory(mcp, store, stack.gate), stack.gate,
        SimpleNamespace(execute_capability=send), None,
        {WORKFLOW: GovernedWorkflowPolicy(
            service_config_id=context.service_config_id, outer_capability_id=WORKFLOW,
            leaf_capability_id=op.leaf_capability_id, remote_tool=op.remote_tool,
            version="1.0.0",
        )},
    )
    stack.engine._governed = governed.operations
    return governed


def _connection(context: Any, fingerprint: bytes, *, epoch: int) -> Connection:
    return Connection(
        tenant_id=context.tenant_id, user_id=context.user_id,
        service_config_id=context.service_config_id, connection_id=context.connection_id,
        registration_id=context.registration_id,
        service_config_version=context.service_config_version,
        binding_epoch=epoch, grant_epoch=epoch,
        login_session_fingerprint=fingerprint.hex(), state="ACTIVE",
        identity_evidence=_SUBJECT,
    )


async def _apps_confirm(stack: Stack, governed: Governed) -> None:
    """The MCP independent entry (Apps confirm endpoint) under the card's login."""
    op = governed.store.op
    profile = SimpleNamespace(enabled=True, tenant_id="default", display_name="Synthetic",
                              service_config_version=op.context.service_config_version)
    service = McpApiService(SimpleNamespace(configs={op.context.service_config_id: profile}),
                            governed.operations, stack.engine)
    token = authenticated_session.set(LOGIN)
    try:
        view = await service.resume(op, ResumeRequest(
            action="confirm", expected_revision=op.revision,
            preview_digest=service.view(op).preview_digest,
        ))
    finally:
        authenticated_session.reset(token)
    assert view.state == "VERIFIED_SUCCESS"


async def _completed_through_apps() -> tuple[Stack, ResponseEnvelope, Governed]:
    stack = _stack()
    card = await _propose(stack)
    governed = await _govern(stack, card)
    await _apps_confirm(stack, governed)
    # The Apps entry itself resumes the workflow once; the chat card never does.
    assert governed.sends == governed.verified == stack.engine.resume_calls == 1
    assert stack.tasks.records[card.task_id].status == "completed"
    return stack, card, governed


def test_c6_apps_completion_then_agent_state_loss_keeps_the_card_claimed() -> None:
    async def run() -> None:
        stack, card, governed = await _completed_through_apps()
        # LRU of one session: another conversation evicts this AgentState.
        stack.runtime._sessions._max = 1
        await _say(stack, QUESTION, session="session-other")
        await _settle()
        assert all(key[3] != SESSION for key in stack.runtime._sessions._entries)
        assert _outcome(await _click(stack, card.response_id)) == "action_already_claimed"
        assert governed.sends == 1 and stack.engine.resume_calls == 1

        restarted = _stack(shared=stack)
        assert _outcome(await _click(restarted, card.response_id)) == "confirmation_invalidated"
        assert governed.sends == 1 and restarted.engine.resume_calls == 0

    asyncio.run(run())


def test_c6_other_account_neither_sees_nor_rebinds_the_card() -> None:
    other = _login(b"login-b", user_id="other-user")

    async def run() -> None:
        stack, card, governed = await _completed_through_apps()
        # Before the owner repairs it, a foreign click must not repair the card.
        pending = dict(stack.coordinator._pending_workflows)
        assert pending
        assert _outcome(await _click(stack, card.response_id, login=other)) == (
            "confirmation_invalidated"
        )
        assert stack.coordinator._pending_workflows == pending
        # The owner's click repairs the card into a claim record.
        assert _outcome(await _click(stack, card.response_id)) == "action_already_claimed"
        assert not stack.coordinator._pending_workflows
        assert _outcome(await _click(stack, card.response_id, login=other)) == (
            "confirmation_invalidated"
        )
        model_calls = len(stack.model_log)
        await _say(stack, f"确认 {card.response_id}", login=other)
        await _settle()
        assert len(stack.model_log) == model_calls + 1
        other_key = SimpleNamespace(tenant_id="default", session_id=SESSION,
                                    ai_user_id="other-user")
        assert stack.coordinator._lookup_confirmation_outcome(other_key, card.response_id) is None
        assert governed.sends == 1 and stack.engine.resume_calls == 1

        fresh_stack = _stack()
        fresh_card = await _propose(fresh_stack)
        fresh = await _govern(fresh_stack, fresh_card)
        before = fresh.store.op
        token = authenticated_session.set(other)
        try:
            async with fresh.store.execution_guard(before):
                with pytest.raises(McpFailure, match="^mcp_authorization_invalid$"):
                    await fresh.operations.rebind_current_session(
                        before, expected_action_digest=before.action_digest,
                        expected_gate_request_id=fresh_card.response_id,
                    )
        finally:
            authenticated_session.reset(token)
        assert fresh.store.op == before and fresh.store.transitions == []
        assert await fresh_stack.gate.get_decision(fresh_card.response_id) is None
        assert fresh.sends == 0

    asyncio.run(run())


def test_c6_same_account_new_login_after_completion_matches_the_main_case() -> None:
    renewed = _login(b"login-renewed", hours=2)

    async def run() -> None:
        stack, card, governed = await _completed_through_apps()
        assert _outcome(await _click(stack, card.response_id, login=renewed)) == (
            "action_already_claimed"
        )
        owner_key = SimpleNamespace(tenant_id="default", session_id=SESSION,
                                    ai_user_id="synthetic-user")
        assert stack.coordinator._lookup_confirmation_outcome(owner_key, card.response_id) == (
            "action_already_claimed"
        )
        await _say(stack, f"确认 {card.response_id}", login=renewed)
        await _settle()
        assert governed.sends == 1 and stack.engine.resume_calls == 1

    asyncio.run(run())


@pytest.mark.parametrize("reauthorized", [False, True])
def test_c6_same_account_new_login_confirms_an_unclaimed_governed_card(
    reauthorized: bool,
) -> None:
    renewed = _login(b"login-renewed", hours=2)

    async def run() -> None:
        stack = _stack()
        card = await _propose(stack)
        governed = await _govern(stack, card)
        before = governed.store.op
        if reauthorized:
            # MCP re-authorization under the new login: same connection, new epochs.
            governed.mcp.current = _connection(before.context, renewed.fingerprint, epoch=2)
        response = await _click(stack, card.response_id, login=renewed)
        after = governed.store.op
        if not reauthorized:
            assert _outcome(response) == "action_gate_unavailable"
            # mcp_authorization_invalid, mapped as the Gateway maps governed failures.
            assert response.data == {
                "action_outcome": "action_gate_unavailable",
                "result": {"error_code": "policy_denied"},
            }
            assert after == before and governed.store.transitions == []
            assert await stack.gate.get_decision(card.response_id) is None
            assert governed.sends == 0 and stack.engine.resume_calls == 0
            assert _pending_tasks(stack) == {card.task_id}
            # The card stays claimable under its own login.
            assert _outcome(await _click(stack, card.response_id)) == "accepted"
            assert governed.sends == 1
            return
        assert _outcome(response) == "accepted"
        assert stack.engine.resume_calls == 1 and governed.sends == governed.verified == 1
        assert after.context.login_session_fingerprint == renewed.fingerprint.hex()
        assert after.context.connection_id == before.context.connection_id
        assert (after.context.binding_epoch, after.context.grant_epoch) == (2, 2)
        assert after.gate_request_id != card.response_id
        assert await stack.gate.get_decision(card.response_id) is None
        decision = await stack.gate.get_decision(after.gate_request_id)
        assert decision is not None and decision.decision == "confirmed"
        assert stack.tasks.records[card.task_id].status == "completed"

    asyncio.run(run())
