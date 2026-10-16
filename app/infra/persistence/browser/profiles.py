"""Immutable profile history with owner, binding, lease and head CAS fencing.

All provider IO happens outside transactions. Lock order is credential, lease,
session, Task, Run, head, generation; callers must not invert it. Injected auth,
binding and cleanup authority checks are bounded local/DB checks only.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from typing import AsyncIterator, Mapping, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infra.browser.local_profile_cleanup import retention_ready
from app.infra.persistence.task_store.transactional import lock_owned_task
from app.ports.browser_profile_store import (
    BrowserProfileCaptureFact,
    BrowserProfileCaptureProofPort,
    BrowserProfileCleanupAuthorityPort,
    BrowserProfileCleanupFact,
    BrowserProfileCleanupProofPort,
    BrowserProfileContext,
    BrowserProfileError,
    BrowserProfileGeneration,
    BrowserProfileHead,
    BrowserProfileSessionBinder,
    ProfileCleanupCandidatePage,
)
from app.ports.browser_store import MAX_BROWSER_REVISION
from app.ports.credential_vault import (
    BrowserBindingFact,
    BrowserBindingReaderPort,
    BrowserCurrentAuthPort,
)

_BINDING = (
    "tenant_id=:tenant_id AND ai_user_id=:ai_user_id"
    " AND target_system=:target_system AND binding_id=:binding_id"
)
_OWNER = _BINDING + " AND session_id=:session_id"
_GEN_OWNER = (
    "g.tenant_id=:tenant_id AND g.ai_user_id=:ai_user_id"
    " AND g.session_id=:session_id AND g.target_system=:target_system"
    " AND g.binding_id=:binding_id"
)
Row = Mapping[str, object]


def _params(context: BrowserProfileContext) -> dict[str, object]:
    claim = context.claim
    return {
        "tenant_id": claim.auth.owner.tenant_id,
        "ai_user_id": claim.auth.owner.user_id,
        "session_id": claim.auth.owner.session_id,
        "target_system": claim.binding.target_system,
        "binding_id": claim.binding.binding_id,
        "task_id": context.task_id,
        "run_id": context.run_id,
    }


def _next(revision: int) -> int:
    if type(revision) is not int or not 0 <= revision < MAX_BROWSER_REVISION:
        raise BrowserProfileError("browser_profile_revision_invalid")
    return revision + 1


def _generation(row: Row) -> BrowserProfileGeneration:
    return BrowserProfileGeneration(
        fact=BrowserProfileCaptureFact(
            generation_id=cast(str, row["generation_id"]),
            binding_revision=cast(int, row["binding_revision"]),
            profile_revision=cast(int, row["profile_revision"]),
            lease_epoch=cast(int, row["lease_epoch"]),
            provider_key=cast(str, row["provider_key"]),
            capture_operation_id=cast(str, row["capture_operation_id"]),
            manifest_digest=cast(bytes, row["manifest_digest"]),
            generation_ref_digest=cast(bytes, row["generation_ref_digest"]),
            subject_digest=cast(bytes, row["subject_digest"]),
            origin_digest=cast(bytes, row["origin_digest"]),
            projection_digest=cast(bytes, row["projection_digest"]),
            captured_bytes=cast(int, row["captured_bytes"]),
        ),
        key_id=cast(str, row["key_id"]),
        nonce=cast(bytes, row["nonce"]),
        ciphertext=cast(bytes, row["ciphertext"]),
    )


class PostgreSQLBrowserProfileStore:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        session_binder: BrowserProfileSessionBinder | None,
        current_auth: BrowserCurrentAuthPort | None,
        binding_reader: BrowserBindingReaderPort | None,
        capture_proof: BrowserProfileCaptureProofPort | None,
        cleanup_proof: BrowserProfileCleanupProofPort | None,
        cleanup_authority: BrowserProfileCleanupAuthorityPort | None,
        retention_seconds: int | None = None,
    ) -> None:
        if (retention_seconds is not None and (
            type(retention_seconds) is not int
            or not 1 <= retention_seconds <= 365 * 24 * 3600
        )):
            raise ValueError("browser_profile_retention_invalid")
        self._sessions = session_factory
        self._binder = session_binder
        self._auth = current_auth
        self._binding = binding_reader
        self._capture = capture_proof
        self._cleanup = cleanup_proof
        self._cleanup_authority = cleanup_authority
        self._retention_seconds = retention_seconds

    async def _authorize(self, context: BrowserProfileContext, *, cleanup: bool) -> None:
        owner = context.claim.auth.owner
        if (
            self._binder is None
            or context.principal.ai_user_id != owner.user_id
            or context.principal.org_ctx.tenant_id != owner.tenant_id
            or not owner.session_id.startswith("sid_v1.")
        ):
            raise BrowserProfileError("browser_profile_owner_invalid")
        try:
            bound = self._binder.bind(context.principal, owner.session_id)
        except Exception:
            raise BrowserProfileError("browser_profile_owner_invalid") from None
        if bound != owner.session_id:
            raise BrowserProfileError("browser_profile_owner_invalid")
        if cleanup:
            if self._cleanup_authority is None:
                raise BrowserProfileError("browser_profile_cleanup_authority_unavailable")
            await self._cleanup_authority.check_cleanup(context)
        else:
            if (
                self._auth is None
                or self._binding is None
                or self._capture is None
                or not isinstance(context.claim.binding, BrowserBindingFact)
            ):
                raise BrowserProfileError("browser_profile_authority_unavailable")
            await self._auth.check_current(context.claim.auth)
            await self._binding.check_binding(context.claim.binding)

    @asynccontextmanager
    async def _locked(
        self,
        context: BrowserProfileContext,
        *,
        cleanup: bool = False,
    ) -> AsyncIterator[tuple[AsyncSession, Row, Row | None, Row | None]]:
        await self._authorize(context, cleanup=cleanup)
        p = _params(context)
        claim = context.claim
        async with self._sessions() as session, session.begin():
            await session.execute(text("SELECT pg_advisory_xact_lock_shared(746420212000)"))
            credential = (
                (
                    await session.execute(
                        text(f"SELECT * FROM oa_session_credentials WHERE {_BINDING} FOR UPDATE"), p
                    )
                )
                .mappings()
                .one_or_none()
            )
            lease = (
                (
                    await session.execute(
                        text(f"SELECT * FROM browser_binding_leases WHERE {_BINDING} FOR UPDATE"), p
                    )
                )
                .mappings()
                .one_or_none()
            )
            owner_session = (
                await session.execute(
                    text(
                        "SELECT session_id FROM sessions WHERE tenant_id=:tenant_id"
                        " AND session_id=:session_id FOR UPDATE"
                    ),
                    p,
                )
            ).scalar_one_or_none()
            task = await lock_owned_task(
                session,
                tenant_id=claim.auth.owner.tenant_id,
                ai_user_id=claim.auth.owner.user_id,
                session_id=claim.auth.owner.session_id,
                task_id=context.task_id,
            )
            run = (
                (
                    await session.execute(
                        text(
                            f"SELECT * FROM browser_runs WHERE {_OWNER} AND task_id=:task_id"
                            " AND run_id=:run_id FOR UPDATE"
                        ),
                        p,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if credential is None or owner_session is None or task is None or run is None:
                raise BrowserProfileError("browser_profile_owner_invalid")
            # Run evidence is exact and mutually exclusive; never turn NULL into a revision.
            if (
                claim.auth.authorization_revision is not None
                or claim.auth.authorization_run_id != context.run_id
                or claim.auth.evidence_version != "verified-session-v1"
                or run["auth_evidence_version"] != claim.auth.evidence_version
                or run["auth_fingerprint"] != claim.auth.fingerprint
                or run["auth_expires_at"] != claim.auth.expires_at
                or run["binding_revision"] != claim.binding.binding_revision
            ):
                raise BrowserProfileError("browser_profile_auth_mismatch")
            await self._authorize(context, cleanup=cleanup)
            if not cleanup:
                now = cast(
                    datetime, (await session.execute(text("SELECT clock_timestamp()"))).scalar_one()
                )
                expected = {
                    "holder_id": claim.holder_id,
                    "holder_session_id": claim.auth.owner.session_id,
                    "binding_revision": claim.binding.binding_revision,
                    "authorization_revision": None,
                    "authorization_run_id": context.run_id,
                    "auth_session_fingerprint": claim.auth.fingerprint,
                    "auth_expires_at": claim.auth.expires_at,
                    "lease_epoch": claim.lease_epoch,
                    "lease_revision": claim.lease_revision,
                    "deadline": claim.deadline,
                    "provider_key": claim.provider_key,
                    "acquisition_operation_id": claim.operation_id,
                    "state": "held",
                    "capacity_held": True,
                }
                binding = cast(BrowserBindingFact, claim.binding)
                if (
                    credential["binding_revision"] != binding.binding_revision
                    or credential["binding_state"] != "active"
                    or credential["revoked_at"] is not None
                    or credential["binding_subject_digest"] != binding.subject_digest
                ):
                    raise BrowserProfileError("browser_profile_binding_stale")
                if (
                    lease is None
                    or any(lease[k] != v for k, v in expected.items())
                    or claim.deadline <= now
                    or claim.auth.expires_at <= now
                ):
                    raise BrowserProfileError("browser_profile_lease_stale")
                if (
                    run["status"] not in {"running", "waiting_user"}
                    or run["cancel_requested"]
                    or run["worker_epoch"] != context.worker_epoch
                    or run["worker_id"] is None
                    or run["worker_deadline"] is None
                    or cast(datetime, run["worker_deadline"]) <= now
                    or run["provider_key"] != claim.provider_key
                    or run["lease_epoch"] != claim.lease_epoch
                ):
                    raise BrowserProfileError("browser_profile_run_stale")
            head = (
                (
                    await session.execute(
                        text(f"SELECT * FROM browser_profiles WHERE {_OWNER} FOR UPDATE"), p
                    )
                )
                .mappings()
                .one_or_none()
            )
            yield session, cast(Row, run), cast(Row | None, head), cast(Row | None, lease)

    async def _get_generation(
        self,
        session: AsyncSession,
        context: BrowserProfileContext,
        generation_id: str,
    ) -> Row:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT g.*,p.cipher_version,p.key_id,p.nonce,p.ciphertext"
                        " FROM browser_profile_generations AS g"
                        " JOIN browser_profile_payloads AS p ON"
                        " p.tenant_id=g.tenant_id AND p.ai_user_id=g.ai_user_id"
                        " AND p.session_id=g.session_id"
                        " AND p.target_system=g.target_system AND p.binding_id=g.binding_id"
                        " AND p.generation_id=g.generation_id"
                        f" WHERE {_GEN_OWNER} AND g.generation_id=:generation_id"
                        " FOR UPDATE OF g"
                    ),
                    {**_params(context), "generation_id": generation_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise BrowserProfileError("browser_profile_generation_missing")
        return cast(Row, row)

    async def _get_cleanup_row(
        self, session: AsyncSession, context: BrowserProfileContext,
        generation_id: str,
    ) -> Row:
        # Candidate checks have no reason to read the immutable ciphertext.
        row = (await session.execute(text(
            "SELECT generation_id,disposition,disposition_changed_at,created_at,lease_epoch,"
            "provider_key,capture_operation_id,generation_ref_digest"
            f" FROM browser_profile_generations WHERE {_OWNER}"
            " AND generation_id=:generation_id FOR UPDATE"
        ), {**_params(context), "generation_id": generation_id})).mappings().one_or_none()
        if row is None:
            raise BrowserProfileError("browser_profile_generation_missing")
        return cast(Row, row)

    def _head_revision(self, head: Row | None, expected: int) -> int:
        revision = cast(int, head["profile_revision"]) if head is not None else 0
        if type(expected) is not int or expected != revision:
            raise BrowserProfileError("browser_profile_stale")
        return _next(revision)

    async def _ensure_head(self, session: AsyncSession, context: BrowserProfileContext) -> None:
        await session.execute(
            text(
                "INSERT INTO browser_profiles(tenant_id,ai_user_id,session_id,"
                "target_system,binding_id)"
                " VALUES(:tenant_id,:ai_user_id,:session_id,:target_system,:binding_id)"
                " ON CONFLICT DO NOTHING"
            ),
            _params(context),
        )

    async def load_active(self, context: BrowserProfileContext) -> BrowserProfileHead:
        async with self._locked(context) as (session, run, head, _lease):
            if head is None or head["active_generation_id"] is None:
                return BrowserProfileHead(cast(int, head["profile_revision"]) if head else 0, None)
            row = await self._get_generation(
                session, context, cast(str, head["active_generation_id"])
            )
            if (
                row["disposition"] != "promoted"
                or row["profile_revision"] != head["profile_revision"]
            ):
                raise BrowserProfileError("browser_profile_head_invalid")
            generation = _generation(row)
            revision = cast(int, head["profile_revision"])
            if (
                generation.fact.binding_revision != context.claim.binding.binding_revision
                or generation.fact.provider_key != run["provider_key"]
                or generation.fact.manifest_digest != run["provider_manifest_digest"]
            ):
                raise BrowserProfileError("browser_profile_binding_stale")
        # Restore necessarily precedes creation of the live Context. A sealed
        # generation is data, never authority; the caller must check the actual
        # subject after restore before admitting business execution. Promotion
        # retains its independent live-subject proof below.
        async with self._locked(context) as (_session, _run, head, _lease):
            if (
                head is None
                or head["profile_revision"] != revision
                or head["active_generation_id"] != generation.fact.generation_id
            ):
                raise BrowserProfileError("browser_profile_stale")
        return BrowserProfileHead(revision, generation)

    async def record_validated(
        self,
        context: BrowserProfileContext,
        generation: BrowserProfileGeneration,
        evidence: bytes,
        *,
        expected_profile_revision: int,
    ) -> None:
        # Validate exact durable owner/lease/Run before asking the provider for proof.
        async with self._locked(context) as (_session, run, head, _lease):
            self._head_revision(head, expected_profile_revision)
            if run["capture_operation_id"] != generation.fact.capture_operation_id or run[
                "capture_status"
            ] not in {"sent", "unknown"}:
                raise BrowserProfileError("browser_profile_capture_stale")
        assert self._capture is not None
        fact = await self._capture.verify_capture(context, generation, evidence)
        if fact != generation.fact:
            raise BrowserProfileError("browser_profile_capture_mismatch")
        async with self._locked(context) as (session, run, head, _lease):
            next_revision = self._head_revision(head, expected_profile_revision)
            binding = cast(BrowserBindingFact, context.claim.binding)
            if (
                fact.binding_revision != binding.binding_revision
                or fact.subject_digest != binding.subject_digest
                or fact.lease_epoch != context.claim.lease_epoch
                or fact.provider_key != context.claim.provider_key
                or fact.profile_revision != next_revision
                or run["provider_manifest_digest"] != fact.manifest_digest
                or run["capture_operation_id"] != fact.capture_operation_id
                or run["capture_status"] not in {"sent", "unknown"}
                or run["profile_generation_id"] is not None
            ):
                raise BrowserProfileError("browser_profile_capture_stale")
            await self._ensure_head(session, context)
            values = {**_params(context), **asdict(fact)}
            columns = (
                "generation_id,tenant_id,ai_user_id,session_id,target_system,binding_id,"
                "binding_revision,profile_revision,lease_epoch,provider_key,capture_operation_id,"
                "manifest_digest,generation_ref_digest,subject_digest,origin_digest,"
                "projection_digest,captured_bytes"
            )
            placeholders = ",".join(":" + column for column in columns.split(","))
            await session.execute(
                text(
                    f"INSERT INTO browser_profile_generations({columns},disposition)"
                    f" VALUES({placeholders},'validated')"
                ),
                values,
            )
            await session.execute(
                text(
                    "INSERT INTO browser_profile_payloads(tenant_id,ai_user_id,session_id,"
                    "target_system,binding_id,generation_id,cipher_version,key_id,nonce,"
                    "ciphertext) VALUES(:tenant_id,:ai_user_id,:session_id,:target_system,"
                    ":binding_id,:generation_id,:cipher_version,:key_id,:nonce,:ciphertext)"
                ),
                {
                    **values,
                    "cipher_version": generation.cipher_version,
                    "key_id": generation.key_id,
                    "nonce": generation.nonce,
                    "ciphertext": generation.ciphertext,
                },
            )
            await session.execute(
                text(
                    "UPDATE browser_runs SET profile_generation_id=:generation_id,"
                    " capture_status='validated',state_revision=state_revision+1,"
                    "updated_at=clock_timestamp()"
                    f" WHERE {_OWNER} AND run_id=:run_id"
                ),
                values,
            )

    async def promote(
        self,
        context: BrowserProfileContext,
        generation_id: str,
        *,
        expected_profile_revision: int,
    ) -> BrowserProfileHead:
        async with self._locked(context) as (session, _run, _head, _lease):
            generation = _generation(await self._get_generation(session, context, generation_id))
        assert self._capture is not None
        await self._capture.check_live_subject(context, generation)
        async with self._locked(context) as (session, run, head, _lease):
            revision = self._head_revision(head, expected_profile_revision)
            row = await self._get_generation(session, context, generation_id)
            binding = cast(BrowserBindingFact, context.claim.binding)
            if (
                head is None
                or row["disposition"] != "validated"
                or generation.fact.profile_revision != revision
                or generation.fact.binding_revision != binding.binding_revision
                or generation.fact.subject_digest != binding.subject_digest
                or generation.fact.lease_epoch != context.claim.lease_epoch
                or generation.fact.provider_key != context.claim.provider_key
                or run["provider_manifest_digest"] != generation.fact.manifest_digest
                or run["profile_generation_id"] != generation_id
                or run["capture_status"] != "validated"
                or run["capture_operation_id"] != generation.fact.capture_operation_id
            ):
                raise BrowserProfileError("browser_profile_promotion_stale")
            await session.execute(
                text(
                    "UPDATE browser_profile_generations SET disposition='promoted'"
                    f" WHERE {_OWNER} AND generation_id=:generation_id"
                ),
                {**_params(context), "generation_id": generation_id},
            )
            await self._replace_head(session, context, head, revision, generation_id)
            await session.execute(
                text(
                    "UPDATE browser_runs SET capture_status='promoted',"
                    "state_revision=state_revision+1,"
                    f" updated_at=clock_timestamp() WHERE {_OWNER} AND run_id=:run_id"
                ),
                _params(context),
            )
        return BrowserProfileHead(revision, generation)

    async def _replace_head(
        self,
        session: AsyncSession,
        context: BrowserProfileContext,
        head: Row,
        revision: int,
        generation_id: str | None,
    ) -> None:
        previous = head["active_generation_id"]
        if previous is not None:
            old = await self._get_generation(session, context, cast(str, previous))
            if (
                old["disposition"] != "promoted"
                or old["profile_revision"] != head["profile_revision"]
            ):
                raise BrowserProfileError("browser_profile_head_invalid")
        await session.execute(
            text(
                "UPDATE browser_profiles SET profile_revision=:revision,"
                "active_generation_id=:generation_id,"
                f" updated_at=clock_timestamp() WHERE {_OWNER}"
            ),
            {**_params(context), "revision": revision, "generation_id": generation_id},
        )
        if previous is not None:
            await session.execute(
                text(
                    "UPDATE browser_profile_generations SET disposition='retired'"
                    f" WHERE {_OWNER} AND generation_id=:generation_id"
                ),
                {**_params(context), "generation_id": previous},
            )

    async def invalidate(
        self,
        context: BrowserProfileContext,
        *,
        expected_profile_revision: int,
    ) -> BrowserProfileHead:
        async with self._locked(context) as (session, _run, head, _lease):
            revision = self._head_revision(head, expected_profile_revision)
            await self._ensure_head(session, context)
            await self._replace_head(
                session, context, head or {"active_generation_id": None}, revision, None
            )
        return BrowserProfileHead(revision, None)

    async def mark_orphan(self, context: BrowserProfileContext, generation_id: str) -> None:
        async with self._locked(context, cleanup=True) as (session, _run, head, _lease):
            row = await self._get_generation(session, context, generation_id)
            if row["disposition"] != "validated" or (
                head is not None and head["active_generation_id"] == generation_id
            ):
                raise BrowserProfileError("browser_profile_orphan_invalid")
            await session.execute(
                text(
                    "UPDATE browser_profile_generations SET disposition='orphan'"
                    f" WHERE {_OWNER} AND generation_id=:generation_id"
                ),
                {**_params(context), "generation_id": generation_id},
            )

    async def _cleanup_candidate(
        self,
        session: AsyncSession,
        context: BrowserProfileContext,
        generation_id: str,
        head: Row | None,
        lease: Row | None,
    ) -> Row:
        row = await self._get_cleanup_row(session, context, generation_id)
        if row["disposition"] not in {"retired", "orphan"} or (
            head is not None and head["active_generation_id"] == generation_id
        ):
            raise BrowserProfileError("browser_profile_cleanup_referenced")
        now = cast(datetime, (await session.execute(text("SELECT clock_timestamp()"))).scalar_one())
        if not retention_ready(
            disposition=row["disposition"],
            created_at=cast(datetime, row["created_at"]),
            disposition_changed_at=cast(datetime | None, row["disposition_changed_at"]),
            head_updated_at=(cast(datetime, head["updated_at"]) if head is not None else None),
            now=now, retention_seconds=self._retention_seconds,
        ):
            raise BrowserProfileError("browser_profile_retention_pending")
        runs = (
            await session.execute(
                text(
                    f"SELECT run_id FROM browser_runs WHERE {_OWNER}"
                    " AND (status IN ('running','waiting_user') OR cleanup IN"
                    " ('pending','quarantined','failed'))"
                    " AND (profile_generation_id=:generation_id OR"
                    " lease_epoch=:generation_epoch)"
                    " ORDER BY run_id FOR UPDATE"
                ),
                {
                    **_params(context),
                    "generation_id": generation_id,
                    "generation_epoch": row["lease_epoch"],
                },
            )
        ).all()
        if runs or (lease is not None and lease["capacity_held"]):
            # Lease stores no generation reference. Conservatively protect every
            # generation while this binding has any outstanding provider resource.
            raise BrowserProfileError("browser_profile_cleanup_referenced")
        return row

    async def discover_cleanup_candidates(
        self, context: BrowserProfileContext, *, limit: int = 100,
        after_generation_id: str | None = None,
    ) -> ProfileCleanupCandidatePage:
        """Return only currently safe candidates; cleanup still needs real proof."""
        if (type(limit) is not int or not 1 <= limit <= 1000
                or (after_generation_id is not None and (
                    type(after_generation_id) is not str
                    or len(after_generation_id) > 96
                    or not after_generation_id
                    or not after_generation_id.replace("_", "").replace("-", "").isalnum()
                ))):
            raise BrowserProfileError("browser_profile_candidate_limit_invalid")
        async with self._locked(context, cleanup=True) as (session, _run, head, lease):
            rows = (await session.execute(text(
                f"SELECT generation_id FROM browser_profile_generations WHERE {_OWNER}"
                " AND disposition IN ('retired','orphan')"
                " AND (:after_id IS NULL OR generation_id>:after_id)"
                " ORDER BY generation_id LIMIT :limit"
            ), {**_params(context), "limit": limit,
                "after_id": after_generation_id})).mappings().all()
            found: list[str] = []
            deferred: list[tuple[str, str]] = []
            for row in rows:
                generation_id = cast(str, row["generation_id"])
                try:
                    await self._cleanup_candidate(
                        session, context, generation_id, head, lease,
                    )
                except BrowserProfileError as error:
                    if error.code in {
                        "browser_profile_cleanup_referenced",
                        "browser_profile_retention_pending",
                    }:
                        deferred.append((generation_id, error.code))
                        continue
                    raise
                receipt = (
                    await session.execute(
                        text(
                            f"SELECT 1 FROM browser_profile_cleanup_receipts WHERE {_OWNER}"
                            " AND generation_id=:generation_id"
                        ),
                        {**_params(context), "generation_id": generation_id},
                    )
                ).scalar_one_or_none()
                if receipt is None:
                    deferred.append((generation_id, "browser_profile_cleanup_proof_missing"))
                    continue
                found.append(generation_id)
            return ProfileCleanupCandidatePage(
                tuple(found), tuple(deferred),
                cast(str, rows[-1]["generation_id"]) if len(rows) == limit else None,
            )

    async def record_provider_cleanup_fact(
        self,
        context: BrowserProfileContext,
        generation_id: str,
        evidence: bytes,
    ) -> None:
        # Capture and Run membership must be durable before accepting a native
        # termination receipt. The provider check happens outside the PG lock.
        async with self._locked(context, cleanup=True) as (session, run, _head, _lease):
            generation = _generation(await self._get_generation(session, context, generation_id))
            if (
                run["profile_generation_id"] != generation_id
                or run["capture_operation_id"] != generation.fact.capture_operation_id
                or run["provider_key"] != generation.fact.provider_key
            ):
                raise BrowserProfileError("browser_profile_cleanup_run_mismatch")
        if self._cleanup is None:
            raise BrowserProfileError("browser_profile_cleanup_proof_unavailable")
        fact = await self._cleanup.verify_cleanup(context, generation, evidence)
        if (
            not isinstance(fact, BrowserProfileCleanupFact)
            or fact.provider_key != generation.fact.provider_key
            or fact.capture_operation_id != generation.fact.capture_operation_id
            or fact.generation_id != generation.fact.generation_id
            or fact.generation_ref_digest != generation.fact.generation_ref_digest
            or type(fact.proof_digest) is not bytes
            or len(fact.proof_digest) != 32
        ):
            raise BrowserProfileError("browser_profile_cleanup_mismatch")
        params = {
            **_params(context), "generation_id": generation_id,
            "provider_key": fact.provider_key,
            "capture_operation_id": fact.capture_operation_id,
            "generation_ref_digest": fact.generation_ref_digest,
            "proof_digest": fact.proof_digest,
        }
        async with self._locked(context, cleanup=True) as (session, run, _head, _lease):
            current = _generation(await self._get_generation(session, context, generation_id))
            if (
                current != generation
                or run["profile_generation_id"] != generation_id
                or run["capture_operation_id"] != generation.fact.capture_operation_id
                or run["provider_key"] != generation.fact.provider_key
            ):
                raise BrowserProfileError("browser_profile_cleanup_stale")
            await session.execute(
                text(
                    "INSERT INTO browser_profile_cleanup_receipts(tenant_id,ai_user_id,"
                    "session_id,target_system,binding_id,generation_id,provider_key,"
                    "capture_operation_id,generation_ref_digest,proof_digest)"
                    " VALUES(:tenant_id,:ai_user_id,:session_id,:target_system,:binding_id,"
                    ":generation_id,:provider_key,:capture_operation_id,"
                    ":generation_ref_digest,:proof_digest) ON CONFLICT DO NOTHING"
                ), params,
            )
            stored = (
                (await session.execute(
                    text(
                        f"SELECT * FROM browser_profile_cleanup_receipts WHERE {_OWNER}"
                        " AND generation_id=:generation_id FOR UPDATE"
                    ), params,
                )).mappings().one_or_none()
            )
            if (
                stored is None
                or any(stored[key] != params[key] for key in (
                    "provider_key", "capture_operation_id",
                    "generation_ref_digest", "proof_digest",
                ))
            ):
                raise BrowserProfileError("browser_profile_cleanup_receipt_conflict")

    async def cleanup(self, context: BrowserProfileContext, generation_id: str) -> None:
        # A durable, verified release receipt is the provider proof after the
        # live resource token expires. The transaction rechecks all local gates.
        async with self._locked(context, cleanup=True) as (session, _run, head, lease):
            row = await self._cleanup_candidate(session, context, generation_id, head, lease)
            params = {**_params(context), "generation_id": generation_id}
            receipt = (
                (await session.execute(
                    text(
                        f"SELECT * FROM browser_profile_cleanup_receipts WHERE {_OWNER}"
                        " AND generation_id=:generation_id FOR UPDATE"
                    ), params,
                )).mappings().one_or_none()
            )
            if receipt is None:
                raise BrowserProfileError("browser_profile_cleanup_proof_missing")
            if (
                any(receipt[key] != row[key] for key in (
                    "provider_key", "capture_operation_id", "generation_ref_digest",
                ))
                or type(receipt["proof_digest"]) is not bytes
                or len(receipt["proof_digest"]) != 32
            ):
                raise BrowserProfileError("browser_profile_cleanup_receipt_conflict")
            params["proof_digest"] = receipt["proof_digest"]
            confirmed = (
                await session.execute(
                    text(
                        "UPDATE browser_profile_generations SET disposition='cleanup_confirmed',"
                        " cleanup_proof_digest=:proof_digest"
                        f" WHERE {_OWNER} AND generation_id=:generation_id"
                        " AND disposition IN ('retired','orphan')"
                        " RETURNING generation_id"
                    ), params,
                )
            ).scalar_one_or_none()
            if confirmed != generation_id:
                raise BrowserProfileError("browser_profile_cleanup_stale")
            removed = (
                await session.execute(
                    text(
                        f"DELETE FROM browser_profile_payloads WHERE {_OWNER}"
                        " AND generation_id=:generation_id RETURNING generation_id"
                    ), params,
                )
            ).scalar_one_or_none()
            if removed != generation_id:
                raise BrowserProfileError("browser_profile_payload_missing")
            remaining = (
                await session.execute(
                    text(
                        f"SELECT 1 FROM browser_profile_payloads WHERE {_OWNER}"
                        " AND generation_id=:generation_id"
                    ), params,
                )
            ).scalar_one_or_none()
            if remaining is not None:
                raise BrowserProfileError("browser_profile_payload_retained")
