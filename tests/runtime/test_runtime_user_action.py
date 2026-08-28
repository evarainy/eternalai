"""Structured UserAction dispatch and fail-closed Workflow resume tests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

import pytest

from app.composition import build_runtime
from app.contracts.sdui.models import UserAction
from app.infra.human_gate.in_memory import InMemoryHumanGate
from app.infra.llm.mock_llm.mock_llm_provider import MockLLMProvider
from app.infra.llm.mock_structured_output.mock_structured_output_provider import (
    MockStructuredOutputProvider,
)
from app.ports.auth import Principal, PrincipalOrgContext
from app.ports.capability_gateway import ExecutionResult
from app.ports.human_gate import (
    HumanGateConflictError,
    HumanGateDecisionRecord,
    VersionBindingMismatchError,
)
from app.runtime.models import CapabilityRef
from app.runtime.runtime import RuntimeImpl, _PendingWorkflow
from app.workflow.engine import WorkflowEngine
from app.workflow.models import WorkflowDefinition, WorkflowStep
from tests.runtime.test_runtime_workflow import (
    BlockingConfirmationGateway,
    Gateway,
    Registry,
    SessionStore,
    TaskStore,
    _capability,
)

_START_MESSAGE = "start structured action"
_WORKFLOW_ID = "oa.workflow.structured-action"
_PREVIEW_ID = "oa.structured.preview"
_EXECUTE_ID = "oa.structured.execute"


class RecordingTrace:
    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []
        self.finalizations: list[dict[str, Any]] = []

    async def start_task_trace(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def record_step(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
        event_type: str,
        status: str,
        capability_id: str | None = None,
        error_code: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        self.steps.append(
            {
                "trace_id": trace_id,
                "task_id": task_id,
                "session_id": session_id,
                "event_type": event_type,
                "status": status,
                "capability_id": capability_id,
                "error_code": error_code,
                "attributes": attributes or {},
            }
        )

    async def finalize_task_trace(self, *args: Any, **kwargs: Any) -> None:
        self.finalizations.append({"args": args, **kwargs})


class CountingHumanGate(InMemoryHumanGate):
    def __init__(self) -> None:
        super().__init__()
        self.record_decision_calls = 0

    async def record_decision(
        self,
        decision: HumanGateDecisionRecord,
    ) -> HumanGateDecisionRecord:
        self.record_decision_calls += 1
        return await super().record_decision(decision)


class ConflictWithoutDecisionGate(CountingHumanGate):
    def __init__(self) -> None:
        super().__init__()
        self.on_conflict: Callable[[], None] | None = None

    async def record_decision(
        self,
        decision: HumanGateDecisionRecord,
    ) -> HumanGateDecisionRecord:
        del decision
        self.record_decision_calls += 1
        if self.on_conflict is not None:
            self.on_conflict()
        raise HumanGateConflictError("synthetic stale decision")


class ControllableVersionGate(CountingHumanGate):
    def __init__(self) -> None:
        super().__init__()
        self.fail_bindings = False
        self.on_failure: Callable[[], None] | None = None

    async def assert_task_bindings(self, *args: Any, **kwargs: Any) -> None:
        if self.fail_bindings:
            if self.on_failure is not None:
                self.on_failure()
            raise VersionBindingMismatchError("synthetic version drift")
        await super().assert_task_bindings(*args, **kwargs)


class ControllableRequestGate(CountingHumanGate):
    def __init__(self) -> None:
        super().__init__()
        self.fail_next_request = False
        self.on_failure: Callable[[], None] | None = None

    async def create_request(self, request: Any) -> Any:
        if self.fail_next_request:
            if self.on_failure is not None:
                self.on_failure()
            raise HumanGateConflictError("synthetic next request conflict")
        return await super().create_request(request)


class CountingWorkflowEngine(WorkflowEngine):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.resume_calls = 0

    async def resume(self, **kwargs: Any) -> Any:
        self.resume_calls += 1
        return await super().resume(**kwargs)


class RaisingExecutionGateway(Gateway):
    async def execute_capability(self, *args: Any, **kwargs: Any) -> ExecutionResult:
        capability_id = args[3]
        if capability_id == _EXECUTE_ID:
            self.calls.append((capability_id, args[4]))
            raise RuntimeError("synthetic adapter exception")
        return await super().execute_capability(*args, **kwargs)


class InjectingGateway(Gateway):
    def __init__(self, results: dict[str, ExecutionResult]) -> None:
        super().__init__(results)
        self.trigger_capability_id: str | None = None
        self.on_trigger: Callable[[], None] | None = None

    async def execute_capability(self, *args: Any, **kwargs: Any) -> ExecutionResult:
        result = await super().execute_capability(*args, **kwargs)
        if args[3] == self.trigger_capability_id and self.on_trigger is not None:
            self.on_trigger()
        return result


class AlternatingPendingMap(dict[tuple[str, str], _PendingWorkflow]):
    def __init__(
        self,
        key: tuple[str, str],
        observed: _PendingWorkflow,
        winner: _PendingWorkflow,
    ) -> None:
        super().__init__({key: winner})
        self._observed = observed
        self._reads = 0

    def get(
        self,
        key: tuple[str, str],
        default: _PendingWorkflow | None = None,
    ) -> _PendingWorkflow | None:
        self._reads += 1
        if self._reads == 1:
            return self._observed
        return super().get(key, default)


@dataclass
class Harness:
    runtime: RuntimeImpl
    principal: Principal
    gateway: Gateway
    gate: CountingHumanGate | None
    engine: CountingWorkflowEngine
    trace: RecordingTrace
    llm: MockLLMProvider
    structured_output: MockStructuredOutputProvider
    waiting: Any


def _principal(user_id: str = "user-action") -> Principal:
    return Principal(
        ai_user_id=user_id,
        display_name="Structured Action User",
        roles=("user",),
        org_ctx=PrincipalOrgContext(),
    )


def _action(response_id: str) -> UserAction:
    return UserAction(
        action_type="confirm",
        response_id=response_id,
        confirmed=True,
    )


def _single_definition() -> WorkflowDefinition:
    return WorkflowDefinition(
        workflow_id=_WORKFLOW_ID,
        version="1.0.0",
        steps=(
            WorkflowStep(
                step_id="confirm",
                capability_id=_PREVIEW_ID,
                confirmed_capability_id=_EXECUTE_ID,
            ),
        ),
    )


def _two_confirmation_definition() -> WorkflowDefinition:
    return WorkflowDefinition(
        workflow_id=_WORKFLOW_ID,
        version="1.0.0",
        steps=(
            WorkflowStep(
                step_id="first",
                capability_id=_PREVIEW_ID,
                confirmed_capability_id=_EXECUTE_ID,
            ),
            WorkflowStep(
                step_id="second",
                capability_id="oa.structured.second.preview",
                confirmed_capability_id="oa.structured.second.execute",
            ),
        ),
    )


async def _build_harness(
    *,
    gate: CountingHumanGate | None = None,
    with_gate: bool = True,
    gateway: Gateway | None = None,
    definition: WorkflowDefinition | None = None,
    confirmed_result: ExecutionResult | None = None,
) -> Harness:
    definition = definition or _single_definition()
    gate = gate if with_gate else None
    if with_gate and gate is None:
        gate = CountingHumanGate()
    capabilities = [
        _capability(definition.workflow_id, "workflow"),
        _capability(_PREVIEW_ID, "query"),
        _capability(_EXECUTE_ID, "action"),
    ]
    if len(definition.steps) == 2:
        capabilities.extend(
            [
                _capability("oa.structured.second.preview", "query"),
                _capability("oa.structured.second.execute", "action"),
            ]
        )
    registry = Registry(*capabilities)
    results = {
        _PREVIEW_ID: ExecutionResult(
            status="waiting_user",
            error_code="confirm_required",
            trace_id="preview",
        ),
        _EXECUTE_ID: confirmed_result
        or ExecutionResult(
            status="completed",
            data={"safe": "accepted"},
            trace_id="execute",
        ),
        "oa.structured.second.preview": ExecutionResult(
            status="waiting_user",
            error_code="confirm_required",
            trace_id="second-preview",
        ),
        "oa.structured.second.execute": ExecutionResult(
            status="completed",
            data={"second": "accepted"},
            trace_id="second-execute",
        ),
    }
    gateway = gateway or Gateway(results)
    gateway.results.update(results)
    task_store = TaskStore()
    trace = RecordingTrace()
    engine = CountingWorkflowEngine(
        definitions={definition.workflow_id: definition},
        capability_registry=registry,
        gateway=gateway,
        task_store=task_store,
        trace_port=trace,
    )
    llm = MockLLMProvider()
    structured_output = MockStructuredOutputProvider()
    structured_output.register(
        _START_MESSAGE,
        CapabilityRef,
        CapabilityRef(
            capability_id=definition.workflow_id,
            capability_type="workflow",
        ),
    )
    runtime = build_runtime(
        task_store=task_store,
        session_store=SessionStore(),
        capability_registry=registry,
        gateway=gateway,
        trace_port=trace,
        llm_provider=llm,
        structured_output=structured_output,
        intent_model="test-intent-model",
        workflow_engine=engine,
        human_gate_port=gate,
    )
    principal = _principal()
    waiting = await runtime.handle_user_message(
        channel="mock",
        ai_user_id=principal.ai_user_id,
        session_id="session-action",
        message=_START_MESSAGE,
        client_capabilities={},
    )
    assert waiting.status == "waiting_user"
    return Harness(
        runtime=runtime,
        principal=principal,
        gateway=gateway,
        gate=gate,
        engine=engine,
        trace=trace,
        llm=llm,
        structured_output=structured_output,
        waiting=waiting,
    )


async def _dispatch(
    harness: Harness,
    *,
    principal: Principal | None = None,
    session_id: str = "session-action",
) -> Any:
    return await harness.runtime.handle_user_action(
        channel="web",
        principal=principal or harness.principal,
        session_id=session_id,
        action=_action(harness.waiting.response_id),
    )


def _outcome(envelope: Any) -> str:
    assert set(envelope.data) == {"action_outcome", "result"}
    return envelope.data["action_outcome"]


def _pending(harness: Harness) -> _PendingWorkflow:
    return harness.runtime._pending_workflows[
        ("session-action", harness.principal.ai_user_id)
    ]


def _winner(pending: _PendingWorkflow, suffix: str) -> _PendingWorkflow:
    return replace(
        pending,
        response_id=f"winner-{suffix}",
        gate_request_id=f"winner-{suffix}",
    )


def test_action_without_human_gate_fails_closed_before_resume_or_routing() -> None:
    async def exercise() -> tuple[Harness, Any, int, int]:
        harness = await _build_harness(with_gate=False)
        gateway_calls = len(harness.gateway.calls)
        llm_calls = len(harness.llm.calls)
        response = await _dispatch(harness)
        return harness, response, gateway_calls, llm_calls

    harness, response, gateway_calls, llm_calls = asyncio.run(exercise())

    assert _outcome(response) == "action_gate_unavailable"
    assert response.data["result"] is None
    assert len(harness.gateway.calls) == gateway_calls
    assert harness.engine.resume_calls == 0
    assert len(harness.llm.calls) == llm_calls


def test_no_pending_and_cross_identity_are_indistinguishable_and_use_fresh_trace_ids() -> None:
    async def exercise() -> tuple[Harness, Any, Any, Any, int]:
        harness = await _build_harness()
        llm_calls = len(harness.llm.calls)
        wrong_user = await _dispatch(harness, principal=_principal("other-user"))
        wrong_session = await _dispatch(harness, session_id="other-session")
        harness.runtime._pending_workflows.clear()
        own_missing = await _dispatch(harness)
        return harness, wrong_user, wrong_session, own_missing, llm_calls

    harness, wrong_user, wrong_session, own_missing, llm_calls = asyncio.run(exercise())

    for response in (wrong_user, wrong_session, own_missing):
        assert _outcome(response) == "no_pending_action"
        assert response.data["result"] is None
    comparable = lambda value: value.model_dump(  # noqa: E731
        exclude={"response_id", "task_id", "session_id", "trace_id"}
    )
    assert comparable(wrong_user) == comparable(wrong_session) == comparable(own_missing)
    action_events = [
        event for event in harness.trace.steps if event["event_type"] == "user_action"
    ]
    inbound = [event for event in action_events if event["attributes"] == {"phase": "inbound"}]
    assert len(inbound) == 3
    assert len({event["trace_id"] for event in inbound}) == 3
    assert len({event["task_id"] for event in inbound}) == 3
    assert all(event["trace_id"] != harness.waiting.trace_id for event in inbound)
    assert all(event["task_id"] != harness.waiting.task_id for event in inbound)
    assert len(harness.llm.calls) == llm_calls


def test_missing_binding_digest_is_rejected_without_resume_or_routing() -> None:
    async def exercise() -> tuple[Harness, Any, int, int]:
        harness = await _build_harness()
        pending = _pending(harness)
        key = ("session-action", harness.principal.ai_user_id)
        harness.runtime._pending_workflows[key] = replace(pending, action_digest=None)
        gateway_calls = len(harness.gateway.calls)
        llm_calls = len(harness.llm.calls)
        response = await _dispatch(harness)
        return harness, response, gateway_calls, llm_calls

    harness, response, gateway_calls, llm_calls = asyncio.run(exercise())

    assert _outcome(response) == "action_binding_incomplete"
    assert len(harness.gateway.calls) == gateway_calls
    assert harness.engine.resume_calls == 0
    assert len(harness.llm.calls) == llm_calls


def test_reference_mismatch_is_rejected_without_resume_or_routing() -> None:
    async def exercise() -> tuple[Harness, Any, int, int]:
        harness = await _build_harness()
        gateway_calls = len(harness.gateway.calls)
        llm_calls = len(harness.llm.calls)
        response = await harness.runtime.handle_user_action(
            channel="web",
            principal=harness.principal,
            session_id="session-action",
            action=_action("different-response"),
        )
        return harness, response, gateway_calls, llm_calls

    harness, response, gateway_calls, llm_calls = asyncio.run(exercise())

    assert _outcome(response) == "action_reference_mismatch"
    assert len(harness.gateway.calls) == gateway_calls
    assert harness.engine.resume_calls == 0
    assert len(harness.llm.calls) == llm_calls


def test_duplicate_click_is_claimed_once_and_resumes_once() -> None:
    async def exercise() -> tuple[Harness, list[Any], int]:
        gateway = BlockingConfirmationGateway(
            {},
            blocked_capability_id=_EXECUTE_ID,
        )
        harness = await _build_harness(gateway=gateway)
        llm_calls = len(harness.llm.calls)
        first = asyncio.create_task(_dispatch(harness))
        await gateway.confirmation_entered.wait()
        duplicate = await _dispatch(harness)
        gateway.release_confirmation.set()
        accepted = await first
        return harness, [accepted, duplicate], llm_calls

    harness, responses, llm_calls = asyncio.run(exercise())

    assert sorted(_outcome(response) for response in responses) == [
        "accepted",
        "action_already_claimed",
    ]
    assert harness.engine.resume_calls == 1
    assert harness.gate is not None
    assert harness.gate.record_decision_calls == 1
    assert [call[0] for call in harness.gateway.calls].count(_EXECUTE_ID) == 1
    assert len(harness.llm.calls) == llm_calls


def test_concurrent_clicks_have_exactly_one_accepted_adapter_call() -> None:
    async def exercise() -> tuple[Harness, list[Any]]:
        gateway = BlockingConfirmationGateway({}, blocked_capability_id=_EXECUTE_ID)
        harness = await _build_harness(gateway=gateway)
        first = asyncio.create_task(_dispatch(harness))
        await gateway.confirmation_entered.wait()
        others = [asyncio.create_task(_dispatch(harness)) for _ in range(3)]
        await asyncio.sleep(0)
        gateway.release_confirmation.set()
        return harness, list(await asyncio.gather(first, *others))

    harness, responses = asyncio.run(exercise())

    outcomes = [_outcome(response) for response in responses]
    assert outcomes.count("accepted") == 1
    assert outcomes.count("action_already_claimed") == 3
    assert [call[0] for call in harness.gateway.calls].count(_EXECUTE_ID) == 1


def test_version_drift_has_precise_outcome_and_never_executes_adapter() -> None:
    async def exercise() -> tuple[Harness, Any, int, int]:
        gate = ControllableVersionGate()
        harness = await _build_harness(gate=gate)
        gate.fail_bindings = True
        gateway_calls = len(harness.gateway.calls)
        llm_calls = len(harness.llm.calls)
        response = await _dispatch(harness)
        return harness, response, gateway_calls, llm_calls

    harness, response, gateway_calls, llm_calls = asyncio.run(exercise())

    assert _outcome(response) == "action_version_conflict"
    assert response.data["result"] is None
    assert len(harness.gateway.calls) == gateway_calls
    assert harness.engine.resume_calls == 0
    assert len(harness.llm.calls) == llm_calls


def test_stale_human_gate_conflict_has_no_result_or_adapter_call() -> None:
    async def exercise() -> tuple[Harness, Any, int, int]:
        gate = ConflictWithoutDecisionGate()
        harness = await _build_harness(gate=gate)
        gateway_calls = len(harness.gateway.calls)
        llm_calls = len(harness.llm.calls)
        response = await _dispatch(harness)
        return harness, response, gateway_calls, llm_calls

    harness, response, gateway_calls, llm_calls = asyncio.run(exercise())

    assert _outcome(response) == "action_stale"
    assert response.data["result"] is None
    assert len(harness.gateway.calls) == gateway_calls
    assert harness.engine.resume_calls == 0
    assert len(harness.llm.calls) == llm_calls


def test_existing_human_gate_decision_maps_to_already_claimed() -> None:
    async def exercise() -> tuple[Harness, Any, int, int]:
        harness = await _build_harness()
        assert harness.gate is not None
        pending = _pending(harness)
        request = await harness.gate.get_request(harness.waiting.response_id)
        assert request is not None
        await harness.gate.record_decision(
            HumanGateDecisionRecord(
                request_id=request.request_id,
                task_id=request.task_id,
                decided_by_ai_user_id=harness.principal.ai_user_id,
                decided_session_id="session-action",
                decided_tenant_id=harness.principal.org_ctx.tenant_id,
                decision="confirmed",
                request_digest=request.request_digest,
                binding_manifest_digest=request.binding_manifest_digest,
                decided_at=datetime.now(UTC),
            )
        )
        harness.gate.record_decision_calls = 0
        assert pending.gate_request_id == request.request_id
        gateway_calls = len(harness.gateway.calls)
        llm_calls = len(harness.llm.calls)
        response = await _dispatch(harness)
        return harness, response, gateway_calls, llm_calls

    harness, response, gateway_calls, llm_calls = asyncio.run(exercise())

    assert _outcome(response) == "action_already_claimed"
    assert len(harness.gateway.calls) == gateway_calls
    assert harness.engine.resume_calls == 0
    assert harness.gate is not None
    assert harness.gate.record_decision_calls == 1
    assert len(harness.llm.calls) == llm_calls


@pytest.mark.parametrize(
    ("result", "expected_status"),
    [
        (
            ExecutionResult(
                status="denied",
                error_code="policy_denied",
                trace_id="denied",
            ),
            "blocked",
        ),
        (
            ExecutionResult(
                status="failed",
                error_code="adapter_http_500",
                trace_id="failed",
            ),
            "failed",
        ),
    ],
)
def test_accepted_action_keeps_business_failure_in_top_level_status(
    result: ExecutionResult,
    expected_status: str,
) -> None:
    async def exercise() -> Any:
        harness = await _build_harness(confirmed_result=result)
        return await _dispatch(harness)

    response = asyncio.run(exercise())

    assert _outcome(response) == "accepted"
    assert response.status == expected_status
    assert response.data["result"] is None


def test_accepted_result_is_the_builder_sanitized_capability_result() -> None:
    async def exercise() -> Any:
        harness = await _build_harness(
            confirmed_result=ExecutionResult(
                status="completed",
                data={"safe": "ok", "password": "synthetic-private-value"},
                trace_id="completed",
            )
        )
        return await _dispatch(harness)

    response = asyncio.run(exercise())

    assert _outcome(response) == "accepted"
    assert response.data["result"] == {
        "safe": "ok",
        "[REDACTED]": "[REDACTED]",
    }
    assert "synthetic-private-value" not in response.model_dump_json()


def test_exception_after_claim_cannot_replay_adapter_execution() -> None:
    async def exercise() -> tuple[Harness, Any, int]:
        harness = await _build_harness(gateway=RaisingExecutionGateway())
        llm_calls = len(harness.llm.calls)
        with pytest.raises(RuntimeError, match="synthetic adapter exception"):
            await _dispatch(harness)
        replay = await _dispatch(harness)
        return harness, replay, llm_calls

    harness, replay, llm_calls = asyncio.run(exercise())

    assert _outcome(replay) == "action_already_claimed"
    assert [call[0] for call in harness.gateway.calls].count(_EXECUTE_ID) == 1
    assert harness.engine.resume_calls == 1
    assert harness.gate is not None
    assert harness.gate.record_decision_calls == 1
    assert len(harness.llm.calls) == llm_calls


def test_claim_and_pending_writer_each_win_without_overwriting_the_winner() -> None:
    async def exercise() -> tuple[
        Harness,
        Any,
        Any,
        _PendingWorkflow,
        _PendingWorkflow,
        _PendingWorkflow,
        int,
        int,
    ]:
        harness = await _build_harness()
        key = ("session-action", harness.principal.ai_user_id)
        original = _pending(harness)
        claim_key = (*key, original.gate_request_id or original.response_id)
        harness.runtime._claimed_pending_confirmations.add(claim_key)
        harness.structured_output.register(
            "replace pending",
            CapabilityRef,
            CapabilityRef(
                capability_id=_WORKFLOW_ID,
                capability_type="workflow",
            ),
        )
        writer_loses = await harness.runtime.handle_user_message(
            channel="mock",
            ai_user_id=harness.principal.ai_user_id,
            session_id="session-action",
            message="replace pending",
            client_capabilities={},
        )
        after_claim_wins = harness.runtime._pending_workflows[key]

        harness.runtime._claimed_pending_confirmations.clear()
        writer_winner = _winner(original, "writer-first")
        harness.runtime._pending_workflows = AlternatingPendingMap(
            key,
            original,
            writer_winner,
        )
        gateway_calls = len(harness.gateway.calls)
        llm_calls = len(harness.llm.calls)
        action_loses = await _dispatch(harness)
        return (
            harness,
            writer_loses,
            action_loses,
            after_claim_wins,
            writer_winner,
            original,
            gateway_calls,
            llm_calls,
        )

    (
        harness,
        writer_loses,
        action_loses,
        after_claim_wins,
        writer_winner,
        original,
        gateway_calls,
        llm_calls,
    ) = asyncio.run(exercise())

    assert writer_loses.status == "failed"
    assert after_claim_wins is original
    assert _outcome(action_loses) == "action_pending_changed"
    key = ("session-action", harness.principal.ai_user_id)
    assert harness.runtime._pending_workflows[key] is writer_winner
    assert len(harness.gateway.calls) == gateway_calls
    assert len(harness.llm.calls) == llm_calls


@pytest.mark.parametrize(
    "cas_site",
    ["version_pop", "stale_pop", "next_request_pop", "waiting_write", "terminal_pop"],
)
def test_each_resume_pending_mutation_preserves_a_concurrent_winner(cas_site: str) -> None:
    async def exercise() -> tuple[Harness, _PendingWorkflow, Any]:
        two_waits = cas_site in {"next_request_pop", "waiting_write"}
        definition = _two_confirmation_definition() if two_waits else _single_definition()
        if cas_site == "version_pop":
            gate: CountingHumanGate = ControllableVersionGate()
            gateway: Gateway = Gateway()
        elif cas_site == "stale_pop":
            gate = ConflictWithoutDecisionGate()
            gateway = Gateway()
        elif cas_site == "next_request_pop":
            gate = ControllableRequestGate()
            gateway = Gateway()
        else:
            gate = CountingHumanGate()
            gateway = InjectingGateway({})
        harness = await _build_harness(
            gate=gate,
            gateway=gateway,
            definition=definition,
        )
        key = ("session-action", harness.principal.ai_user_id)
        original = _pending(harness)
        winner = _winner(original, cas_site)

        def install_winner() -> None:
            harness.runtime._pending_workflows[key] = winner

        if isinstance(gate, ControllableVersionGate):
            gate.fail_bindings = True
            gate.on_failure = install_winner
        elif isinstance(gate, ConflictWithoutDecisionGate):
            gate.on_conflict = install_winner
        elif isinstance(gate, ControllableRequestGate):
            gate.fail_next_request = True
            gate.on_failure = install_winner
        else:
            assert isinstance(gateway, InjectingGateway)
            gateway.trigger_capability_id = (
                "oa.structured.second.preview" if cas_site == "waiting_write" else _EXECUTE_ID
            )
            gateway.on_trigger = install_winner
        response = await _dispatch(harness)
        return harness, winner, response

    harness, winner, response = asyncio.run(exercise())

    key = ("session-action", harness.principal.ai_user_id)
    assert harness.runtime._pending_workflows[key] is winner
    if cas_site == "version_pop":
        assert _outcome(response) == "action_version_conflict"
    elif cas_site == "stale_pop":
        assert _outcome(response) == "action_stale"
    else:
        assert _outcome(response) == "accepted"


def test_second_structured_confirmation_uses_fresh_claim_and_succeeds() -> None:
    async def exercise() -> tuple[Harness, Any, Any]:
        harness = await _build_harness(definition=_two_confirmation_definition())
        first = await _dispatch(harness)
        second_response_id = first.response_id
        second = await harness.runtime.handle_user_action(
            channel="web",
            principal=harness.principal,
            session_id="session-action",
            action=_action(second_response_id),
        )
        return harness, first, second

    harness, first, second = asyncio.run(exercise())

    assert _outcome(first) == "accepted"
    assert first.status == "waiting_user"
    assert first.response_id != harness.waiting.response_id
    assert _outcome(second) == "accepted"
    assert second.status == "completed"
    assert harness.engine.resume_calls == 2
    assert [call[0] for call in harness.gateway.calls].count(_EXECUTE_ID) == 1
    assert [call[0] for call in harness.gateway.calls].count(
        "oa.structured.second.execute"
    ) == 1


def test_user_action_trace_records_inbound_before_outcome() -> None:
    async def exercise() -> tuple[Harness, Any]:
        harness = await _build_harness()
        response = await _dispatch(harness)
        return harness, response

    harness, response = asyncio.run(exercise())
    action_events = [
        event for event in harness.trace.steps if event["event_type"] == "user_action"
    ]

    assert _outcome(response) == "accepted"
    assert [event["attributes"] for event in action_events] == [
        {"phase": "inbound"},
        {"phase": "outcome", "action_outcome": "accepted"},
    ]
    assert action_events[0]["trace_id"] == action_events[1]["trace_id"]
    assert action_events[0]["task_id"] == action_events[1]["task_id"]
