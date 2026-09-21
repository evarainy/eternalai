"""Runtime API router tests."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, get_args

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.v1.auth import make_require_principal
from app.api.v1.runtime import (
    ActionResponseData,
    ActionResponseEnvelope,
    make_router,
)
from app.contracts.sdui.models import UserAction
from app.event_loop import make_event_loop
from app.infra.gateway.capability_gateway import CapabilityGateway
from app.infra.identity.mock_identity_mapping import MockIdentityMapping
from app.infra.identity.postgresql import PostgreSQLOAIdentityMapping
from app.infra.llm.json_structured_output import JSONStructuredOutputProvider
from app.infra.orchestration.agent_adapter import AgentOrchestrationAdapter
from app.infra.policy.minimal_policy_guard import MinimalPolicyGuard
from app.infra.sdui.response_envelope_builder import ResponseEnvelopeBuilder
from app.main import create_app
from app.ports.adapter import AdapterResult
from app.ports.auth import Principal, PrincipalOrgContext
from app.ports.capability_registry import CapabilitySpec
from app.ports.llm_provider import LLMCompletionResponse, LLMMessage
from app.ports.request_context import RequestOrgContext
from app.ports.response_envelope import ResponseEnvelope
from app.ports.runtime import UserActionOutcome
from app.runtime.runtime import RuntimeImpl
from tests.auth_fakes import (
    TEST_CSRF_ALLOWED_ORIGINS,
    TEST_CSRF_HEADERS,
    MemorySessionRevocations,
    StaticSessionTokens,
    auth_cookies,
    make_session_binder,
)
from tests.runtime.registry_fakes import (
    StaticCapabilityRegistry,
    runtime_output_schema,
    schema_digest,
)
from tests.runtime.test_runtime_capability_selection import (
    ExistingSessionStore,
    RecordingTaskStore,
    RecordingTracePort,
)


class FakeRuntime:
    def __init__(self, action_data: dict[str, Any] | None = None) -> None:
        self.calls = 0
        self.ai_user_ids: list[str] = []
        self.principals: list[Principal] = []
        self.session_ids: list[str] = []
        self.action_data = (
            {
                "action_outcome": "accepted",
                "result": None,
            }
            if action_data is None
            else action_data
        )

    async def handle_user_message(
        self,
        channel: str,
        principal: Principal,
        session_id: str,
        message: str,
        client_capabilities: dict[str, Any],
    ) -> ResponseEnvelope:
        self.calls += 1
        self.principals.append(principal)
        self.ai_user_ids.append(principal.ai_user_id)
        self.session_ids.append(session_id)
        return ResponseEnvelopeBuilder().build_message(
            response_id="response-1",
            task_id="task-1",
            session_id=session_id,
            message="ok",
            fallback_text="ok",
            trace_id="trace-1",
            status="completed",
        )

    async def handle_user_action(
        self,
        channel: str,
        principal: Principal,
        session_id: str,
        action: UserAction,
    ) -> ResponseEnvelope:
        del channel, action
        self.calls += 1
        self.ai_user_ids.append(principal.ai_user_id)
        self.session_ids.append(session_id)
        return ResponseEnvelopeBuilder().build_message(
            response_id="response-action",
            task_id="task-action",
            session_id=session_id,
            message="accepted",
            fallback_text="accepted",
            trace_id="trace-action",
            data=self.action_data,
        )


def _client(runtime: FakeRuntime | None = None) -> TestClient:
    session_tokens = StaticSessionTokens()
    app = FastAPI()
    app.include_router(
        make_router(
            runtime or FakeRuntime(),
            make_require_principal(session_tokens, MemorySessionRevocations()),
            make_session_binder(),
        ),
        prefix="/api/v1/runtime",
    )
    client = TestClient(app, base_url="https://testserver")
    client.cookies.update(auth_cookies())
    return client


def _valid_body() -> dict[str, Any]:
    return {
        "channel": "web",
        "session_id": "session-1",
        "message": "hello",
        "client_capabilities": {},
    }


def _valid_action_body() -> dict[str, Any]:
    return {
        "channel": "web",
        "session_id": "session-1",
        "action": {
            "action_type": "confirm",
            "response_id": "response-1",
            "confirmed": True,
        },
    }


def test_runtime_handle_endpoint_returns_response_envelope_json_with_injected_runtime() -> None:
    runtime = FakeRuntime()
    response = _client(runtime).post("/api/v1/runtime/handle", json=_valid_body())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["schema_version"] == "phase0.sdui.v1"
    assert body["task_id"] == "task-1"
    assert body["session_id"].startswith("sid_v1.")
    assert body["trace_id"] == "trace-1"
    assert runtime.ai_user_ids == ["usr_v1_synthetic"]
    assert runtime.principals[0].org_ctx.tenant_id == "default"
    assert runtime.session_ids == [body["session_id"]]


def test_runtime_action_endpoint_dispatches_only_structured_user_action() -> None:
    runtime = FakeRuntime()
    response = _client(runtime).post(
        "/api/v1/runtime/action",
        json=_valid_action_body(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["data"] == {"action_outcome": "accepted", "result": None}
    assert runtime.ai_user_ids == ["usr_v1_synthetic"]
    assert runtime.session_ids == [body["session_id"]]


def test_runtime_action_endpoint_returns_exact_rejection_data_shape() -> None:
    runtime = FakeRuntime(
        action_data={
            "action_outcome": "action_reference_mismatch",
            "result": None,
        }
    )

    response = _client(runtime).post(
        "/api/v1/runtime/action",
        json=_valid_action_body(),
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "action_outcome": "action_reference_mismatch",
        "result": None,
    }


def test_runtime_action_endpoint_serializes_projected_result_object() -> None:
    runtime = FakeRuntime(
        action_data={
            "action_outcome": "accepted",
            "result": {"safe": "ok"},
        }
    )

    response = _client(runtime).post(
        "/api/v1/runtime/action",
        json=_valid_action_body(),
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "action_outcome": "accepted",
        "result": {"safe": "ok"},
    }


def test_action_response_data_accepts_every_outcome_and_rejects_unknown() -> None:
    outcomes = get_args(UserActionOutcome.__value__)

    for outcome in outcomes:
        assert ActionResponseData(action_outcome=outcome, result=None).action_outcome == outcome

    with pytest.raises(ValidationError):
        ActionResponseData(action_outcome="unknown", result=None)


@pytest.mark.parametrize(
    "invalid_data",
    (
        {"action_outcome": "accepted"},
        {"result": None},
        {
            "action_outcome": "accepted",
            "result": None,
            "unexpected": "blocked",
        },
        {
            "action_outcome": "accepted",
            "result": None,
            "business_key": "must-stay-inside-result",
        },
    ),
)
def test_action_response_envelope_rejects_missing_extra_or_flattened_data(
    invalid_data: dict[str, Any],
) -> None:
    invalid_data_envelope = ResponseEnvelopeBuilder().build_message(
        response_id="response-action-invalid",
        task_id="task-action-invalid",
        session_id="session-action-invalid",
        message="invalid",
        fallback_text="invalid",
        trace_id="trace-action-invalid",
        data=invalid_data,
    )

    with pytest.raises(ValidationError):
        ActionResponseEnvelope.model_validate(invalid_data_envelope.model_dump())


@pytest.mark.parametrize(
    "invalid_data",
    (
        {"action_outcome": "accepted"},
        {"result": None},
        {
            "action_outcome": "accepted",
            "result": None,
            "unexpected": "RAW_INVALID_ACTION_DATA",
        },
        {
            "action_outcome": "accepted",
            "result": None,
            "business_key": "RAW_FLATTENED_ACTION_DATA",
        },
        {"action_outcome": "future_outcome", "result": None},
    ),
)
def test_runtime_action_invalid_data_returns_deterministic_failed_envelope(
    invalid_data: dict[str, Any],
) -> None:
    runtime = FakeRuntime(action_data=invalid_data)
    response = _client(runtime).post(
        "/api/v1/runtime/action",
        json=_valid_action_body(),
    )

    assert runtime.calls == 1
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["message"] == (
        "操作响应未通过安全校验，无法确认本次操作结果。请先核对业务状态，避免重复提交。"
    )
    assert "未执行" not in response.text
    assert "retry" not in response.text.lower()
    assert response.json()["data"] == {
        "action_outcome": "action_gate_unavailable",
        "result": None,
    }
    assert response.json()["ui"]["action"] == "none"
    assert "RAW_" not in response.text


def test_runtime_action_endpoint_rejects_free_text_and_extra_fields() -> None:
    runtime = FakeRuntime()
    body = _valid_action_body()
    body["message"] = "confirm response-1"

    response = _client(runtime).post("/api/v1/runtime/action", json=body)

    assert response.status_code == 422
    assert runtime.calls == 0


def test_runtime_handle_endpoint_rejects_extra_fields() -> None:
    body = _valid_body()
    body["extra_field"] = "not allowed"

    response = _client().post("/api/v1/runtime/handle", json=body)

    assert response.status_code == 422


def test_runtime_handle_endpoint_cannot_inject_roles() -> None:
    runtime = FakeRuntime()
    body = _valid_body()
    body["roles"] = ["admin"]

    response = _client(runtime).post("/api/v1/runtime/handle", json=body)

    assert response.status_code == 422
    assert runtime.calls == 0


def test_formal_app_runtime_route_fails_closed_without_provider() -> None:
    session_tokens = StaticSessionTokens()
    client = TestClient(
        create_app(
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
        json=_valid_body(),
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "runtime_unavailable",
            "message": "Runtime provider is not configured.",
        }
    }


def test_formal_app_runtime_route_validates_before_unavailable() -> None:
    body = _valid_body()
    body["extra_field"] = "not allowed"

    session_tokens = StaticSessionTokens()
    client = TestClient(
        create_app(
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
        json=body,
    )

    assert response.status_code == 422


def test_missing_principal_precedes_body_validation() -> None:
    body = _valid_body()
    body["extra_field"] = "not allowed"

    response = TestClient(create_app(csrf_allowed_origins=TEST_CSRF_ALLOWED_ORIGINS)).post(
        "/api/v1/runtime/handle",
        json=body,
    )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "authentication_required"


@pytest.mark.parametrize("kind", ["cancel", "reject", "expired"])
def test_terminal_actions_follow_authenticated_csrf_bound_route(kind: str) -> None:
    import asyncio
    from datetime import timedelta

    from app.infra.auth.crypto import HMACSessionToken, PrincipalSessionBinder
    from tests.runtime.test_runtime_user_action import _START_MESSAGE, _build_harness

    harness = asyncio.run(_build_harness())
    tokens = HMACSessionToken(signing_key=bytes(range(32)), ttl_seconds=3600)
    binder = PrincipalSessionBinder(binding_key=bytes(reversed(range(32))))
    client = TestClient(
        create_app(
            runtime=harness.runtime,
            session_revocations=MemorySessionRevocations(),
            session_tokens=tokens,
            session_binder=binder.bind,
            csrf_allowed_origins=TEST_CSRF_ALLOWED_ORIGINS,
        ),
        base_url="https://testserver",
    )
    body = {
        "channel": "web",
        "session_id": "terminal-client-session",
        "action": {"action_type": "cancel", "response_id": "unknown"},
    }
    assert (
        client.post("/api/v1/runtime/action", json=body, headers=TEST_CSRF_HEADERS).status_code
        == 401
    )
    client.cookies.set("eternalai_session", tokens.issue(harness.principal))
    assert client.post("/api/v1/runtime/action", json=body).status_code == 403
    waiting = client.post(
        "/api/v1/runtime/handle",
        json={
            "channel": "web",
            "session_id": "terminal-client-session",
            "message": _START_MESSAGE,
            "client_capabilities": {},
        },
        headers=TEST_CSRF_HEADERS,
    )
    assert waiting.status_code == 200
    card = waiting.json()
    assert card["status"] == "waiting_user"
    body["session_id"] = card["session_id"]
    body["action"] = {
        "action_type": "confirm" if kind == "expired" else kind,
        "response_id": card["response_id"],
    }
    if kind == "expired":
        body["action"]["confirmed"] = True
        pending = harness.runtime._pending_workflows[
            (card["session_id"], harness.principal.ai_user_id)
        ]
        harness.runtime._utc_clock = lambda: pending.expires_at + timedelta(microseconds=1)
    invalid = {
        **body,
        "action": {"action_type": "cancel", "response_id": card["response_id"], "confirmed": True},
    }
    assert (
        client.post("/api/v1/runtime/action", json=invalid, headers=TEST_CSRF_HEADERS).status_code
        == 422
    )
    foreign = {
        **body,
        "session_id": binder.bind(
            harness.principal.model_copy(update={"ai_user_id": "foreign-user"}), "peer"
        ),
    }
    assert (
        client.post("/api/v1/runtime/action", json=foreign, headers=TEST_CSRF_HEADERS).status_code
        == 404
    )
    assert harness.engine.resume_calls == harness.gate.record_decision_calls == 0
    response = client.post("/api/v1/runtime/action", json=body, headers=TEST_CSRF_HEADERS)
    assert response.status_code == 200
    expected = "confirmation_invalidated" if kind == "expired" else "cancelled"
    assert response.json()["status"] == expected
    assert response.json()["data"] == {"action_outcome": expected, "result": None}
    assert harness.engine.resume_calls == 0
    assert harness.gate.record_decision_calls == (0 if kind == "expired" else 1)


class _CandidateOnlyLLM:
    """Select a wanted capability only when the host actually offered it."""

    def __init__(self, wanted: frozenset[str]) -> None:
        self.wanted = wanted
        self.calls: list[list[LLMMessage]] = []
        self.offered: list[list[str]] = []

    async def complete(
        self,
        messages: list[LLMMessage],
        model: str,
        response_format: dict[str, Any] | None = None,
    ) -> LLMCompletionResponse:
        self.calls.append(list(messages))
        segment = next(
            message.content
            for message in messages
            if message.role == "system" and '{"capability_candidates":' in message.content
        )
        items = json.loads(segment.split("\n", maxsplit=1)[1])["capability_candidates"]["items"]
        offered = [item["capability_id"] for item in items]
        self.offered.append(offered)
        chosen = next((item for item in offered if item in self.wanted), None)
        if chosen is None:
            return LLMCompletionResponse(content='{"match":"none"}', model_used=model)
        return LLMCompletionResponse(
            content=json.dumps({"match": "capability", "capability_id": chosen, "arguments": {}}),
            model_used=model,
        )

    chat = complete


class _CountingAdapter:
    def __init__(self) -> None:
        self.capability_ids: list[str] = []
        self.execution_contexts: list[dict[str, Any]] = []

    async def execute(
        self,
        capability_id: str,
        arguments: dict[str, Any],
        execution_context: dict[str, Any],
    ) -> AdapterResult:
        self.capability_ids.append(capability_id)
        self.execution_contexts.append(execution_context)
        return AdapterResult(status="success", data={})


class _TenantCandidatePolicy:
    """Explicit synthetic preview policy keyed only by trusted principal fields."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def preview_capability(
        self,
        *,
        ai_user_id: str,
        capability_id: str,
        request_context: RequestOrgContext,
    ) -> str:
        self.calls.append((ai_user_id, request_context.tenant_id, capability_id))
        owner_tenant = capability_id.removeprefix("zz.").removesuffix("-only")
        if capability_id.endswith("-only") and owner_tenant != request_context.tenant_id:
            return "exclude"
        return "defer"


