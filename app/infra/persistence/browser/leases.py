"""PostgreSQL leases: lock capacity before binding/claim, never hold it over provider IO.

Registry entries pin one actual capacity pool and an immutable provider manifest.
Aliases never create capacity. Outstanding claims require their original registry
manifest and stable proof-context key across restarts. Missing/changed authority
fails closed and cannot clear quarantine. No production provider is registered here.
"""

from __future__ import annotations

import hmac
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import AsyncIterator, Mapping, cast
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.browser_skill.models import BrowserOwner
from app.infra.persistence.browser.crypto import (
    BrowserClaimProofContext,
    BrowserResourceCipher,
    ResourceEnvelope,
)
from app.ports.browser_store import (
    MAX_BROWSER_REVISION,
    BrowserBindingKey,
    BrowserCleanupAuthorityPort,
    BrowserLeaseBindingSnapshot,
    BrowserLeaseClaim,
    BrowserLeaseError,
    BrowserProviderExpectation,
    BrowserProviderFact,
    BrowserProviderProofPort,
    BrowserResourceSubjectPort,
)
from app.ports.credential_vault import (
    BrowserAuthFact,
    BrowserBindingFact,
    BrowserBindingReaderPort,
    BrowserCurrentAuthPort,
)

_B = "tenant_id=:tenant_id AND ai_user_id=:ai_user_id AND target_system=:target_system"
_B += " AND binding_id=:binding_id"
_TABLE = "browser_binding_leases"
_WRITER_LOCK = 746420211000
Row = Mapping[str, object]


@dataclass(frozen=True, slots=True)
class BrowserProviderPool:
    provider_key: str
    aliases: tuple[str, ...]
    manifest_digest: bytes

    def __post_init__(self) -> None:
        if (re.fullmatch(r"[A-Za-z0-9_-]{1,96}", self.provider_key) is None
                or not isinstance(self.manifest_digest, bytes)
                or len(self.manifest_digest) != 32
                or any(not a or a != a.strip() for a in self.aliases)):
            raise ValueError("browser_provider_pool_invalid")


class BrowserProviderPoolRegistry:
    """Trusted configuration, not a request-supplied endpoint or environment label."""

    def __init__(self, pools: tuple[BrowserProviderPool, ...]) -> None:
        self._aliases: dict[str, BrowserProviderPool] = {}
        for pool in pools:
            for alias in (pool.provider_key, *pool.aliases):
                if alias in self._aliases:
                    raise ValueError("browser_provider_pool_alias_duplicate")
                self._aliases[alias] = pool

    def resolve(self, alias: str) -> BrowserProviderPool:
        pool = self._aliases.get(alias)
        if pool is None:
            raise BrowserLeaseError("browser_provider_pool_unknown")
        return pool


def _key(binding: BrowserBindingFact | BrowserBindingKey) -> dict[str, object]:
    return {"tenant_id": binding.tenant_id, "ai_user_id": binding.ai_user_id,
            "target_system": binding.target_system, "binding_id": binding.binding_id}


def _increment(value: object) -> int:
    if type(value) is not int or not 0 <= value < MAX_BROWSER_REVISION:
        raise BrowserLeaseError("browser_lease_revision_exhausted")
    return value + 1


def _ttl(value: int) -> timedelta:
    if type(value) is not int or not 1 <= value <= 300:
        raise BrowserLeaseError("browser_lease_ttl_invalid")
    return timedelta(seconds=value)


def _matches(row: Row, claim: BrowserLeaseClaim, *, business: bool) -> None:
    expected: dict[str, object] = {
        **_key(claim.binding), "lease_epoch": claim.lease_epoch,
        "holder_id": claim.holder_id, "holder_session_id": claim.auth.owner.session_id,
        "binding_revision": claim.binding.binding_revision,
        "authorization_revision": claim.auth.authorization_revision,
        "auth_session_fingerprint": claim.auth.fingerprint,
        "auth_expires_at": claim.auth.expires_at,
        "acquisition_operation_id": claim.operation_id, "provider_key": claim.provider_key,
    }
    if business:
        expected.update(lease_revision=claim.lease_revision, deadline=claim.deadline, state="held")
    if (not row.get("capacity_held") or row.get("state") not in {"held", "quarantined"}
            or any(row.get(k) != v for k, v in expected.items())):
        raise BrowserLeaseError("browser_lease_stale")


