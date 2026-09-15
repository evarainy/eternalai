"""Production wiring and read-overview components, with explicit store fidelity."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.composition import build_production_components, build_runtime
from app.config import ProductionSettings
from app.infra.adapters.oa.capabilities import expected_oa_capabilities
from app.infra.gateway.capability_gateway import CapabilityGateway
from app.infra.human_gate.in_memory import InMemoryHumanGate
from app.infra.llm.json_structured_output import JSONStructuredOutputProvider
from app.infra.llm.mock_llm.mock_llm_provider import MockLLMProvider
from app.infra.policy.minimal_policy_guard import MinimalPolicyGuard
from app.infra.workflow.catalog import OVERVIEW_ID, production_workflow_capabilities
from app.infra.workflow.production import validate_production_workflows, validate_selected_workflow
from app.main import create_production_app
from app.ports.adapter import AdapterResult
from app.ports.identity_mapping import IdentityCheckResult
from app.ports.llm_provider import LLMCompletionResponse
from app.ports.policy_guard import PolicyDecision
from app.workflow.definitions import production_workflow_definitions
from app.workflow.engine import WorkflowEngine
from tests.runtime.principal_fakes import runtime_principal
from tests.runtime.registry_fakes import StaticCapabilityRegistry
from tests.runtime.test_runtime_workflow import SessionStore, TaskStore, Trace

PENDING_ID = "oa.list_pending_workflows"
MESSAGES_ID = "oa.list_system_messages"
MESSAGE = "查看 OA 待办与系统消息概览 oa.read_overview"


def overview_data(*, complete=False, empty=False):
    pending = (
        []
        if empty
        else [
            {
                "todo_id": "synthetic-todo",
                "title": "待办甲",
                "status": "待处理",
                "received_at": "2026-09-16",
                "created_at": "2026-09-15",
                "workflow_type_id": "synthetic-type",
            }
        ]
    )
    messages = (
        []
        if empty
        else [
            {
                "message_id": "synthetic-message",
                "title": "消息乙",
                "content": "synthetic-content",
                "source_name": "synthetic-source",
                "occurred_at": "2026-09-16",
                "business_state": "unread",
                "link": None,
                "mobile_link": None,
            }
        ]
    )
    return {
        "pending": {
            "workflows": pending,
            "returned_count": len(pending),
            "authoritative_count": len(pending),
            "is_complete": True,
        },
        "messages": {
            "messages": messages,
            "returned_count": len(messages),
            "is_complete": complete,
        },
    }


class ReadAdapter:
    def __init__(self, data=None):
        self.data = overview_data() if data is None else data
        self.calls = []
        self.failure_id = None
        self.failure = None
        self.after_first = None

    async def execute(self, capability_id, arguments, execution_context):
        self.calls.append((capability_id, deepcopy(arguments), deepcopy(execution_context)))
        if capability_id == self.failure_id:
            return self.failure
        if len(self.calls) == 1 and self.after_first:
            self.after_first()
        key = "pending" if capability_id == PENDING_ID else "messages"
        return AdapterResult(status="success", data=deepcopy(self.data[key]))


class Identity:
    def __init__(self):
        self.calls = []
        self.failure_index = None
        self.bind_status = "active"

    async def resolve_execution_identity(
        self, ai_user_id, target_system, execution_identity, request_context
    ):
        self.calls.append((ai_user_id, request_context))
        return IdentityCheckResult(
            bind_status=self.bind_status if len(self.calls) == self.failure_index else "active",
            binding_id="synthetic-binding",
            target_system=target_system,
            execution_identity=execution_identity,
        )


class OwnedTrace(Trace):
    def __init__(self):
        super().__init__()
        self.owners = []

    async def record_step(self, *args, **kwargs):
        self.owners.append((kwargs.get("tenant_id"), kwargs.get("ai_user_id")))
        await super().record_step(*args, **kwargs)

    async def record_gateway_call(self, *args, **kwargs):
        await self.record_step(*args, event_type="gateway_pre_recorded", **kwargs)


def component_harness(*, data=None, arguments=None):
    """Real Runtime/engine/Gateway/Policy, explicitly in-memory persistence."""
    definitions = production_workflow_definitions()
    descriptors = {item.capability_id: item for item in production_workflow_capabilities()}
    registry = StaticCapabilityRegistry(*expected_oa_capabilities(), *descriptors.values())
    store, trace, gate = TaskStore(), OwnedTrace(), InMemoryHumanGate()
    adapter, identity, llm = ReadAdapter(data), Identity(), MockLLMProvider()
    llm.register(
        MESSAGE,
        LLMCompletionResponse(
            content=json.dumps(
                {
                    "match": "capability",
                    "capability_id": OVERVIEW_ID,
                    "capability_type": "workflow",
                    "arguments": {} if arguments is None else arguments,
                }
            )
        ),
    )
    gateway = CapabilityGateway(
        capability_registry=registry,
        identity_mapping=identity,
        policy_guard=MinimalPolicyGuard(),
        adapters={"oa": adapter},
        trace_port=trace,
        human_gate_port=gate,
    )
    gateway.execute_capability = AsyncMock(wraps=gateway.execute_capability)
    engine = WorkflowEngine(
        definitions=definitions,
        capability_registry=registry,
        gateway=gateway,
        task_store=store,
        trace_port=trace,
        human_gate_port=gate,
    )
    validator = partial(
        validate_selected_workflow,
        registry=registry,
        definitions=definitions,
        descriptors=descriptors,
    )
    runtime = build_runtime(
        task_store=store,
        session_store=SessionStore(),
        capability_registry=registry,
        gateway=gateway,
        trace_port=trace,
        llm_provider=llm,
        structured_output=JSONStructuredOutputProvider(),
        intent_model="synthetic-intent",
        workflow_engine=engine,
        validate_workflow=validator,
        human_gate_port=gate,
    )
    return SimpleNamespace(**locals())


@pytest.mark.parametrize("fault", ["gate", "engine", "empty", "key", "steps", "confirmed"])
def test_production_builder_rejects_missing_shared_gate_or_invalid_definitions(monkeypatch, fault):
    import app.composition as composition

    if fault == "gate":
        monkeypatch.setattr(composition, "PostgreSQLHumanGate", lambda *args: None)
    elif fault == "engine":
        monkeypatch.setattr(composition, "WorkflowEngine", lambda **kwargs: None)
    else:
        definitions = production_workflow_definitions()
        definition = definitions[OVERVIEW_ID]
        if fault == "empty":
            definitions = {}
        elif fault == "key":
            definitions = {"oa.wrong": definition}
        elif fault == "steps":
            definitions[OVERVIEW_ID] = replace(definition, steps=())
        else:
            definitions[OVERVIEW_ID] = replace(
                definition,
                steps=(
                    replace(definition.steps[0], confirmed_capability_id="oa.synthetic_action"),
                    definition.steps[1],
                ),
            )
        monkeypatch.setattr(composition, "production_workflow_definitions", lambda: definitions)
    with pytest.raises(RuntimeError, match="^workflow_configuration_invalid$"):
        build_production_components(
            replace(
                ProductionSettings.from_environment(),
                oa_read_adapter_mode="mock",
            )
        )


async def run_overview(
    h, *, user="synthetic-reader", tenant="synthetic-tenant", sid="synthetic-chat"
):
    return await h.runtime.handle_user_message(
        channel="web",
        principal=runtime_principal(user, tenant_id=tenant),
        session_id=sid,
        message=MESSAGE,
        client_capabilities={},
    )


@pytest.mark.parametrize("fail", [False, True])
def test_production_lifespan_runs_validation_before_schedulers(monkeypatch, fail) -> None:
    import app.composition as composition

    order = []

    # Real create_production_app and builder. Only external scheduling and Registry I/O
    # are intercepted here; persisted HTTP execution is a separate DB test.
    async def list_rows(self, **kwargs):
        order.append("validate")
        if fail:
            return [production_workflow_capabilities()[0].model_copy(update={"version": "9.0.0"})]
        return []

    monkeypatch.setattr(composition.PostgreSQLCapabilityRegistry, "list", list_rows)
    monkeypatch.setattr(
        composition.PostgreSQLCapabilityRegistry, "get", AsyncMock(return_value=None)
    )
    for cls, label in (
        (composition.CredentialPollingScheduler, "poll"),
        (composition.OrganizationDirectoryScheduler, "directory"),
    ):

        async def start(self, label=label):
            order.append(label)

        async def stop(self, label=label):
            order.append(f"stop-{label}")

        monkeypatch.setattr(cls, "start", start)
        monkeypatch.setattr(cls, "stop", stop)
    application = create_production_app(
        replace(
            ProductionSettings.from_environment(),
            oa_read_adapter_mode="mock",
        )
    )

    async def exercise():
        async with application.router.lifespan_context(application):
            order.append("ready")

    if fail:
        with pytest.raises(RuntimeError, match="^workflow_contract_mismatch$"):
            asyncio.run(exercise())
        assert order == ["validate"]
    else:
        asyncio.run(exercise())
        assert order == ["validate", "poll", "directory", "ready", "stop-directory", "stop-poll"]


@pytest.mark.parametrize("complete,empty", [(False, False), (True, False), (True, True)])
def test_overview_validates_aggregate_and_preserves_completeness(complete, empty) -> None:
    h = component_harness(data=overview_data(complete=complete, empty=empty))
    response = asyncio.run(run_overview(h))
    assert response.status == "completed"
    assert response.data == h.adapter.data
    assert h.store.status_updates[-1] == ("completed", None)
    assert [call[0] for call in h.adapter.calls] == [PENDING_ID, MESSAGES_ID]
    assert h.gateway.execute_capability.await_count == 2
    assert "系统消息返回" in response.message
    assert ("可能还有更多消息" in response.message) is (not complete)
    if empty:
        assert "OA 待办 0 条" in response.message
    else:
        assert "待办甲" in response.message and "消息乙" in response.message
    assert "synthetic-content" not in response.message
    prompt = "\n".join(message.content for message in h.llm.calls[0]["messages"])
    assert all(capability_id in prompt for capability_id in (OVERVIEW_ID, PENDING_ID, MESSAGES_ID))
    assert "additionalProperties" in prompt
    events = [event.event_type for event in h.store.events]
    assert events.count("workflow_started") == events.count("workflow_completed") == 1
    assert events.count("workflow_step_finished") == 2


@pytest.mark.parametrize("key", ["ai_user_id", "tenant_id", "session_id", "scope", "unknown"])
def test_overview_rejects_extra_input_before_leaf_calls(key) -> None:
    h = component_harness(arguments={key: "synthetic-injected-owner"})
    response = asyncio.run(run_overview(h))
    assert response.status == "failed"
    assert h.store.status_updates[-1] == ("failed", "internal_error")
    assert h.gateway.execute_capability.await_count == 0
    assert h.adapter.calls == []
    assert "synthetic-injected-owner" not in response.model_dump_json()
    assert "synthetic-injected-owner" not in json.dumps(h.trace.steps)


@pytest.mark.parametrize("step", [PENDING_ID, MESSAGES_ID])
@pytest.mark.parametrize(
    "failure,error,envelope,attempts",
    [
        ("policy", "policy_denied", "blocked", 1),
        ("unbound", "identity_unbound", "failed", 1),
        ("expired", "identity_expired", "failed", 1),
        ("revoked", "identity_revoked", "failed", 1),
        ("needs_binding_scope", "needs_binding_scope", "failed", 1),
        ("adapter", "adapter_payload_invalid", "failed", 1),
        ("timeout", "adapter_timeout", "failed", 2),
    ],
)
def test_overview_short_circuits_each_failure(step, failure, error, envelope, attempts) -> None:
    h = component_harness()
    before = int(step == MESSAGES_ID)
    if failure == "policy":
        original = h.gateway._policy_guard.decide

        async def decide(**kwargs):
            if kwargs["capability_id"] == step:
                return PolicyDecision(decision="deny", reason_code="synthetic-denial")
            return await original(**kwargs)

        h.gateway._policy_guard.decide = decide
    elif failure in {"unbound", "expired", "revoked", "needs_binding_scope"}:
        h.identity.failure_index = before + 1
        h.identity.bind_status = failure
    else:
        h.adapter.failure_id = step
        h.adapter.failure = AdapterResult(
            status="timeout" if failure == "timeout" else "error", error_code=error
        )
    response = asyncio.run(run_overview(h))
    assert response.status == envelope
    assert response.data is None
    assert h.store.status_updates[-1] == ("failed", error)
    assert h.gateway.execute_capability.await_count == before + attempts
    expected_adapter_calls = before + (attempts if failure in {"adapter", "timeout"} else 0)
    assert len(h.adapter.calls) == expected_adapter_calls
    if step == PENDING_ID:
        assert all(call[0] != MESSAGES_ID for call in h.adapter.calls)
    assert any(event.get("error_code") == error for event in h.trace.steps)


@pytest.mark.parametrize("fault", ["missing", "count", "extra"])
def test_bad_aggregate_fails_task_before_build_response(fault) -> None:
    h = component_harness()
    data = overview_data()
    if fault == "missing":
        data.pop("messages")
    elif fault == "count":
        data["pending"]["authoritative_count"] = 9
    else:
        data["extra"] = "synthetic-extra"
    from app.workflow.models import WorkflowRunResult

    h.runtime._orchestration._workflow_engine.execute = AsyncMock(
        return_value=WorkflowRunResult(
            workflow_id=OVERVIEW_ID,
            workflow_version="1.0.0",
            trace_id="synthetic-trace",
            status="completed",
            output=data,
            step_outputs={},
        )
    )
    response = asyncio.run(run_overview(h))
    assert response.status == "failed" and response.data is None
    assert h.store.status_updates[-1] == ("failed", "adapter_payload_invalid")
    assert h.adapter.calls == []


@pytest.mark.parametrize("window", ["binding", "execute", "between_steps"])
def test_enabled_contract_drift_after_start_is_fail_closed(window) -> None:
    h = component_harness()

    def drift():
        current = h.registry._capabilities[MESSAGES_ID]
        h.registry._capabilities[MESSAGES_ID] = current.model_copy(update={"version": "2.0.0"})

    async def exercise():
        await validate_production_workflows(
            registry=h.registry, definitions=h.definitions, descriptors=h.descriptors
        )
        if window == "binding":
            original = h.runtime._orchestration.resolve_task_version_bindings

            async def changed(**kwargs):
                drift()
                return await original(**kwargs)

            h.runtime._orchestration.resolve_task_version_bindings = changed
        elif window == "execute":
            original = h.runtime._orchestration.execute_capability

            async def changed(**kwargs):
                drift()
                return await original(**kwargs)

            h.runtime._orchestration.execute_capability = changed
        else:
            h.adapter.after_first = drift
        return await run_overview(h)

    response = asyncio.run(exercise())
    assert response.status == "failed"
    assert h.store.status_updates[-1] == ("failed", "internal_error")
    assert len(h.adapter.calls) == int(window == "between_steps")
    assert h.gateway.execute_capability.await_count == int(window == "between_steps")


def test_request_owner_reaches_each_leaf_and_trace() -> None:
    h = component_harness()

    async def exercise():
        for suffix in ("one", "two"):
            response = await run_overview(
                h, user=f"user-{suffix}", tenant=f"tenant-{suffix}", sid=f"chat-{suffix}"
            )
            assert response.status == "completed"
            task = h.store.records[response.task_id]
            assert (task.ai_user_id, task.tenant_id, task.session_id) == (
                f"user-{suffix}",
                f"tenant-{suffix}",
                f"chat-{suffix}",
            )

    asyncio.run(exercise())
    owners = [
        (call.args[1], call.args[2], call.args[-1].tenant_id)
        for call in h.gateway.execute_capability.await_args_list
    ]
    assert owners == [
        (f"chat-{s}", f"user-{s}", f"tenant-{s}") for s in ("one", "two") for _ in range(2)
    ]
    assert {(tenant, user) for tenant, user in h.trace.owners} == {
        ("tenant-one", "user-one"),
        ("tenant-two", "user-two"),
    }


def test_read_catalog_never_produces_a_confirmed_action() -> None:
    h = component_harness()
    h.gateway._policy_guard.decide = AsyncMock(return_value=PolicyDecision(decision="confirm"))
    response = asyncio.run(run_overview(h))
    assert response.status == "failed"
    assert h.store.status_updates[-1] == ("failed", "internal_error")
    assert h.adapter.calls == []
    assert h.engine._checkpoints == {}
    assert h.runtime._pending_workflows == {}


# The following tests require migrated_database_url and are intentionally not
# executed in the comparison implementation phase. All data are synthetic.
@pytest.fixture
def pg_workflow_factory(migrated_database_url, monkeypatch):
    from contextlib import asynccontextmanager
    from uuid import uuid4

    import app.composition as composition
    from app.ports.capability_registry import CapabilitySpec
    from app.ports.response_projection_contract import canonical_schema_digest
    from app.workflow.models import WorkflowDefinition, WorkflowStep

    @asynccontextmanager
    async def factory(*, confirmation=False):
        definitions = production_workflow_definitions()
        descriptors = list(production_workflow_capabilities())
        leaves = list(expected_oa_capabilities())
        workflow_id = "oa.audit_confirmation"
        preview_id, execute_id = "oa.audit_preview_confirm", "oa.audit_execute"
        if confirmation:
            input_schema = {"type": "object", "properties": {}, "additionalProperties": False}
            output_schema = {"type": "object", "properties": {"result": {"type": "string"}}}

            def spec(identifier, kind):
                return CapabilitySpec(
                    capability_id=identifier,
                    name=identifier,
                    type=kind,
                    intent_tags=[identifier],
                    input_schema=input_schema,
                    output_schema=output_schema,
                    input_schema_digest=canonical_schema_digest(input_schema),
                    output_schema_digest=canonical_schema_digest(output_schema),
                    risk_level="low",
                    owner="synthetic-workflow-test",
                    version="1.0.0",
                    status="active",
                    short_description="合成确认测试",
                    target_system="oa",
                    execution_identity="user_delegated",
                    binding_required=True,
                )

            descriptors.append(spec(workflow_id, "workflow"))
            leaves.extend((spec(preview_id, "query"), spec(execute_id, "action")))
            definitions[workflow_id] = WorkflowDefinition(
                workflow_id,
                "1.0.0",
                (WorkflowStep("confirm", preview_id, confirmed_capability_id=execute_id),),
            )
        monkeypatch.setattr(
            composition, "production_workflow_definitions", lambda: deepcopy(definitions)
        )
        monkeypatch.setattr(
            composition,
            "production_workflow_capabilities",
            lambda: tuple(item.model_copy(deep=True) for item in descriptors),
        )
        llm, identity, adapter = MockLLMProvider(), Identity(), ReadAdapter()
        original_execute = adapter.execute

        async def execute(capability_id, arguments, execution_context):
            if capability_id == execute_id:
                adapter.calls.append((capability_id, arguments, execution_context))
                return AdapterResult(status="success", data={"result": "confirmed-once"})
            return await original_execute(capability_id, arguments, execution_context)

        adapter.execute = execute
        message = workflow_id if confirmation else MESSAGE
        chosen = workflow_id if confirmation else OVERVIEW_ID
        llm.register(
            message,
            LLMCompletionResponse(
                content=json.dumps(
                    {
                        "match": "capability",
                        "capability_id": chosen,
                        "capability_type": "workflow",
                        "arguments": {},
                    }
                )
            ),
        )
        settings = replace(
            ProductionSettings.from_environment(),
            database_url=migrated_database_url,
            oa_read_adapter_mode="mock",
        )

        def build():
            return build_production_components(
                settings,
                llm_provider=llm,
                structured_output=JSONStructuredOutputProvider(),
                identity_mapping=identity,
                adapters={"oa": adapter},
            )

        components = build()
        runtime = components.runtime
        registry = runtime._capability_registry
        prior = {}
        try:
            for item in (*leaves, *descriptors):
                previous = await registry.get(item.capability_id)
                prior[item.capability_id] = previous
                if previous is None:
                    await registry.create(item)
                else:
                    assert previous == item or previous == item.model_copy(
                        update={"status": "disabled"}
                    )
                    if previous.status == "disabled":
                        await registry.update(item.capability_id, {"status": "active"})
            await components.validate_workflows()
            sid = f"synthetic-workflow-{uuid4().hex}"
            principal = runtime_principal(
                f"reader-{uuid4().hex}", tenant_id="synthetic-workflow-tenant"
            )

            async def start():
                return await runtime.handle_user_message(
                    channel="web",
                    principal=principal,
                    session_id=sid,
                    message=message,
                    client_capabilities={},
                )

            yield SimpleNamespace(**locals())
        finally:
            # Retain rows and task evidence. Restore prior descriptors, and disable
            # only newly inserted synthetic rows; no DELETE or schema operations.
            for identifier, previous in prior.items():
                if previous is not None:
                    await registry.update(
                        identifier, previous.model_dump(exclude={"capability_id"})
                    )
                elif await registry.get(identifier) is not None:
                    await registry.disable(identifier)
            await runtime._task_store._session_factory.kw["bind"].dispose()

    return factory


def _run_pg(coroutine):
    from app.event_loop import make_event_loop

    with asyncio.Runner(loop_factory=make_event_loop) as runner:
        return runner.run(coroutine)


def test_http_overview_executes_two_reads_with_real_stores(pg_workflow_factory) -> None:
    from httpx import ASGITransport, AsyncClient

    from app.main import create_app
    from tests.auth_fakes import TEST_CSRF_ALLOWED_ORIGINS, TEST_CSRF_HEADERS

    async def exercise():
        async with pg_workflow_factory() as h:
            components = h.components
            app = create_app(
                runtime=h.runtime,
                session_tokens=components.session_tokens,
                session_binder=components.session_binder.bind,
                csrf_allowed_origins=TEST_CSRF_ALLOWED_ORIGINS,
                validate_workflows=components.validate_workflows,
            )
            ticket = components.session_tokens.issue(h.principal)
            async with app.router.lifespan_context(app):
                async with AsyncClient(
                    transport=ASGITransport(app=app),
                    base_url="https://testserver",
                    cookies={"eternalai_session": ticket},
                ) as client:
                    response = await client.post(
                        "/api/v1/runtime/handle",
                        headers=TEST_CSRF_HEADERS,
                        json={
                            "session_id": h.sid,
                            "message": MESSAGE,
                            "channel": "web",
                            "client_capabilities": {},
                        },
                    )
            assert response.status_code == 200
            body = response.json()
            assert body["status"] == "completed" and body["data"] == h.adapter.data
            assert "待办甲" in body["message"] and "消息乙" in body["message"]
            assert [call[0] for call in h.adapter.calls] == [PENDING_ID, MESSAGES_ID]
            task = await h.runtime._task_store.get_task(body["task_id"])
            assert task.status == "completed"
            assert (task.ai_user_id, task.tenant_id) == (
                h.principal.ai_user_id,
                h.principal.org_ctx.tenant_id,
            )
            events = await h.runtime._task_store.list_events(task.task_id)
            kinds = [event.event_type for event in events]
            assert kinds.count("workflow_started") == kinds.count("workflow_completed") == 1
            assert kinds.count("workflow_step_finished") == 2
            trace_query = components.admin_registry_service._trace_query
            trace = await trace_query.list_events_by_task(task.task_id, tenant_id=task.tenant_id)
            assert trace and all(event.ai_user_id == task.ai_user_id for event in trace)
            assert (
                await trace_query.list_events_by_task(task.task_id, tenant_id="synthetic-other")
                == []
            )
            prompt = "\n".join(m.content for m in h.llm.calls[0]["messages"])
            assert all(
                identifier in prompt for identifier in (OVERVIEW_ID, PENDING_ID, MESSAGES_ID)
            )

    _run_pg(exercise())


@pytest.mark.parametrize("mode", ["button", "text"])
def test_production_confirm_and_text_resume_share_pg_claim(pg_workflow_factory, mode) -> None:
    from app.contracts.sdui.models import ConfirmUserAction

    async def exercise():
        async with pg_workflow_factory(confirmation=True) as h:
            waiting = await h.start()
            assert waiting.status == "waiting_user"
            pending = h.runtime._pending_workflows[(h.sid, h.principal.ai_user_id)]
            request = await h.runtime._human_gate_port.get_request(pending.gate_request_id)
            assert request is not None and request.task_id == waiting.task_id
            if mode == "button":
                response = await h.runtime.handle_user_action(
                    channel="web",
                    principal=h.principal,
                    session_id=h.sid,
                    action=ConfirmUserAction(
                        action_type="confirm", response_id=waiting.response_id, confirmed=True
                    ),
                )
                assert response.data["action_outcome"] == "accepted"
                assert response.data["result"] == {"result": "confirmed-once"}
            else:
                response = await h.runtime.handle_user_message(
                    channel="web",
                    principal=h.principal,
                    session_id=h.sid,
                    message="确认",
                    client_capabilities={},
                )
                assert response.data == {"result": "confirmed-once"}
            assert response.status == "completed"
            assert [call[0] for call in h.adapter.calls] == [h.execute_id]
            assert (await h.runtime._task_store.get_task(waiting.task_id)).status == "completed"
            assert (
                await h.runtime._human_gate_port.get_decision(pending.gate_request_id) is not None
            )

    _run_pg(exercise())


@pytest.mark.parametrize("kind", ["reject", "cancel", "duplicate"])
def test_production_reject_cancel_and_duplicate_are_once_only(pg_workflow_factory, kind) -> None:
    from app.contracts.sdui.models import CancelUserAction, ConfirmUserAction, RejectUserAction

    async def exercise():
        async with pg_workflow_factory(confirmation=True) as h:
            waiting = await h.start()
            assert waiting.status == "waiting_user"
            action = (
                CancelUserAction(action_type="cancel", response_id=waiting.response_id)
                if kind == "cancel"
                else RejectUserAction(action_type="reject", response_id=waiting.response_id)
                if kind == "reject"
                else ConfirmUserAction(
                    action_type="confirm",
                    response_id=waiting.response_id,
                    confirmed=True,
                )
            )

            async def dispatch():
                return await h.runtime.handle_user_action(
                    channel="web", principal=h.principal, session_id=h.sid, action=action
                )

            responses = await asyncio.gather(dispatch(), dispatch())
            expected = 1 if kind == "duplicate" else 0
            assert len(h.adapter.calls) == expected
            accepted = sum(response.data["action_outcome"] == "accepted" for response in responses)
            assert accepted == expected
            replay = await dispatch()
            assert replay.data["action_outcome"] != "accepted"
            assert len(h.adapter.calls) == expected
            task = await h.runtime._task_store.get_task(waiting.task_id)
            assert task.status == ("completed" if kind == "duplicate" else "cancelled")

    _run_pg(exercise())


@pytest.mark.parametrize("fault", ["foreign", "version", "status", "policy", "digest"])
def test_production_confirmation_rejects_foreign_reference_and_version_drift(
    pg_workflow_factory, fault
) -> None:
    from app.contracts.sdui.models import ConfirmUserAction

    async def exercise():
        async with pg_workflow_factory(confirmation=True) as h:
            waiting = await h.start()
            assert waiting.status == "waiting_user"
            principal = h.principal
            if fault == "foreign":
                principal = runtime_principal("synthetic-foreign", tenant_id="synthetic-other")
            elif fault == "digest":
                key = (h.sid, h.principal.ai_user_id)
                pending = h.runtime._pending_workflows[key]
                h.runtime._pending_workflows[key] = replace(pending, action_digest="0" * 64)
            else:
                patch = (
                    {"version": "2.0.0"}
                    if fault == "version"
                    else (
                        {"status": "disabled"}
                        if fault == "status"
                        else {"policy_digest": "changed"}
                    )
                )
                await h.registry.update(h.execute_id, patch)

            async def dispatch(reference):
                return await h.runtime.handle_user_action(
                    channel="web",
                    principal=principal,
                    session_id=h.sid,
                    action=ConfirmUserAction(
                        action_type="confirm", response_id=reference, confirmed=True
                    ),
                )

            response = await dispatch(waiting.response_id)
            assert response.data["action_outcome"] != "accepted"
            assert h.adapter.calls == []
            if fault == "foreign":
                unknown = await dispatch("synthetic-unknown-reference")
                excluded = {"response_id", "task_id", "session_id", "trace_id"}
                assert response.model_dump(exclude=excluded) == unknown.model_dump(exclude=excluded)
            else:
                assert response.data["action_outcome"] == "action_version_conflict"

    _run_pg(exercise())


def test_rebuilt_production_components_cannot_resume_old_checkpoint(pg_workflow_factory) -> None:
    from app.contracts.sdui.models import ConfirmUserAction

    async def exercise():
        async with pg_workflow_factory(confirmation=True) as h:
            waiting = await h.start()
            assert waiting.status == "waiting_user"
            calls = len(h.llm.calls)
            rebuilt = h.build()
            try:
                await rebuilt.validate_workflows()
                response = await rebuilt.runtime.handle_user_action(
                    channel="web",
                    principal=h.principal,
                    session_id=h.sid,
                    action=ConfirmUserAction(
                        action_type="confirm", response_id=waiting.response_id, confirmed=True
                    ),
                )
                assert response.data["action_outcome"] == "confirmation_invalidated"
                assert response.status == "confirmation_invalidated"
                assert h.adapter.calls == [] and len(h.llm.calls) == calls
            finally:
                await rebuilt.runtime._task_store._session_factory.kw["bind"].dispose()

    _run_pg(exercise())


def test_missing_output_through_human_gate_fails_task_without_completion() -> None:
    class MissingOutput(dict):
        def __contains__(self, key):
            return False

    h = component_harness()
    original = h.engine._run_steps

    async def missing(**kwargs):
        kwargs["step_outputs"] = MissingOutput()
        return await original(**kwargs)

    h.engine._run_steps = missing
    response = asyncio.run(run_overview(h))
    assert response.status == "failed"
    assert h.store.status_updates[-1] == ("failed", "internal_error")
    assert len(h.adapter.calls) == 2
    events = [event.event_type for event in h.store.events]
    assert "workflow_completed" not in events and "workflow_failed" in events


@pytest.mark.parametrize("field,value", [("type", "query"), ("status", "disabled")])
def test_selected_overview_type_and_status_drift_cannot_take_query_path(field, value) -> None:
    h = component_harness()
    original = h.runtime._orchestration.resolve_task_version_bindings

    async def mutate(**kwargs):
        changed = h.descriptors[OVERVIEW_ID].model_copy(update={field: value})
        h.registry._capabilities[OVERVIEW_ID] = changed
        if field == "type":
            kwargs["capability"] = changed
        return await original(**kwargs)

    h.runtime._orchestration.resolve_task_version_bindings = mutate
    response = asyncio.run(run_overview(h))
    assert response.status == "failed"
    assert h.store.status_updates[-1] == ("failed", "internal_error")
    assert h.gateway.execute_capability.await_count == 0 and h.adapter.calls == []
