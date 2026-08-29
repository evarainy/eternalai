"""Gas-gap E2E: verified Principal + bound session + real runtime persistence."""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast
from urllib.request import OpenerDirector, Request

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import text

from app.composition import (
    build_credential_store,
    build_principal_role_reader,
    build_production_components,
)
from app.config import ProductionSettings
from app.db.session import make_async_session_factory
from app.execution_fabric.mock_adapters.oa.mock_oa_adapter import MockOAAdapter
from app.infra.auth.crypto import identity_surrogate
from app.infra.auth.oa import OACredentialVerifier
from app.infra.identity.mock_identity_mapping import MockIdentityMapping
from app.infra.llm.openai_compatible import OpenAICompatibleLLMProvider
from app.infra.observability.postgresql_trace import (
    PostgreSQLTraceReader,
    PostgreSQLTraceWriter,
)
from app.infra.persistence.capability_registry.repository import (
    PostgreSQLCapabilityRegistry,
)
from app.infra.persistence.task_store.postgresql import PostgreSQLTaskStore
from app.main import create_app
from app.ports.auth import LoginCredential
from app.ports.capability_registry import CapabilitySpec
from scripts.smoke import full_chain as smoke_full_chain
from scripts.smoke.capabilities import expected_oa_capabilities
from scripts.smoke.full_chain_contract import FullChainOutcome
from scripts.smoke.trace_contract import (
    REQUIRED_TRACE_EVENTS as _REQUIRED_TRACE_EVENTS,
)
from tests.auth_fakes import TEST_CSRF_ALLOWED_ORIGINS, TEST_CSRF_HEADERS
from tests.runtime.registry_fakes import runtime_output_schema, schema_digest

if hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # type: ignore[attr-defined]

_CAPABILITY_ID = "oa.list_pending_workflows"
_LOGIN_ID = "pilot-fixture-login"
_PASSWORD = "pilot-fixture-passphrase"
_COOKIE_SENTINELS = {
    "ecology_JSessionid": "fixture-jsession-cookie",
    "loginidweaver": "fixture-login-cookie",
    "loginuuids": "fixture-uuid-cookie",
}


@dataclass(frozen=True)
class PilotObservation:
    status_code: int
    envelope_status: str
    session_was_bound: bool
    client_identity_was_ignored: bool
    trace_event_types: frozenset[str]
    sensitive_values_absent: bool
    llm_request_count: int


class FakeOAHttpSession:
    def __init__(self, rsa_public_key: str) -> None:
        self._rsa_public_key = rsa_public_key

    async def get_json(
        self,
        path: str,
        parameters: dict[str, str],
    ) -> dict[str, Any]:
        if path == "/rsa/weaver.rsa.GetRsaInfo":
            return {
                "rsa_pub": self._rsa_public_key,
                "rsa_code": "fixture-code",
                "rsa_flag": "RSA",
            }
        assert path == "/api/hrm/usericon/getUserIcon"
        return {"lastname": "Pilot Fixture User"}

    async def post_form(
        self,
        path: str,
        fields: dict[str, str],
    ) -> dict[str, Any]:
        assert path == "/api/hrm/login/checkLogin"
        assert fields["loginid"] != _LOGIN_ID
        assert fields["userpassword"] != _PASSWORD
        return {
            "msgcode": "0",
            "loginstatus": True,
            "userid": "fixture-oa-user",
        }

    def cookies(self) -> dict[str, str]:
        return dict(_COOKIE_SENTINELS)


class FakeLLMResponse:
    def __init__(self) -> None:
        content = {
            "capability_id": _CAPABILITY_ID,
            "arguments": {},
            "target_system": "oa",
            "capability_type": "query",
        }
        self._raw = json.dumps(
            {
                "model": "qwen3.5-27b",
                "choices": [{"message": {"content": json.dumps(content)}}],
                "usage": {"total_tokens": 37},
            }
        ).encode("utf-8")

    def __enter__(self) -> FakeLLMResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def getcode(self) -> int:
        return 200

    def read(self, _limit: int) -> bytes:
        return self._raw


class RecordingLLMOpener:
    def __init__(self) -> None:
        self.requests: list[Request] = []

    def open(self, request: Request, timeout: float) -> FakeLLMResponse:
        assert timeout == 120
        self.requests.append(request)
        return FakeLLMResponse()


