"""Real production composition, fixed PG and independent service listeners."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from functools import partial
from uuid import uuid4

import httpx
import pytest

from app.composition import build_production_components
from app.config import ProductionSettings
from app.event_loop import make_event_loop
from app.execution_fabric.mock_adapters.oa.mock_oa_adapter import MockOAAdapter
from app.infra.adapters.business_mcp.catalog import catalog
from app.infra.adapters.business_mcp.fixed_read_profile import (
    RESOURCE,
    SERVICE_CONFIG_ID,
    FixedReadApproval,
    build_fixed_read_profile,
)
from app.infra.llm.mock_llm.mock_llm_provider import MockLLMProvider
from app.infra.llm.mock_structured_output.mock_structured_output_provider import (
    MockStructuredOutputProvider,
)
from app.infra.persistence.capability_registry.repository import PostgreSQLCapabilityRegistry
from app.infra.persistence.task_store.postgresql import PostgreSQLTaskStore
from app.main import create_app
from app.mcp.models import McpFailure, Ownership, ServiceConfig
from app.ports.auth import (
    AuthenticatedSessionContext,
    Principal,
    PrincipalOrgContext,
    VerifiedSessionToken,
    authenticated_session,
)
from app.ports.capability_gateway import RequestOrgContext
from app.ports.human_gate import build_task_version_binding_manifest
from app.ports.mcp_store import AuthorizationTransaction, Connection
from app.ports.task_store import TaskRecord
from app.runtime.models import IntentOutput, MatchedIntent
from app.version_binding import capability_version_bindings
from tests.auth_fakes import TEST_CSRF_ALLOWED_ORIGINS, TEST_CSRF_HEADERS
from tests.infra.mcp.test_transport import serving
from tests.infra.persistence.test_mcp_store import bind, database
from tests.mcp.test_contracts import VALID
from tests.workflow.test_engine import RecordingTrace
from tests.workflow.test_mcp_recovery import (
    BusinessPeer,
    output_contract,
    read_output_contract,
)


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
                    assert failed["data"]["action_outcome"] == "accepted"
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
                    (p.service_config_id, "business_context_get"): read_output_contract()
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
                    specs, mappings = catalog(p, {"business_context_get": read_output_contract()})
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
                    after_disconnect = await call("service-a", build())
                    assert (after_disconnect.status, after_disconnect.error_code) == (
                        "binding_required",
                        "identity_expired",
                    )
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


def test_failed_rebind_voids_older_attempt_so_its_late_callback_cannot_open_reads():
    service = ServiceConfig(
        service_config_id=SERVICE_CONFIG_ID,
        service_config_version=1,
        deployment_id="synthetic",
        tenant_id="default",
        display_name="Synthetic",
        endpoint=RESOURCE,
        issuer="https://provider.invalid",
        resource=RESOURCE,
        registration_endpoint="https://provider.invalid/register",
        authorization_endpoint="https://provider.invalid/authorize",
        token_endpoint="https://provider.invalid/token",
        callback_uri="https://client.invalid/callback",
        callback_config_version=1,
        enabled=True,
    )
    profile = build_fixed_read_profile(
        service,
        FixedReadApproval(
            service_config_id=SERVICE_CONFIG_ID,
            service_config_version=1,
            tenant_id="default",
            local_owner_source="authenticated_session",
            version="synthetic-identity-v1",
            approval_evidence="synthetic",
            issuer_parameter_supported=True,
            identity_binding_mode="oauth_owner",
        ),
    )
    principal = Principal(
        ai_user_id="synthetic-user",
        display_name="Synthetic",
        roles=(),
        org_ctx=PrincipalOrgContext(tenant_id="default"),
    )
    old = AuthenticatedSessionContext(
        principal=principal,
        fingerprint=b"o" * 32,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    new = replace(old, fingerprint=b"n" * 32)
    other = replace(old, principal=principal.model_copy(update={"ai_user_id": "other-user"}))

    class Revocations:
        async def is_revoked(self, fingerprint: bytes) -> bool:
            return False

    class Store:
        connected: Connection | None = None
        disconnect_fails = False
        disconnects = 0

        async def connection(self, **_):
            return self.connected

        async def disconnect(self, **_):
            if self.disconnect_fails:
                raise McpFailure("synthetic_disconnect_failed")
            self.disconnects += 1
            return True

    owner = Ownership(
        tenant_id="default",
        user_id="synthetic-user",
        service_config_id=SERVICE_CONFIG_ID,
        connection_id="synthetic-connection",
    )

    def transaction(session, epoch):
        return AuthorizationTransaction(
            state_digest=f"synthetic-state-{epoch}",
            owner=owner,
            registration_id="synthetic-registration",
            client_id="synthetic-client",
            binding_epoch=epoch,
            service_config_version=1,
            login_session_fingerprint=session.fingerprint.hex(),
            issuer=service.issuer,
            resource=service.resource,
            callback_uri=service.callback_uri,
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
            verifier="synthetic-verifier",
        )

    def active(txn):
        return Connection(
            **owner.model_dump(),
            registration_id=txn.registration_id,
            service_config_version=1,
            binding_epoch=txn.binding_epoch,
            grant_epoch=1,
            login_session_fingerprint=txn.login_session_fingerprint,
            state="ACTIVE",
            identity_policy_version="synthetic-identity-v1",
            identity_evidence="synthetic-evidence",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )

    def readable(session):
        token = authenticated_session.set(session)
        try:
            return profile.permits(
                tenant_id="default", user_id="synthetic-user", service_config_id=SERVICE_CONFIG_ID
            )
        finally:
            authenticated_session.reset(token)

    async def begin(attempt, txn):
        async with profile.authorization_lock:
            profile.claim_authorization_attempt(attempt, None)
            profile.claim_authorization_attempt(attempt, txn)

    async def run():
        store, revocations = Store(), Revocations()
        bind = partial(profile.bind_authorization_owner, revocations=revocations, store=store)
        complete = partial(
            profile.complete_authorization_owner, store=store, revocations=revocations
        )

        stale = await bind(SERVICE_CONFIG_ID, old)
        stale_txn = transaction(old, 1)
        await begin(stale, stale_txn)
        assert profile.authorization_transaction_attempt(stale_txn) == stale
        # The old callback already stored ACTIVE, but has not completed yet.
        store.connected = active(stale_txn)

        # Re-authorizing from the same session fails in disconnect, so the
        # stored ACTIVE grant and its epoch still match the old transaction.
        store.disconnect_fails = True
        with pytest.raises(McpFailure) as failed:
            await bind(SERVICE_CONFIG_ID, old)
        assert failed.value.code == "synthetic_disconnect_failed"
        assert store.disconnects == 0

        # The late old callback, a stale DCR and old reads are all refused.
        with pytest.raises(McpFailure) as late:
            await complete(old, stale_txn, stale)
        assert late.value.code == "mcp_authorization_invalid"
        with pytest.raises(McpFailure) as callback:
            profile.authorization_transaction_attempt(stale_txn)
        assert callback.value.code == "mcp_authorization_stale"
        with pytest.raises(McpFailure) as dcr:
            await begin(stale, transaction(old, 2))
        assert dcr.value.code == "mcp_authorization_stale"
        assert not readable(old)
        assert not readable(new)

        # Another user is refused without voiding anything.
        store.disconnect_fails = False
        with pytest.raises(McpFailure) as refused:
            await bind(SERVICE_CONFIG_ID, other)
        assert refused.value.code == "mcp_service_unavailable"
        assert store.disconnects == 0

        # A fresh explicit authorization by the same user completes normally.
        fresh = await bind(SERVICE_CONFIG_ID, new)
        assert store.disconnects == 1
        fresh_txn = transaction(new, 2)
        await begin(fresh, fresh_txn)
        assert not readable(new)
        store.connected = active(fresh_txn)
        await complete(new, fresh_txn, fresh)
        assert readable(new)
        assert not readable(old)

    asyncio.run(run(), loop_factory=make_event_loop)
