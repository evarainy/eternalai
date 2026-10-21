"""SQL-shaped fake transaction tests. They do not claim real PostgreSQL/provider coverage."""

from __future__ import annotations

import asyncio
import copy
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.browser_skill.models import BrowserOwner
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
    BrowserLeaseError,
    BrowserProviderFact,
)
from app.ports.credential_vault import (
    BrowserAuthFact,
    BrowserAuthorizationError,
    BrowserBindingFact,
)


class Result:
    def __init__(self, value: Any = None, rowcount: int = 0) -> None:
        self.value, self.rowcount = value, rowcount

    def mappings(self) -> Result:
        return self

    def one_or_none(self) -> Any:
        return copy.deepcopy(self.value)

    def one(self) -> Any:
        assert self.value is not None
        return copy.deepcopy(self.value)

    def scalar_one(self) -> Any:
        return self.one()

    def scalar_one_or_none(self) -> Any:
        return self.one_or_none()


class FakeDatabase:
    def __init__(self) -> None:
        self.now = datetime(2030, 1, 1, tzinfo=UTC)
        self.quotas: dict[tuple[str, str | None], int] = {("pool", None): 2, ("pool", "tenant"): 1}
        self.credentials: dict[tuple[str, ...], dict[str, Any]] = {}
        self.rows: dict[tuple[str, ...], dict[str, Any]] = {}
        self.events: list[str] = []
        self.active = 0
        self.lock = asyncio.Lock()
        self.lock_delay = timedelta()
        self.after_commit: Any = None

    def session(self) -> Session:
        return Session(self)


class Session:
    def __init__(self, db: FakeDatabase) -> None:
        self.db = db

    async def __aenter__(self) -> Session:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    @asynccontextmanager
    async def begin(self) -> Any:
        async with self.db.lock:
            before = copy.deepcopy(self.db.rows)
            self.db.active += 1
            try:
                yield
            except BaseException:
                self.db.rows = before
                self.db.events.append("rollback")
                raise
            else:
                self.db.events.append("commit")
                if self.db.after_commit is not None:
                    self.db.after_commit()
            finally:
                self.db.active -= 1

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Result:
        sql, params = str(statement), params or {}
        key = tuple(params.get(k, "") for k in
                    ("tenant_id", "ai_user_id", "target_system", "binding_id"))
        if "pg_advisory_xact_lock_shared" in sql:
            self.db.events.append("writer_lock")
            return Result()
        if "SELECT max_active" in sql:
            global_quota = "IS NULL" in sql
            self.db.events.append("global_quota" if global_quota else "tenant_quota")
            return Result(self.db.quotas.get((params["provider_key"],
                                            None if global_quota else params["tenant_id"])))
        if "FROM oa_session_credentials" in sql:
            self.db.events.append("binding_lock")
            return Result(self.db.credentials.get(key))
        if "SELECT * FROM browser_binding_leases" in sql:
            if "FOR UPDATE" in sql:
                self.db.events.append("lease_lock")
                self.db.now += self.db.lock_delay
                self.db.lock_delay = timedelta()
            return Result(self.db.rows.get(key))
        if "SELECT clock_timestamp()" in sql:
            self.db.events.append("clock")
            return Result(self.db.now)
        if "SELECT count(*)" in sql:
            rows = [r for r in self.db.rows.values()
                    if r["capacity_held"] and r["provider_key"] == params["provider_key"]]
            tenant_count = sum(r["tenant_id"] == params["tenant_id"] for r in rows)
            return Result({"global_count": len(rows), "tenant_count": tenant_count})
        if "INSERT INTO browser_binding_leases" in sql:
            assert key not in self.db.rows
            self.db.rows[key] = {
                **params, "lease_epoch": 0, "lease_revision": 0, "state": "released",
                "capacity_held": False, "resource_cipher_version": None, "resource_key_id": None,
                "resource_nonce": None, "encrypted_resource_ref": None,
                "resource_proof_digest": None,
            }
            return Result(rowcount=1)
        if "UPDATE browser_binding_leases" in sql:
            assert self.db.active == 1
            if key not in self.db.rows:
                return Result(rowcount=0)
            self.db.rows[key].update(params)
            return Result(rowcount=1)
        raise AssertionError("unexpected synthetic SQL")