def _topk_spec(capability_id: str, *, name: str | None = None) -> CapabilitySpec:
    output_schema = runtime_output_schema("registry_fakes.default")
    return CapabilitySpec(
        capability_id=capability_id,
        name=name or f"Synthetic {capability_id}",
        type="query",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        input_schema_digest=f"input-{capability_id}",
        output_schema=output_schema,
        output_schema_digest=schema_digest(output_schema),
        risk_level="low",
        owner="runtime-api-topk",
        version="1.0.0",
        status="active",
        short_description="Synthetic capability.",
        target_system=None,
        execution_identity="user_delegated",
        binding_required=False,
    )


def _topk_client(
    *,
    capabilities: list[CapabilitySpec],
    llm: _CandidateOnlyLLM,
    candidate_policy: Any,
    session_tokens: StaticSessionTokens,
    identity_mapping: MockIdentityMapping | PostgreSQLOAIdentityMapping | None = None,
) -> tuple[TestClient, _CountingAdapter, RuntimeImpl]:
    registry = StaticCapabilityRegistry(*capabilities)
    adapter = _CountingAdapter()
    trace_port = RecordingTracePort()
    builder = ResponseEnvelopeBuilder()
    runtime = RuntimeImpl(
        candidate_policy=candidate_policy,
        task_store=RecordingTaskStore(),
        session_store=ExistingSessionStore(),
        capability_registry=registry,
        orchestration=AgentOrchestrationAdapter(
            capability_registry=registry,
            gateway=CapabilityGateway(
                adapter=adapter,
                capability_registry=registry,
                identity_mapping=identity_mapping,
                policy_guard=MinimalPolicyGuard(),
                trace_port=trace_port,
            ),
            workflow_engine=None,
            response_builder=builder,
        ),
        trace_port=trace_port,
        llm_provider=llm,
        structured_output=JSONStructuredOutputProvider(),
        intent_model="topk-api-test",
        response_builder=builder,
    )
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
        backend_options={"loop_factory": make_event_loop},
    )
    client.cookies.update(auth_cookies())
    return client, adapter, runtime