def _rsa_public_key() -> str:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_der = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return base64.b64encode(public_der).decode("ascii")


async def _healthy() -> bool:
    return True


def test_pilot_request_uses_verified_principal_and_persists_full_trace() -> None:
    observation = asyncio.run(_run_pilot_request())

    assert observation.status_code == 200
    assert observation.envelope_status == "completed"
    assert observation.session_was_bound is True
    assert observation.client_identity_was_ignored is True
    assert _REQUIRED_TRACE_EVENTS <= observation.trace_event_types
    assert observation.sensitive_values_absent is True
    assert observation.llm_request_count == 1


@pytest.mark.parametrize(
    ("capability_id", "probe", "pack_name"),
    [
        (
            "oa.list_pending_workflows",
            "查询我的待办",
            "ecology9-pending-workflows-v3",
        ),
        (
            "oa.list_system_messages",
            "查询我的系统消息",
            "ecology9-system-messages-v1",
        ),
    ],
)
def test_replay_oa_provider_runs_through_complete_runtime_chain(
    capability_id: str,
    probe: str,
    pack_name: str,
) -> None:
    outcome = asyncio.run(
        _run_replay_full_chain(
            capability_id=capability_id,
            probe=probe,
            pack_name=pack_name,
        )
    )

    assert outcome.passed(expected_capability_ids=(capability_id,)) is True
    assert len(outcome.capabilities) == 1
    assert outcome.capabilities[0].capability_id == capability_id
    assert outcome.capabilities[0].trace_events_complete is True


def test_http_replay_full_chain_sends_session_cookie_and_recovers_principal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CSRF_ALLOWED_ORIGINS", "http://testserver")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    runtime_status_codes: list[int] = []
    principal_observations: list[str] = []
    original_post = httpx.AsyncClient.post

    async def record_runtime_status(
        client: httpx.AsyncClient,
        url: str,
        *args: Any,
        **kwargs: Any,
    ) -> httpx.Response:
        response = await original_post(client, url, *args, **kwargs)
        if url == "/api/v1/runtime/handle":
            runtime_status_codes.append(response.status_code)
        return response

    monkeypatch.setattr(httpx.AsyncClient, "post", record_runtime_status)
    settings = ProductionSettings.from_environment()

    outcome = asyncio.run(
        _run_replay_full_chain(
            capability_id="oa.list_pending_workflows",
            probe="查询我的待办",
            pack_name="ecology9-pending-workflows-v3",
            principal_observations=principal_observations,
        )
    )

    assert runtime_status_codes == [200]
    assert principal_observations == [identity_surrogate(_LOGIN_ID, key=settings.identity_hmac_key)]
    assert outcome.passed(expected_capability_ids=("oa.list_pending_workflows",)) is True


async def _run_replay_full_chain(
    *,
    capability_id: str,
    probe: str,
    pack_name: str,
    principal_observations: list[str] | None = None,
) -> FullChainOutcome:
    base_settings = ProductionSettings.from_environment()
    pack_dir = Path(__file__).parents[1] / "contract_packs" / "oa" / pack_name
    settings = replace(
        base_settings,
        environment_name="production",
        oa_read_adapter_mode="replay",
        oa_read_contract_pack_dir=pack_dir,
        phase0_mock_mode=False,
    )
    session_factory = make_async_session_factory(database_url=settings.database_url)
    ai_user_id = identity_surrogate(_LOGIN_ID, key=settings.identity_hmac_key)
    llm_provider, authentication = smoke_full_chain._build_replay_dependencies(settings)
    capability = next(
        item for item in expected_oa_capabilities() if item.capability_id == capability_id
    )
    await _cleanup(
        session_factory,
        ai_user_id,
        capability_ids=(capability_id,),
    )
    await PostgreSQLCapabilityRegistry(session_factory).create(capability)
    try:
        outcome = await smoke_full_chain.run_full_chain_check(
            settings,
            LoginCredential(loginid=_LOGIN_ID, userpassword=_PASSWORD),
            probes=((probe, capability_id),),
            llm_provider=llm_provider,
            authentication=authentication,
        )
        if principal_observations is not None:
            tasks = await PostgreSQLTaskStore(session_factory).list_tasks(ai_user_id=ai_user_id)
            principal_observations.extend(task.ai_user_id for task in tasks)
        return outcome
    finally:
        await _cleanup(
            session_factory,
            ai_user_id,
            capability_ids=(capability_id,),
        )


