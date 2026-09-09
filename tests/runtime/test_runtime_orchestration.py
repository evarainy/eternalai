"""Runtime-to-Port wiring, immutable previews and confirmation lifecycle proofs."""

from __future__ import annotations

import ast
import asyncio
import copy
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, call, create_autospec

import pytest

from app.composition import build_runtime
from app.contracts.sdui.models import CancelUserAction, ConfirmUserAction, RejectUserAction
from app.infra.llm.mock_llm.mock_llm_provider import MockLLMProvider
from app.infra.llm.mock_structured_output.mock_structured_output_provider import (
    MockStructuredOutputProvider,
)
from app.infra.orchestration.agent_adapter import AgentOrchestrationAdapter
from app.infra.sdui.response_envelope_builder import ResponseEnvelopeBuilder
from app.infra.workflow.engine_adapter import WorkflowEngineAdapter
from app.ports.agent_orchestration import (
    AgentCapabilitySelection,
    AgentOrchestrationPort,
    AgentResponseContext,
    AgentTaskVersionBindings,
    ConfirmationPreview,
    OrchestrationContractError,
)
from app.ports.capability_gateway import ExecutionResult, RequestOrgContext
from app.ports.capability_registry import CapabilityRegistryPort
from app.ports.human_gate import HumanGateConflictError, VersionBindingMismatchError
from app.ports.response_projection_contract import ProjectionContractSnapshot
from app.runtime import runtime as runtime_module
from app.runtime.models import CapabilityRef, IntentOutput, MatchedIntent
from app.runtime.runtime import RuntimeImpl, _pending_confirmation_claim_key
from app.version_binding import (
    capability_version_bindings,
    immutable_request_digest,
    merge_version_bindings,
)
from tests.infra.orchestration.test_agent_adapter import _capability
from tests.runtime.test_runtime_user_action import (
    CountingHumanGate,
    CountingWorkflowEngine,
    RecordingTrace,
    _principal,
    _single_definition,
    _two_confirmation_definition,
)
from tests.runtime.test_runtime_workflow import Gateway, Registry, SessionStore, TaskStore


def _harness(
    *,
    workflow: bool = True,
    with_gate: bool = True,
    two: bool = False,
    arguments: dict[str, Any] | None = None,
) -> SimpleNamespace:
    definition = _two_confirmation_definition() if two else _single_definition()
    capability_id = definition.workflow_id if workflow else "oa.synthetic.action"
    capability = _capability(
        capability_id, type="workflow" if workflow else "action", version="1.0.0"
    )
    capabilities = [capability] + [
        _capability(cap_id, type=kind, version="1.0.0")
        for cap_id, kind in [
            ("oa.structured.preview", "query"),
            ("oa.structured.execute", "action"),
            ("oa.structured.second.preview", "query"),
            ("oa.structured.second.execute", "action"),
        ]
    ]
    registry = Registry(*capabilities)
    results = {
        capability_id: ExecutionResult(
            status="completed", data={"result": "plain"}, trace_id="plain"
        ),
        "oa.structured.preview": ExecutionResult(
            status="waiting_user", error_code="confirm_required", trace_id="preview"
        ),
        "oa.structured.execute": ExecutionResult(
            status="completed", data={"result": "first"}, trace_id="first"
        ),
        "oa.structured.second.preview": ExecutionResult(
            status="waiting_user", error_code="confirm_required", trace_id="second-preview"
        ),
        "oa.structured.second.execute": ExecutionResult(
            status="completed", data={"result": "second"}, trace_id="second"
        ),
    }
    gateway = Gateway(results)
    gate = CountingHumanGate() if with_gate else None
    tasks = TaskStore()
    trace = RecordingTrace()
    engine = (
        CountingWorkflowEngine(
            definitions={definition.workflow_id: definition},
            capability_registry=registry,
            gateway=gateway,
            task_store=tasks,
            trace_port=trace,
        )
        if workflow
        else None
    )
    structured = MockStructuredOutputProvider()
    actual_arguments = arguments if arguments is not None else {"remark": "合成说明", "amount": 12}
    structured.register(
        "start seam",
        IntentOutput,
        MatchedIntent(
            match="capability",
            capability_id=capability_id,
            arguments=actual_arguments,
            capability_type="workflow" if workflow else "action",
        ),
    )
    runtime = build_runtime(
        task_store=tasks,
        session_store=SessionStore(),
        capability_registry=registry,
        gateway=gateway,
        trace_port=trace,
        llm_provider=MockLLMProvider(),
        structured_output=structured,
        intent_model="test-intent",
        workflow_engine=engine,
        human_gate_port=gate,
    )
    return SimpleNamespace(
        runtime=runtime,
        registry=registry,
        capability=capability,
        gateway=gateway,
        gate=gate,
        tasks=tasks,
        trace=trace,
        engine=engine,
        structured=structured,
        principal=_principal("seam-user", tenant_id="seam-tenant"),
        session_id="seam-session",
        arguments=actual_arguments,
        waiting=None,
    )