class Authority:
    def __init__(self) -> None:
        self.revoked = False
        self.binding_stale = False
        self.clean_allowed = True

    async def check_current(self, fact: Any) -> None:
        if self.revoked:
            raise BrowserAuthorizationError("browser_auth_revoked")

    async def check_binding(self, fact: Any) -> None:
        if self.binding_stale:
            raise BrowserAuthorizationError("browser_binding_stale")

    async def check_cleanup(self, claim: Any) -> None:
        if not self.clean_allowed:
            raise BrowserAuthorizationError("browser_cleanup_denied")

    async def check_recovery(self, key: Any) -> None:
        if not self.clean_allowed:
            raise BrowserAuthorizationError("browser_recovery_denied")


class Subject:
    def __init__(self, db: FakeDatabase) -> None:
        self.db, self.wrong = db, False
        self.checked: list[bytes] = []

    async def check_subject(self, claim: Any, resource: bytes) -> None:
        assert self.db.active == 0, "provider subject IO must happen outside the transaction"
        self.checked.append(resource)
        if self.wrong:
            raise BrowserAuthorizationError("browser_subject_mismatch")


class SyntheticProvider:
    """Only test evidence indexed here is trusted; arbitrary bytes are rejected."""

    def __init__(self, db: FakeDatabase) -> None:
        self.db = db
        self.outcome = "acquired"
        self.resource = b"synthetic-resource"
        self.mutation: dict[str, Any] = {}
        self.entered: asyncio.Event | None = None
        self.resume: asyncio.Event | None = None
        self.calls = 0

    async def verify(self, expected: Any, evidence: bytes) -> BrowserProviderFact:
        assert self.db.active == 0, "provider proof IO must happen outside the transaction"
        self.calls += 1
        if evidence != b"synthetic-provider-evidence":
            raise BrowserLeaseError("browser_provider_proof_invalid")
        if self.entered is not None and self.resume is not None:
            self.entered.set()
            await self.resume.wait()
        claim = expected.claim
        fact = BrowserProviderFact(claim.provider_key, claim.operation_id, claim.holder_id,
                                   claim.lease_epoch, expected.challenge, expected.manifest_digest,
                                   self.resource, self.outcome)  # type: ignore[arg-type]
        return replace(fact, **self.mutation)


class Harness:
    def __init__(self) -> None:
        self.db = FakeDatabase()
        self.auth = Authority()
        self.subject = Subject(self.db)
        self.provider = SyntheticProvider(self.db)
        self.registry = BrowserProviderPoolRegistry((
            BrowserProviderPool("pool", ("endpoint-a", "endpoint-b"), b"m" * 32),))
        self.store = PostgreSQLBrowserLeaseStore(
            session_factory=self.db.session,  # type: ignore[arg-type]
            current_auth=self.auth, binding_reader=self.auth, registry=self.registry,
            resource_cipher=BrowserResourceCipher({"test": b"k" * 32}, active_key_id="test"),
            proof_context=BrowserClaimProofContext(b"p" * 32), proof_verifier=self.provider,
            cleanup_authority=self.auth, resource_subject=self.subject,
        )
        owner = BrowserOwner(tenant_id="tenant", user_id="user", session_id="sid_v1.synthetic")
        self.fact = BrowserAuthFact(owner, 17, b"a" * 32, self.db.now + timedelta(hours=1))
        self.binding = BrowserBindingFact("tenant", "user", "oa", "binding", 3, b"s" * 32)
        self.add_binding(self.binding)

    def add_binding(self, binding: BrowserBindingFact) -> None:
        self.db.credentials[(binding.tenant_id, binding.ai_user_id,
                             binding.target_system, binding.binding_id)] = {
            "binding_revision": binding.binding_revision, "binding_state": "active",
            "binding_subject_digest": binding.subject_digest, "revoked_at": None,
        }

    @property
    def row(self) -> dict[str, Any]:
        return self.db.rows[("tenant", "user", "oa", "binding")]

    async def reserve(self, alias: str = "endpoint-a") -> Any:
        return await self.store.reserve(self.fact, self.binding, alias, ttl_seconds=60)

    async def acquired(self) -> Any:
        claim = await self.store.start_acquisition(await self.reserve())
        return await self.store.record_acquired(claim, b"synthetic-provider-evidence")


