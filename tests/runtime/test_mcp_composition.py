"""Real production composition, fixed PG and independent service listeners."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest

from app.composition import build_production_components
from app.config import ProductionSettings
from app.event_loop import make_event_loop
from app.execution_fabric.mock_adapters.oa.mock_oa_adapter import MockOAAdapter
from app.infra.adapters.business_mcp.catalog import catalog
from app.infra.llm.mock_llm.mock_llm_provider import MockLLMProvider
from app.infra.llm.mock_structured_output.mock_structured_output_provider import (
    MockStructuredOutputProvider,
)
from app.infra.persistence.capability_registry.repository import PostgreSQLCapabilityRegistry
from app.infra.persistence.task_store.postgresql import PostgreSQLTaskStore
from app.main import create_app
from app.mcp.models import McpFailure
from app.ports.auth import (
    AuthenticatedSessionContext,
    Principal,
    PrincipalOrgContext,
    VerifiedSessionToken,
    authenticated_session,
)
from app.ports.capability_gateway import RequestOrgContext
from app.ports.human_gate import build_task_version_binding_manifest
from app.ports.task_store import TaskRecord
from app.runtime.models import IntentOutput, MatchedIntent
from app.version_binding import capability_version_bindings
from tests.auth_fakes import TEST_CSRF_ALLOWED_ORIGINS, TEST_CSRF_HEADERS
from tests.infra.mcp.test_transport import serving
from tests.infra.persistence.test_mcp_store import bind, database
from tests.mcp.test_contracts import VALID
from tests.workflow.test_engine import RecordingTrace
from tests.workflow.test_mcp_recovery import BusinessPeer, output_contract


def test_http_runtime_confirmation_failure_and_apps_recovery_are_wired(
    migrated_database_url, monkeypatch
):
    peer = BusinessPeer("2025-11-25", fault="http500")
    with serving(peer) as profile:

        async def run():
            async with database(migrated_database_url) as (fixture_store, _, revocations):
                monkeypatch.setattr(
                    "app.composition.make_async_session_factory", lambda **_: fixture_store.sessions
                )
                tool = "talk_preparation_save"
                outer = f"business.{profile.service_config_id}.{tool}"
                structured = MockStructuredOutputProvider()
                for message in ("synthetic-chat-request", outer):
                    structured.register(
                        message,
                        IntentOutput,
                        MatchedIntent(
                            match="capability",
                            capability_id=outer,
                            arguments=VALID[tool],
                            capability_type="workflow",
                            target_system="business_platform",
                        ),
                    )
                llm = MockLLMProvider()
                settings = replace(
                    ProductionSettings.from_environment(),
                    mcp_services=(profile,),
                    database_url=migrated_database_url,
                    credential_encryption_key=b"s" * 32,
                )
                components = build_production_components(
                    settings,
                    adapters={"oa": MockOAAdapter()},
                    llm_provider=llm,
                    structured_output=structured,
                    mcp_contracts={(profile.service_config_id, tool): output_contract()},
                    mcp_isolated_test_contracts=True,
                )
                store = components.mcp_service.oauth.store
                await bind(store, profile)
                registry = PostgreSQLCapabilityRegistry(store.sessions)
                specs, mappings = catalog(profile, {tool: output_contract()})
                for spec in specs:
                    await registry.create(spec)
                # Exercise the real selector above its eight-candidate limit.
                # All synthetic distractors share this fixture's rollback transaction.
                template = next(spec for spec in specs if spec.capability_id == outer)
                for index in range(9):
                    unrelated = f"unrelatedcatalog{uuid4().hex}x{index}"
                    await registry.create(
                        template.model_copy(
                            update={
                                "capability_id": unrelated,
                                "name": unrelated,
                                "intent_tags": [unrelated],
                                "short_description": unrelated,
                            }
                        )
                    )
                for mapping in mappings:
                    await store.bind_capability(mapping)

                class Tokens:
                    def inspect(self, value):
                        return VerifiedSessionToken(
                            principal=Principal(
                                ai_user_id="synthetic-user" if value == "owner" else "other-user",
                                display_name="Synthetic",
                                roles=(),
                                org_ctx=PrincipalOrgContext(tenant_id="default"),
                            ),
                            fingerprint=b"synthetic-session",
                            expires_at=datetime.now(UTC) + timedelta(minutes=5),
                            version=2,
                        )

                application = create_app(
                    runtime=components.runtime,
                    mcp_service=components.mcp_service,
                    session_tokens=Tokens(),
                    session_revocations=revocations,
                    session_binder=components.session_binder.bind,
                    csrf_allowed_origins=TEST_CSRF_ALLOWED_ORIGINS,
                )
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=application),
                    base_url="https://testserver",
                    cookies={"eternalai_session": "owner"},
                ) as client:
                    response = await client.post(
                        "/api/v1/runtime/handle",
                        headers=TEST_CSRF_HEADERS,
                        json={
                            "channel": "web",
                            "session_id": "synthetic-browser",
                            "message": "synthetic-chat-request",
                            "client_capabilities": {},
                        },
                    )
                    assert response.status_code == 200
                    # A registered mock answer cannot bypass relevance selection.
                    assert response.json()["status"] == "failed"
                    assert llm.calls == [] and peer.effects == 0
                    response = await client.post(
                        "/api/v1/runtime/handle",
                        headers=TEST_CSRF_HEADERS,
                        json={
                            "channel": "web",
                            "session_id": "synthetic-browser",
                            "message": outer,
                            "client_capabilities": {},
                        },
                    )
                    assert response.status_code == 200
                    assert len(llm.calls) == 1
                    pending = response.json()
                    assert (
                        pending["status"] == "waiting_user"
                        and pending["ui"]["target_system"] == "business_platform"
                    )
                    operation_id = pending["data"]["operation_id"]
                    assert (
                        len(operation_id) == 32
                        and pending["data"]["state"] == "WAITING_LOCAL_CONFIRM"
                    )
                    assert peer.effects == 0
                    response = await client.post(
                        "/api/v1/runtime/action",
                        headers=TEST_CSRF_HEADERS,
                        json={
                            "channel": "web",
                            "session_id": pending["session_id"],
                            "action": {
                                "action_type": "confirm",
                                "response_id": pending["response_id"],
                                "confirmed": True,
                            },
                        },
                    )
                    assert response.status_code == 200
                    failed = response.json()
                    assert failed["status"] == "failed"
                    assert failed["data"]["result"] == {
                        "operation_id": operation_id,
                        "state": "UNKNOWN",
                    }
                    assert peer.effects == 1
                    listing = await client.get("/api/v1/mcp/operations")
                    assert listing.status_code == 200 and len(listing.json()) == 1
                    operation = listing.json()[0]
                    assert (
                        operation["operation_id"] == operation_id
                        and operation["recovery_action"] == "manual_reconcile"
                    )
                    assert operation["argument_preview"] == {"personId": "p1"}
                    assert "outline" not in operation and "payloadHash" not in operation
                    assert (
                        await client.get(f"/api/v1/mcp/operations/{operation_id}")
                    ).json() == operation
                    client.cookies.set("eternalai_session", "other")
                    assert (await client.get("/api/v1/mcp/operations")).json() == []
                    assert peer.effects == 1

        asyncio.run(run(), loop_factory=make_event_loop)


def test_two_services_same_user_same_tool_are_isolated_after_composition_restart(
    migrated_database_url, monkeypatch
):
    first, second = BusinessPeer("2025-11-25"), BusinessPeer("2026-07-28")
    with serving(first) as one, serving(second) as two:
        one = one.model_copy(update={"service_config_id": "service-a"})
        two = two.model_copy(update={"service_config_id": "service-b"})

        async def run():
            async with database(migrated_database_url) as (fixture_store, _, _):
                monkeypatch.setattr(
                    "app.composition.make_async_session_factory", lambda **_: fixture_store.sessions
                )
                settings = replace(
                    ProductionSettings.from_environment(),
                    mcp_services=(one, two),
                    database_url=migrated_database_url,
                    credential_encryption_key=b"s" * 32,
                )
                contracts = {
                    (p.service_config_id, "business_context_get"): output_contract()
                    for p in (one, two)
                }

                def build():
                    return build_production_components(
                        settings,
                        adapters={"oa": MockOAAdapter()},
                        trace_port=RecordingTrace(),
                        mcp_contracts=contracts,
                        mcp_isolated_test_contracts=True,
                    )

                with pytest.raises(McpFailure, match="mcp_output_contract_unapproved"):
                    build_production_components(
                        settings,
                        adapters={"oa": MockOAAdapter()},
                        trace_port=RecordingTrace(),
                        mcp_contracts=contracts,
                    )
                components = build()
                store = components.mcp_service.oauth.store
                registry, tasks = (
                    PostgreSQLCapabilityRegistry(store.sessions),
                    PostgreSQLTaskStore(store.sessions),
                )
                refs = {}
                for p in (one, two):
                    await bind(store, p, token_value="synthetic-token-" + p.service_config_id)
                    specs, mappings = catalog(p, {"business_context_get": output_contract()})
                    for spec in specs:
                        await registry.create(spec)
                    for mapping in mappings:
                        await store.bind_capability(mapping)
                    capability = next(item for item in specs if item.name == "business_context_get")
                    task = uuid4().hex
                    await tasks.create_task(
                        TaskRecord(
                            task_id=task,
                            session_id="synthetic-chat",
                            ai_user_id="synthetic-user",
                            tenant_id="default",
                            status="running",
                        )
                    )
                    await components.mcp_service.operations.gates.bind_task(
                        build_task_version_binding_manifest(
                            task_id=task,
                            bindings=capability_version_bindings(capability),
                            locked_at=datetime.now(UTC),
                        )
                    )
                    refs[p.service_config_id] = (task, capability.capability_id)
                token = authenticated_session.set(
                    AuthenticatedSessionContext(
                        principal=Principal(
                            ai_user_id="synthetic-user",
                            display_name="Synthetic",
                            roles=(),
                            org_ctx=PrincipalOrgContext(tenant_id="default"),
                        ),
                        fingerprint=b"synthetic-session",
                        expires_at=datetime.now(UTC) + timedelta(minutes=5),
                    )
                )
                try:

                    async def call(service, current):
                        task, capability = refs[service]
                        return await current.mcp_service.operations.gateway.execute_capability(
                            task,
                            "synthetic-chat",
                            "synthetic-user",
                            capability,
                            {},
                            RequestOrgContext(request_id=uuid4().hex, tenant_id="default"),
                        )

                    for current in (components, build()):
                        assert (await call("service-a", current)).status == "completed"
                        assert (await call("service-b", current)).status == "completed"
                    assert (first.effects, second.effects) == (2, 2)
                    contexts = components.mcp_service.operations.contexts
                    a_task, a_capability = refs["service-a"]
                    a_mapping = await store.mapping(a_capability, "1.0.0")
                    context = await contexts.build(
                        task_id=a_task,
                        chat_session_id="synthetic-chat",
                        user_id="synthetic-user",
                        tenant_id="default",
                        mapping=a_mapping,
                    )
                    b_connection = await store.connection(
                        tenant_id="default", user_id="synthetic-user", service_config_id="service-b"
                    )
                    for change in (
                        {"service_config_id": "service-b"},
                        {"connection_id": b_connection.connection_id},
                        {"registration_id": b_connection.registration_id},
                    ):
                        with pytest.raises(McpFailure, match="mcp_authorization_invalid"):
                            await store.resolve(context.model_copy(update=change))
                    assert (first.effects, second.effects) == (2, 2)
                    await store.disconnect(
                        tenant_id="default",
                        user_id="synthetic-user",
                        connection_id=context.connection_id,
                    )
                    assert (await call("service-a", build())).status == "denied"
                    assert (await call("service-b", build())).status == "completed"
                    assert (first.effects, second.effects) == (2, 3)
                    headers_a = {
                        value
                        for _, headers, _ in first.calls
                        for key, value in headers.items()
                        if key.lower() == "authorization"
                    }
                    headers_b = {
                        value
                        for _, headers, _ in second.calls
                        for key, value in headers.items()
                        if key.lower() == "authorization"
                    }
                    assert headers_a == {"Bearer synthetic-token-service-a"}
                    assert headers_b == {"Bearer synthetic-token-service-b"}
                    assert context.registration_id != b_connection.registration_id
                finally:
                    authenticated_session.reset(token)

        asyncio.run(run(), loop_factory=make_event_loop)
