"""Runtime gateway status mapping tests."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.admin.actions import ADMIN_LITE_POLICY_CAPABILITY_IDS
from app.infra.gateway.capability_gateway import CapabilityGateway
from app.infra.llm.mock_llm.mock_llm_provider import MockLLMProvider
from app.infra.llm.mock_structured_output.mock_structured_output_provider import (
    MockStructuredOutputProvider,
)
from app.infra.orchestration.agent_adapter import AgentOrchestrationAdapter
from app.infra.policy.minimal_policy_guard import MinimalPolicyGuard
from app.infra.sdui.response_envelope_builder import ResponseEnvelopeBuilder
from app.main import create_app
from app.ports.adapter import AdapterResult
from app.ports.capability_gateway import ExecutionResult, RequestOrgContext
from app.ports.response_envelope import ResponseEnvelope
from app.ports.task_store import SessionRecord, TaskEventRecord, TaskRecord
from app.runtime.models import IntentOutput, MatchedIntent
from app.runtime.runtime import RuntimeImpl
from tests.auth_fakes import (
    TEST_CSRF_ALLOWED_ORIGINS,
    TEST_CSRF_HEADERS,
    MemorySessionRevocations,
    StaticSessionTokens,
    auth_cookies,
    make_session_binder,
)
from tests.runtime.principal_fakes import runtime_principal
from tests.runtime.registry_fakes import StaticCapabilityRegistry


class SpyTaskStore:
    def __init__(self) -> None:
        self.created: list[TaskRecord] = []
        self.status_updates: list[tuple[str, str, str | None]] = []

    async def create_task(self, record: TaskRecord) -> TaskRecord:
        self.created.append(record)
        return record

    async def get_task(self, task_id: str) -> TaskRecord | None:
        return None

    async def update_status(
        self,
        task_id: str,
        status: str,
        error_code: str | None = None,
    ) -> TaskRecord:
        self.status_updates.append((task_id, status, error_code))
        created = self.created[0]
        return TaskRecord(
            task_id=task_id,
            session_id=created.session_id,
            ai_user_id=created.ai_user_id,
            tenant_id=created.tenant_id,
            status=status,
            trace_id=created.trace_id,
            error_code=error_code,
        )

    async def append_event(self, task_id: str, event: Any) -> None:
        return None

    async def list_tasks(
        self,
        *,
        session_id: str | None = None,
        ai_user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> list[TaskRecord]:
        return []

    async def list_events(self, task_id: str) -> list[TaskEventRecord]:
        return []


class ExistingSessionStore:
    async def create_session(self, record: SessionRecord) -> SessionRecord:
        return record

    async def get_session(self, session_id: str, *, tenant_id: str) -> SessionRecord | None:
        return SessionRecord(session_id=session_id, tenant_id=tenant_id)


class SpyTracePort:
    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []
        self.record_gateway_call_count = 0

    def set_sanitizer(self, hook: Any) -> None:
        return None

    async def record_event(self, event: Any) -> None:
        return None

    async def start_task_trace(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
        **_owner: Any,
    ) -> None:
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
        **_owner: Any,
    ) -> None:
        self.steps.append(
            {
                "event_type": event_type,
                "status": status,
                "capability_id": capability_id,
                "error_code": error_code,
            }
        )

    async def record_policy_decision(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
        status: str,
        capability_id: str | None = None,
        error_code: str | None = None,
        attributes: dict[str, Any] | None = None,
        **_owner: Any,
    ) -> None:
        return None

    async def record_gateway_call(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
        status: str,
        capability_id: str | None = None,
        error_code: str | None = None,
        attributes: dict[str, Any] | None = None,
        **_owner: Any,
    ) -> None:
        self.record_gateway_call_count += 1

    async def finalize_task_trace(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
        status: str,
        capability_id: str | None = None,
        error_code: str | None = None,
        attributes: dict[str, Any] | None = None,
        **_owner: Any,
    ) -> None:
        return None


class SpyGateway:
    def __init__(self, result: ExecutionResult) -> None:
        self.result = result
        self.calls: list[str] = []
        self.request_contexts: list[RequestOrgContext] = []

    async def execute_capability(
        self,
        task_id: str,
        session_id: str,
        ai_user_id: str,
        capability_id: str,
        arguments: dict[str, Any],
        request_context: RequestOrgContext,
    ) -> ExecutionResult:
        self.calls.append(capability_id)
        self.request_contexts.append(request_context)
        return self.result


class AdapterSentinel:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(
        self,
        capability_id: str,
        arguments: dict[str, Any],
        execution_context: dict[str, Any],
    ) -> AdapterResult:
        self.calls += 1
        return AdapterResult(status="success", data={"unexpected": True})


class CountingGateway:
    def __init__(self, gateway: CapabilityGateway) -> None:
        self.gateway = gateway
        self.calls = 0

    async def execute_capability(
        self,
        task_id: str,
        session_id: str,
        ai_user_id: str,
        capability_id: str,
        arguments: dict[str, Any],
        request_context: RequestOrgContext,
    ) -> ExecutionResult:
        self.calls += 1
        return await self.gateway.execute_capability(
            task_id=task_id,
            session_id=session_id,
            ai_user_id=ai_user_id,
            capability_id=capability_id,
            arguments=arguments,
            request_context=request_context,
        )


def _run_mapping(result: ExecutionResult) -> tuple[ResponseEnvelope, SpyTaskStore, SpyGateway]:
    async def exercise_runtime() -> tuple[ResponseEnvelope, SpyTaskStore, SpyGateway]:
        task_store = SpyTaskStore()
        structured_output = MockStructuredOutputProvider()
        structured_output.register(
            "mapped message",
            IntentOutput,
            MatchedIntent(
                match="capability", capability_id="mapped.cap", arguments={"key": "value"}
            ),
        )
        gateway = SpyGateway(result)
        orchestration_registry = StaticCapabilityRegistry("mapped.cap")
        orchestration_workflow = None
        orchestration_builder = ResponseEnvelopeBuilder()
        runtime = RuntimeImpl(
            candidate_policy=MinimalPolicyGuard(),
            task_store=task_store,
            session_store=ExistingSessionStore(),
            capability_registry=orchestration_registry,
            orchestration=AgentOrchestrationAdapter(
                capability_registry=orchestration_registry,
                gateway=gateway,
                workflow_engine=orchestration_workflow,
                response_builder=orchestration_builder,
            ),
            trace_port=SpyTracePort(),
            llm_provider=MockLLMProvider(),
            structured_output=structured_output,
            intent_model="test-intent-model",
            response_builder=orchestration_builder,
        )

        envelope = await runtime.handle_user_message(
            channel="mock",
            principal=runtime_principal("ai-user-1"),
            session_id="session-1",
            message="mapped message",
            client_capabilities={},
        )
        return envelope, task_store, gateway

    return asyncio.run(exercise_runtime())


def test_denied_result_maps_to_failed_task_blocked_envelope_and_single_gateway_call() -> None:
    result, task_store, gateway = _run_mapping(ExecutionResult(status="denied", trace_id="tr1"))

    assert task_store.status_updates[-1][1] == "failed"
    assert isinstance(result, ResponseEnvelope)
    assert result.status == "blocked"
    assert result.ui.component_type == "operator_handback_card"
    assert len(gateway.calls) == 1


def test_runtime_constructs_business_request_context_with_empty_roles() -> None:
    _result, _task_store, gateway = _run_mapping(
        ExecutionResult(status="completed", trace_id="trace-empty-roles")
    )

    assert len(gateway.request_contexts) == 1
    assert gateway.request_contexts[0].roles == []


def test_runtime_api_denies_active_admin_capability_before_gateway_pre_record_or_adapter() -> None:
    message = "list the admin registry"
    capability_id = "admin_registry_list"
    task_store = SpyTaskStore()
    trace_port = SpyTracePort()
    registry = StaticCapabilityRegistry(capability_id)
    adapter = AdapterSentinel()
    policy_guard = MinimalPolicyGuard(admin_capability_ids=ADMIN_LITE_POLICY_CAPABILITY_IDS)
    gateway = CountingGateway(
        CapabilityGateway(
            adapter=adapter,
            capability_registry=registry,
            policy_guard=policy_guard,
            trace_port=trace_port,
            tenant_id="default",
        )
    )
    structured_output = MockStructuredOutputProvider()
    structured_output.register(
        message,
        IntentOutput,
        MatchedIntent(match="capability", capability_id=capability_id, arguments={}),
    )
    orchestration_registry = registry
    orchestration_workflow = None
    orchestration_builder = ResponseEnvelopeBuilder()
    runtime = RuntimeImpl(
        candidate_policy=MinimalPolicyGuard(),
        task_store=task_store,
        session_store=ExistingSessionStore(),
        capability_registry=orchestration_registry,
        orchestration=AgentOrchestrationAdapter(
            capability_registry=orchestration_registry,
            gateway=gateway,
            workflow_engine=orchestration_workflow,
            response_builder=orchestration_builder,
        ),
        trace_port=trace_port,
        llm_provider=MockLLMProvider(),
        structured_output=structured_output,
        intent_model="test-intent-model",
        response_builder=orchestration_builder,
    )

    session_tokens = StaticSessionTokens()
    client = TestClient(
        create_app(
            runtime=runtime,
            session_revocations=MemorySessionRevocations(),
            session_tokens=session_tokens,
            session_binder=make_session_binder(),
            session_cookie_ttl_seconds=3600,
            csrf_allowed_origins=TEST_CSRF_ALLOWED_ORIGINS,
        ),
        base_url="https://testserver",
    )
    client.cookies.update(auth_cookies())
    response = client.post(
        "/api/v1/runtime/handle",
        headers=TEST_CSRF_HEADERS,
        json={
            "channel": "api",
            "session_id": "runtime-session",
            "message": message,
            "client_capabilities": {},
        },
    )

    assert response.status_code == 200
    # Candidate preview excludes the management-plane ID for a business request,
    # so it is never offered to the model and the Gateway is never reached.
    assert response.json()["status"] == "no_capability_found"
    assert capability_id not in response.json()["message"]
    assert task_store.status_updates[-1][2] == "capability_not_found"
    assert gateway.calls == 0
    assert trace_port.record_gateway_call_count == 0
    assert adapter.calls == 0
    assert all(step["event_type"] != "policy_checked" for step in trace_port.steps)
    assert all(step["event_type"] != "capability_selected" for step in trace_port.steps)


def test_binding_required_result_maps_to_failed_task_blocked_envelope_with_operator_handback() -> (
    None
):
    result, task_store, _gateway = _run_mapping(
        ExecutionResult(
            status="binding_required",
            error_code="identity_unbound",
            trace_id="tr2",
        )
    )

    assert task_store.status_updates[-1][1] == "failed"
    assert isinstance(result, ResponseEnvelope)
    assert result.status == "blocked"
    assert result.ui.component_type in {"binding_required_card", "operator_handback_card"}


def test_timeout_result_maps_to_failed_task_failed_envelope_and_preserves_trace_id() -> None:
    result, task_store, _gateway = _run_mapping(
        ExecutionResult(status="timeout", trace_id="gw-timeout-trace")
    )

    assert task_store.status_updates[-1][1] == "failed"
    assert isinstance(result, ResponseEnvelope)
    assert result.status == "failed"
    assert result.trace_id is not None
    assert result.trace_id != ""


def test_failed_result_maps_to_failed_task_failed_envelope() -> None:
    result, task_store, _gateway = _run_mapping(ExecutionResult(status="failed", trace_id="tr4"))

    assert task_store.status_updates[-1][1] == "failed"
    assert isinstance(result, ResponseEnvelope)
    assert result.status == "failed"


def test_gateway_no_capability_found_maps_to_no_capability_task_and_envelope() -> None:
    result, task_store, _gateway = _run_mapping(
        ExecutionResult(status="no_capability_found", trace_id="tr5")
    )

    assert task_store.status_updates[-1][1] == "no_capability_found"
    assert isinstance(result, ResponseEnvelope)
    assert result.status == "no_capability_found"


def test_waiting_user_result_maps_to_waiting_user_task_and_confirm_card() -> None:
    result, task_store, _gateway = _run_mapping(
        ExecutionResult(status="waiting_user", trace_id="tr6")
    )

    assert task_store.status_updates[-1][1] == "waiting_user"
    assert isinstance(result, ResponseEnvelope)
    assert result.status == "waiting_user"
    assert result.ui.component_type == "confirm_card"


def _chat_browser_fixture(*, failure: str | None = None, principal: Any = None) -> SimpleNamespace:
    """Real Runtime/Gateway/Adapter; only external ports are deterministic fakes.

    This helper is component evidence, not PostgreSQL or real-model evidence.
    """
    from unittest.mock import AsyncMock

    from app.infra.adapters.browser.adapter import BrowserCapabilityAdapter
    from app.infra.llm.json_structured_output import JSONStructuredOutputProvider
    from app.ports.browser_chat import BrowserChatError
    from app.ports.browser_gateway import BrowserGatewayRegistration
    from app.ports.capability_registry import CapabilitySpec
    from app.ports.identity_mapping import IdentityCheckResult
    from app.ports.llm_provider import LLMCompletionResponse
    from tests.runtime.registry_fakes import runtime_output_schema, schema_digest

    principal = principal or runtime_principal("ai-user-1")
    output_schema = runtime_output_schema("registry_fakes.default")
    spec = CapabilitySpec(
        capability_id="oa.browser.synthetic.read", name="Synthetic browser query",
        type="query", input_schema={"type": "object", "properties": {
            "business_key": {"type": "string", "enum": ["DEMO-731", "DEMO-842"]},
        }, "required": ["business_key"], "additionalProperties": False},
        input_schema_digest="chat-component-input", output_schema=output_schema,
        output_schema_digest=schema_digest(output_schema), risk_level="low",
        owner="synthetic fixture", version="v1", status="active",
        short_description="Query the synthetic browser record by business_key.",
        target_system="oa", execution_identity="user_delegated", binding_required=True,
    )
    registry = StaticCapabilityRegistry(spec)
    registration = BrowserGatewayRegistration(
        principal.org_ctx.tenant_id, principal.ai_user_id, spec.capability_id,
        spec.version, "chat_read_fixture", "v1",
    )
    llm = MockLLMProvider()
    message = "Query synthetic browser record DEMO-731"
    llm.register(message, LLMCompletionResponse(content=json.dumps({
        "match": "capability", "capability_id": spec.capability_id,
        "target_system": "oa", "capability_type": "query",
        "arguments": {"business_key": "DEMO-731"},
    }), model_used="glm-4.7"))

    class Submission:
        def __init__(self) -> None:
            self.receipt: ResponseEnvelope | None = None
            self.submit_calls: list[dict[str, Any]] = []
            self.lookup_calls: list[dict[str, Any]] = []
            self.lookup_error: str | None = None

        async def lookup_ordinary_request(self, **kwargs: Any) -> ResponseEnvelope | None:
            self.lookup_calls.append(kwargs)
            if self.lookup_error is not None:
                raise BrowserChatError(self.lookup_error)
            return self.receipt

        async def submit(self, **kwargs: Any) -> ResponseEnvelope:
            self.submit_calls.append(kwargs)
            self.receipt = _chat_browser_receipt(kwargs["bound_session"])
            # Callback models a successful durable accept before later I/O fails.
            kwargs["on_admitted"](self.receipt)
            if failure == "post_commit_reply":
                raise RuntimeError("synthetic_post_commit_reply_unavailable")
            return self.receipt

    class Handoff:
        def __init__(self) -> None:
            self.acquired = True
            self.commits: list[Any] = []
            self.closed = 0

        @asynccontextmanager
        async def try_request_route(self, scope: Any):
            yield self.acquired

        async def commit(self, invocation: Any, admission: Any) -> None:
            self.commits.append((invocation, admission))
            if failure == "handoff":
                raise RuntimeError("synthetic_handoff_unavailable")

        async def fail_if_open(self, scope: Any, error_code: str) -> str:
            self.closed += 1
            return "handed_off" if self.commits and failure != "handoff" else "failed"

    class Trace(SpyTracePort):
        async def record_step(self, *args: Any, **kwargs: Any) -> None:
            event = kwargs.get("event_type") or args[3]
            if (failure == "adapter_audit" and event == "adapter_called"
                    or failure == "response_audit" and event == "response_envelope_created"):
                raise RuntimeError("synthetic_audit_unavailable")
            await super().record_step(*args, **kwargs)

        async def finalize_task_trace(self, *args: Any, **kwargs: Any) -> None:
            if failure == "finalize_audit":
                raise RuntimeError("synthetic_finalize_unavailable")
            await super().finalize_task_trace(*args, **kwargs)

    submission, handoff, trace, tasks = Submission(), Handoff(), Trace(), SpyTaskStore()
    identity = SimpleNamespace(
        resolve_execution_identity=AsyncMock(return_value=IdentityCheckResult(
            bind_status="active", binding_id="synthetic-binding", target_system="oa",
            execution_identity="user_delegated",
        )),
    )
    fallback = AdapterSentinel()
    gateway = CapabilityGateway(
        adapter=fallback, capability_registry=registry, identity_mapping=identity,
        policy_guard=MinimalPolicyGuard(), trace_port=trace,
        tenant_id=principal.org_ctx.tenant_id,
    )
    gateway.install_browser_dispatch(
        registration, BrowserCapabilityAdapter(registration, submission),
    )
    builder = ResponseEnvelopeBuilder()
    runtime = RuntimeImpl(
        candidate_policy=MinimalPolicyGuard(), task_store=tasks,
        session_store=ExistingSessionStore(), capability_registry=registry,
        orchestration=AgentOrchestrationAdapter(capability_registry=registry, gateway=gateway,
                                              workflow_engine=None, response_builder=builder),
        trace_port=trace, llm_provider=llm, structured_output=JSONStructuredOutputProvider(),
        intent_model="glm-4.7", response_builder=builder,
    )
    runtime.install_browser_handoff(handoff, submission)
    return SimpleNamespace(runtime=runtime, gateway=gateway, principal=principal, spec=spec,
                           llm=llm, message=message, submission=submission, handoff=handoff,
                           trace=trace, tasks=tasks, fallback=fallback)


def _chat_browser_receipt(session_id: str) -> ResponseEnvelope:
    from app.contracts.sdui.models import UIComponent

    return ResponseEnvelope(
        response_id="synthetic-response", task_id="canonical-B", session_id=session_id,
        trace_id="canonical-trace", status="running", message="Synthetic admission",
        fallback_text="Synthetic admission", ui=UIComponent(component_type="none", action="none"),
        data={"kind": "accepted", "task_id": "canonical-B", "run_id": "canonical-R",
              "state_revision": 1},
    )


@contextmanager
def _chat_browser_scope(principal: Any, session_id: str = "session-1"):
    from app.ports.auth import AuthenticatedSessionContext, authenticated_session
    from app.ports.browser_gateway import BrowserRequestScope, browser_request_scope

    verified = AuthenticatedSessionContext(principal, b"synthetic-only" * 3,
                                          datetime.now(UTC) + timedelta(hours=1))
    auth_token = authenticated_session.set(verified)
    try:
        scope = BrowserRequestScope(verified, session_id, "73100000-0000-4000-8000-000000000001")
        scope_token = browser_request_scope.set(scope)
        try:
            yield scope
        finally:
            browser_request_scope.reset(scope_token)
    finally:
        authenticated_session.reset(auth_token)


@pytest.mark.parametrize("failure", [
    "handoff", "adapter_audit", "response_audit", "finalize_audit", "post_commit_reply",
])
def test_chat_browser_durable_admission_survives_later_failures(failure: str) -> None:
    async def scenario() -> None:
        fixture = _chat_browser_fixture(failure=failure)
        with _chat_browser_scope(fixture.principal) as scope:
            result = await fixture.runtime.handle_user_message(
                "web", fixture.principal, scope.bound_session, fixture.message, {},
            )
            receipt = fixture.submission.receipt
            assert receipt is not None
            assert result.status == "running"
            assert (result.task_id, result.trace_id, result.data) == (
                receipt.task_id, receipt.trace_id, receipt.data,
            )
            assert len(fixture.llm.calls) == len(fixture.submission.submit_calls) == 1
            assert fixture.fallback.calls == 0
            assert scope.attempt.admission.run_id == "canonical-R"
            assert result.trace_summary == (
                "audit_unavailable" if failure in {"response_audit", "finalize_audit"}
                else "handoff_unavailable"
            )
            assert all(task.task_id != "canonical-B" for task in fixture.tasks.created)

    asyncio.run(scenario())


@pytest.mark.parametrize("replay_status", ["running", "failed"])
def test_chat_browser_same_key_replays_before_llm(replay_status: str) -> None:
    async def scenario() -> None:
        fixture = _chat_browser_fixture()
        receipt = _chat_browser_receipt("session-1")
        if replay_status == "failed":
            receipt = receipt.model_copy(update={"status": "failed", "data": {
                "kind": "failed", "error_code": "browser_request_abandoned",
            }})
        fixture.submission.receipt = receipt
        with _chat_browser_scope(fixture.principal) as scope:
            result = await fixture.runtime.handle_user_message(
                "web", fixture.principal, scope.bound_session, fixture.message, {},
            )
            assert result is receipt
            assert len(fixture.submission.lookup_calls) == 1
            assert fixture.submission.lookup_calls[0] == {
                "principal": fixture.principal, "bound_session": "session-1",
                "client_request_id": scope.client_request_id, "message": fixture.message,
                "channel": "web",
            }
            assert (
                fixture.llm.calls == fixture.submission.submit_calls == fixture.tasks.created == []
            )
            assert fixture.handoff.commits == []

    asyncio.run(scenario())


@pytest.mark.parametrize("condition", ["contended", "conflict", "lookup_unavailable"])
def test_chat_browser_uncertain_lookup_never_reparses_or_dispatches(condition: str) -> None:
    async def scenario() -> None:
        fixture = _chat_browser_fixture()
        fixture.handoff.acquired = condition != "contended"
        fixture.submission.lookup_error = {
            "conflict": "request_key_conflict", "lookup_unavailable": "browser_chat_unavailable",
        }.get(condition)
        with _chat_browser_scope(fixture.principal) as scope:
            result = await fixture.runtime.handle_user_message(
                "web", fixture.principal, scope.bound_session, fixture.message, {},
            )
            assert (
                fixture.llm.calls == fixture.submission.submit_calls == fixture.tasks.created == []
            )
            assert fixture.handoff.commits == []
            assert result.status == ("failed" if condition == "conflict" else "running")
            if condition == "conflict":
                assert result.data["error_code"] == "adapter_payload_invalid"
                assert result.data["browser_reason"] == "request_key_conflict"
            else:
                assert result.data["kind"] == "unconfirmed"
            assert "run_id" not in result.data

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "condition", ["model_mismatch", "timeout", "provider_error", "invalid_json"],
)
def test_chat_browser_invalid_model_output_never_admits(condition: str) -> None:
    from unittest.mock import AsyncMock

    from app.ports.llm_provider import LLMCompletionResponse

    async def scenario() -> None:
        fixture = _chat_browser_fixture()
        if condition == "timeout":
            fixture.llm.complete = AsyncMock(side_effect=TimeoutError())
        else:
            content = fixture.llm._responses[fixture.message].content
            fixture.llm.register(fixture.message, LLMCompletionResponse(
                content=None if condition == "provider_error" else "not-json"
                if condition == "invalid_json" else content,
                model_used="wrong-model" if condition == "model_mismatch" else "glm-4.7",
                error_code="timeout" if condition == "provider_error" else None,
            ))
        with _chat_browser_scope(fixture.principal) as scope:
            result = await fixture.runtime.handle_user_message(
                "web", fixture.principal, scope.bound_session, fixture.message, {},
            )
            assert result.status == "failed"
            assert fixture.submission.submit_calls == fixture.handoff.commits == []
            assert scope.attempt.admission is None
            assert fixture.fallback.calls == 0
            if condition == "timeout":
                assert fixture.llm.complete.await_count == 1
            else:
                assert len(fixture.llm.calls) == 1
                assert fixture.llm.calls[0]["model"] == "glm-4.7"
                assert fixture.llm.calls[0]["response_format"] == {"type": "json_object"}

    asyncio.run(scenario())