def test_lock_order_committed_send_started_and_actual_resource_subject() -> None:
    async def scenario() -> None:
        h = Harness()
        claim = await h.reserve()
        assert h.db.events[:6] == ["writer_lock", "global_quota", "tenant_quota",
                                   "binding_lock", "lease_lock", "clock"]
        claim = await h.store.start_acquisition(claim)
        assert h.db.events[-1] == "commit"
        assert h.row["acquisition_send_started"] is True
        assert h.row["capacity_held"] is True
        claim = await h.store.record_acquired(claim, b"synthetic-provider-evidence")
        assert h.row["acquisition_phase"] == "acquired"
        assert h.subject.checked == [b"synthetic-resource"]
        assert await h.store.authorize_resource(claim) == b"synthetic-resource"
        assert len(h.subject.checked) == 2
        assert b"synthetic-resource" not in repr(h.row).encode()
    asyncio.run(scenario())


def test_aliases_share_one_quota_and_released_epoch_remains_monotonic() -> None:
    async def scenario() -> None:
        h = Harness()
        first = await h.reserve("endpoint-a")
        other = replace(h.binding, binding_id="binding2")
        h.add_binding(other)
        with pytest.raises(BrowserLeaseError) as error:
            await h.store.reserve(h.fact, other, "endpoint-b", ttl_seconds=60)
        assert error.value.code == "browser_capacity_exhausted"
        assert await h.store.cleanup(first)
        assert h.row["lease_epoch"] == 1 and h.row["capacity_held"] is False
        second = await h.reserve("endpoint-b")
        assert second.provider_key == first.provider_key == "pool"
        assert second.lease_epoch == 2 and second.lease_revision > first.lease_revision
        with pytest.raises(BrowserLeaseError) as stale:
            await h.store.cleanup(first)
        assert stale.value.code == "browser_lease_stale"
        assert h.row["capacity_held"] is True
    asyncio.run(scenario())


@pytest.mark.parametrize("which", ["global", "tenant", "unknown"])
def test_unconfigured_quota_or_unknown_pool_never_admits(which: str) -> None:
    async def scenario() -> None:
        h = Harness()
        if which != "unknown":
            h.db.quotas.pop(("pool", None if which == "global" else "tenant"))
        with pytest.raises(BrowserLeaseError) as error:
            await h.reserve("unregistered" if which == "unknown" else "endpoint-a")
        assert error.value.code == ("browser_provider_pool_unknown" if which == "unknown"
                                    else "browser_capacity_unconfigured")
        assert h.db.rows == {}
    asyncio.run(scenario())


@pytest.mark.parametrize("dependency", ["_auth", "_binding", "_subject", "_registry",
                                       "_proof_context", "_proof_verifier", "_cleanup_authority"])
def test_missing_trusted_authority_fails_closed_before_reserve(dependency: str) -> None:
    async def scenario() -> None:
        h = Harness()
        setattr(h.store, dependency, None)
        with pytest.raises(BrowserLeaseError) as error:
            await h.reserve()
        assert error.value.code in {"browser_authority_unavailable",
                                    "browser_provider_authority_unavailable"}
        assert h.db.rows == {}
    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["renew", "start", "resource", "record"])
def test_logout_denies_each_business_action_and_retains_capacity(action: str) -> None:
    async def scenario() -> None:
        h = Harness()
        claim = await (h.acquired() if action == "resource" else h.reserve())
        if action == "record":
            claim = await h.store.start_acquisition(claim)
        h.auth.revoked = True
        before = copy.deepcopy(h.row)
        with pytest.raises(BrowserAuthorizationError) as error:
            if action == "renew":
                await h.store.renew(claim, ttl_seconds=60)
            elif action == "start":
                await h.store.start_acquisition(claim)
            elif action == "resource":
                await h.store.authorize_resource(claim)
            else:
                await h.store.record_acquired(claim, b"synthetic-provider-evidence")
        assert error.value.code == "browser_auth_revoked"
        assert h.row == before
    asyncio.run(scenario())