def _handle_body(message: str, session_id: str = "topk-session") -> dict[str, Any]:
    return {
        "channel": "web",
        "session_id": session_id,
        "message": message,
        "client_capabilities": {},
    }


def test_authenticated_handle_reaches_ninth_capability_through_topk() -> None:
    target = _topk_spec("zz.tail-target", name="差旅补贴查询")
    earlier = [_topk_spec(f"aa.item-{index}") for index in range(8)]
    llm = _CandidateOnlyLLM(frozenset({"zz.tail-target"}))
    client, adapter, _runtime = _topk_client(
        capabilities=[*earlier, target],
        llm=llm,
        candidate_policy=MinimalPolicyGuard(),
        session_tokens=StaticSessionTokens(roles=("user",)),
    )

    response = client.post(
        "/api/v1/runtime/handle",
        headers=TEST_CSRF_HEADERS,
        json=_handle_body("帮我查询差旅补贴"),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert llm.offered == [["zz.tail-target"]]
    assert adapter.capability_ids == ["zz.tail-target"]
    assert response.json()["message"].endswith(
        "本次仅在相关性最高的部分能力中选择；若目标不符，请补充具体操作。"
    )


@pytest.fixture(params=["binding_rows", "postgresql"])
def topk_identity_mapping(request: pytest.FixtureRequest) -> Iterator[Any]:
    from uuid import uuid4

    subjects = {subject: "usr_v1_" + uuid4().hex + uuid4().hex[:11] for subject in ("a", "b")}
    if request.param == "binding_rows":
        yield (
            MockIdentityMapping(
                rows=[
                    {
                        "ai_user_id": user,
                        "target_system": "oa",
                        "execution_identity": "user_delegated",
                        "bind_status": "active",
                        "binding_id": f"oa-session-v1:{user}",
                    }
                    for user in subjects.values()
                ]
            ),
            subjects,
        )
        return

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from app.db.session import make_async_session_factory
    from scripts.check_dev_environment import resolve_database_configuration

    resolution = resolve_database_configuration()
    assert resolution.check.passed, "The fixed synthetic test database must be available"
    assert resolution.database_url is not None
    from app.db.config import normalize_database_url

    engine = create_async_engine(
        normalize_database_url(resolution.database_url), poolclass=NullPool
    )
    factory = make_async_session_factory(engine)
    now = datetime.now(UTC)

    async def insert_bindings() -> None:
        async with engine.begin() as connection:
            for user in subjects.values():
                await connection.execute(
                    text(
                        "INSERT INTO oa_session_credentials "
                        "(ai_user_id, cipher_version, nonce, encrypted_payload, "
                        "expires_at, updated_at) "
                        "VALUES (:user, 'synthetic-unused', :blob, :blob, :expiry, :now)"
                    ),
                    {
                        "user": user,
                        "blob": b"synthetic-unused-ciphertext",
                        "expiry": now + timedelta(hours=1),
                        "now": now,
                    },
                )

    async def clean_bindings() -> None:
        try:
            async with engine.begin() as connection:
                for user in subjects.values():
                    await connection.execute(
                        text("DELETE FROM oa_session_credentials WHERE ai_user_id = :user"),
                        {"user": user},
                    )
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=make_event_loop) as runner:
        try:
            runner.run(insert_bindings())
            yield PostgreSQLOAIdentityMapping(session_factory=factory), subjects
        finally:
            runner.run(clean_bindings())


