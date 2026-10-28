"""AgentScope runtime write proposals: publication order, handoff, deadline, release.

The model is scripted and the confirmation coordinator is a recording fake;
every other dependency is the production class or a minimal in-memory store.
The real coordinator and workflow engine are exercised by the C1-C6 suite.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from agentscope.credential import OpenAICredential
from agentscope.message import TextBlock, ToolCallBlock
from agentscope.model import ChatResponse, OpenAIChatModel
from pydantic import SecretStr

from app.contracts.sdui.models import ConfirmUserAction, UserAction
from app.evaluator import TerminalEvaluator
from app.evaluator.overview import OverviewPostconditionEvaluator
from app.infra.human_gate.in_memory import InMemoryHumanGate
from app.infra.llm.agentscope_chat import M1_TURN_LIMITS
from app.infra.orchestration.agent_adapter import AgentOrchestrationAdapter
from app.infra.orchestration.agentscope_read_tools import AgentScopeRuntimeDependencies
from app.infra.orchestration.agentscope_runtime.runtime import AGENT_NAME, AgentScopeRuntime
from app.infra.orchestration.agentscope_runtime.session import SessionCoordinator
from app.infra.sdui.response_envelope_builder import ResponseEnvelopeBuilder
from app.knowledge import BasicKnowledge
from app.ports.agent_orchestration import AgentTaskVersionBindings
from app.ports.auth import AuthenticatedSessionContext, authenticated_session
from app.ports.capability_gateway import ExecutionResult, RequestOrgContext
from app.ports.capability_registry import CapabilitySpec
from app.ports.human_gate import VersionBinding
from app.ports.response_envelope import ResponseEnvelope
from app.ports.runtime import (
    ConfirmationOwner,
    RuntimeSessionBusyError,
    StagedWaiting,
    WaitingProposal,
    chat_client_request_id,
)
from app.ports.task_store import SessionRecord, TaskRecord
from app.version_binding import merge_version_bindings
from tests.runtime.principal_fakes import runtime_principal
from tests.runtime.registry_fakes import runtime_output_schema, schema_digest

SESSION_ID = "session-proposal"
WRITE_ID = "oa.leave_apply"
WRITE_TOOL = "oa__leave_apply"
WRITE_GROUP = "oa_action"
_INPUT_SCHEMA = {
    "type": "object",
    "properties": {"days": {"type": "integer", "description": "请假天数"}},
    "required": ["days"],
    "additionalProperties": False,
}

ModelStep = ChatResponse | Callable[[], Awaitable[ChatResponse]]


def _workflow() -> CapabilitySpec:
    output_schema = runtime_output_schema("registry_fakes.default")
    return CapabilitySpec(
        capability_id=WRITE_ID,
        name="发起请假",
        type="workflow",
        input_schema=_INPUT_SCHEMA,
        output_schema=output_schema,
        input_schema_digest=f"input-{WRITE_ID}",
        output_schema_digest=schema_digest(output_schema),
        risk_level="medium",
        owner="test-proposal",
        version="1.0.0",
        status="active",
        short_description="发起一次请假流程。",
        target_system="oa",
        execution_identity="user_delegated",
        binding_required=False,
    )


def _calls(*calls: tuple[str, dict[str, Any]]) -> ChatResponse:
    return ChatResponse(
        content=[
            ToolCallBlock(id=f"call-{name}-{index}", name=name, input=json.dumps(arguments))
            for index, (name, arguments) in enumerate(calls)
        ],
        is_last=True,
    )


def _text(text: str) -> ChatResponse:
    return ChatResponse(content=[TextBlock(text=text)], is_last=True)


class ScriptedModel(OpenAIChatModel):
    """Answers each model call with the next scripted step; never calls the network."""

    def __init__(self, steps: list[ModelStep]) -> None:
        super().__init__(
            credential=OpenAICredential(
                api_key=SecretStr("EMPTY"), base_url="http://model.invalid/v1"
            ),
            model="scripted",
            stream=False,
            max_retries=0,
        )
        self.steps = steps
        self.calls = 0

    async def _call_api(self, model_name: str, messages: Any, tools: Any = None,
                        tool_choice: Any = None, **kwargs: Any) -> ChatResponse:
        self.calls += 1
        step = self.steps.pop(0)
        return step if isinstance(step, ChatResponse) else await step()


def _proposal_steps() -> list[ModelStep]:
    return [_calls(("reset_tools", {WRITE_GROUP: True})), _calls((WRITE_TOOL, {"days": 2}))]


class Registry:
    def __init__(self, *capabilities: CapabilitySpec) -> None:
        self.items = {item.capability_id: item for item in capabilities}

    async def get(self, capability_id: str) -> CapabilitySpec | None:
        return self.items.get(capability_id)

    async def list(self, target_system: str | None = None, type: str | None = None,
                   status: str | None = None) -> list[CapabilitySpec]:
        return list(self.items.values())


class Policy:
    async def preview_capability(self, **kwargs: Any) -> str:
        return "defer"


class Orchestration(AgentOrchestrationAdapter):
    """Production selection and response building; the workflow run is scripted."""

    def __init__(self, registry: Registry, execute: Callable[[], Awaitable[ExecutionResult]]):
        super().__init__(
            capability_registry=registry,  # type: ignore[arg-type]
            gateway=None,  # type: ignore[arg-type]
            workflow_engine=None,
            response_builder=ResponseEnvelopeBuilder(),
        )
        self._execute = execute
        self.executions = 0

    async def resolve_task_version_bindings(
        self, *, capability: CapabilitySpec, intent_version_binding: VersionBinding
    ) -> AgentTaskVersionBindings:
        # A workflow capability projects through its workflow binding.
        tool = VersionBinding(
            resource_type="workflow",
            resource_id=capability.capability_id,
            version=capability.version,
            digest=hashlib.sha256(capability.model_dump_json().encode()).hexdigest(),
        )
        return AgentTaskVersionBindings(
            bindings=merge_version_bindings((intent_version_binding,), (tool,)),
            projection_binding=tool,
        )

    async def execute_capability(self, *, request_context: RequestOrgContext,
                                 **kwargs: Any) -> ExecutionResult:
        self.executions += 1
        return await self._execute()


def _waiting() -> Callable[[], Awaitable[ExecutionResult]]:
    async def execute() -> ExecutionResult:
        return ExecutionResult(status="waiting_user", error_code="confirm_required", trace_id="t")

    return execute


class Timeline:
    def __init__(self) -> None:
        self.entries: list[str] = []


class TaskStore:
    def __init__(self, timeline: Timeline) -> None:
        self.timeline = timeline
        self.records: dict[str, TaskRecord] = {}

    async def create_task(self, record: TaskRecord) -> TaskRecord:
        self.records[record.task_id] = record
        return record

    async def update_status(self, task_id: str, status: str,
                            error_code: str | None = None) -> TaskRecord:
        self.timeline.entries.append(f"status:{status}")
        record = self.records[task_id].model_copy(update={"status": status})
        self.records[task_id] = record
        return record

    async def append_event(self, task_id: str, event: Any) -> None:
        return None


class SessionStore:
    async def get_session(self, session_id: str, *, tenant_id: str) -> SessionRecord | None:
        return SessionRecord(session_id=session_id, tenant_id=tenant_id)

    async def create_session(self, record: SessionRecord) -> SessionRecord:
        return record


class Trace:
    def __init__(self, timeline: Timeline) -> None:
        self.timeline = timeline
        self.block: dict[str, asyncio.Event] = {}

    async def start_task_trace(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def record_step(self, trace_id: str, task_id: str, session_id: str, *,
                          event_type: str, status: str, **kwargs: Any) -> None:
        gate = self.block.pop(event_type, None)
        if gate is not None:
            await gate.wait()
        self.timeline.entries.append(f"step:{event_type}")

    async def finalize_task_trace(self, *args: Any, **kwargs: Any) -> None:
        self.timeline.entries.append("finalize")


class Coordinator:
    """Recording ConfirmationCoordinatorPort; publish and abandon are controllable."""

    def __init__(self, timeline: Timeline, *, publish: bool = True) -> None:
        self.timeline = timeline
        self.publish = publish
        self.messages: list[tuple[ConfirmationOwner, str, str | None]] = []
        self.actions: list[ConfirmationOwner] = []
        self.staged: list[WaitingProposal] = []
        self.abandon_gate: asyncio.Event | None = None
        self.preview = AgentOrchestrationAdapter(
            capability_registry=Registry(),  # type: ignore[arg-type]
            gateway=None,  # type: ignore[arg-type]
            workflow_engine=None,
            response_builder=ResponseEnvelopeBuilder(),
        )
        self.reply: ResponseEnvelope | None = None

    async def handle_confirmation_message(self, owner: ConfirmationOwner, message: str, *,
                                          client_request_id: str | None) -> ResponseEnvelope | None:
        self.messages.append((owner, message, client_request_id))
        return self.reply

    async def handle_confirmation_action(self, owner: ConfirmationOwner,
                                         action: UserAction) -> ResponseEnvelope:
        self.actions.append(owner)
        assert self.reply is not None
        return self.reply

    async def stage_waiting(self, proposal: WaitingProposal) -> StagedWaiting:
        self.staged.append(proposal)
        self.timeline.entries.append("stage")
        confirmation = self.preview.prepare_confirmation(
            capability_id=proposal.capability.capability_id,
            arguments=proposal.arguments,
            capability=proposal.capability,
        )
        return StagedWaiting(proposal.response_id, proposal.execution, confirmation, object())

    def publish_waiting(self, staged: StagedWaiting) -> bool:
        self.timeline.entries.append(f"publish:{self.publish}")
        return self.publish

    async def abandon_waiting(self, staged: StagedWaiting) -> None:
        self.timeline.entries.append("abandon")
        if self.abandon_gate is not None:
            await self.abandon_gate.wait()
        self.timeline.entries.append("abandon_done")


class Harness:
    def __init__(self, steps: list[ModelStep], *, publish: bool = True,
                 execute: Callable[[], Awaitable[ExecutionResult]] | None = None,
                 turn_timeout: float = M1_TURN_LIMITS.turn_timeout_seconds) -> None:
        self.timeline = Timeline()
        self.model = ScriptedModel(steps)
        self.registry = Registry(_workflow())
        self.orchestration = Orchestration(self.registry, execute or _waiting())
        self.tasks = TaskStore(self.timeline)
        self.trace = Trace(self.timeline)
        self.coordinator = Coordinator(self.timeline, publish=publish)
        self.sessions = SessionCoordinator(idle_ttl_seconds=600)
        self.principal = runtime_principal()
        self.runtime = AgentScopeRuntime(
            AgentScopeRuntimeDependencies(
                capability_registry=self.registry,  # type: ignore[arg-type]
                candidate_policy=Policy(),  # type: ignore[arg-type]
                orchestration=self.orchestration,
                task_store=self.tasks,  # type: ignore[arg-type]
                session_store=SessionStore(),  # type: ignore[arg-type]
                trace_port=self.trace,  # type: ignore[arg-type]
                human_gate=InMemoryHumanGate(),
                response_builder=ResponseEnvelopeBuilder(),
                knowledge=BasicKnowledge(),
                terminal_evaluator=TerminalEvaluator(),
                overview_evaluator=OverviewPostconditionEvaluator(),
                model_factory=lambda: self.model,
                model_name="scripted",
                session_idle_ttl_seconds=600,
                confirmations=self.coordinator,
            ),
            limits=replace(M1_TURN_LIMITS, turn_timeout_seconds=turn_timeout),
            sessions=self.sessions,
        )

    def login(self, *, expires_in: float = 3600, principal: Any = None) -> None:
        authenticated_session.set(
            AuthenticatedSessionContext(
                principal=principal or self.principal,
                fingerprint=b"fingerprint-proposal",
                expires_at=datetime.now(UTC) + timedelta(seconds=expires_in),
            )
        )

    async def send(self, message: str = "帮我请两天假") -> ResponseEnvelope:
        return await self.runtime.handle_user_message(
            "web", self.principal, SESSION_ID, message, {}
        )

    def state(self) -> Any:
        key = self.sessions.key_for(
            tenant_id=self.principal.org_ctx.tenant_id,
            ai_user_id=self.principal.ai_user_id,
            fingerprint=b"fingerprint-proposal",
            session_id=SESSION_ID,
        )
        return key, self.sessions._entries[key]


async def _settle() -> None:
    # Let independent interrupt, close and abandon tasks run to completion.
    for _ in range(50):
        await asyncio.sleep(0)


def test_publication_follows_waiting_status_and_envelope_event() -> None:
    async def exercise() -> tuple[Harness, ResponseEnvelope]:
        harness = Harness(_proposal_steps())
        harness.login()
        envelope = await harness.send()
        await _settle()
        return harness, envelope

    harness, envelope = asyncio.run(exercise())
    entries = harness.timeline.entries
    publish = entries.index("publish:True")
    assert entries.index("stage") < entries.index("step:response_envelope_created") < publish
    assert entries.index("status:waiting_user") < publish
    # Nothing closes the task after the handoff: no terminal event, evaluation or finalize.
    assert entries[publish + 1:] == []
    assert "step:task_failed" not in entries and "finalize" not in entries
    assert envelope.status == "waiting_user"
    assert envelope.ui.component_type == "confirm_card"
    staged = harness.coordinator.staged
    assert len(staged) == 1 and envelope.response_id == staged[0].response_id
    assert staged[0].owner == ConfirmationOwner(
        tenant_id="tenant-test", session_id=SESSION_ID, ai_user_id="ai-user-1"
    )
    assert harness.orchestration.executions == 1 and harness.model.calls == 2
    _, entry = harness.state()
    # The parked call is closed by the registered interrupt, and the session is free.
    assert not entry.state.has_awaiting_tool_calls(AGENT_NAME) and not entry.busy


def test_cancellation_after_publication_never_closes_the_turn_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = AgentScopeRuntime._finish_intent

    async def cancelled_after_handoff(self: AgentScopeRuntime, turn: Any, ledger: Any,
                                      catalog: Any, outcome: Any) -> Any:
        if ledger.handoff:
            raise asyncio.CancelledError
        return await original(self, turn, ledger, catalog, outcome)

    monkeypatch.setattr(AgentScopeRuntime, "_finish_intent", cancelled_after_handoff)

    async def exercise() -> Harness:
        harness = Harness(_proposal_steps())
        harness.login()
        with pytest.raises(asyncio.CancelledError):
            await harness.send()
        await _settle()
        return harness

    harness = asyncio.run(exercise())
    entries = harness.timeline.entries
    assert "publish:True" in entries
    assert entries[-1] == "publish:True"
    assert "status:failed" not in entries and "step:task_failed" not in entries
    assert "abandon" not in entries
    (record,) = harness.tasks.records.values()
    assert record.status == "waiting_user"


def test_blocked_proposal_core_times_out_and_abandons_the_staged_item(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def exercise() -> tuple[Harness, ResponseEnvelope, float]:
        harness = Harness(_proposal_steps(), turn_timeout=0.5)
        # The waiting marks never finish: the proposal core outlives the deadline.
        harness.trace.block["response_envelope_created"] = asyncio.Event()
        harness.login()
        started = asyncio.get_running_loop().time()
        envelope = await asyncio.wait_for(harness.send(), 5)
        elapsed = asyncio.get_running_loop().time() - started
        await _settle()
        return harness, envelope, elapsed

    with caplog.at_level(logging.INFO, logger="uvicorn.error.agentscope_model"):
        harness, envelope, elapsed = asyncio.run(exercise())
    entries = harness.timeline.entries
    assert any("error=turn_timeout" in record.getMessage() for record in caplog.records)
    assert elapsed < 2.0
    assert envelope.status == "failed"
    assert "stage" in entries and entries.count("abandon") == 1
    assert not any(item.startswith("publish") for item in entries)
    (record,) = harness.tasks.records.values()
    assert record.status == "failed"


def test_error_continuation_runs_under_the_same_turn_deadline(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def stalled() -> ChatResponse:
        await asyncio.sleep(30)
        return _text("不应到达")

    async def exercise() -> tuple[Harness, ResponseEnvelope, float]:
        # Activated and called in one response: the write tool was never exposed,
        # so an ERROR goes back and the model's continuation stalls.
        harness = Harness(
            [_calls(("reset_tools", {WRITE_GROUP: True}), (WRITE_TOOL, {"days": 2})), stalled],
            turn_timeout=0.5,
        )
        harness.login()
        started = asyncio.get_running_loop().time()
        envelope = await asyncio.wait_for(harness.send(), 5)
        elapsed = asyncio.get_running_loop().time() - started
        await _settle()
        return harness, envelope, elapsed

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        harness, envelope, elapsed = asyncio.run(exercise())
    messages = [record.getMessage() for record in caplog.records]
    assert any("agentscope_write_rejected code=tool_not_exposed" in item for item in messages)
    assert any("error=turn_timeout" in item for item in messages)
    assert harness.model.calls == 2 and elapsed < 2.0
    assert envelope.status == "failed"
    assert harness.orchestration.executions == 0 and harness.coordinator.staged == []


def test_release_waits_for_interrupt_and_abandon_before_rollback() -> None:
    async def unit() -> list[str]:
        sessions = SessionCoordinator(idle_ttl_seconds=600)
        key = sessions.key_for(tenant_id="t", ai_user_id="u", fingerprint=b"f", session_id="s")
        expires = datetime.now(UTC) + timedelta(hours=1)
        sessions.acquire(key, expires_at=expires)
        loop = asyncio.get_running_loop()
        interrupt: asyncio.Future[bool] = loop.create_future()
        abandon: asyncio.Future[None] = loop.create_future()
        events: list[str] = []
        sessions.release_after(key, [interrupt, abandon],
                               before_release=lambda: events.append("rollback"))
        interrupt.set_result(True)
        await asyncio.sleep(0)
        with pytest.raises(RuntimeSessionBusyError):
            sessions.acquire(key, expires_at=expires)
        assert events == []
        abandon.set_exception(TimeoutError())
        abandon.exception()
        await asyncio.sleep(0)
        events.append("released" if sessions.acquire(key, expires_at=expires) else "none")
        return events

    assert asyncio.run(unit()) == ["rollback", "released"]

    async def runtime() -> tuple[Harness, ResponseEnvelope, bool, int, int]:
        # The CAS is lost: the staged item is abandoned while the turn rolls back.
        harness = Harness([*_proposal_steps(), _text("好的")], publish=False)
        harness.coordinator.abandon_gate = asyncio.Event()
        harness.login()
        envelope = await harness.send()
        await _settle()
        _, entry = harness.state()
        busy_while_abandoning = entry.busy
        with pytest.raises(RuntimeSessionBusyError):
            await harness.send("再问一次")
        context_during = len(entry.state.context)
        harness.coordinator.abandon_gate.set()
        await _settle()
        context_after = len(entry.state.context)
        await harness.send("你好")
        return harness, envelope, busy_while_abandoning, context_during, context_after

    harness, envelope, busy, during, after = asyncio.run(runtime())
    assert envelope.status == "failed"
    assert busy and during > 0 and after == 0
    entries = harness.timeline.entries
    assert entries.index("publish:False") < entries.index("abandon") < entries.index("abandon_done")


@pytest.mark.parametrize("login", ["missing", "other_principal", "expired"])
def test_confirmation_entries_require_a_valid_login(login: str) -> None:
    async def exercise() -> tuple[Harness, ResponseEnvelope, ResponseEnvelope]:
        harness = Harness([])
        if login == "other_principal":
            harness.login(principal=runtime_principal("ai-user-2"))
        elif login == "expired":
            harness.login(expires_in=-1)
        token = chat_client_request_id.set("request-1")
        try:
            text = await harness.send("确认")
            action = await harness.runtime.handle_user_action(
                "web", harness.principal, SESSION_ID,
                ConfirmUserAction(action_type="confirm", response_id="r", confirmed=True),
            )
        finally:
            chat_client_request_id.reset(token)
        return harness, text, action

    harness, text, action = asyncio.run(exercise())
    for envelope in (text, action):
        assert envelope.status == "failed"
        assert envelope.message == "当前登录会话无效，请重新登录。"
    # Never claimed: the coordinator is not reached, no model call is made.
    assert harness.coordinator.messages == [] and harness.coordinator.actions == []
    assert harness.model.calls == 0

    async def valid() -> Harness:
        harness = Harness([])
        harness.coordinator.reply = text
        harness.login()
        token = chat_client_request_id.set("request-1")
        try:
            await harness.send("确认")
            await harness.runtime.handle_user_action(
                "web", harness.principal, SESSION_ID,
                ConfirmUserAction(action_type="confirm", response_id="r", confirmed=True),
            )
        finally:
            chat_client_request_id.reset(token)
        return harness

    accepted = asyncio.run(valid())
    owner = ConfirmationOwner(tenant_id="tenant-test", session_id=SESSION_ID,
                              ai_user_id="ai-user-1")
    assert accepted.coordinator.messages == [(owner, "确认", "request-1")]
    assert accepted.coordinator.actions == [owner] and accepted.model.calls == 0