def test_renew_preserves_ciphertext_and_original_claim_can_cleanup_after_rebind_logout() -> None:
    async def scenario() -> None:
        h = Harness()
        original = await h.acquired()
        envelope = h.row["encrypted_resource_ref"]
        h.db.now += timedelta(seconds=10)
        renewed = await h.store.renew(original, ttl_seconds=80)
        assert h.row["encrypted_resource_ref"] == envelope
        assert renewed.deadline > original.deadline
        assert await h.store.authorize_resource(renewed) == b"synthetic-resource"
        h.auth.revoked = True
        h.auth.binding_stale = True
        h.db.credentials[("tenant", "user", "oa", "binding")]["binding_revision"] = 4
        h.provider.outcome = "terminated"
        assert await h.store.cleanup(original, b"synthetic-provider-evidence")
        assert h.row["state"] == "released"
        assert h.row["capacity_held"] is False
        assert h.row["lease_epoch"] == original.lease_epoch
        assert h.row["encrypted_resource_ref"] is None
        assert h.row["release_outcome"] == "terminated"
        revision = h.row["lease_revision"]
        assert await h.store.cleanup(original)
        assert h.row["lease_revision"] == revision
    asyncio.run(scenario())


def test_acquired_wrong_subject_is_never_usable_and_still_charged() -> None:
    async def scenario() -> None:
        h = Harness()
        claim = await h.store.start_acquisition(await h.reserve())
        h.subject.wrong = True
        with pytest.raises(BrowserAuthorizationError) as error:
            await h.store.record_acquired(claim, b"synthetic-provider-evidence")
        assert error.value.code == "browser_subject_mismatch"
        assert h.row["acquisition_phase"] == "acquiring"
        assert h.row["capacity_held"] is True
        assert h.row["encrypted_resource_ref"] is None
    asyncio.run(scenario())


def test_decryptable_resource_does_not_grant_business_access_with_changed_binding_subject() -> None:
    async def scenario() -> None:
        h = Harness()
        claim = await h.acquired()
        changed = replace(claim, binding=replace(claim.binding, subject_digest=b"z" * 32))
        before = copy.deepcopy(h.row)
        with pytest.raises(BrowserLeaseError) as error:
            await h.store.authorize_resource(changed)
        assert error.value.code == "browser_binding_stale"
        assert h.row == before
    asyncio.run(scenario())


@pytest.mark.parametrize("field,value", [
    ("provider_key", "other"), ("operation_id", "other"), ("holder_id", "other"),
    ("lease_epoch", 2), ("challenge", b"x" * 32), ("manifest_digest", b"x" * 32),
    ("resource_ref", b"other-resource"), ("outcome", "disconnected"),
])
def test_cleanup_requires_every_provider_identity_dimension(field: str, value: object) -> None:
    async def scenario() -> None:
        h = Harness()
        claim = await h.acquired()
        h.provider.outcome = "released"
        h.provider.mutation = {field: value}
        before = copy.deepcopy(h.row)
        with pytest.raises(BrowserLeaseError) as error:
            await h.store.cleanup(claim, b"synthetic-provider-evidence")
        assert error.value.code == "browser_provider_proof_mismatch"
        assert h.row == before
    asyncio.run(scenario())


@pytest.mark.parametrize("evidence", [b"disconnected", b"digest", b"expired", "released"])
def test_arbitrary_evidence_cannot_release_capacity(evidence: Any) -> None:
    async def scenario() -> None:
        h = Harness()
        claim = await h.acquired()
        with pytest.raises(BrowserLeaseError) as error:
            await h.store.cleanup(claim, evidence)
        assert error.value.code == "browser_provider_proof_invalid"
        assert h.row["capacity_held"] is True
    asyncio.run(scenario())


def test_unknown_send_without_handle_keeps_quota_until_actual_termination() -> None:
    async def scenario() -> None:
        h = Harness()
        claim = await h.store.start_acquisition(await h.reserve())
        h.db.now += timedelta(hours=1)
        assert not await h.store.cleanup(claim)
        assert h.row["state"] == "quarantined"
        assert h.row["capacity_held"] is True
        assert h.row["encrypted_resource_ref"] is None
        h.provider.outcome = "terminated"
        assert await h.store.cleanup(claim, b"synthetic-provider-evidence")
        assert h.row["capacity_held"] is False
    asyncio.run(scenario())