def test_topk_does_not_cross_principal_or_session(topk_identity_mapping: Any) -> None:
    capabilities = [
        _topk_spec("zz.shared", name="共享报表查询"),
        _topk_spec("zz.tenant-a-only", name="甲方报表查询"),
        _topk_spec("zz.tenant-b-only", name="乙方报表查询"),
    ]
    capabilities = [
        spec.model_copy(update={"target_system": "oa", "binding_required": True})
        for spec in capabilities
    ]
    identity, subjects = topk_identity_mapping
    llm = _CandidateOnlyLLM(frozenset({"zz.tenant-a-only", "zz.tenant-b-only"}))
    policy = _TenantCandidatePolicy()
    tokens = StaticSessionTokens(roles=("user",))
    client, adapter, _runtime = _topk_client(
        capabilities=capabilities,
        llm=llm,
        candidate_policy=policy,
        session_tokens=tokens,
        identity_mapping=identity,
    )
    principal_a = Principal(
        ai_user_id=subjects["a"],
        display_name="Synthetic A",
        roles=("user",),
        org_ctx=PrincipalOrgContext(tenant_id="tenant-a"),
    )
    principal_b = Principal(
        ai_user_id=subjects["b"],
        display_name="Synthetic B",
        roles=("user",),
        org_ctx=PrincipalOrgContext(tenant_id="tenant-b"),
    )

    tokens.principal = principal_a
    first = client.post(
        "/api/v1/runtime/handle", headers=TEST_CSRF_HEADERS, json=_handle_body("报表查询")
    )
    tokens.principal = principal_b
    second = client.post(
        "/api/v1/runtime/handle", headers=TEST_CSRF_HEADERS, json=_handle_body("报表查询")
    )
    foreign_session = make_session_binder()(principal_a, "topk-session")
    foreign = client.post(
        "/api/v1/runtime/handle",
        headers=TEST_CSRF_HEADERS,
        json=_handle_body("报表查询", session_id=foreign_session),
    )
    unauthenticated = TestClient(client.app, base_url="https://testserver").post(
        "/api/v1/runtime/handle", headers=TEST_CSRF_HEADERS, json=_handle_body("报表查询")
    )

    assert first.status_code == second.status_code == 200
    assert first.json()["status"] == second.json()["status"] == "completed"
    assert llm.offered == [
        ["zz.shared", "zz.tenant-a-only"],
        ["zz.shared", "zz.tenant-b-only"],
    ]
    assert adapter.capability_ids == ["zz.tenant-a-only", "zz.tenant-b-only"]
    assert adapter.execution_contexts == [
        {"credential_ref": f"oa-session-v1:{subjects['a']}"},
        {"credential_ref": f"oa-session-v1:{subjects['b']}"},
    ]
    assert {(user, tenant) for user, tenant, _ in policy.calls} == {
        (subjects["a"], "tenant-a"),
        (subjects["b"], "tenant-b"),
    }
    second_prompt = "\n".join(message.content for message in llm.calls[1])
    assert "zz.tenant-a-only" not in second_prompt
    assert foreign.status_code == 404
    assert unauthenticated.status_code == 401
    assert len(llm.calls) == 2
    assert len(adapter.capability_ids) == 2

    tokens.principal = principal_a
    repeated = client.post(
        "/api/v1/runtime/handle", headers=TEST_CSRF_HEADERS, json=_handle_body("报表查询")
    )
    assert repeated.status_code == 200
    assert repeated.json()["status"] == "completed"
    memory_segments = [
        json.loads(message.content.split("\n", maxsplit=1)[1])["session_memory"]
        for message in llm.calls[2]
        if '"session_memory":' in message.content
    ]
    assert memory_segments == [
        [{"capability_id": "zz.tenant-a-only", "terminal_status": "completed"}]
    ]
    assert llm.offered[2] == ["zz.shared", "zz.tenant-a-only"]
    assert adapter.execution_contexts[2] == {"credential_ref": f"oa-session-v1:{subjects['a']}"}
