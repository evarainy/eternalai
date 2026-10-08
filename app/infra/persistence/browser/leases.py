"""PostgreSQL leases: lock capacity before binding/claim, never hold it over provider IO.

Registry entries pin one actual capacity pool and an immutable provider manifest.
Aliases never create capacity. Outstanding claims require their original registry
manifest and stable proof-context key across restarts. Missing/changed authority
fails closed and cannot clear quarantine. No production provider is registered here.
"""

from __future__ import annotations

import hmac
import re
from collections.abc import Awaitable, Callable
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
from app.infra.persistence.browser.runs import _snapshot
from app.ports.browser_run_store import RunSnapshot
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
        "authorization_run_id": claim.auth.authorization_run_id,
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
        current_in_session: Callable[
            [AsyncSession, BrowserAuthFact, BrowserBindingFact, Row | None], Awaitable[None]
        ] | None = None,
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
        self._current_session = current_in_session

    def _pool(self, alias: str) -> BrowserProviderPool:
        if (self._registry is None or self._proof_context is None or self._proof_verifier is None
                or self._cleanup_authority is None):
            raise BrowserLeaseError("browser_provider_authority_unavailable")
        return self._registry.resolve(alias)

    async def _current(
        self, auth: BrowserAuthFact, binding: BrowserBindingFact | BrowserLeaseBindingSnapshot,
        *, session: AsyncSession | None = None, credential: Row | None = None,
    ) -> None:
        if not isinstance(binding, BrowserBindingFact):
            raise BrowserLeaseError("browser_cleanup_claim_not_business")
        if (self._auth is None or self._binding is None or self._subject is None
                or auth.owner.tenant_id != binding.tenant_id
                or auth.owner.user_id != binding.ai_user_id):
            raise BrowserLeaseError("browser_authority_unavailable")
        if self._current_session is None:
            await self._auth.check_current(auth)
            await self._binding.check_binding(binding)
        elif session is None:
            async with self._sessions() as current, current.begin():
                await self._current_session(current, auth, binding, None)
        else:
            await self._current_session(session, auth, binding, credential)

    async def has_capacity(self, binding: BrowserBindingFact, provider_alias: str) -> bool:
        """Scheduling hint only; reserve remains the atomic, authorized admission."""
        async with self._sessions() as session:
            return await self._has_capacity_in_session(session, binding, provider_alias)

    async def _has_capacity_in_session(
        self, session: AsyncSession, binding: BrowserBindingFact, provider_alias: str,
    ) -> bool:
        """Nonlocking hint in the caller's session; never reserve capacity here."""
        pool = self._pool(provider_alias)
        params = {**_key(binding), "provider_key": pool.provider_key}
        row = (await session.execute(text(
            "SELECT "
            "(SELECT max_active FROM browser_capacity_limits"
            " WHERE provider_key=:provider_key AND tenant_id IS NULL) AS global_limit,"
            "(SELECT max_active FROM browser_capacity_limits"
            " WHERE provider_key=:provider_key AND tenant_id=:tenant_id) AS tenant_limit,"
            "(SELECT count(*) FROM browser_binding_leases"
            " WHERE provider_key=:provider_key AND capacity_held) AS global_count,"
            "(SELECT count(*) FROM browser_binding_leases WHERE provider_key=:provider_key"
            " AND tenant_id=:tenant_id AND capacity_held) AS tenant_count,"
            f"EXISTS (SELECT 1 FROM {_TABLE} WHERE {_B}"
            " AND state<>'released') AS binding_busy"
        ), params)).mappings().one()
        if (type(row["global_limit"]) is not int or type(row["tenant_limit"]) is not int
                or row["global_limit"] <= 0 or row["tenant_limit"] <= 0):
            raise BrowserLeaseError("browser_capacity_unconfigured")
        return (not row["binding_busy"] and row["global_count"] < row["global_limit"]
                and row["tenant_count"] < row["tenant_limit"])

    async def has_run_lease(self, run: RunSnapshot) -> bool:
        """Exact persisted reservation hint, including a crash before attach.

        This neither grants business authority nor recovers a resource. Epoch
        and provider also match when already attached; reserve remains atomic.
        """
        async with self._sessions() as session:
            return await self._has_run_lease_in_session(session, run)

    async def _has_run_lease_in_session(self, session: AsyncSession, run: RunSnapshot) -> bool:
        """Exact reservation hint without opening or closing another session."""
        admission = run.admission
        params = {
            "tenant_id": run.owner.tenant_id, "ai_user_id": run.owner.user_id,
            "target_system": admission.target_system, "binding_id": admission.binding_id,
            "binding_revision": admission.binding_revision,
            "session_id": run.owner.session_id, "run_id": run.run_id,
            "fingerprint": admission.auth_fingerprint, "expires_at": admission.auth_expires_at,
        }
        exact = (
            " AND binding_revision=:binding_revision AND holder_session_id=:session_id"
            " AND authorization_run_id=:run_id AND authorization_revision IS NULL"
            " AND auth_session_fingerprint=:fingerprint AND auth_expires_at=:expires_at"
            " AND capacity_held AND state IN ('held','quarantined')"
        )
        if run.lease_epoch is not None:
            exact += " AND lease_epoch=:lease_epoch"
            params["lease_epoch"] = run.lease_epoch
        if run.provider_key is not None:
            exact += " AND provider_key=:provider_key"
            params["provider_key"] = run.provider_key
        return bool((await session.execute(text(
            f"SELECT EXISTS (SELECT 1 FROM {_TABLE} WHERE {_B}" + exact + ")",
        ), params)).scalar_one())

    async def _prove_no_run_resource(self, run: RunSnapshot) -> bool:
        """Cancellation proof under the same earlier locks used by Run actions.

        The credential row also serializes a missing lease row with reserve's
        INSERT. This is a read-only proof, never a release of a foreign claim.
        """
        params = {
            "tenant_id": run.owner.tenant_id, "ai_user_id": run.owner.user_id,
            "session_id": run.owner.session_id, "task_id": run.task_id, "run_id": run.run_id,
            "target_system": run.admission.target_system, "binding_id": run.admission.binding_id,
        }
        async with self._sessions() as session, session.begin():
            await session.execute(text("SELECT pg_advisory_xact_lock_shared(:key)"),
                                  {"key": _WRITER_LOCK})
            credential = (await session.execute(text(
                f"SELECT binding_id FROM oa_session_credentials WHERE {_B} FOR UPDATE",
            ), params)).scalar_one_or_none()
            if credential is None:
                raise BrowserLeaseError("browser_binding_missing")
            statements = (
                f"SELECT lease_epoch FROM {_TABLE} WHERE {_B} FOR UPDATE",
                "SELECT session_id FROM sessions WHERE tenant_id=:tenant_id"
                " AND session_id=:session_id FOR UPDATE",
                "SELECT task_id FROM tasks WHERE tenant_id=:tenant_id AND ai_user_id=:ai_user_id"
                " AND session_id=:session_id AND task_id=:task_id FOR UPDATE",
                "SELECT run_id FROM browser_runs WHERE tenant_id=:tenant_id"
                " AND ai_user_id=:ai_user_id AND session_id=:session_id"
                " AND task_id=:task_id AND run_id=:run_id FOR UPDATE",
            )
            for statement in statements:
                await session.execute(text(statement), params)
            return await self._prove_no_run_resource_in_session(session, run)

    async def _prove_no_run_resource_in_session(
        self, session: AsyncSession, run: RunSnapshot,
    ) -> bool:
        """Caller holds credential -> lease -> session -> Task -> Run locks."""
        if not session.in_transaction():
            raise BrowserLeaseError("browser_lease_stale")
        params = {
            "tenant_id": run.owner.tenant_id, "ai_user_id": run.owner.user_id,
            "session_id": run.owner.session_id, "task_id": run.task_id, "run_id": run.run_id,
            "target_system": run.admission.target_system, "binding_id": run.admission.binding_id,
        }
        row = (await session.execute(text(
            "SELECT * FROM browser_runs WHERE tenant_id=:tenant_id AND ai_user_id=:ai_user_id"
            " AND session_id=:session_id AND task_id=:task_id AND run_id=:run_id",
        ), params)).mappings().one_or_none()
        if row is None:
            return False
        current = _snapshot(row)
        # acknowledge_cancel checks a candidate with this one flag changed,
        # before persistence. Every other admission/worker/state field matches.
        if current != run and replace(current, cancel_acknowledged=True) != run:
            return False
        active = current.status in {"running", "waiting_user"}
        if (not current.cancel_requested or current.effect != "not_sent"
                or current.lease_epoch is not None or current.provider_key is not None
                or current.provider_manifest_digest is not None or current.verification is not None
                or current.capture_status != "not_requested"
                or current.capture_operation_id is not None or current.profile_generation_id is not None
                or current.protected_result is not None or current.result_digest is not None
                or current.verification_evidence_digest is not None):
            return False
        if active:
            now = (await session.execute(text("SELECT clock_timestamp()"))).scalar_one()
            if (current.phase not in {"queued", "acquiring"} or current.worker_id is None
                    or current.worker_epoch < 1 or current.worker_deadline is None
                    or current.worker_deadline <= now):
                return False
        elif (current.status != "cancelled" or current.phase is not None
              or not current.cancel_acknowledged):
            return False
        # A run_id match blocks proof even if another associated field is
        # missing/corrupt or points at a different binding. Never infer absence
        # from failure of the narrower has_run_lease identity match.
        if (await session.execute(text(
            f"SELECT 1 FROM {_TABLE} WHERE tenant_id=:tenant_id"
            " AND authorization_run_id=:run_id LIMIT 1",
        ), params)).scalar_one_or_none() is not None:
            return False
        lease = (await session.execute(text(
            f"SELECT * FROM {_TABLE} WHERE {_B}",
        ), params)).mappings().one_or_none()
        if lease is None:
            return True
        if lease["state"] == "released":
            # Reuse cleanup's fully cleared reservation/acquisition fields.
            return (not lease["capacity_held"] and not lease["acquisition_send_started"]
                    and lease["acquisition_phase"] == "reservation_only"
                    and all(lease[name] is None for name in (
                        "holder_id", "holder_session_id", "binding_revision",
                        "authorization_revision", "authorization_run_id",
                        "auth_session_fingerprint", "auth_expires_at", "deadline", "provider_key",
                        "acquisition_operation_id", "resource_cipher_version", "resource_key_id",
                        "resource_nonce", "encrypted_resource_ref", "resource_proof_digest",
                    ))
                    and (lease["lease_epoch"] == 0 or (
                        lease["release_outcome"] in {"released", "terminated"}
                        and lease["release_proof_digest"] is not None)))
        # A complete, positively identified other Run is allowed to retain its
        # own lease/acquisition. Unknown/partial ownership fails closed.
        return (lease["state"] in {"held", "quarantined"} and lease["capacity_held"]
                and isinstance(lease["authorization_run_id"], str)
                and re.fullmatch(r"[A-Za-z0-9_-]{1,96}", lease["authorization_run_id"]) is not None
                and lease["authorization_run_id"] != run.run_id
                and lease["authorization_revision"] is None
                and lease["acquisition_phase"] in {"reservation_only", "acquiring", "acquired", "unknown"}
                and all(lease[name] is not None for name in (
                    "holder_id", "holder_session_id", "binding_revision", "auth_session_fingerprint",
                    "auth_expires_at", "deadline", "provider_key", "acquisition_operation_id",
                )))

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
                                        row["auth_expires_at"],
                                        authorization_run_id=row["authorization_run_id"],
                                        evidence_version=("verified-session-v1"
                                            if row["authorization_run_id"] is not None else None))
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
            await self._current(auth, binding, session=session, credential=credential)
            _binding_current(credential, binding)
            if auth.authorization_run_id is not None:
                # The credential lock is shared with cancellation/proof. An
                # old pre-reserve await cannot create a claim after cancel.
                eligible = (await session.execute(text(
                    "SELECT 1 FROM browser_runs WHERE tenant_id=:tenant_id"
                    " AND ai_user_id=:ai_user_id AND session_id=:session_id AND run_id=:run_id"
                    " AND target_system=:target_system AND binding_id=:binding_id"
                    " AND binding_revision=:binding_revision AND auth_fingerprint=:fingerprint"
                    " AND auth_expires_at=:expires_at AND status IN ('running','waiting_user')"
                    " AND phase='acquiring' AND effect='not_sent' AND NOT cancel_requested"
                    " AND lease_epoch IS NULL AND provider_key IS NULL",
                ), {**_key(binding), "session_id": auth.owner.session_id,
                    "run_id": auth.authorization_run_id, "binding_revision": binding.binding_revision,
                    "fingerprint": auth.fingerprint, "expires_at": auth.expires_at,
                })).scalar_one_or_none()
                if eligible != 1:
                    raise BrowserLeaseError("browser_lease_stale")
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
                "authorization_run_id": auth.authorization_run_id,
                "auth_session_fingerprint": auth.fingerprint, "auth_expires_at": auth.expires_at,
                "deadline": claim.deadline, "state": "held", "capacity_held": True,
                "provider_key": pool.provider_key, "acquisition_operation_id": claim.operation_id,
                "acquisition_phase": "reservation_only", "acquisition_send_started": False,
                "release_outcome": None, "release_proof_digest": None,
            }, now)
        return claim

    async def _business_locked(
        self, session: AsyncSession, claim: BrowserLeaseClaim,
        credential: Row, row: Row | None, now: datetime,
    ) -> Row:
        await self._current(claim.auth, claim.binding, session=session, credential=credential)
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
            row = await self._business_locked(session, claim, credential, row, now)
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
            async with self._locked(claim.binding, pool) as (session, credential, row, now, _, _):
                checked = await self._business_locked(session, claim, credential, row, now)
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
            row = await self._business_locked(session, claim, credential, row, now)
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
            row = await self._business_locked(session, claim, credential, row, now)
            revision = _increment(row["lease_revision"])
            deadline = min(now + duration, claim.auth.expires_at)
            await self._update(session, claim.binding,
                               {"lease_revision": revision, "deadline": deadline}, now)
        return replace(claim, lease_revision=revision, deadline=deadline)

    async def authorize_resource(self, claim: BrowserLeaseClaim) -> bytes:
        pool = self._pool(claim.provider_key)
        # _snapshot performs the pre-subject authority check under the lease locks.
        snapshot = await self._snapshot(claim, business=True)
        if snapshot["acquisition_phase"] != "acquired":
            raise BrowserLeaseError("browser_resource_unavailable")
        resource = self._resource(snapshot, claim)
        if resource is None or self._subject is None:
            raise BrowserLeaseError("browser_resource_unavailable")
        await self._subject.check_subject(claim, resource)
        # A new lock transaction rechecks authority after the external DOM await.
        async with self._locked(claim.binding, pool) as (session, credential, row, now, _, _):
            row = await self._business_locked(session, claim, credential, row, now)
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
                "authorization_run_id": None,
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