def test_lock_wait_uses_fresh_database_time_and_expired_renew_never_revives() -> None:
    async def scenario() -> None:
        h = Harness()
        claim = await h.reserve()
        h.db.lock_delay = timedelta(seconds=61)
        before = copy.deepcopy(h.row)
        with pytest.raises(BrowserLeaseError) as error:
            await h.store.renew(claim, ttl_seconds=60)
        assert error.value.code == "browser_lease_expired"
        assert h.row == before
        assert h.db.events[-3:] == ["lease_lock", "clock", "rollback"]
    asyncio.run(scenario())


def test_counter_exhaustion_rolls_back_and_never_wraps() -> None:
    async def scenario() -> None:
        h = Harness()
        claim = await h.reserve()
        h.row["lease_revision"] = MAX_BROWSER_REVISION
        exhausted = replace(claim, lease_revision=MAX_BROWSER_REVISION)
        with pytest.raises(BrowserLeaseError) as error:
            await h.store.start_acquisition(exhausted)
        assert error.value.code == "browser_lease_revision_exhausted"
        assert h.row["acquisition_send_started"] is False
        assert h.row["lease_revision"] == MAX_BROWSER_REVISION
        h.row["lease_revision"] = claim.lease_revision
        assert await h.store.cleanup(claim)
        h.row["lease_epoch"] = MAX_BROWSER_REVISION
        with pytest.raises(BrowserLeaseError) as epoch_error:
            await h.reserve()
        assert epoch_error.value.code == "browser_lease_revision_exhausted"
        assert h.row["lease_epoch"] == MAX_BROWSER_REVISION
    asyncio.run(scenario())


def test_reservation_cleanup_fences_future_send_and_started_send_forbids_not_sent_release() -> None:
    async def scenario() -> None:
        h = Harness()
        claim = await h.reserve()
        assert await h.store.cleanup(claim)
        with pytest.raises(BrowserLeaseError) as error:
            await h.store.start_acquisition(claim)
        assert error.value.code == "browser_lease_stale"
        started = await h.store.start_acquisition(await h.reserve())
        assert not await h.store.cleanup(started)
        assert h.row["capacity_held"] is True
        assert h.row["state"] == "quarantined"
    asyncio.run(scenario())


def test_cancelled_and_late_acquired_do_not_drop_capacity_or_overwrite_cleanup() -> None:
    async def scenario() -> None:
        h = Harness()
        claim = await h.store.start_acquisition(await h.reserve())
        h.provider.entered, h.provider.resume = asyncio.Event(), asyncio.Event()
        pending = asyncio.create_task(
            h.store.record_acquired(claim, b"synthetic-provider-evidence"))
        await h.provider.entered.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert h.row["capacity_held"] is True
        assert h.row["acquisition_phase"] == "acquiring"
        h.provider.entered = asyncio.Event()
        pending = asyncio.create_task(
            h.store.record_acquired(claim, b"synthetic-provider-evidence"))
        await h.provider.entered.wait()
        assert not await h.store.cleanup(claim)
        h.provider.resume.set()
        with pytest.raises(BrowserLeaseError) as error:
            await pending
        assert error.value.code == "browser_lease_stale"
        assert h.row["state"] == "quarantined"
        assert h.row["encrypted_resource_ref"] is None
        assert h.row["capacity_held"] is True
        assert h.subject.checked == []
    asyncio.run(scenario())


def test_cleanup_needs_independent_authority_and_cannot_use_rebound_claim_identity() -> None:
    async def scenario() -> None:
        h = Harness()
        claim = await h.reserve()
        h.auth.clean_allowed = False
        with pytest.raises(BrowserAuthorizationError) as denied:
            await h.store.cleanup(claim)
        assert denied.value.code == "browser_cleanup_denied"
        h.auth.clean_allowed = True
        wrong = replace(claim, binding=replace(claim.binding, binding_revision=4))
        with pytest.raises(BrowserLeaseError) as stale:
            await h.store.cleanup(wrong)
        assert stale.value.code == "browser_lease_stale"
        assert h.row["capacity_held"] is True
    asyncio.run(scenario())