async def _run_pilot_request() -> PilotObservation:
    settings = ProductionSettings.from_environment()
    session_factory = make_async_session_factory(database_url=settings.database_url)
    ai_user_id = identity_surrogate(_LOGIN_ID, key=settings.identity_hmac_key)
    authentication = OACredentialVerifier(
        session_factory=lambda: FakeOAHttpSession(_rsa_public_key()),
        credential_store=build_credential_store(
            session_factory=session_factory,
            encryption_key=settings.credential_encryption_key,
        ),
        role_reader=build_principal_role_reader(session_factory=session_factory),
        identity_hmac_key=settings.identity_hmac_key,
        credential_ttl_seconds=settings.oa_credential_ttl_seconds,
    )
    llm_opener = RecordingLLMOpener()
    llm_provider = OpenAICompatibleLLMProvider(
        base_url=settings.llm_base_url,
        timeout_seconds=settings.llm_timeout_seconds,
        max_tokens=settings.llm_max_tokens,
        temperature=settings.llm_temperature,
        top_p=settings.llm_top_p,
        top_k=settings.llm_top_k,
        enable_thinking=settings.llm_enable_thinking,
        opener=cast(OpenerDirector, llm_opener),
    )
    identity_mapping = MockIdentityMapping(
        rows=(
            {
                "ai_user_id": ai_user_id,
                "bind_status": "active",
                "binding_id": "fixture-binding",
                "target_system": "oa",
                "execution_identity": "user_delegated",
                "binding_scope": "pilot-fixture",
                "account_set_id": None,
                "device_domain_id": None,
                "reason_code": None,
            },
        )
    )
    adapter = MockOAAdapter()
    adapter.set_state(
        {
            "pending_workflows": [
                {
                    "message_id": "fixture-message",
                    "title": "Fixture workflow",
                    "content": "Fixture workflow content",
                    "source_name": "Fixture workflow type",
                    "occurred_at": "2026-08-06 09:00:00",
                    "business_state": "1",
                    "link": "/workflow/fixture-message",
                    "mobile_link": "/mobile/workflow/fixture-message",
                }
            ]
        }
    )
    components = build_production_components(
        settings,
        llm_provider=llm_provider,
        authentication=authentication,
        identity_mapping=identity_mapping,
        adapters={"oa": adapter},
        trace_port=PostgreSQLTraceWriter(session_factory),
        health_checks={
            "database": _healthy,
            "redis": _healthy,
            "vllm": _healthy,
        },
    )
    application = create_app(
        runtime=components.runtime,
        admin_registry_service=components.admin_registry_service,
        authentication=components.authentication,
        session_tokens=components.session_tokens,
        session_binder=components.session_binder.bind,
        session_cookie_ttl_seconds=components.session_cookie_ttl_seconds,
        health_checks=dict(components.health_checks),
        csrf_allowed_origins=TEST_CSRF_ALLOWED_ORIGINS,
    )
    registry = PostgreSQLCapabilityRegistry(session_factory)
    await _cleanup(session_factory, ai_user_id)
    output_schema = runtime_output_schema("test_pilot_foundation_e2e.pending_workflows")
    await registry.create(
        CapabilitySpec(
            capability_id=_CAPABILITY_ID,
            name="OA pending workflow fixture",
            type="query",
            intent_tags=["pending-workflows"],
            input_schema={},
            output_schema=output_schema,
            input_schema_digest="fixture-input",
            output_schema_digest=schema_digest(output_schema),
            risk_level="low",
            owner="pilot-fixture",
            version="1.0.0",
            status="active",
            short_description="Gas-gap pilot capability",
            target_system="oa",
            execution_identity="user_delegated",
            binding_required=True,
        )
    )
    try:
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="https://testserver",
        ) as client:
            login_response = await client.post(
                "/api/v1/auth/login",
                headers=TEST_CSRF_HEADERS,
                json={
                    "loginid": _LOGIN_ID,
                    "userpassword": _PASSWORD,
                },
            )
            assert login_response.status_code == 200
            response = await client.post(
                "/api/v1/runtime/handle",
                headers={
                    **TEST_CSRF_HEADERS,
                    "X-EternalAI-Roles": "admin",
                    "X-EternalAI-AI-User-ID": "self-reported-attacker",
                    "X-EternalAI-Session-ID": "self-reported-session",
                },
                json={
                    "channel": "web",
                    "session_id": "pilot-client-session",
                    "message": "查询我的 OA 待办",
                    "client_capabilities": {},
                },
            )
        assert response.status_code == 200
        envelope = response.json()
        assert envelope["status"] == "completed"
        assert envelope["session_id"].startswith("sid_v1.")
        assert envelope["session_id"] != "pilot-client-session"
        assert envelope["data"] == {
            "workflows": [
                {
                    "message_id": "fixture-message",
                    "title": "Fixture workflow",
                    "content": "Fixture workflow content",
                    "source_name": "Fixture workflow type",
                    "occurred_at": "2026-08-06 09:00:00",
                    "business_state": "1",
                    "link": "/workflow/fixture-message",
                    "mobile_link": "/mobile/workflow/fixture-message",
                }
            ]
        }

        tasks = await PostgreSQLTaskStore(session_factory).list_tasks(ai_user_id=ai_user_id)
        assert len(tasks) == 1
        assert tasks[0].session_id == envelope["session_id"]
        assert tasks[0].ai_user_id == ai_user_id
        assert tasks[0].ai_user_id != "self-reported-attacker"

        trace_events = await PostgreSQLTraceReader(session_factory).list_events_by_trace(
            envelope["trace_id"]
        )
        event_types = frozenset(event.event_type for event in trace_events)
        persisted_trace = repr(trace_events)
        sensitive_values_absent = all(
            forbidden not in persisted_trace
            for forbidden in (
                _LOGIN_ID,
                _PASSWORD,
                *_COOKIE_SENTINELS.values(),
                "self-reported-attacker",
                "self-reported-session",
            )
        )

        request_payload = json.loads(cast(bytes, llm_opener.requests[0].data))
        assert request_payload["response_format"] == {"type": "json_object"}
        assert request_payload["model"] == "qwen3.5-27b"
        return PilotObservation(
            status_code=response.status_code,
            envelope_status=str(envelope["status"]),
            session_was_bound=(
                envelope["session_id"].startswith("sid_v1.")
                and envelope["session_id"] != "pilot-client-session"
            ),
            client_identity_was_ignored=(
                tasks[0].ai_user_id == ai_user_id
                and tasks[0].ai_user_id != "self-reported-attacker"
            ),
            trace_event_types=event_types,
            sensitive_values_absent=sensitive_values_absent,
            llm_request_count=len(llm_opener.requests),
        )
    finally:
        await _cleanup(session_factory, ai_user_id)


