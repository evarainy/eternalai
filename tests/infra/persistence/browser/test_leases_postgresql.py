"""Real PostgreSQL lease transactions with explicitly synthetic provider/auth authority.

Run only through the attested isolated runner and existing migrated_database_url.
Rows are unique per test and intentionally retained; these tests perform no cleanup
deletion, schema changes or provider network IO. They do not certify real providers.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any, AsyncIterator
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.browser_skill.models import BrowserOwner
from app.db.config import normalize_database_url
from app.infra.auth.postgresql import PostgreSQLCredentialStore
from app.infra.persistence.browser.crypto import BrowserClaimProofContext, BrowserResourceCipher
from app.infra.persistence.browser.leases import (
    BrowserProviderPool,
    BrowserProviderPoolRegistry,
    PostgreSQLBrowserLeaseStore,
)
from app.ports.browser_store import (
    MAX_BROWSER_REVISION,
    BrowserBindingKey,
    BrowserLeaseBindingSnapshot,
    BrowserLeaseClaim,
    BrowserLeaseError,
    BrowserProviderExpectation,
    BrowserProviderFact,
)
from app.ports.credential_vault import (
    BrowserAuthFact,
    BrowserAuthorizationError,
    BrowserBindingFact,
)

_WHERE = "tenant_id=:tenant AND ai_user_id=:user AND target_system='oa' AND binding_id=:binding"


class SyntheticAuthority:
    def __init__(self) -> None:
        self.revoked = False
        self.wrong_subject = False
        self.outcome = "acquired"
        self.mismatch = False
        self.resource = b"synthetic-pg-resource"

    async def check_current(self, auth: BrowserAuthFact) -> None:
        if self.revoked:
            raise BrowserAuthorizationError("browser_auth_revoked")

    async def check_cleanup(self, claim: BrowserLeaseClaim) -> None:
        # Dedicated synthetic reconciler authority is independent from auth.revoked.
        if not claim.operation_id:
            raise BrowserAuthorizationError("browser_cleanup_denied")

    async def check_recovery(self, key: BrowserBindingKey) -> None:
        if not key.binding_id:
            raise BrowserAuthorizationError("browser_recovery_denied")

    async def check_subject(self, claim: BrowserLeaseClaim, resource: bytes) -> None:
        if self.wrong_subject or resource != self.resource:
            raise BrowserAuthorizationError("browser_subject_mismatch")

    async def verify(
        self, expected: BrowserProviderExpectation, evidence: bytes,
    ) -> BrowserProviderFact:
        if evidence != b"synthetic-pg-provider-evidence":
            raise BrowserLeaseError("browser_provider_proof_invalid")
        claim = expected.claim
        return BrowserProviderFact(
            claim.provider_key, "wrong-operation" if self.mismatch else claim.operation_id,
            claim.holder_id, claim.lease_epoch, expected.challenge, expected.manifest_digest,
            self.resource, self.outcome,  # type: ignore[arg-type]
        )


class PgHarness:
    def __init__(self, first: AsyncEngine, second: AsyncEngine, name: str) -> None:
        self.engines = first, second
        self.factories = (async_sessionmaker(first, expire_on_commit=False),
                          async_sessionmaker(second, expire_on_commit=False))
        self.waiter_name = name
        self.pool = "pool_" + uuid4().hex
        self.tenant = "tenant_" + uuid4().hex
        self.user = "user_" + uuid4().hex
        self.sid = "sid_v1." + uuid4().hex
        self.authority = SyntheticAuthority()
        self.registry = BrowserProviderPoolRegistry((
            BrowserProviderPool(self.pool, (self.pool + "_a", self.pool + "_b"), b"m" * 32),))
        self.stores = tuple(self.store(factory) for factory in self.factories)

    def store(self, factory: async_sessionmaker[AsyncSession]) -> PostgreSQLBrowserLeaseStore:
        return PostgreSQLBrowserLeaseStore(
            session_factory=factory, current_auth=self.authority,
            binding_reader=PostgreSQLCredentialStore(session_factory=factory,
                                                     encryption_key=b"k" * 32),
            registry=self.registry,
            resource_cipher=BrowserResourceCipher({"synthetic": b"k" * 32},
                                                   active_key_id="synthetic"),
            proof_context=BrowserClaimProofContext(b"p" * 32), proof_verifier=self.authority,
            cleanup_authority=self.authority, resource_subject=self.authority,
        )

    async def seed(
        self, *, tenant: str | None = None, global_limit: int = 2, tenant_limit: int = 1,
    ) -> tuple[BrowserAuthFact, BrowserBindingFact]:
        tenant = tenant or self.tenant
        user, sid, binding_id = "user_" + uuid4().hex, "sid_v1." + uuid4().hex, uuid4().hex
        async with self.factories[0]() as session, session.begin():
            now = (await session.execute(text("SELECT clock_timestamp()"))).scalar_one()
            assert isinstance(now, datetime)
            await session.execute(text(
                "INSERT INTO sessions(tenant_id,session_id) VALUES(:tenant,:sid)"
            ), {"tenant": tenant, "sid": sid})
            await session.execute(text(
                "INSERT INTO oa_session_credentials(tenant_id,ai_user_id,target_system,binding_id,"
                "binding_revision,binding_state,binding_subject_digest,binding_subject_verified_at,"
                "cipher_version,nonce,encrypted_payload,expires_at,updated_at) VALUES"
                "(:tenant,:user,'oa',:binding,3,'active',:subject,clock_timestamp(),"
                "'aes256gcm-session-v2',:nonce,:encrypted,:expires,clock_timestamp())"
            ), {"tenant": tenant, "user": user, "binding": binding_id, "subject": b"s" * 32,
                "nonce": b"n" * 12, "encrypted": b"c" * 16, "expires": now + timedelta(hours=1)})
            await session.execute(text(
                "INSERT INTO browser_capacity_limits(quota_id,provider_key,tenant_id,max_active)"
                " VALUES(:id,:pool,NULL,:limit) ON CONFLICT DO NOTHING"
            ), {"id": uuid4().hex, "pool": self.pool, "limit": global_limit})
            await session.execute(text(
                "INSERT INTO browser_capacity_limits(quota_id,provider_key,tenant_id,max_active)"
                " VALUES(:id,:pool,:tenant,:limit) ON CONFLICT DO NOTHING"
            ), {"id": uuid4().hex, "pool": self.pool, "tenant": tenant, "limit": tenant_limit})
        owner = BrowserOwner(tenant_id=tenant, user_id=user, session_id=sid)
        auth = BrowserAuthFact(owner, 19, b"a" * 32, now + timedelta(hours=1))
        binding = BrowserBindingFact(tenant, user, "oa", binding_id, 3, b"s" * 32)
        return auth, binding

    async def reserve(
        self, auth: BrowserAuthFact, binding: BrowserBindingFact, *, worker: int = 0,
        ttl: int = 60, alias: str = "_a",
    ) -> BrowserLeaseClaim:
        return await self.stores[worker].reserve(auth, binding, self.pool + alias, ttl_seconds=ttl)

    async def row(self, binding: BrowserBindingFact) -> dict[str, Any]:
        async with self.factories[0]() as session:
            row = (await session.execute(text(
                "SELECT * FROM browser_binding_leases WHERE " + _WHERE
            ), {"tenant": binding.tenant_id, "user": binding.ai_user_id,
                "binding": binding.binding_id})).mappings().one()
        return dict(row)

    async def count(self) -> int:
        async with self.factories[0]() as session:
            value = (await session.execute(text(
                "SELECT count(*) FROM browser_binding_leases WHERE provider_key=:pool"
                " AND capacity_held"
            ), {"pool": self.pool})).scalar_one()
        return int(value)


@asynccontextmanager
async def harness(database_url: str) -> AsyncIterator[PgHarness]:
    url = make_url(normalize_database_url(database_url))
    assert url.host == "127.0.0.1" and url.port == 15432 and url.database == "eternalai_test"
    name = "browser_lease_waiter_" + uuid4().hex
    first = create_async_engine(url, connect_args={"application_name": name + "_one"})
    second = create_async_engine(url, connect_args={"application_name": name})
    try:
        async with first.connect() as one, second.connect() as two:
            first_pid = (await one.execute(text("SELECT pg_backend_pid()"))).scalar_one()
            second_pid = (await two.execute(text("SELECT pg_backend_pid()"))).scalar_one()
            assert first_pid != second_pid
        yield PgHarness(first, second, name)
    finally:
        await first.dispose()
        await second.dispose()


def test_two_connections_serialize_same_full_binding(migrated_database_url: str) -> None:
    async def scenario() -> None:
        async with harness(migrated_database_url) as h:
            auth, binding = await h.seed()
            outcomes = await asyncio.gather(h.reserve(auth, binding),
                                            h.reserve(auth, binding, worker=1),
                                            return_exceptions=True)
            claims = [o for o in outcomes if isinstance(o, BrowserLeaseClaim)]
            errors = [o for o in outcomes if isinstance(o, BrowserLeaseError)]
            assert len(claims) == len(errors) == 1
            assert errors[0].code == "browser_binding_busy"
            assert await h.count() == 1
            assert await h.stores[1].cleanup(claims[0])
            next_claim = await h.reserve(auth, binding, worker=1)
            assert next_claim.lease_epoch == claims[0].lease_epoch + 1
            with pytest.raises(BrowserLeaseError) as stale:
                await h.stores[0].cleanup(claims[0])
            assert stale.value.code == "browser_lease_stale"
            assert await h.count() == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("cross_tenant", [False, True])
def test_aliases_cannot_bypass_tenant_or_global_capacity(
    migrated_database_url: str, cross_tenant: bool,
) -> None:
    async def scenario() -> None:
        async with harness(migrated_database_url) as h:
            first = await h.seed(global_limit=1 if cross_tenant else 3)
            second = await h.seed(tenant="tenant_" + uuid4().hex if cross_tenant else h.tenant)
            outcomes = await asyncio.gather(h.reserve(*first, alias="_a"),
                                            h.reserve(*second, worker=1, alias="_b"),
                                            return_exceptions=True)
            assert sum(isinstance(o, BrowserLeaseClaim) for o in outcomes) == 1
            errors = [o for o in outcomes if isinstance(o, BrowserLeaseError)]
            assert len(errors) == 1 and errors[0].code == "browser_capacity_exhausted"
            assert await h.count() == 1
    asyncio.run(scenario())


def test_reservation_cleanup_races_send_start_in_real_transactions(
    migrated_database_url: str,
) -> None:
    async def scenario() -> None:
        async with harness(migrated_database_url) as h:
            auth, binding = await h.seed()
            claim = await h.reserve(auth, binding)
            started, cleaned = await asyncio.gather(h.stores[0].start_acquisition(claim),
                                                    h.stores[1].cleanup(claim),
                                                    return_exceptions=True)
            row = await h.row(binding)
            if isinstance(started, BrowserLeaseClaim):
                assert cleaned is False
                assert row["state"] == "quarantined" and row["capacity_held"] is True
                assert row["acquisition_send_started"] is True
            else:
                assert isinstance(started, BrowserLeaseError)
                assert started.code == "browser_lease_stale" and cleaned is True
                assert row["state"] == "released" and row["capacity_held"] is False
                assert row["acquisition_send_started"] is False
    asyncio.run(scenario())


def test_renewed_cipher_and_original_cleanup_survive_rebind_logout(
    migrated_database_url: str,
) -> None:
    async def scenario() -> None:
        async with harness(migrated_database_url) as h:
            auth, binding = await h.seed()
            claim = await h.stores[0].start_acquisition(await h.reserve(auth, binding))
            original = await h.stores[0].record_acquired(claim, b"synthetic-pg-provider-evidence")
            before = await h.row(binding)
            renewed = await h.stores[1].renew(original, ttl_seconds=120)
            after = await h.row(binding)
            assert after["encrypted_resource_ref"] == before["encrypted_resource_ref"]
            assert await h.stores[1].authorize_resource(renewed) == h.authority.resource
            h.authority.revoked = True
            async with h.factories[0]() as session, session.begin():
                await session.execute(text(
                    "UPDATE oa_session_credentials SET binding_revision=binding_revision+1,"
                    "binding_subject_digest=:subject,"
                    "credential_write_revision=credential_write_revision+1"
                    " WHERE " + _WHERE
                ), {"tenant": binding.tenant_id, "user": binding.ai_user_id,
                    "binding": binding.binding_id, "subject": b"z" * 32})
            with pytest.raises(BrowserAuthorizationError) as denied:
                await h.stores[1].renew(renewed, ttl_seconds=60)
            assert denied.value.code == "browser_auth_revoked"
            h.authority.outcome = "terminated"
            assert await h.stores[1].cleanup(original, b"synthetic-pg-provider-evidence")
            row = await h.row(binding)
            assert row["lease_epoch"] == original.lease_epoch
            assert row["capacity_held"] is False and row["encrypted_resource_ref"] is None
            assert row["release_outcome"] == "terminated"
    asyncio.run(scenario())


def test_unknown_no_handle_and_wrong_proof_keep_real_capacity(migrated_database_url: str) -> None:
    async def scenario() -> None:
        async with harness(migrated_database_url) as h:
            auth, binding = await h.seed()
            claim = await h.stores[0].start_acquisition(await h.reserve(auth, binding))
            assert not await h.stores[1].cleanup(claim)
            h.authority.outcome, h.authority.mismatch = "terminated", True
            with pytest.raises(BrowserLeaseError) as mismatch:
                await h.stores[1].cleanup(claim, b"synthetic-pg-provider-evidence")
            assert mismatch.value.code == "browser_provider_proof_mismatch"
            row = await h.row(binding)
            assert row["state"] == "quarantined" and row["capacity_held"] is True
            assert row["encrypted_resource_ref"] is None
            assert await h.count() == 1
            h.authority.mismatch = False
            assert await h.stores[1].cleanup(claim, b"synthetic-pg-provider-evidence")
            assert await h.count() == 0
    asyncio.run(scenario())


def test_expiry_is_rechecked_after_waiting_for_quota_lock(migrated_database_url: str) -> None:
    async def scenario() -> None:
        async with harness(migrated_database_url) as h:
            auth, binding = await h.seed()
            claim = await h.reserve(auth, binding, ttl=1)
            async with h.factories[0]() as blocker:
                async with blocker.begin():
                    await blocker.execute(text(
                        "SELECT quota_id FROM browser_capacity_limits WHERE provider_key=:pool"
                        " AND tenant_id IS NULL FOR UPDATE"
                    ), {"pool": h.pool})
                    waiting = asyncio.create_task(h.stores[1].renew(claim, ttl_seconds=60))
                    try:
                        async with asyncio.timeout(10):
                            while True:
                                await blocker.execute(text("SELECT pg_stat_clear_snapshot()"))
                                observed = (await blocker.execute(text(
                                    "SELECT EXISTS(SELECT 1 FROM pg_stat_activity"
                                    " WHERE application_name=:name AND wait_event_type='Lock')"
                                ), {"name": h.waiter_name})).scalar_one()
                                if observed:
                                    break
                                await asyncio.sleep(0.01)
                            while (await blocker.execute(text(
                                "SELECT clock_timestamp() <= :deadline"
                            ), {"deadline": claim.deadline})).scalar_one():
                                await asyncio.sleep(0.01)
                    except BaseException:
                        waiting.cancel()
                        await asyncio.gather(waiting, return_exceptions=True)
                        raise
                with pytest.raises(BrowserLeaseError) as expired:
                    await waiting
                assert expired.value.code == "browser_lease_expired"
            row = await h.row(binding)
            assert row["lease_revision"] == claim.lease_revision
            assert row["deadline"] == claim.deadline and row["capacity_held"] is True
    asyncio.run(scenario())


def test_database_checks_reject_partial_envelope_and_revision_overflow(
    migrated_database_url: str,
) -> None:
    async def scenario() -> None:
        async with harness(migrated_database_url) as h:
            auth, binding = await h.seed()
            claim = await h.reserve(auth, binding)
            params = {"tenant": binding.tenant_id, "user": binding.ai_user_id,
                      "binding": binding.binding_id}
            for assignment in (
                "resource_key_id='partial'", "release_proof_digest=decode('aa','hex')",
                "lease_epoch=9007199254740992",
            ):
                with pytest.raises(IntegrityError):
                    async with h.factories[0]() as session, session.begin():
                        await session.execute(text(
                            "UPDATE browser_binding_leases SET " + assignment + " WHERE " + _WHERE
                        ), params)
                row = await h.row(binding)
                assert row["lease_epoch"] == claim.lease_epoch
                assert row["resource_key_id"] is None and row["release_proof_digest"] is None
            async with h.factories[0]() as session, session.begin():
                await session.execute(text(
                    "UPDATE browser_binding_leases SET lease_revision=:maximum WHERE " + _WHERE
                ), {**params, "maximum": MAX_BROWSER_REVISION})
            with pytest.raises(BrowserLeaseError) as exhausted:
                await h.stores[1].start_acquisition(
                    replace(claim, lease_revision=MAX_BROWSER_REVISION))
            assert exhausted.value.code == "browser_lease_revision_exhausted"
            row = await h.row(binding)
            assert row["lease_revision"] == MAX_BROWSER_REVISION
            assert row["acquisition_send_started"] is False and row["capacity_held"] is True
    asyncio.run(scenario())


def test_restarted_store_recovers_cleanup_after_unbind_without_old_subject(
    migrated_database_url: str,
) -> None:
    async def scenario() -> None:
        async with harness(migrated_database_url) as h:
            auth, binding = await h.seed()
            sent = await h.stores[0].start_acquisition(await h.reserve(auth, binding))
            original = await h.stores[0].record_acquired(sent, b"synthetic-pg-provider-evidence")
            h.authority.revoked = True
            async with h.factories[0]() as session, session.begin():
                await session.execute(text(
                    "UPDATE oa_session_credentials SET binding_revision=binding_revision+1,"
                    "binding_state='unbound',binding_subject_digest=NULL,"
                    "binding_subject_verified_at=NULL WHERE " + _WHERE
                ), {"tenant": binding.tenant_id, "user": binding.ai_user_id,
                    "binding": binding.binding_id})
            restarted = h.store(h.factories[1])
            recovered = await restarted.recover_cleanup(BrowserBindingKey(
                binding.tenant_id, binding.ai_user_id, binding.target_system, binding.binding_id))
            assert isinstance(recovered.binding, BrowserLeaseBindingSnapshot)
            assert not hasattr(recovered.binding, "subject_digest")
            assert recovered.auth == original.auth
            assert recovered.operation_id == original.operation_id
            with pytest.raises(BrowserLeaseError) as denied:
                await restarted.authorize_resource(recovered)
            assert denied.value.code == "browser_cleanup_claim_not_business"
            h.authority.outcome = "terminated"
            assert await restarted.cleanup(recovered, b"synthetic-pg-provider-evidence")
            row = await h.row(binding)
            assert row["capacity_held"] is False and row["encrypted_resource_ref"] is None
            assert row["lease_epoch"] == original.lease_epoch
    asyncio.run(scenario())