def test_registry_rejects_alias_collision_including_another_canonical_pool() -> None:
    with pytest.raises(ValueError, match="browser_provider_pool_alias_duplicate"):
        BrowserProviderPoolRegistry((BrowserProviderPool("pool", ("other",), b"m" * 32),
                                     BrowserProviderPool("other", (), b"n" * 32)))


def test_restart_recovery_after_unbind_has_no_subject_and_cannot_grant_business() -> None:
    async def scenario() -> None:
        h = Harness()
        original = await h.acquired()
        h.auth.revoked = True
        h.auth.binding_stale = True
        h.db.credentials[("tenant", "user", "oa", "binding")].update(
            binding_revision=4, binding_state="unbound", binding_subject_digest=None)
        key = BrowserBindingKey("tenant", "user", "oa", "binding")
        recovered = await h.store.recover_cleanup(key)
        assert isinstance(recovered.binding, BrowserLeaseBindingSnapshot)
        assert not hasattr(recovered.binding, "subject_digest")
        assert recovered.auth == original.auth
        assert recovered.lease_epoch == original.lease_epoch
        assert recovered.operation_id == original.operation_id
        for operation in (h.store.renew(recovered, ttl_seconds=60),
                          h.store.authorize_resource(recovered),
                          h.store.start_acquisition(recovered),
                          h.store.reserve(recovered.auth, recovered.binding,
                                          "endpoint-a", ttl_seconds=60)):
            with pytest.raises(BrowserLeaseError) as denied:
                await operation
            assert denied.value.code == "browser_cleanup_claim_not_business"
        h.provider.outcome = "terminated"
        assert await h.store.cleanup(recovered, b"synthetic-provider-evidence")
        assert h.row["capacity_held"] is False
        assert h.row["lease_epoch"] == original.lease_epoch
    asyncio.run(scenario())


def test_recovery_authority_precedes_database_read() -> None:
    async def scenario() -> None:
        h = Harness()
        h.auth.clean_allowed = False

        def forbidden_session() -> None:
            raise AssertionError("unauthorized recovery must not read database")

        h.store._sessions = forbidden_session  # type: ignore[assignment]
        with pytest.raises(BrowserAuthorizationError) as denied:
            await h.store.recover_cleanup(BrowserBindingKey("tenant", "user", "oa", "binding"))
        assert denied.value.code == "browser_recovery_denied"
        h.store._cleanup_authority = None
        with pytest.raises(BrowserLeaseError) as missing:
            await h.store.recover_cleanup(BrowserBindingKey("tenant", "user", "oa", "binding"))
        assert missing.value.code == "browser_cleanup_authority_unavailable"
    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["record", "resource"])
def test_expired_business_claim_is_denied_before_provider_or_subject_io(action: str) -> None:
    async def scenario() -> None:
        h = Harness()
        claim = (await h.acquired() if action == "resource"
                 else await h.store.start_acquisition(await h.reserve()))
        calls = h.provider.calls
        h.subject.checked.clear()
        h.db.lock_delay = timedelta(seconds=61)
        before = copy.deepcopy(h.row)
        with pytest.raises(BrowserLeaseError) as expired:
            if action == "record":
                await h.store.record_acquired(claim, b"synthetic-provider-evidence")
            else:
                await h.store.authorize_resource(claim)
        assert expired.value.code == "browser_lease_expired"
        assert h.provider.calls == calls
        assert h.subject.checked == []
        assert h.row == before
    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["record", "resource"])
def test_revocation_after_unlock_precedes_provider_or_subject_io(action: str) -> None:
    async def scenario() -> None:
        h = Harness()
        claim = (await h.acquired() if action == "resource"
                 else await h.store.start_acquisition(await h.reserve()))
        calls = h.provider.calls
        h.subject.checked.clear()
        h.db.after_commit = lambda: setattr(h.auth, "revoked", True)
        before = copy.deepcopy(h.row)
        with pytest.raises(BrowserAuthorizationError) as revoked:
            if action == "record":
                await h.store.record_acquired(claim, b"synthetic-provider-evidence")
            else:
                await h.store.authorize_resource(claim)
        assert revoked.value.code == "browser_auth_revoked"
        assert h.provider.calls == calls
        assert h.subject.checked == []
        assert h.row == before
    asyncio.run(scenario())
