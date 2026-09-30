"""Real fixed PostgreSQL; all inserted records roll back after each test."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, AsyncIterator
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import make_async_engine
from app.event_loop import make_event_loop
from app.infra.persistence.mcp.repository import PostgreSQLMcpStore
from app.infra.persistence.mcp.schema import grants, transactions
from app.mcp.models import McpFailure, ServiceConfig
from app.ports.mcp import McpAuthorizationContext


class Revocations:
    revoked = False

    async def is_revoked(self, fingerprint: bytes) -> bool:
        assert fingerprint in {b"synthetic-session", b"synthetic-new-session"}
        return self.revoked

    async def revoke(self, fingerprint: bytes, *, expires_at: datetime) -> None:
        self.revoked = True


def config(service: str = "service-a") -> ServiceConfig:
    return ServiceConfig(
        service_config_id=service,
        service_config_version=1,
        deployment_id="synthetic",
        tenant_id="default",
        display_name="Synthetic",
        endpoint="https://provider.invalid/mcp",
        issuer="https://provider.invalid",
        resource="https://provider.invalid/" + service,
        registration_endpoint="https://provider.invalid/register",
        authorization_endpoint="https://provider.invalid/authorize",
        token_endpoint="https://provider.invalid/token",
        callback_uri="https://client.invalid/callback",
        callback_config_version=1,
        enabled=True,
    )


@asynccontextmanager
async def database(url: str) -> AsyncIterator[tuple[PostgreSQLMcpStore, Any, Revocations]]:
    engine = make_async_engine(url)
    assert (engine.url.host, engine.url.port, engine.url.database) == (
        "127.0.0.1",
        15432,
        "eternalai_test",
    )
    async with engine.connect() as conn:
        outer = await conn.begin()
        sessions = async_sessionmaker(
            conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        revocations = Revocations()
        try:
            yield PostgreSQLMcpStore(sessions, b"s" * 32, revocations), conn, revocations
        finally:
            await outer.rollback()
    await engine.dispose()


async def bind(
    store: PostgreSQLMcpStore,
    profile: ServiceConfig,
    user: str = "synthetic-user",
    *,
    token_value: str = "synthetic-token-canary",
    fingerprint: bytes = b"synthetic-session",
) -> Any:
    await store.configure(profile)
    registration_id, client_id, created = await store.begin_registration(profile)
    if created:
        client_id = "synthetic-client-" + profile.service_config_id
        await store.complete_registration(registration_id, client_id)
    assert client_id is not None
    transaction = await store.begin_authorization(
        profile,
        user_id=user,
        fingerprint=fingerprint.hex(),
        registration_id=registration_id,
        client_id=client_id,
        state_digest=uuid4().hex,
        verifier="synthetic-verifier-canary",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    claimed = await store.claim_authorization(
        transaction.state_digest,
        tenant_id=profile.tenant_id,
        user_id=user,
        fingerprint=fingerprint.hex(),
        issuer=profile.issuer,
        resource=profile.resource,
        callback_uri=profile.callback_uri,
        now=datetime.now(UTC),
    )
    await store.store_grant(
        claimed,
        token=token_value,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        identity_policy_version="synthetic-policy",
        identity_evidence="synthetic-approved",
    )
    return transaction


def test_durable_registration_grant_and_service_isolation(migrated_database_url: str) -> None:
    async def run() -> None:
        async with database(migrated_database_url) as (store, conn, revocations):
            first = await bind(store, config())
            second = await bind(store, config("service-b"))
            assert first.owner.connection_id != second.owner.connection_id
            assert first.registration_id != second.registration_id
            restarted = PostgreSQLMcpStore(store.sessions, b"s" * 32, revocations)
            registration, client, created = await restarted.begin_registration(config())
            assert (registration, client, created) == (
                first.registration_id,
                first.client_id,
                False,
            )
            connection = await restarted.connection(
                tenant_id="default", user_id="synthetic-user", service_config_id="service-a"
            )
            assert connection is not None and connection.state == "ACTIVE"
            context = McpAuthorizationContext(
                **{
                    key: getattr(connection, key)
                    for key in (
                        "tenant_id",
                        "user_id",
                        "service_config_id",
                        "connection_id",
                        "registration_id",
                        "service_config_version",
                        "binding_epoch",
                        "grant_epoch",
                        "login_session_fingerprint",
                    )
                },
                task_id="synthetic-task",
                chat_session_id="synthetic-chat",
                capability_id="synthetic.read",
                capability_version="1",
            )
            assert await restarted.resolve(context) == "synthetic-token-canary"
            for patch in (
                {"service_config_id": "service-b"},
                {"user_id": "other-user"},
                {"tenant_id": "other-tenant"},
                {"grant_epoch": 99},
                {"connection_id": second.owner.connection_id},
            ):
                with pytest.raises(McpFailure, match="mcp_authorization_invalid"):
                    await restarted.resolve(context.model_copy(update=patch))
            encrypted = (await conn.execute(sa.select(grants.c.encrypted_payload))).scalars().all()
            assert len(encrypted) == 2
            assert all(b"synthetic-token-canary" not in row for row in encrypted)
            assert (
                await conn.execute(sa.select(transactions.c.encrypted_payload))
            ).scalars().all() == [b"", b""]
            assert not await restarted.disconnect(
                tenant_id="other-tenant",
                user_id="synthetic-user",
                connection_id=context.connection_id,
            )
            assert await restarted.disconnect(
                tenant_id="default", user_id="synthetic-user", connection_id=context.connection_id
            )
            with pytest.raises(McpFailure, match="mcp_authorization_invalid"):
                await restarted.resolve(context)

    asyncio.run(run(), loop_factory=make_event_loop)


def test_duplicate_callback_has_single_claim_and_unknown_registration_is_not_retried(
    migrated_database_url: str,
) -> None:
    async def run() -> None:
        async with database(migrated_database_url) as (store, _, _):
            transaction = await bind(store, config())
            with pytest.raises(McpFailure, match="mcp_authorization_invalid"):
                await store.claim_authorization(
                    transaction.state_digest,
                    tenant_id="default",
                    user_id="synthetic-user",
                    fingerprint=b"synthetic-session".hex(),
                    issuer=config().issuer,
                    resource=config().resource,
                    callback_uri=config().callback_uri,
                    now=datetime.now(UTC),
                )
            other = config("service-b")
            await store.configure(other)
            registration, _, created = await store.begin_registration(other)
            assert created is True
            await store.complete_registration(registration, "")
            with pytest.raises(McpFailure, match="mcp_registration_requires_reconciliation"):
                await store.begin_registration(other)

    asyncio.run(run(), loop_factory=make_event_loop)


@pytest.mark.parametrize("status", [401, 403])
@pytest.mark.parametrize("phase", ["http", "initialized"])
def test_http_rejection_persists_and_late_response_cannot_revoke_new_grant(
    migrated_database_url: str,
    status: int,
    phase: str,
) -> None:
    from app.infra.mcp.driver import McpDriver
    from app.mcp.contracts import input_digest, safety_digest
    from tests.infra.mcp.test_transport import Peer, serving

    async def context_for(store, service="service-a", user="synthetic-user"):
        connection = await store.connection(
            tenant_id="default", user_id=user, service_config_id=service
        )
        assert connection is not None
        return McpAuthorizationContext(
            **{
                key: getattr(connection, key)
                for key in (
                    "tenant_id",
                    "user_id",
                    "service_config_id",
                    "connection_id",
                    "registration_id",
                    "service_config_version",
                    "binding_epoch",
                    "grant_epoch",
                    "login_session_fingerprint",
                )
            },
            task_id="synthetic-task",
            chat_session_id="synthetic-chat",
            capability_id="synthetic.read",
            capability_version="1",
        )

    peer = Peer("2025-11-25", fault=f"{phase}{status}")
    with serving(peer) as profile:

        async def run():
            async with database(migrated_database_url) as (store, _, revocations):
                await bind(store, profile)
                await bind(store, config("service-b"))
                await bind(store, profile, user="synthetic-other-user")
                original = await context_for(store)
                separate_service = await context_for(store, service="service-b")
                separate_user = await context_for(store, user="synthetic-other-user")
                with pytest.raises(McpFailure, match="^mcp_authorization_rejected$"):
                    await McpDriver({"service-a": profile}, store).call(
                        original,
                        "business_context_get",
                        {},
                        write=False,
                        input_digest=input_digest("business_context_get"),
                        safety_digest=safety_digest("business_context_get"),
                    )
                restarted = PostgreSQLMcpStore(store.sessions, b"s" * 32, revocations)
                rejected = await restarted.connection(
                    tenant_id="default",
                    user_id="synthetic-user",
                    service_config_id="service-a",
                )
                assert rejected is not None and rejected.state == "DISCONNECTED"
                assert rejected.binding_epoch == original.binding_epoch + 1
                with pytest.raises(McpFailure, match="mcp_authorization_invalid"):
                    await restarted.resolve(original)
                assert await restarted.resolve(separate_service) == "synthetic-token-canary"
                assert await restarted.resolve(separate_user) == "synthetic-token-canary"
                await bind(restarted, profile, token_value="synthetic-replacement")
                renewed = await context_for(restarted)
                await restarted.reject(original)
                assert await restarted.resolve(renewed) == "synthetic-replacement"
                assert await restarted.resolve(separate_service) == "synthetic-token-canary"

        asyncio.run(run(), loop_factory=make_event_loop)
    assert peer.effects == 0
    assert [body["method"] for _, _, body in peer.calls] == (
        ["initialize", "notifications/initialized"] if phase == "initialized" else ["initialize"]
    )