async def _start(h: SimpleNamespace) -> Any:
    h.waiting = await h.runtime.handle_user_message(
        channel="web",
        principal=h.principal,
        session_id=h.session_id,
        message="start seam",
        client_capabilities={},
    )
    return h.waiting


def _pending(h: SimpleNamespace) -> Any:
    return h.runtime._pending_workflows[(h.session_id, h.principal.ai_user_id)]


async def _confirm(h: SimpleNamespace, *, response_id: str | None = None) -> Any:
    return await h.runtime.handle_user_action(
        channel="web",
        principal=h.principal,
        session_id=h.session_id,
        action=ConfirmUserAction(
            action_type="confirm", confirmed=True, response_id=response_id or h.waiting.response_id
        ),
    )


async def _text_confirm(h: SimpleNamespace) -> Any:
    return await h.runtime.handle_user_message(
        channel="mock",
        principal=h.principal,
        session_id=h.session_id,
        message=f"确认 {h.waiting.task_id}",
        client_capabilities={},
    )


def _business_count(h: SimpleNamespace) -> int:
    return sum(
        capability_id in {"oa.structured.execute", "oa.structured.second.execute"}
        for capability_id, _ in h.gateway.calls
    )


def test_new_task_uses_port_for_selection_binding_execution_and_response() -> None:
    h = _harness(workflow=False)
    response_entry = Mock(wraps=h.runtime._build_envelope)
    h.runtime._build_envelope = response_entry
    registry = create_autospec(CapabilityRegistryPort, instance=True, spec_set=True)
    registry.list.return_value = [h.capability]
    registry.get.side_effect = AssertionError("Runtime cannot select directly")
    h.runtime._capability_registry = registry
    port = create_autospec(AgentOrchestrationPort, instance=True, spec_set=True)
    h.runtime._orchestration = port
    port.select_capability.return_value = AgentCapabilitySelection(
        h.capability, "unique_intent_tag"
    )

    async def resolve(**kwargs: Any) -> AgentTaskVersionBindings:
        resources = capability_version_bindings(kwargs["capability"])
        return AgentTaskVersionBindings(
            bindings=merge_version_bindings((kwargs["intent_version_binding"],), resources),
            projection_binding=next(
                binding for binding in resources if binding.resource_type == "tool"
            ),
        )

    port.resolve_task_version_bindings.side_effect = resolve
    execution = ExecutionResult(
        status="completed", data={"result": "port execution"}, trace_id="port-trace"
    )
    port.execute_capability.return_value = execution
    returned: list[Any] = []

    def response(**kwargs: Any) -> Any:
        context = kwargs["context"]
        result = ResponseEnvelopeBuilder().build_message(
            context.response_id,
            context.task_id,
            context.session_id,
            "PORT_RESPONSE",
            "Port response",
            context.trace_id,
            data={"result": "port sentinel"},
        )
        returned.append(result)
        return result

    port.build_response.side_effect = response
    envelope = asyncio.run(_start(h))
    assert len(returned) == 1
    assert envelope is returned[0]
    assert response_entry.call_count == 1
    assert envelope.data == {"result": "port sentinel"}
    assert port.resolve_task_version_bindings.await_count == 1
    selected = port.resolve_task_version_bindings.await_args.kwargs["capability"]
    assert selected is not h.capability
    assert selected == h.capability
    expected_context = AgentResponseContext(
        envelope.response_id,
        envelope.task_id,
        h.session_id,
        envelope.trace_id,
        h.capability.capability_id,
    )
    assert port.mock_calls == [
        call.select_capability(
            capability_id=h.capability.capability_id, target_system=None, capability_type="action"
        ),
        call.resolve_task_version_bindings(
            capability=selected, intent_version_binding=h.runtime._intent_version_binding
        ),
        call.execute_capability(
            task_id=envelope.task_id,
            session_id=h.session_id,
            ai_user_id="seam-user",
            capability=selected,
            arguments=h.arguments,
            request_context=RequestOrgContext(
                request_id=envelope.trace_id, channel="web", tenant_id="seam-tenant"
            ),
        ),
        call.build_response(
            context=expected_context,
            execution=execution,
            projection=ProjectionContractSnapshot.from_capability(selected),
            confirmation=None,
        ),
    ]
    assert registry.mock_calls == [call.list(status="active")]
    assert h.gateway.calls == []
    events = [event for event in h.tasks.events if event.event_type == "capability_selected"]
    assert [event.payload for event in events] == [
        {
            "capability_id": h.capability.capability_id,
            "selection_rule": "unique_intent_tag",
        }
    ]
    assert [
        event["attributes"]["selection_rule"]
        for event in h.trace.steps
        if event["event_type"] == "capability_selected"
    ] == ["unique_intent_tag"]