async def _cleanup(
    session_factory: Any,
    ai_user_id: str,
    *,
    capability_ids: tuple[str, ...] = (_CAPABILITY_ID,),
) -> None:
    async with session_factory() as session:
        await session.execute(
            text(
                "DELETE FROM trace_events"
                " WHERE task_id IN"
                " (SELECT task_id FROM tasks WHERE ai_user_id = :ai_user_id)"
            ),
            {"ai_user_id": ai_user_id},
        )
        await session.execute(
            text(
                "DELETE FROM task_events"
                " WHERE task_id IN"
                " (SELECT task_id FROM tasks WHERE ai_user_id = :ai_user_id)"
            ),
            {"ai_user_id": ai_user_id},
        )
        await session.execute(
            text(
                "DELETE FROM sessions"
                " WHERE session_id IN"
                " (SELECT session_id FROM tasks WHERE ai_user_id = :ai_user_id)"
            ),
            {"ai_user_id": ai_user_id},
        )
        await session.execute(
            text("DELETE FROM tasks WHERE ai_user_id = :ai_user_id"),
            {"ai_user_id": ai_user_id},
        )
        await session.execute(
            text("DELETE FROM oa_session_credentials WHERE ai_user_id = :ai_user_id"),
            {"ai_user_id": ai_user_id},
        )
        for capability_id in capability_ids:
            await session.execute(
                text("DELETE FROM capabilities WHERE capability_id = :capability_id"),
                {"capability_id": capability_id},
            )
        await session.commit()