def _binding_current(row: Row, binding: BrowserBindingFact) -> None:
    if (row.get("binding_revision") != binding.binding_revision
            or row.get("binding_state") != "active" or row.get("revoked_at") is not None
            or row.get("binding_subject_digest") != binding.subject_digest):
        raise BrowserLeaseError("browser_binding_stale")


class PostgreSQLBrowserLeaseStore:
    """Injected current-auth/binding checkers are bounded local/DB authority reads.

    They may run under the short lock transaction and must never call providers or
    upstream networks. Remote authorization IO must precede a genuine local/DB
    authority recheck; an absent such authority leaves production admission closed.
    Provider-proof and exact-resource SUBJECT IO always run after unlocking.
    """

    def __init__(
        self, *, session_factory: async_sessionmaker[AsyncSession],
        current_auth: BrowserCurrentAuthPort | None,
        binding_reader: BrowserBindingReaderPort | None,
        registry: BrowserProviderPoolRegistry | None,
        resource_cipher: BrowserResourceCipher,
        proof_context: BrowserClaimProofContext | None,
        proof_verifier: BrowserProviderProofPort | None,
        cleanup_authority: BrowserCleanupAuthorityPort | None,
        resource_subject: BrowserResourceSubjectPort | None,
    ) -> None:
        self._sessions = session_factory
        self._auth = current_auth
        self._binding = binding_reader
        self._registry = registry
        self._cipher = resource_cipher
        self._proof_context = proof_context
        self._proof_verifier = proof_verifier
        self._cleanup_authority = cleanup_authority
        self._subject = resource_subject

    def _pool(self, alias: str) -> BrowserProviderPool:
        if (self._registry is None or self._proof_context is None or self._proof_verifier is None
                or self._cleanup_authority is None):
            raise BrowserLeaseError("browser_provider_authority_unavailable")
        return self._registry.resolve(alias)

    async def _current(
        self, auth: BrowserAuthFact, binding: BrowserBindingFact | BrowserLeaseBindingSnapshot,
    ) -> None:
        if not isinstance(binding, BrowserBindingFact):
            raise BrowserLeaseError("browser_cleanup_claim_not_business")
        if (self._auth is None or self._binding is None or self._subject is None
                or auth.owner.tenant_id != binding.tenant_id
                or auth.owner.user_id != binding.ai_user_id):
            raise BrowserLeaseError("browser_authority_unavailable")
        await self._auth.check_current(auth)
        await self._binding.check_binding(binding)

    async def _cleanup_allowed(self, claim: BrowserLeaseClaim) -> None:
        if self._cleanup_authority is None:
            raise BrowserLeaseError("browser_cleanup_authority_unavailable")
        await self._cleanup_authority.check_cleanup(claim)

    async def recover_cleanup(self, key: BrowserBindingKey) -> BrowserLeaseClaim:
        """No current subject/auth is invented and no reference is decrypted here."""
        if self._cleanup_authority is None:
            raise BrowserLeaseError("browser_cleanup_authority_unavailable")
        await self._cleanup_authority.check_recovery(key)
        async with self._sessions() as session:
            row = (await session.execute(text(f"SELECT * FROM {_TABLE} WHERE {_B}"),
                                         _key(key))).mappings().one_or_none()
        if row is None or row["state"] not in {"held", "quarantined"} or not row["capacity_held"]:
            raise BrowserLeaseError("browser_lease_stale")
        self._pool(row["provider_key"])
        snapshot = BrowserLeaseBindingSnapshot(
            row["tenant_id"], row["ai_user_id"], row["target_system"], row["binding_id"],
            row["binding_revision"],
        )
        owner = BrowserOwner(tenant_id=row["tenant_id"], user_id=row["ai_user_id"],
                             session_id=row["holder_session_id"])
        captured_auth = BrowserAuthFact(owner, row["authorization_revision"],
                                        bytes(row["auth_session_fingerprint"]),
                                        row["auth_expires_at"])
        claim = BrowserLeaseClaim(
            captured_auth, snapshot, row["lease_epoch"], row["lease_revision"],
            row["holder_id"], row["acquisition_operation_id"], row["provider_key"], row["deadline"])
        await self._cleanup_allowed(claim)
        return claim

    @asynccontextmanager
    async def _locked(
        self, binding: BrowserBindingFact | BrowserBindingKey, pool: BrowserProviderPool,
    ) -> AsyncIterator[tuple[AsyncSession, Row, Row | None, datetime, int, int]]:
        params = {**_key(binding), "provider_key": pool.provider_key}
        async with self._sessions() as session, session.begin():
            await session.execute(text("SELECT pg_advisory_xact_lock_shared(:writer_lock)"),
                                  {"writer_lock": _WRITER_LOCK})
            global_limit = (await session.execute(text(
                "SELECT max_active FROM browser_capacity_limits WHERE provider_key=:provider_key"
                " AND tenant_id IS NULL FOR UPDATE"
            ), params)).scalar_one_or_none()
            tenant_limit = (await session.execute(text(
                "SELECT max_active FROM browser_capacity_limits WHERE provider_key=:provider_key"
                " AND tenant_id=:tenant_id FOR UPDATE"
            ), params)).scalar_one_or_none()
            if (type(global_limit) is not int or type(tenant_limit) is not int
                    or global_limit <= 0 or tenant_limit <= 0):
                raise BrowserLeaseError("browser_capacity_unconfigured")
            credential = (await session.execute(text(
                "SELECT binding_revision,binding_state,binding_subject_digest,revoked_at"
                f" FROM oa_session_credentials WHERE {_B} FOR UPDATE"
            ), params)).mappings().one_or_none()
            if credential is None:
                raise BrowserLeaseError("browser_binding_missing")
            row = (await session.execute(text(
                f"SELECT * FROM {_TABLE} WHERE {_B} FOR UPDATE"
            ), params)).mappings().one_or_none()
            # transaction_timestamp()/now() could predate waiting for these locks.
            now = (await session.execute(text("SELECT clock_timestamp()"))).scalar_one()
            if not isinstance(now, datetime) or now.tzinfo is None:
                raise BrowserLeaseError("browser_database_clock_invalid")
            yield (session, cast(Row, credential), cast(Row | None, row), now,
                   global_limit, tenant_limit)

    async def reserve(
        self, auth: BrowserAuthFact, binding: BrowserBindingFact,
        provider_alias: str, *, ttl_seconds: int,
    ) -> BrowserLeaseClaim:
        duration, pool = _ttl(ttl_seconds), self._pool(provider_alias)
        await self._current(auth, binding)
        async with self._locked(binding, pool) as (session, credential, row, now, glob, tenant):
            await self._current(auth, binding)
            _binding_current(credential, binding)
            if auth.expires_at <= now:
                raise BrowserLeaseError("browser_auth_expired")
            if row is not None and row["state"] != "released":
                raise BrowserLeaseError("browser_binding_busy")
            counts = (await session.execute(text(
                f"SELECT count(*) AS global_count, count(*) FILTER (WHERE tenant_id=:tenant_id)"
                f" AS tenant_count FROM {_TABLE} WHERE provider_key=:provider_key AND capacity_held"
            ), {"tenant_id": binding.tenant_id, "provider_key": pool.provider_key}
            )).mappings().one()
            if counts["global_count"] >= glob or counts["tenant_count"] >= tenant:
                raise BrowserLeaseError("browser_capacity_exhausted")
            claim = BrowserLeaseClaim(auth, binding,
                _increment(row["lease_epoch"] if row else 0),
                _increment(row["lease_revision"] if row else 0), uuid4().hex, uuid4().hex,
                pool.provider_key, min(now + duration, auth.expires_at))
            if row is None:
                await session.execute(text(
                    f"INSERT INTO {_TABLE}(tenant_id,ai_user_id,target_system,binding_id)"
                    " VALUES(:tenant_id,:ai_user_id,:target_system,:binding_id)"
                ), _key(binding))
            await self._update(session, binding, {
                "lease_epoch": claim.lease_epoch, "lease_revision": claim.lease_revision,
                "holder_id": claim.holder_id, "holder_session_id": auth.owner.session_id,
                "binding_revision": binding.binding_revision,
                "authorization_revision": auth.authorization_revision,
                "auth_session_fingerprint": auth.fingerprint, "auth_expires_at": auth.expires_at,
                "deadline": claim.deadline, "state": "held", "capacity_held": True,
                "provider_key": pool.provider_key, "acquisition_operation_id": claim.operation_id,
                "acquisition_phase": "reservation_only", "acquisition_send_started": False,
                "release_outcome": None, "release_proof_digest": None,
            }, now)
        return claim

    async def _business_locked(
        self, claim: BrowserLeaseClaim, credential: Row, row: Row | None, now: datetime,
    ) -> Row:
        await self._current(claim.auth, claim.binding)
        if not isinstance(claim.binding, BrowserBindingFact):
            raise BrowserLeaseError("browser_cleanup_claim_not_business")
        _binding_current(credential, claim.binding)
        if row is None:
            raise BrowserLeaseError("browser_lease_stale")
        _matches(row, claim, business=True)
        if claim.deadline <= now or claim.auth.expires_at <= now:
            raise BrowserLeaseError("browser_lease_expired")
        return row

    async def start_acquisition(self, claim: BrowserLeaseClaim) -> BrowserLeaseClaim:
        pool = self._pool(claim.provider_key)
        async with self._locked(claim.binding, pool) as (session, credential, row, now, _, _):
            row = await self._business_locked(claim, credential, row, now)
            if row["acquisition_phase"] != "reservation_only" or row["acquisition_send_started"]:
                raise BrowserLeaseError("browser_acquisition_already_started")
            revision = _increment(row["lease_revision"])
            await self._update(session, claim.binding, {
                "lease_revision": revision, "acquisition_phase": "acquiring",
                "acquisition_send_started": True,
            }, now)
        # This return happens only after the send-started transaction committed.
        return replace(claim, lease_revision=revision)

    async def _snapshot(self, claim: BrowserLeaseClaim, *, business: bool) -> Row:
        if business:
            pool = self._pool(claim.provider_key)
            async with self._locked(claim.binding, pool) as (_, credential, row, now, _, _):
                checked = await self._business_locked(claim, credential, row, now)
                snapshot = dict(checked)
            return snapshot
        async with self._sessions() as session:
            stored_row = (await session.execute(text(f"SELECT * FROM {_TABLE} WHERE {_B}"),
                                                _key(claim.binding))).mappings().one_or_none()
        if stored_row is None:
            raise BrowserLeaseError("browser_lease_stale")
        result = cast(Row, stored_row)
        _matches(result, claim, business=business)
        return result

    def _resource(self, row: Row, claim: BrowserLeaseClaim) -> bytes | None:
        if row["resource_cipher_version"] is None:
            return None
        envelope = ResourceEnvelope(
            cast(str, row["resource_cipher_version"]), cast(str, row["resource_key_id"]),
            bytes(cast(bytes, row["resource_nonce"])),
            bytes(cast(bytes, row["encrypted_resource_ref"])),
        )
        return self._cipher.decrypt(claim, envelope)

    async def _verified(
        self, claim: BrowserLeaseClaim, pool: BrowserProviderPool,
        resource: bytes | None, evidence: bytes,
    ) -> BrowserProviderFact:
        context, verifier = self._proof_context, self._proof_verifier
        if (context is None or verifier is None or not isinstance(evidence, bytes)
                or not evidence):
            raise BrowserLeaseError("browser_provider_proof_invalid")
        challenge = context.challenge(claim, pool.manifest_digest)
        expected = BrowserProviderExpectation(claim, challenge, pool.manifest_digest, resource)
        fact = await verifier.verify(expected, evidence)
        if (not isinstance(fact, BrowserProviderFact)
                or fact.provider_key != claim.provider_key
                or fact.operation_id != claim.operation_id or fact.holder_id != claim.holder_id
                or fact.lease_epoch != claim.lease_epoch
                or not isinstance(fact.challenge, bytes)
                or not hmac.compare_digest(fact.challenge, challenge)
                or not isinstance(fact.manifest_digest, bytes)
                or not hmac.compare_digest(fact.manifest_digest, pool.manifest_digest)
                or not isinstance(fact.resource_ref, bytes) or not fact.resource_ref
                or (resource is not None and fact.resource_ref != resource)
                or fact.outcome not in {"acquired", "released", "terminated"}):
            raise BrowserLeaseError("browser_provider_proof_mismatch")
        return fact

    async def record_acquired(
        self, claim: BrowserLeaseClaim, evidence: bytes,
    ) -> BrowserLeaseClaim:
        pool = self._pool(claim.provider_key)
        await self._current(claim.auth, claim.binding)
        snapshot = await self._snapshot(claim, business=True)
        if snapshot["acquisition_phase"] != "acquiring":
            raise BrowserLeaseError("browser_acquisition_phase_invalid")
        # The lock wait/commit is an await boundary. Recheck current authority
        # after unlocking, immediately before consuming provider evidence.
        await self._current(claim.auth, claim.binding)
        fact = await self._verified(claim, pool, None, evidence)
        if fact.outcome != "acquired":
            raise BrowserLeaseError("browser_provider_proof_mismatch")
        if self._subject is None:
            raise BrowserLeaseError("browser_subject_authority_unavailable")
        # Evidence retrieval itself can outlive the claim or race trusted cleanup.
        # Revalidate under locks before touching the exact resource for SUBJECT.
        snapshot = await self._snapshot(claim, business=True)
        if snapshot["acquisition_phase"] != "acquiring":
            raise BrowserLeaseError("browser_acquisition_phase_invalid")
        await self._current(claim.auth, claim.binding)
        await self._subject.check_subject(claim, fact.resource_ref)
        await self._current(claim.auth, claim.binding)
        envelope = self._cipher.encrypt(claim, fact.resource_ref)
        async with self._locked(claim.binding, pool) as (session, credential, row, now, _, _):
            row = await self._business_locked(claim, credential, row, now)
            if row["acquisition_phase"] != "acquiring":
                raise BrowserLeaseError("browser_acquisition_phase_invalid")
            revision = _increment(row["lease_revision"])
            assert self._proof_context is not None
            await self._update(session, claim.binding, {
                "lease_revision": revision, "acquisition_phase": "acquired",
                "resource_cipher_version": envelope.cipher_version,
                "resource_key_id": envelope.key_id, "resource_nonce": envelope.nonce,
                "encrypted_resource_ref": envelope.encrypted_payload,
                "resource_proof_digest": self._proof_context.digest("acquire-proof.v1", evidence),
            }, now)
        return replace(claim, lease_revision=revision)

    async def renew(
        self, claim: BrowserLeaseClaim, *, ttl_seconds: int,
    ) -> BrowserLeaseClaim:
        duration, pool = _ttl(ttl_seconds), self._pool(claim.provider_key)
        async with self._locked(claim.binding, pool) as (session, credential, row, now, _, _):
            row = await self._business_locked(claim, credential, row, now)
            revision = _increment(row["lease_revision"])
            deadline = min(now + duration, claim.auth.expires_at)
            await self._update(session, claim.binding,
                               {"lease_revision": revision, "deadline": deadline}, now)
        return replace(claim, lease_revision=revision, deadline=deadline)

    async def authorize_resource(self, claim: BrowserLeaseClaim) -> bytes:
        pool = self._pool(claim.provider_key)
        await self._current(claim.auth, claim.binding)
        snapshot = await self._snapshot(claim, business=True)
        if snapshot["acquisition_phase"] != "acquired":
            raise BrowserLeaseError("browser_resource_unavailable")
        await self._current(claim.auth, claim.binding)
        resource = self._resource(snapshot, claim)
        if resource is None or self._subject is None:
            raise BrowserLeaseError("browser_resource_unavailable")
        await self._subject.check_subject(claim, resource)
        await self._current(claim.auth, claim.binding)
        async with self._locked(claim.binding, pool) as (_, credential, row, now, _, _):
            row = await self._business_locked(claim, credential, row, now)
            if row["encrypted_resource_ref"] != snapshot["encrypted_resource_ref"]:
                raise BrowserLeaseError("browser_lease_stale")
        return resource

    async def quarantine(self, claim: BrowserLeaseClaim) -> BrowserLeaseClaim:
        pool = self._pool(claim.provider_key)
        await self._cleanup_allowed(claim)
        async with self._locked(claim.binding, pool) as (session, _, row, now, _, _):
            if row is None:
                raise BrowserLeaseError("browser_lease_stale")
            _matches(row, claim, business=False)
            revision = _increment(row["lease_revision"])
            await self._update(session, claim.binding, {
                "state": "quarantined", "lease_revision": revision,
                "acquisition_phase": "unknown" if row["acquisition_send_started"]
                else "reservation_only",
            }, now)
        return replace(claim, lease_revision=revision, deadline=cast(datetime, row["deadline"]))

    async def cleanup(
        self, claim: BrowserLeaseClaim, evidence: bytes | None = None,
    ) -> bool:
        pool = self._pool(claim.provider_key)
        await self._cleanup_allowed(claim)
        fact: BrowserProviderFact | None = None
        if evidence is not None:
            snapshot = await self._snapshot(claim, business=False)
            fact = await self._verified(claim, pool, self._resource(snapshot, claim), evidence)
            if fact.outcome not in {"released", "terminated"}:
                raise BrowserLeaseError("browser_provider_release_unproven")
        async with self._locked(claim.binding, pool) as (session, _, row, now, _, _):
            if row is None:
                raise BrowserLeaseError("browser_lease_stale")
            if row["state"] == "released" and row["lease_epoch"] == claim.lease_epoch:
                return True
            _matches(row, claim, business=False)
            never_sent = (row["acquisition_phase"] == "reservation_only"
                          and not row["acquisition_send_started"]
                          and row["resource_cipher_version"] is None)
            revision = _increment(row["lease_revision"])
            if not never_sent and fact is None:
                await self._update(session, claim.binding, {
                    "state": "quarantined", "acquisition_phase": "unknown",
                    "lease_revision": revision,
                }, now)
                return False
            # All provider IO is finished. Revalidate any reference acquired during IO.
            if fact is not None:
                current_resource = self._resource(row, claim)
                if current_resource is not None and current_resource != fact.resource_ref:
                    raise BrowserLeaseError("browser_provider_proof_mismatch")
            context = self._proof_context
            if context is None:
                raise BrowserLeaseError("browser_provider_authority_unavailable")
            proof = (context.digest("release-proof.v1", evidence) if evidence is not None
                     else context.digest("never-sent.v1", context.challenge(
                         claim, pool.manifest_digest)))
            await self._update(session, claim.binding, {
                "lease_revision": revision, "state": "released", "capacity_held": False,
                "holder_id": None, "holder_session_id": None, "binding_revision": None,
                "authorization_revision": None, "auth_session_fingerprint": None,
                "auth_expires_at": None, "deadline": None, "provider_key": None,
                "acquisition_operation_id": None, "acquisition_phase": "reservation_only",
                "acquisition_send_started": False, "resource_cipher_version": None,
                "resource_key_id": None, "resource_nonce": None, "encrypted_resource_ref": None,
                "resource_proof_digest": None,
                "release_outcome": fact.outcome if fact else "released",
                "release_proof_digest": proof,
            }, now)
        return True

    @staticmethod
    async def _update(
        session: AsyncSession, binding: BrowserBindingFact | BrowserBindingKey,
        values: dict[str, object], now: datetime,
    ) -> None:
        # Column names come exclusively from fixed implementation dictionaries.
        values = {**values, "updated_at": now}
        assignments = ",".join(f"{key}=:{key}" for key in values)
        result = await session.execute(text(
            f"UPDATE {_TABLE} SET {assignments} WHERE {_B}"
        ), {**_key(binding), **values})
        if result.rowcount != 1:  # type: ignore[attr-defined]
            raise BrowserLeaseError("browser_lease_stale")