def test_resume_uses_port_after_decision_and_returns_its_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        h = _harness()
        await _start(h)
        pending = _pending(h)
        events: list[str] = []
        response_entry = Mock(wraps=h.runtime._build_envelope)
        monkeypatch.setattr(h.runtime, "_build_envelope", response_entry)
        original_decision = h.runtime._record_confirmation_decision
        original_resume = h.runtime._orchestration.resume_capability
        original_build = h.runtime._orchestration.build_response
        responses: list[Any] = []

        async def decision(*args: Any, **kwargs: Any) -> Any:
            result = await original_decision(*args, **kwargs)
            events.append("decision")
            return result

        async def resume(**kwargs: Any) -> Any:
            events.append("resume")
            assert kwargs == {
                "task_id": pending.task_id,
                "confirmed": True,
                "expected_action_digest": pending.action_digest,
            }
            assert h.gate.record_decision_calls == 1
            return await original_resume(**kwargs)

        def build(**kwargs: Any) -> Any:
            events.append("response")
            result = original_build(**kwargs).model_copy(
                update={"message": "PORT_RESUMED_RESPONSE"}
            )
            responses.append(result)
            return result

        monkeypatch.setattr(h.runtime, "_record_confirmation_decision", decision)
        monkeypatch.setattr(h.runtime._orchestration, "resume_capability", resume)
        monkeypatch.setattr(h.runtime._orchestration, "build_response", build)
        result = await _confirm(h)
        assert events == ["decision", "resume", "response"]
        assert response_entry.call_count == 1
        assert result.message == responses[0].message == "PORT_RESUMED_RESPONSE"
        assert result.data == {"action_outcome": "accepted", "result": {"result": "first"}}
        assert h.engine.resume_calls == _business_count(h) == 1
        again = await _confirm(h)
        assert again.data["action_outcome"] == "action_already_claimed"
        assert events == ["decision", "resume", "response"]
        assert _business_count(h) == 1

    asyncio.run(exercise())


def test_waiting_without_runtime_workflow_port_cannot_create_human_gate_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = _harness()

    async def inconsistent_execution(**kwargs: Any) -> ExecutionResult:
        # Bind legitimately first, then expose the missing control-side engine.
        # Otherwise the missing-manifest guard would mask the engine guard.
        h.runtime._workflow_engine = None
        return ExecutionResult(status="waiting_user", trace_id="synthetic")

    execute = AsyncMock(side_effect=inconsistent_execution)
    monkeypatch.setattr(h.runtime._orchestration, "execute_capability", execute)
    create = AsyncMock(wraps=h.gate.create_request)
    monkeypatch.setattr(h.gate, "create_request", create)
    error: Exception | None = None
    result = None
    try:
        result = asyncio.run(_start(h))
    except Exception as exc:
        error = exc
    assert error is None, "Missing Runtime WorkflowPort must produce a failed response"
    assert result is not None and result.status == "failed"
    assert h.tasks.status_updates[-1] == ("failed", "internal_error")
    assert create.await_args_list == []
    assert h.runtime._pending_workflows == {}
    assert h.gateway.calls == []
    assert execute.await_count == 1


@pytest.mark.parametrize("mismatch", ["missing", "id", "target"])
def test_resumed_preview_contract_error_propagates_without_discard_or_binding_outcome(
    mismatch: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        h = _harness(two=True)
        await _start(h)
        pending = _pending(h)
        key = (h.session_id, h.principal.ai_user_id)
        claim_key = _pending_confirmation_claim_key(key, pending)
        port = h.runtime._orchestration
        workflow_port = h.runtime._workflow_engine
        assert isinstance(port, AgentOrchestrationAdapter)
        assert isinstance(workflow_port, WorkflowEngineAdapter)
        resume = AsyncMock(wraps=workflow_port.resume)
        discard = Mock(wraps=workflow_port.discard_checkpoint)
        retire = AsyncMock(wraps=h.runtime._retire_pending_confirmation)
        archive = Mock(wraps=h.runtime._archive_confirmation)
        failed = Mock(wraps=h.runtime._response_builder.build_failed)
        monkeypatch.setattr(workflow_port, "resume", resume)
        monkeypatch.setattr(workflow_port, "discard_checkpoint", discard)
        monkeypatch.setattr(h.runtime, "_retire_pending_confirmation", retire)
        monkeypatch.setattr(h.runtime, "_archive_confirmation", archive)
        monkeypatch.setattr(h.runtime._response_builder, "build_failed", failed)
        original_build = port.build_response
        captured: dict[str, Any] = {}

        def invalid_preview(**kwargs: Any) -> Any:
            assert kwargs["execution"].status == "waiting_user"
            assert _business_count(h) == 1
            captured["checkpoint"] = h.engine._checkpoints[pending.task_id]
            captured["claim"] = h.runtime._claimed_pending_confirmations[claim_key]
            preview = kwargs["confirmation"]
            kwargs["confirmation"] = {
                "missing": None,
                "id": replace(preview, capability_id="oa.other"),
                "target": replace(preview, target_system="u8"),
            }[mismatch]
            try:
                return original_build(**kwargs)
            except OrchestrationContractError as error:
                captured["error"] = error
                raise

        monkeypatch.setattr(port, "build_response", invalid_preview)
        with pytest.raises(OrchestrationContractError) as caught:
            await _confirm(h)
        assert caught.value is captured["error"]
        assert resume.await_args_list == [
            call(
                task_id=pending.task_id,
                confirmed=True,
                expected_action_digest=pending.action_digest,
            )
        ]
        assert h.engine.resume_calls == _business_count(h) == 1
        assert discard.call_args_list == []
        assert h.engine._checkpoints[pending.task_id] is captured["checkpoint"]
        assert h.runtime._pending_workflows[key] is pending
        assert claim_key in h.runtime._claimed_pending_confirmations
        claim = h.runtime._claimed_pending_confirmations[claim_key]
        assert claim is captured["claim"]
        assert claim.state == "processing"
        assert claim.pending is pending
        assert claim.cleanup_complete is False
        assert retire.await_args_list == archive.call_args_list == failed.call_args_list == []
        assert h.tasks.records[pending.task_id].status == "waiting_user"
        again = await _confirm(h)
        assert again.data["action_outcome"] == "action_already_claimed"
        assert h.engine.resume_calls == _business_count(h) == 1
        assert discard.call_args_list == []
        assert h.engine._checkpoints[pending.task_id] is captured["checkpoint"]

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "fault", ["none", "registry-mutation", "selected-schema-drift", "wrong-projection-binding"]
)
def test_selected_snapshot_and_binding_remain_consistent_across_seam(
    fault: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = _harness(workflow=False)
    source_schema = copy.deepcopy(h.capability.output_schema)
    original = h.runtime._orchestration.resolve_task_version_bindings
    snapshots: list[Any] = []

    async def resolve(**kwargs: Any) -> Any:
        selected = kwargs["capability"]
        if fault == "registry-mutation":
            h.capability.output_schema["properties"]["result"]["type"] = "integer"
        if fault == "selected-schema-drift":
            # Content changes while the declared digest deliberately stays fixed.
            selected.output_schema["properties"]["result"]["type"] = "integer"
        result = await original(**kwargs)
        snapshots.append((selected, result))
        if fault == "wrong-projection-binding":
            return replace(
                result,
                projection_binding=result.projection_binding.model_copy(
                    update={"resource_id": "oa.other"},
                ),
            )
        return result

    execute = AsyncMock(wraps=h.runtime._orchestration.execute_capability)
    build = Mock(wraps=h.runtime._orchestration.build_response)
    monkeypatch.setattr(h.runtime._orchestration, "resolve_task_version_bindings", resolve)
    monkeypatch.setattr(h.runtime._orchestration, "execute_capability", execute)
    monkeypatch.setattr(h.runtime._orchestration, "build_response", build)
    result = asyncio.run(_start(h))
    assert len(snapshots) == 1
    selected, bindings = snapshots[0]
    if fault in {"none", "registry-mutation"}:
        assert result.status == "completed"
        assert result.data == {"result": "plain"}
        assert selected.output_schema == source_schema
        assert selected is not h.capability
        assert execute.await_count == 1
        projection = build.call_args.kwargs["projection"]
        assert projection.load_output_schema() == source_schema
        assert projection.matches(selected)
        manifest = asyncio.run(h.gate.get_task_binding(result.task_id))
        assert manifest.bindings == bindings.bindings
        assert bindings.projection_binding.resource_id == selected.capability_id
    else:
        assert result.status == "failed"
        assert h.tasks.status_updates[-1] == ("failed", "internal_error")
        assert "版本" in result.message
        assert execute.await_args_list == []
        assert h.gateway.calls == []


@pytest.mark.parametrize("with_gate", [False, True])
def test_resume_uses_saved_projection_without_registry_lookup(
    with_gate: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        h = _harness(with_gate=with_gate, two=True)
        await _start(h)
        first_pending = _pending(h)
        snapshot = first_pending.projection_snapshot
        if with_gate:
            h.registry.items[h.capability.capability_id] = h.capability.model_copy(
                update={
                    "output_schema": {"type": "object", "properties": {"new": {"type": "string"}}}
                },
                deep=True,
            )
            result = await _confirm(h)
            assert result.data["action_outcome"] == "action_version_conflict"
            assert h.engine.resume_calls == _business_count(h) == 0
            return
        h.registry.items[h.capability.capability_id] = h.capability.model_copy(
            update={"output_schema": {"type": "object", "properties": {"new": {"type": "string"}}}},
            deep=True,
        )
        original_build = h.runtime._orchestration.build_response
        get = AsyncMock(wraps=h.registry.get)
        listing = AsyncMock(wraps=h.registry.list)
        monkeypatch.setattr(h.registry, "get", get)
        monkeypatch.setattr(h.registry, "list", listing)
        projections: list[Any] = []

        def response(**kwargs: Any) -> Any:
            before = (get.await_count, listing.await_count)
            projections.append(kwargs["projection"])
            result = original_build(**kwargs)
            assert (get.await_count, listing.await_count) == before
            return result

        monkeypatch.setattr(h.runtime._orchestration, "build_response", response)
        second = await _text_confirm(h)
        assert second.status == "waiting_user"
        assert _pending(h).projection_snapshot == snapshot
        final = await _text_confirm(h)
        assert final.status == "completed"
        assert final.data == {"result": "second"}
        assert projections == [snapshot, snapshot]
        assert h.capability.capability_id not in [args.args[0] for args in get.await_args_list]
        assert listing.await_args_list == []

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "path", ["plain-policy", "first-gate", "first-no-gate", "resume-gate", "resume-no-gate"]
)
def test_confirmation_digest_and_builder_input_share_values_not_identity(
    path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        h = _harness(
            workflow=path != "plain-policy",
            with_gate="no-gate" not in path,
            two=path.startswith("resume"),
        )
        if path == "plain-policy":
            h.gateway.results[h.capability.capability_id] = ExecutionResult(
                status="waiting_user",
                error_code="confirm_required",
                trace_id="policy-confirm",
            )
        if path.startswith("resume"):
            await _start(h)
        original_prepare = h.runtime._orchestration.prepare_confirmation
        prepare = Mock(wraps=original_prepare)
        payloads: list[dict[str, Any]] = []
        digests: list[dict[str, Any]] = []
        original_builder = h.runtime._orchestration._response_builder.build_confirm_card
        original_digest = runtime_module.immutable_request_digest

        def build(*args: Any, **kwargs: Any) -> Any:
            payloads.append(kwargs["payload"])
            return original_builder(*args, **kwargs)

        def digest(**kwargs: Any) -> str:
            digests.append(kwargs)
            return original_digest(**kwargs)

        monkeypatch.setattr(h.runtime._orchestration, "prepare_confirmation", prepare)
        monkeypatch.setattr(h.runtime._orchestration._response_builder, "build_confirm_card", build)
        monkeypatch.setattr(runtime_module, "immutable_request_digest", digest)
        if path == "resume-gate":
            result = await _confirm(h)
        elif path == "resume-no-gate":
            result = await _text_confirm(h)
        else:
            result = await _start(h)
        resumed = path.startswith("resume")
        expected = {
            "capability_id": h.capability.capability_id,
            "operation_summary": "" if resumed else "合成操作：核对后提交",
            "target_system": "oa",
            "field_names": [] if resumed else ["amount", "remark"],
            "displayed_argument_values": {} if resumed else {"remark": "合成说明", "amount": "12"},
        }
        assert prepare.call_args_list == [
            call(
                capability_id=h.capability.capability_id,
                arguments={} if resumed else h.arguments,
                capability=None if resumed else h.capability,
            )
        ]
        assert payloads == [expected]
        assert result.model_dump()["ui"]["payload"] == expected
        assert result.status == "waiting_user"
        if path in {"first-gate", "resume-gate"}:
            pending = _pending(h)
            request = await h.gate.get_request(pending.gate_request_id)
            assert len(digests) == 1
            assert digests[0]["preview"] == payloads[0]
            assert digests[0]["preview"] is not payloads[0]
            assert request.request_digest == immutable_request_digest(
                task_id=pending.task_id,
                action_digest=pending.action_digest,
                preview=expected,
                binding_manifest_digest=pending.binding_manifest_digest,
            )
        else:
            assert digests == []

    asyncio.run(exercise())


def test_preview_marker_preserves_pre_sanitize_digest_and_post_sanitize_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        h = _harness(arguments={"remark": "SYNTHETIC_token_canary", "amount": 12})
        payloads: list[Any] = []
        original = h.runtime._orchestration._response_builder.build_confirm_card

        def build(*args: Any, **kwargs: Any) -> Any:
            payloads.append(copy.deepcopy(kwargs["payload"]))
            return original(*args, **kwargs)

        monkeypatch.setattr(h.runtime._orchestration._response_builder, "build_confirm_card", build)
        result = await _start(h)
        pending = _pending(h)
        request = await h.gate.get_request(pending.gate_request_id)
        expected = {
            "capability_id": h.capability.capability_id,
            "operation_summary": "合成操作：核对后提交",
            "target_system": "oa",
            "field_names": ["amount", "remark"],
            "displayed_argument_values": {"remark": "SYNTHETIC_token_canary", "amount": "12"},
        }
        assert payloads == [expected]
        assert request.request_digest == immutable_request_digest(
            task_id=pending.task_id,
            action_digest=pending.action_digest,
            preview=expected,
            binding_manifest_digest=pending.binding_manifest_digest,
        )
        final_payload = result.model_dump()["ui"]["payload"]
        assert final_payload["displayed_argument_values"] == {
            "remark": "[REDACTED]",
            "amount": "12",
        }
        assert "SYNTHETIC_token_canary" not in result.model_dump_json()
        assert request.request_digest != immutable_request_digest(
            task_id=pending.task_id,
            action_digest=pending.action_digest,
            preview=final_payload,
            binding_manifest_digest=pending.binding_manifest_digest,
        )

    asyncio.run(exercise())


@pytest.mark.parametrize("fault", ["gate", "publish"])
def test_gate_or_publish_failure_drops_preview_and_keeps_failure(
    fault: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = _harness()
    build = Mock(wraps=h.runtime._orchestration.build_response)
    discard = Mock(wraps=h.runtime._workflow_engine.discard_checkpoint)
    monkeypatch.setattr(h.runtime._orchestration, "build_response", build)
    monkeypatch.setattr(h.runtime._workflow_engine, "discard_checkpoint", discard)
    if fault == "gate":
        monkeypatch.setattr(
            h.gate,
            "create_request",
            AsyncMock(side_effect=HumanGateConflictError("synthetic conflict")),
        )
    else:
        monkeypatch.setattr(h.runtime, "_publish_pending_workflow", Mock(return_value=False))
    result = asyncio.run(_start(h))
    assert result.status == "failed"
    assert result.ui.component_type != "confirm_card"
    assert h.tasks.status_updates[-1] == ("failed", "internal_error")
    assert build.call_count == 1
    assert build.call_args.kwargs["execution"].status == "failed"
    assert build.call_args.kwargs["confirmation"] is None
    assert discard.call_args_list == [call(result.task_id)]
    assert result.task_id not in h.engine._checkpoints


@pytest.mark.parametrize(
    "fault", ["reject", "cancel", "expired", "exception", "cancelled-error", "cleanup-failure"]
)
def test_reject_cancel_expiry_and_exception_keep_terminal_lifecycle_through_seam(
    fault: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        h = _harness()
        await _start(h)
        pending = _pending(h)
        key = (h.session_id, h.principal.ai_user_id)
        claim_key = _pending_confirmation_claim_key(key, pending)
        resume = AsyncMock(wraps=h.runtime._orchestration.resume_capability)
        monkeypatch.setattr(h.runtime._orchestration, "resume_capability", resume)
        injected = (
            asyncio.CancelledError("synthetic cancelled execution")
            if fault == "cancelled-error"
            else RuntimeError("synthetic execution failure")
        )
        original_gateway = h.gateway.execute_capability

        async def execute(*args: Any, **kwargs: Any) -> Any:
            if args[3] == "oa.structured.execute":
                h.gateway.calls.append((args[3], args[4]))
                raise injected
            return await original_gateway(*args, **kwargs)

        if fault in {"exception", "cancelled-error", "cleanup-failure"}:
            monkeypatch.setattr(h.gateway, "execute_capability", execute)
        cleanup_error = RuntimeError("synthetic cleanup write failure")
        if fault == "cleanup-failure":

            async def fail_cleanup(*args: Any, **kwargs: Any) -> Any:
                raise cleanup_error

            monkeypatch.setattr(h.tasks, "update_status", fail_cleanup)
        if fault == "expired":
            h.runtime._monotonic_clock = lambda: pending.monotonic_deadline + 0.001
            h.runtime._utc_clock = lambda: pending.expires_at + timedelta(milliseconds=1)

        async def dispatch() -> Any:
            if fault in {"reject", "cancel"}:
                model = RejectUserAction if fault == "reject" else CancelUserAction
                return await h.runtime.handle_user_action(
                    channel="web",
                    principal=h.principal,
                    session_id=h.session_id,
                    action=model(action_type=fault, response_id=pending.response_id),
                )
            return await _confirm(h)

        if fault in {"cancelled-error", "cleanup-failure"}:
            expected_error = injected if fault == "cancelled-error" else cleanup_error
            with pytest.raises(type(expected_error)) as caught:
                await dispatch()
            assert caught.value is expected_error
        else:
            response = await dispatch()
            expected_status = (
                "cancelled" if fault in {"reject", "cancel"} else "confirmation_invalidated"
            )
            assert response.status == response.data["action_outcome"] == expected_status
            assert response.data["result"] is None
        assert key not in h.runtime._pending_workflows
        assert pending.task_id not in h.engine._checkpoints
        claim = h.runtime._claimed_pending_confirmations[claim_key]
        assert claim.state == (
            "cancelled" if fault in {"reject", "cancel"} else "confirmation_invalidated"
        )
        assert claim.cleanup_complete is (fault != "cleanup-failure")
        expected_executions = 0 if fault in {"reject", "cancel", "expired"} else 1
        assert (
            resume.await_count == h.engine.resume_calls == _business_count(h) == expected_executions
        )
        terminal_events = [
            event
            for event in h.trace.steps
            if event["task_id"] == pending.task_id
            and event["event_type"]
            in {"task_cancelled", "task_confirmation_invalidated", "evaluation_recorded"}
        ]
        original_finalizations = [
            event for event in h.trace.finalizations if event["args"][1] == pending.task_id
        ]
        if fault == "cleanup-failure":
            assert terminal_events == original_finalizations == []
            assert h.tasks.records[pending.task_id].status == "waiting_user"
            with pytest.raises(RuntimeError, match="cleanup is incomplete"):
                await _confirm(h)
        else:
            status = "cancelled" if fault in {"reject", "cancel"} else "confirmation_invalidated"
            error = (
                None
                if status == "cancelled"
                else "confirm_required"
                if fault == "expired"
                else "internal_error"
            )
            assert h.tasks.status_updates[-1] == (status, error)
            assert [event["event_type"] for event in terminal_events] == [
                "task_cancelled" if status == "cancelled" else "task_confirmation_invalidated",
                "evaluation_recorded",
            ]
            assert {
                (event["tenant_id"], event["ai_user_id"], event["session_id"])
                for event in terminal_events
            } == {
                ("seam-tenant", "seam-user", "seam-session"),
            }
            assert len(original_finalizations) == 1
            assert original_finalizations[0]["tenant_id"] == "seam-tenant"
            assert original_finalizations[0]["ai_user_id"] == "seam-user"
            replay = await _confirm(h)
            assert replay.data["action_outcome"] == status
        assert h.engine.resume_calls == _business_count(h) == expected_executions

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("site", "cas_winner"),
    [
        ("initial-gate", True),
        ("initial-publish", True),
        ("resume-request", True),
        ("resume-request", False),
        ("retire", True),
        ("retire", False),
        ("process-exception", True),
        ("process-exception", False),
    ],
)
def test_all_five_checkpoint_cleanup_sites_forward_and_preserve_cas_winner(
    site: str,
    cas_winner: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        h = _harness(two=site == "resume-request")
        assert isinstance(h.runtime._workflow_engine, WorkflowEngineAdapter)
        discard = Mock(wraps=h.runtime._workflow_engine.discard_checkpoint)
        monkeypatch.setattr(h.runtime._workflow_engine, "discard_checkpoint", discard)
        key = (h.session_id, h.principal.ai_user_id)
        preserved: dict[str, Any] = {}
        if site == "initial-gate":
            monkeypatch.setattr(
                h.gate,
                "create_request",
                AsyncMock(side_effect=HumanGateConflictError("synthetic conflict")),
            )
            result = await _start(h)
            assert result.status == "failed"
            retired_task_id = result.task_id
        elif site == "initial-publish":
            await _start(h)
            winner = _pending(h)
            winner_checkpoint = h.engine._checkpoints[winner.task_id]
            monkeypatch.setattr(h.runtime, "_publish_pending_workflow", Mock(return_value=False))
            result = await _start(h)
            retired_task_id = result.task_id
            assert result.status == "failed"
            assert retired_task_id != winner.task_id
            assert h.runtime._pending_workflows[key] is winner
            assert h.engine._checkpoints[winner.task_id] is winner_checkpoint
        else:
            await _start(h)
            pending = _pending(h)
            retired_task_id = pending.task_id

            def install_winner() -> None:
                if not cas_winner:
                    winner = replace(
                        pending, response_id="synthetic-winner", gate_request_id="synthetic-winner"
                    )
                    h.runtime._pending_workflows[key] = winner
                    preserved["pending"] = winner
                    preserved["checkpoint"] = h.engine._checkpoints[pending.task_id]

            if site == "resume-request":

                async def request_failure(*args: Any, **kwargs: Any) -> Any:
                    install_winner()
                    raise HumanGateConflictError("synthetic next request conflict")

                monkeypatch.setattr(h.gate, "create_request", request_failure)
                result = await _confirm(h)
                assert result.status == "failed"
                assert h.engine.resume_calls == _business_count(h) == 1
            elif site == "retire":
                original_decision = h.runtime._record_confirmation_decision

                async def decision(*args: Any, **kwargs: Any) -> Any:
                    result = await original_decision(*args, **kwargs)
                    install_winner()
                    return result

                monkeypatch.setattr(h.runtime, "_record_confirmation_decision", decision)
                result = await h.runtime.handle_user_action(
                    channel="web",
                    principal=h.principal,
                    session_id=h.session_id,
                    action=RejectUserAction(action_type="reject", response_id=pending.response_id),
                )
                assert result.status == "cancelled"
                assert h.engine.resume_calls == _business_count(h) == 0
            else:

                async def binding_failure(*args: Any, **kwargs: Any) -> Any:
                    install_winner()
                    raise VersionBindingMismatchError("synthetic binding conflict")

                monkeypatch.setattr(h.gate, "assert_task_bindings", binding_failure)
                result = await _confirm(h)
                assert result.data["action_outcome"] == "action_version_conflict"
                assert h.engine.resume_calls == _business_count(h) == 0
        if cas_winner:
            assert discard.call_args_list == [call(retired_task_id)]
            assert retired_task_id not in h.engine._checkpoints
            if site != "initial-publish":
                assert key not in h.runtime._pending_workflows
        else:
            assert discard.call_args_list == []
            assert h.runtime._pending_workflows[key] is preserved["pending"]
            assert h.engine._checkpoints[retired_task_id] is preserved["checkpoint"]
        if site != "resume-request":
            assert _business_count(h) == 0

    asyncio.run(exercise())


def test_runtime_response_entry_is_only_port_forwarding() -> None:
    source = Path(runtime_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    runtime_class = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "RuntimeImpl"
    )
    entry = next(
        node
        for node in runtime_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "_build_envelope"
    )
    assert [ast.unparse(node.func) for node in ast.walk(entry) if isinstance(node, ast.Call)] == [
        "self._orchestration.build_response",
        "AgentResponseContext",
    ]
    assert not any(isinstance(node, (ast.If, ast.IfExp, ast.Match)) for node in ast.walk(entry))
    entry_callers = [
        method.name
        for method in runtime_class.body
        if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
        for node in ast.walk(method)
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "self._build_envelope"
    ]
    assert sorted(entry_callers) == ["_resume_pending_workflow", "handle_user_message"]
    builder_owners = [
        method.name
        for method in runtime_class.body
        if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
        for node in ast.walk(method)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and ast.unparse(node.func.value) == "self._response_builder"
    ]
    assert sorted(builder_owners) == sorted(
        [
            "_finish_user_action_attempt",
            "_confirmation_terminal_envelope",
            "_process_pending_confirmation",
            "_build_stale_confirmation_response",
            "_resume_pending_workflow",
            "_finish_version_binding_failure",
            "_finish_intent_failure",
            "_finish_no_capability_found",
        ]
    )
    runtime = RuntimeImpl.__new__(RuntimeImpl)
    port = create_autospec(AgentOrchestrationPort, instance=True, spec_set=True)
    runtime._orchestration = port
    execution = ExecutionResult(status="waiting_user", trace_id="execution-trace")
    preview = ConfirmationPreview("oa.synthetic.action", "合成", "oa", (), ())
    sentinel = ResponseEnvelopeBuilder().build_message(
        "response", "task", "session", "sentinel", "sentinel", "trace"
    )
    port.build_response.return_value = sentinel
    returned = runtime._build_envelope(
        "response",
        "task",
        "session",
        execution,
        "trace",
        CapabilityRef(capability_id="oa.synthetic.action"),
        confirmation=preview,
    )
    assert returned is sentinel
    assert port.mock_calls == [
        call.build_response(
            context=AgentResponseContext(
                "response", "task", "session", "trace", "oa.synthetic.action"
            ),
            execution=execution,
            projection=None,
            confirmation=preview,
        )
    ]
