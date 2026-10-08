"""Canonical browser requests and durable Runs in short PostgreSQL transactions.

No provider IO or plaintext input/result is handled here. All entry points require
injected current authority; an exact-owner match or a Run FK is never a grant.
Task/Run lifecycle locks follow Task -> Run, after authority's earlier locks.
Queue claiming follows the same lifecycle order, with nonblocking candidate locks.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Literal, cast
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.browser_skill.models import BrowserOwner
from app.browser_skill.run_contracts import BrowserRunRef, CommitFact, _mint_commit_fact
from app.infra.persistence.task_store.transactional import (
    append_owned_event,
    lock_owned_task,
    transition_owned_task,
)
from app.ports.browser_run_store import (
    MAX_RUN_REVISION,
    BrowserRunAuthorityPort,
    BrowserRunStoreError,
    CanonicalRequest,
    CaptureStatus,
    DispatchFailureCode,
    ProtectedRunEnvelope,
    RunAction,
    RunAdmission,
    RunCleanup,
    RunEffect,
    RunPhase,
    RunSnapshot,
    RunStatus,
    RunVerification,
    checked_run_digest,
    checked_run_id,
)
from app.ports.credential_vault import BrowserAuthorizationError
from app.ports.task_store import TaskEventRecord

_OWNER = "tenant_id=:tenant_id AND ai_user_id=:ai_user_id AND session_id=:session_id"
_TASK = _OWNER + " AND task_id=:task_id"
_RUN = _TASK + " AND run_id=:run_id"
_WRITER_LOCK = 746420212000
_REQUEST_VERSION = "browser.request.v1"
_ACTIVE = {"running", "waiting_user"}
_CLAIM_BATCH = 16
_CLAIM_SKIPPABLE = frozenset({
    "browser_claim_candidate_ineligible", "browser_claim_candidate_busy",
    "browser_run_not_found", "browser_run_stale",
})
_CAPTURE_TERMINAL = {"not_requested", "promoted", "failed", "quarantined"}
_CAPTURE_TRANSITIONS = {
    "not_requested": {"prepared"},
    "prepared": {"sent", "failed", "quarantined"},
    "sent": {"unknown", "validated", "failed", "quarantined"},
    "unknown": {"validated", "failed", "quarantined"},
    "validated": {"promoted", "failed", "quarantined"},
}
_PHASE_TRANSITIONS = {
    "queued": {"acquiring"},
    "acquiring": {"running", "waiting_user"},
    "running": {"verifying", "waiting_user"},
    "verifying": {"waiting_user"},
    # Resuming waiting_user is a separately authorized feature, not replay here.
    "waiting_user": set(),
}
Row = RowMapping


@dataclass(slots=True, repr=False)
class _ClaimScan:
    """Private scheduling position only; every candidate still needs real authority."""

    upper: tuple[datetime, str] | None = None
    after: tuple[datetime, str] | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def _owner(owner: BrowserOwner) -> dict[str, object]:
    checked = BrowserOwner(
        tenant_id=owner.tenant_id,
        user_id=owner.user_id,
        session_id=owner.session_id,
    )
    return {
        "tenant_id": checked.tenant_id,
        "ai_user_id": checked.user_id,
        "session_id": checked.session_id,
    }


def _identity(owner: BrowserOwner, task_id: str, run_id: str | None = None) -> dict[str, object]:
    checked_run_id(task_id)
    values = {**_owner(owner), "task_id": task_id}
    if run_id is not None:
        checked_run_id(run_id)
        values["run_id"] = run_id
    return values


def _ttl(seconds: int) -> timedelta:
    if type(seconds) is not int or not 1 <= seconds <= 300:
        raise BrowserRunStoreError("browser_run_ttl_invalid")
    return timedelta(seconds=seconds)


def _code(code: str | None) -> None:
    if code is not None and (
        not isinstance(code, str) or re.fullmatch(r"[a-z][a-z0-9_]{0,95}", code) is None
    ):
        raise BrowserRunStoreError("browser_run_error_code_invalid")


def _envelope(row: Row, purpose: str) -> ProtectedRunEnvelope:
    return ProtectedRunEnvelope(
        cipher_version=cast(str, row[f"{purpose}_cipher_version"]),
        key_id=cast(str, row[f"{purpose}_key_id"]),
        nonce=cast(bytes, row[f"{purpose}_nonce"]),
        ciphertext=cast(bytes, row[f"{purpose}_ciphertext"]),
    )


def _snapshot(row: Row) -> RunSnapshot:
    admission = RunAdmission(
        owner=BrowserOwner(
            tenant_id=row["tenant_id"],
            user_id=row["ai_user_id"],
            session_id=row["session_id"],
        ),
        task_id=cast(str, row["task_id"]),
        run_id=cast(str, row["run_id"]),
        target_system=cast(str, row["target_system"]),
        binding_id=cast(str, row["binding_id"]),
        binding_revision=cast(int, row["binding_revision"]),
        auth_fingerprint=cast(bytes, row["auth_fingerprint"]),
        auth_expires_at=cast(datetime, row["auth_expires_at"]),
        publication_digest=cast(bytes, row["publication_digest"]),
        input_revision=cast(int, row["input_revision"]),
        input_digest=cast(bytes, row["input_digest"]),
        protected_input=_envelope(row, "input"),
        auth_evidence_version=cast(Literal["verified-session-v1"], row["auth_evidence_version"]),
    )
    return RunSnapshot(
        admission=admission,
        state_revision=cast(int, row["state_revision"]),
        status=cast(RunStatus, row["status"]),
        phase=cast(RunPhase | None, row["phase"]),
        worker_id=cast(str | None, row["worker_id"]),
        worker_epoch=cast(int, row["worker_epoch"]),
        worker_deadline=cast(datetime | None, row["worker_deadline"]),
        provider_key=cast(str | None, row["provider_key"]),
        provider_manifest_digest=cast(bytes | None, row["provider_manifest_digest"]),
        lease_epoch=cast(int | None, row["lease_epoch"]),
        profile_generation_id=cast(str | None, row["profile_generation_id"]),
        capture_operation_id=cast(str | None, row["capture_operation_id"]),
        capture_status=cast(CaptureStatus, row["capture_status"]),
        cancel_requested=cast(bool, row["cancel_requested"]),
        cancel_acknowledged=cast(bool, row["cancel_acknowledged"]),
        effect=cast(RunEffect, row["effect"]),
        verification=cast(RunVerification | None, row["verification"]),
        verification_evidence_digest=cast(bytes | None, row["verification_evidence_digest"]),
        cleanup=cast(RunCleanup, row["cleanup"]),
        error_code=cast(str | None, row["error_code"]),
        dispatch_failure_code=cast(DispatchFailureCode | None, row["dispatch_failure_code"]),
        terminal_revision=cast(int | None, row["terminal_revision"]),
        terminal_event_id=cast(str | None, row["terminal_event_id"]),
        result_digest=cast(bytes | None, row["result_digest"]),
        protected_result=_envelope(row, "result") if row["result_digest"] is not None else None,
    )


class PostgreSQLBrowserRunStore:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        authority: BrowserRunAuthorityPort,
        digest_keys: Mapping[str, bytes],
        active_digest_key_id: str,
        claim_ready: Callable[[RunSnapshot], Awaitable[bool]] | None = None,
        claim_ready_in_session: Callable[
            [AsyncSession, RunSnapshot], Awaitable[bool]
        ] | None = None,
    ) -> None:
        if (
            authority is None
            or active_digest_key_id not in digest_keys
            or not digest_keys
            or any(
                not isinstance(k, str) or not k.strip() or not isinstance(v, bytes) or len(v) != 32
                for k, v in digest_keys.items()
            )
        ):
            raise BrowserRunStoreError("browser_request_key_configuration_invalid")
        self._sessions = session_factory
        self._authority = authority
        self._digest_keys = dict(digest_keys)
        self._active_digest_key_id = active_digest_key_id
        self._claim_ready = claim_ready
        self._claim_ready_session = claim_ready_in_session
        # Retain progress for outstanding owners; discard entries once no due
        # work remains. A fixed cycle upper prevents new arrivals delaying wrap.
        self._claim_scans: dict[tuple[str, str, str], _ClaimScan] = {}

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[AsyncSession]:
        async with self._sessions() as session, session.begin():
            await session.execute(
                text("SELECT pg_advisory_xact_lock_shared(:key)"), {"key": _WRITER_LOCK}
            )
            yield session

    async def _now(self, session: AsyncSession) -> datetime:
        return cast(
            datetime, (await session.execute(text("SELECT clock_timestamp()"))).scalar_one()
        )

    def _digest(
        self,
        owner: BrowserOwner,
        request_id: str,
        semantic_input: bytes,
        key_id: str,
    ) -> bytes:
        key = self._digest_keys.get(key_id)
        if key is None:
            # This is not an input conflict; no parsing/dispatch is permissible.
            raise BrowserRunStoreError("browser_request_digest_key_unavailable")
        identity = json.dumps(
            [
                _REQUEST_VERSION,
                "hmac-sha256-v1",
                key_id,
                owner.tenant_id,
                owner.user_id,
                owner.session_id,
                request_id,
            ],
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("ascii")
        return hmac.new(
            key, len(identity).to_bytes(8, "big") + identity + semantic_input, hashlib.sha256
        ).digest()

    async def _request_row(
        self,
        session: AsyncSession,
        owner: BrowserOwner,
        client_request_id: str,
    ) -> Row | None:
        result = await session.execute(
            text(
                f"SELECT * FROM tasks WHERE {_OWNER} AND client_request_id=:client_request_id"
                " FOR UPDATE",
            ),
            {**_owner(owner), "client_request_id": client_request_id},
        )
        return result.mappings().one_or_none()

    async def _task_run(
        self,
        session: AsyncSession,
        owner: BrowserOwner,
        task_id: str,
    ) -> RunSnapshot | None:
        rows = (
            (
                await session.execute(
                    text(
                        f"SELECT * FROM browser_runs WHERE {_TASK}"
                        " ORDER BY created_at DESC LIMIT 2",
                    ),
                    _identity(owner, task_id),
                )
            )
            .mappings()
            .all()
        )
        if len(rows) > 1:
            raise BrowserRunStoreError("browser_canonical_run_ambiguous")
        return _snapshot(rows[0]) if rows else None

    def _request(
        self,
        owner: BrowserOwner,
        row: Row,
        *,
        parse_winner: bool,
        run: RunSnapshot | None,
    ) -> CanonicalRequest:
        return CanonicalRequest(
            owner=owner,
            task_id=cast(str, row["task_id"]),
            trace_id=cast(str | None, row["trace_id"]),
            client_request_id=cast(str, row["client_request_id"]),
            request_digest_key_id=cast(str, row["request_digest_key_id"]),
            request_digest=cast(bytes, row["request_digest"]),
            processing_owner=cast(str | None, row["processing_owner"]),
            processing_deadline=cast(datetime | None, row["processing_deadline"]),
            parse_winner=parse_winner,
            task_status=cast(str, row["status"]),
            error_code=cast(str | None, row["error_code"]),
            run=run,
        )

    async def get_or_create_request(
        self,
        owner: BrowserOwner,
        client_request_id: str,
        semantic_input: bytes,
        *,
        processing_owner: str,
        ttl_seconds: int = 120,
    ) -> CanonicalRequest:
        checked_run_id(client_request_id)
        checked_run_id(processing_owner)
        ttl = _ttl(ttl_seconds)
        if not isinstance(semantic_input, bytes) or not 1 <= len(semantic_input) <= 1_048_576:
            raise BrowserRunStoreError("browser_request_input_invalid")
        async with self._transaction() as session:
            await self._authority.check_owner(session, owner)
            # Lock by the full canonical key before allocating any Task/Trace IDs.
            lock_identity = json.dumps(
                [owner.tenant_id, owner.user_id, owner.session_id, client_request_id],
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("ascii")
            lock_id = int.from_bytes(hashlib.sha256(lock_identity).digest()[:8], "big", signed=True)
            await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_id})
            row = await self._request_row(session, owner, client_request_id)
            winner = row is None
            if row is None:
                key_id = self._active_digest_key_id
                params = {
                    **_owner(owner),
                    "task_id": uuid4().hex,
                    "trace_id": uuid4().hex,
                    "client_request_id": client_request_id,
                    "request_schema_version": _REQUEST_VERSION,
                    "request_digest": self._digest(
                        owner, client_request_id, semantic_input, key_id
                    ),
                    "request_digest_key_id": key_id,
                    "processing_owner": processing_owner,
                    "processing_deadline": await self._now(session) + ttl,
                }
                row = (
                    (
                        await session.execute(
                            text(
                                "INSERT INTO tasks (task_id,trace_id,tenant_id,ai_user_id,"
                                "session_id,status,"
                                "client_request_id,request_schema_version,request_digest,request_digest_key_id,"
                                "processing_owner,processing_deadline)"
                                " VALUES (:task_id,:trace_id,:tenant_id,"
                                ":ai_user_id,:session_id,'created',:client_request_id,:request_schema_version,"
                                ":request_digest,:request_digest_key_id,:processing_owner,:processing_deadline)"
                                " RETURNING *",
                            ),
                            params,
                        )
                    )
                    .mappings()
                    .one()
                )
            else:
                digest = self._digest(
                    owner,
                    client_request_id,
                    semantic_input,
                    cast(str, row["request_digest_key_id"]),
                )
                if row["request_schema_version"] != _REQUEST_VERSION or not hmac.compare_digest(
                    digest,
                    cast(bytes, row["request_digest"]),
                ):
                    raise BrowserRunStoreError("request_key_conflict")
            run = await self._task_run(session, owner, cast(str, row["task_id"]))
            if run is not None:
                await self._authority.check_run(session, run, "read")
            elif (
                not winner
                and row["status"] == "created"
                and (
                    row["processing_deadline"] is None
                    or cast(datetime, row["processing_deadline"]) <= await self._now(session)
                )
            ):
                # Never steal a pre-Run parsing claim, even if its worker vanished.
                row = (
                    (
                        await session.execute(
                            text(
                                "UPDATE tasks SET status='failed',"
                                "error_code='browser_request_abandoned',"
                                "processing_owner=NULL,processing_deadline=NULL"
                                f" WHERE {_TASK} AND status='created' RETURNING *",
                            ),
                            _identity(owner, cast(str, row["task_id"])),
                        )
                    )
                    .mappings()
                    .one()
                )
            result = self._request(owner, row, parse_winner=winner, run=run)
        return result

    async def _lock_request(self, session: AsyncSession, request: CanonicalRequest) -> Row:
        if not request.parse_winner or request.processing_owner is None:
            raise BrowserRunStoreError("browser_request_not_processing_owner")
        row = await self._request_row(session, request.owner, request.client_request_id)
        if (
            row is None
            or row["task_id"] != request.task_id
            or row["status"] != "created"
            or row["processing_owner"] != request.processing_owner
            or row["processing_deadline"] != request.processing_deadline
            or row["request_schema_version"] != _REQUEST_VERSION
            or row["request_digest_key_id"] != request.request_digest_key_id
            or row["request_digest"] != request.request_digest
            or row["processing_deadline"] is None
            or cast(datetime, row["processing_deadline"]) <= await self._now(session)
        ):
            raise BrowserRunStoreError("browser_request_claim_stale")
        return row

    async def reject_request(self, request: CanonicalRequest, error_code: str) -> None:
        _code(error_code)
        if not error_code:
            raise BrowserRunStoreError("browser_run_error_code_invalid")
        async with self._transaction() as session:
            await self._authority.check_owner(session, request.owner)
            await self._lock_request(session, request)
            await session.execute(
                text(
                    "UPDATE tasks SET status='failed',error_code=:error_code,processing_owner=NULL,"
                    f"processing_deadline=NULL WHERE {_TASK} AND status='created'",
                ),
                {**_identity(request.owner, request.task_id), "error_code": error_code},
            )

    async def accept(self, request: CanonicalRequest, admission: RunAdmission) -> CommitFact:
        if request.owner != admission.owner or request.task_id != admission.task_id:
            raise BrowserRunStoreError("browser_request_owner_mismatch")
        # Revalidate immutable scalar invariants even for an unchecked object construction.
        admission.__post_init__()
        async with self._transaction() as session:
            await self._authority.check_owner(session, request.owner)
            await self._lock_request(session, request)
            await self._authority.check_admission(session, request, admission)
            if admission.auth_expires_at <= await self._now(session):
                raise BrowserRunStoreError("browser_run_authorization_expired")
            if await self._task_run(session, request.owner, request.task_id) is not None:
                raise BrowserRunStoreError("browser_request_already_accepted")
            parameters = {
                **_identity(admission.owner, admission.task_id, admission.run_id),
                "target_system": admission.target_system,
                "binding_id": admission.binding_id,
                "binding_revision": admission.binding_revision,
                "auth_evidence_version": admission.auth_evidence_version,
                "auth_fingerprint": admission.auth_fingerprint,
                "auth_expires_at": admission.auth_expires_at,
                "publication_digest": admission.publication_digest,
                "input_revision": admission.input_revision,
                "input_digest": admission.input_digest,
                "input_cipher_version": admission.protected_input.cipher_version,
                "input_key_id": admission.protected_input.key_id,
                "input_nonce": admission.protected_input.nonce,
                "input_ciphertext": admission.protected_input.ciphertext,
            }
            columns = ",".join(parameters)
            placeholders = ",".join(f":{key}" for key in parameters)
            await session.execute(
                text(
                    f"INSERT INTO browser_runs ({columns}) VALUES ({placeholders})",
                ),
                parameters,
            )
            updated = await transition_owned_task(
                session,
                tenant_id=admission.owner.tenant_id,
                ai_user_id=admission.owner.user_id,
                session_id=admission.owner.session_id,
                task_id=admission.task_id,
                expected_statuses=("created",),
                status="running",
            )
            if updated is None:
                raise BrowserRunStoreError("browser_request_claim_stale")
            await session.execute(
                text(
                    "UPDATE tasks SET processing_owner=NULL,processing_deadline=NULL"
                    f" WHERE {_TASK}",
                ),
                _identity(admission.owner, admission.task_id),
            )
        # Successful __aexit__ above is the real database COMMIT boundary.
        return _mint_commit_fact(
            BrowserRunRef(
                owner=admission.owner,
                task_id=admission.task_id,
                run_id=admission.run_id,
                state_revision=0,
            )
        )

    async def _read_run(
        self,
        session: AsyncSession,
        owner: BrowserOwner,
        task_id: str,
        run_id: str,
        *,
        lock: bool = False,
    ) -> RunSnapshot:
        row = (
            (
                await session.execute(
                    text(
                        f"SELECT * FROM browser_runs WHERE {_RUN}"
                        + (" FOR UPDATE" if lock else ""),
                    ),
                    _identity(owner, task_id, run_id),
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise BrowserRunStoreError("browser_run_not_found")
        return _snapshot(row)

    async def get(self, owner: BrowserOwner, task_id: str, run_id: str) -> RunSnapshot:
        async with self._transaction() as session:
            run = await self._read_run(session, owner, task_id, run_id)
            await self._authority.check_run(session, run, "read")
        return run

    async def get_for_cleanup(
        self, owner: BrowserOwner, task_id: str, run_id: str,
    ) -> RunSnapshot:
        """Recover exact historical identity under independent service authority.

        No business-session check or provider/outcome proof is substituted here.
        Provider IO is the trusted reconciler's responsibility after this returns.
        """
        async with self._transaction() as session:
            before = await self._read_run(session, owner, task_id, run_id)
            # This callback first authorizes the recovery service, then takes any
            # credential -> lease -> session locks. Never repeat it after Task.
            await self._authority.before_run_lock(session, before, "cleanup")
            task = await lock_owned_task(
                session,
                tenant_id=owner.tenant_id,
                ai_user_id=owner.user_id,
                session_id=owner.session_id,
                task_id=task_id,
            )
            if task is None:
                raise BrowserRunStoreError("browser_run_not_found")
            current = await self._read_run(session, owner, task_id, run_id, lock=True)
            if (current.admission != before.admission
                    or current.state_revision != before.state_revision):
                raise BrowserRunStoreError("browser_run_stale")
            await self._authority.authorize_cleanup(session, current)
        return current

    async def claim_next(
        self,
        owner: BrowserOwner,
        *,
        worker_id: str,
        ttl_seconds: int = 60,
    ) -> RunSnapshot | None:
        checked_run_id(worker_id)
        ttl = _ttl(ttl_seconds)
        _owner(owner)
        key = (owner.tenant_id, owner.user_id, owner.session_id)
        scan = self._claim_scans.setdefault(key, _ClaimScan())
        async with scan.lock:
            candidates = await self._claim_candidates(owner, scan)
            for row in candidates:
                try:
                    result = await self._claim_candidate(
                        owner, row["task_id"], row["run_id"], worker_id, ttl,
                    )
                except BrowserRunStoreError as exc:
                    if exc.code not in _CLAIM_SKIPPABLE:
                        raise
                    result = None
                # Only explicit candidate rejection or a committed claim advances
                # the cursor. Infrastructure/integrity failures remain visible.
                scan.after = (row["created_at"], row["run_id"])
                if scan.after == scan.upper:
                    scan.after = scan.upper = None
                if result is not None:
                    return result
            return None

    async def _claim_candidates(self, owner: BrowserOwner, scan: _ClaimScan) -> list[Row]:
        eligible = (
            f"{_OWNER} AND status IN ('running','waiting_user')"
            " AND (worker_deadline IS NULL OR worker_deadline<=clock_timestamp())"
        )
        async with self._transaction() as session:
            if scan.upper is None:
                last = (await session.execute(text(
                    "SELECT created_at,run_id FROM browser_runs WHERE " + eligible
                    + " ORDER BY created_at DESC,run_id DESC LIMIT 1",
                ), _owner(owner))).mappings().one_or_none()
                if last is None:
                    key = (owner.tenant_id, owner.user_id, owner.session_id)
                    self._claim_scans.pop(key, None)
                    return []
                scan.upper = (last["created_at"], last["run_id"])
            params = {
                **_owner(owner), "upper_time": scan.upper[0], "upper_id": scan.upper[1],
                "limit": _CLAIM_BATCH,
            }
            after_clause = ""
            if scan.after is not None:
                params.update(after_time=scan.after[0], after_id=scan.after[1])
                after_clause = " AND (created_at,run_id)>(:after_time,:after_id)"
            rows = list((await session.execute(text(
                "SELECT task_id,run_id,created_at FROM browser_runs WHERE " + eligible
                + " AND (created_at,run_id)<=(:upper_time,:upper_id)" + after_clause
                + " ORDER BY created_at,run_id LIMIT :limit",
            ), params)).mappings().all())
        if not rows:
            # Do not wrap in this pass: a cycle is finite even during continuous
            # arrivals. The next scheduler pass starts a fresh bounded cycle.
            scan.upper = scan.after = None
        return rows

    async def _claim_candidate(
        self, owner: BrowserOwner, task_id: str, run_id: str,
        worker_id: str, ttl: timedelta,
    ) -> RunSnapshot | None:
        async with self._transaction() as session:
            before = await self._read_run(session, owner, task_id, run_id)
            # A local/DB scheduling hint only, bound to this candidate. No row
            # changes on rejection; the finite scan can reach a recovering Run.
            if self._claim_ready_session is not None:
                if not await self._claim_ready_session(session, before):
                    return None
            elif self._claim_ready is not None and not await self._claim_ready(before):
                return None
            # Exact protected admission is checked before acquiring earlier locks.
            # This action uses nonblocking credential -> lease -> session locks.
            await self._authority.before_run_lock(session, before, "claim")
            task = (await session.execute(text(
                f"SELECT status FROM tasks WHERE {_TASK} FOR UPDATE SKIP LOCKED",
            ), _identity(owner, task_id))).scalar_one_or_none()
            if task is None:
                raise BrowserRunStoreError("browser_claim_candidate_busy")
            row = (
                (
                    await session.execute(
                        text(
                            f"SELECT * FROM browser_runs WHERE {_RUN}"
                            " FOR UPDATE SKIP LOCKED",
                        ),
                        _identity(owner, task_id, run_id),
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise BrowserRunStoreError("browser_claim_candidate_busy")
            current = _snapshot(row)
            now = await self._now(session)
            if (
                current.admission != before.admission
                or current.state_revision != before.state_revision
                or current.status not in _ACTIVE or task != current.status
                or (current.worker_deadline is not None and current.worker_deadline > now)
            ):
                raise BrowserRunStoreError("browser_run_stale")
            await self._authority.check_run(session, current, "claim")
            if current.worker_epoch >= MAX_RUN_REVISION:
                raise BrowserRunStoreError("browser_run_worker_epoch_exhausted")
            result = await self._update(
                session,
                current,
                {
                    "worker_id": worker_id,
                    "worker_epoch": current.worker_epoch + 1,
                    "worker_deadline": now + ttl,
                },
            )
        # Recovery observes persisted phase/effect/verification/capture; it is NOT queued again.
        return result

    async def _locked(
        self,
        session: AsyncSession,
        expected: RunSnapshot,
        action: RunAction,
        *,
        worker: bool = True,
        terminal_ok: bool = False,
        cleanup_outcome: RunCleanup | None = None,
        read_candidate: RunSnapshot | None = None,
    ) -> RunSnapshot:
        if action == "cleanup":
            if cleanup_outcome is None:
                raise BrowserRunStoreError("browser_run_cleanup_invalid")
        before = read_candidate
        if before is None:
            before = await self._read_run(
                session, expected.owner, expected.task_id, expected.run_id,
            )
        elif before != expected:
            raise BrowserRunStoreError("browser_run_stale")
        await self._authority.before_run_lock(session, before, action)
        task = await lock_owned_task(
            session,
            tenant_id=expected.owner.tenant_id,
            ai_user_id=expected.owner.user_id,
            session_id=expected.owner.session_id,
            task_id=expected.task_id,
        )
        if task is None:
            raise BrowserRunStoreError("browser_run_not_found")
        current = await self._read_run(
            session,
            expected.owner,
            expected.task_id,
            expected.run_id,
            lock=True,
        )
        if (
            current.state_revision != expected.state_revision
            or current.admission != expected.admission
            or (not terminal_ok and current.status not in _ACTIVE)
        ):
            raise BrowserRunStoreError("browser_run_stale")
        if worker and (
            current.worker_id is None
            or current.worker_id != expected.worker_id
            or current.worker_epoch != expected.worker_epoch
            or current.worker_deadline != expected.worker_deadline
            or current.worker_deadline is None
            or current.worker_deadline <= await self._now(session)
        ):
            raise BrowserRunStoreError("browser_run_worker_stale")
        if action == "cleanup":
            # Historical resource cleanup has its own service authority and must
            # remain possible after the original user's business grant expires.
            assert cleanup_outcome is not None
            await self._authority.check_cleanup(session, current, cleanup_outcome)
        else:
            await self._authority.check_run(session, current, action)
        return current

    async def _update(
        self,
        session: AsyncSession,
        current: RunSnapshot,
        changes: Mapping[str, object],
    ) -> RunSnapshot:
        if current.state_revision >= MAX_RUN_REVISION:
            raise BrowserRunStoreError("browser_run_revision_exhausted")
        # Column names are internal constants at call sites, never wire input.
        parameters = {
            **_identity(current.owner, current.task_id, current.run_id),
            **changes,
            "expected_revision": current.state_revision,
        }
        assignments = ",".join(f"{key}=:{key}" for key in changes)
        row = (
            (
                await session.execute(
                    text(
                        f"UPDATE browser_runs SET {assignments},state_revision=state_revision+1,"
                        f"updated_at=clock_timestamp() WHERE {_RUN}"
                        " AND state_revision=:expected_revision"
                        " RETURNING *",
                    ),
                    parameters,
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise BrowserRunStoreError("browser_run_stale")
        return _snapshot(row)

    async def refresh_worker(
        self, previous: RunSnapshot, *, ttl_seconds: int = 60,
    ) -> RunSnapshot:
        """Keep read/renew in one transaction; no authority is reused across calls."""
        _ttl(ttl_seconds)
        async with self._transaction() as session:
            candidate = await self._read_run(
                session, previous.owner, previous.task_id, previous.run_id,
            )
            if (
                candidate.worker_id != previous.worker_id
                or candidate.worker_epoch != previous.worker_epoch
                or candidate.admission != previous.admission
                or candidate.status not in _ACTIVE
            ):
                raise BrowserRunStoreError("browser_run_checkpoint_stale")
            # Reuse only the candidate read in this transaction, before any locks.
            # _locked still reads the actual row again after locks and checks authority/CAS.
            current = await self._locked(session, candidate, "renew", read_candidate=candidate)
            if current.state_revision >= MAX_RUN_REVISION:
                raise BrowserRunStoreError("browser_run_revision_exhausted")
            row = (
                await session.execute(
                    text(
                        "UPDATE browser_runs SET worker_deadline="
                        "clock_timestamp() + :ttl_seconds * INTERVAL '1 second',"
                        "state_revision=state_revision+1,updated_at=clock_timestamp()"
                        f" WHERE {_RUN} AND state_revision=:expected_revision"
                        " AND worker_id=:expected_worker_id AND worker_epoch=:expected_worker_epoch"
                        " AND worker_deadline=:expected_worker_deadline"
                        " AND status IN ('running','waiting_user')"
                        " AND worker_deadline > clock_timestamp()"
                        " AND auth_expires_at > clock_timestamp() RETURNING *"
                    ),
                    {**_identity(current.owner, current.task_id, current.run_id),
                     "ttl_seconds": ttl_seconds, "expected_revision": current.state_revision,
                     "expected_worker_id": current.worker_id,
                     "expected_worker_epoch": current.worker_epoch,
                     "expected_worker_deadline": current.worker_deadline},
                )
            ).mappings().one_or_none()
            if row is None:
                # The row remains locked; classify expiry without hiding a CAS
                # failure or returning an unrenewed snapshot as success.
                now = await self._now(session)
                if current.worker_deadline is None or current.worker_deadline <= now:
                    raise BrowserRunStoreError("browser_run_worker_stale")
                if current.admission.auth_expires_at <= now:
                    raise BrowserAuthorizationError("browser_auth_expired")
                raise BrowserRunStoreError("browser_run_stale")
            result = _snapshot(row)
        return result

    async def renew(self, claim: RunSnapshot, *, ttl_seconds: int = 60) -> RunSnapshot:
        ttl = _ttl(ttl_seconds)
        async with self._transaction() as session:
            current = await self._locked(session, claim, "renew")
            result = await self._update(
                session,
                current,
                {
                    "worker_deadline": await self._now(session) + ttl,
                },
            )
        return result

    async def advance(
        self,
        claim: RunSnapshot,
        *,
        phase: RunPhase,
        effect: RunEffect,
    ) -> RunSnapshot:
        action: RunAction = (
            "effect_settle" if phase == claim.phase and effect == "unknown" else "advance"
        )
        async with self._transaction() as session:
            current = await self._locked(session, claim, action)
            if (
                current.verification == "verified"
                # Decide again against the locked row, not a caller's snapshot.
                or (action == "effect_settle" and (phase != current.phase or effect != "unknown"))
                # Cancellation stops new work, but late dispatch observations must
                # still be persisted at the same phase before cancellation ack.
                or (current.cancel_requested and phase != current.phase)
                or (
                    phase != current.phase
                    and phase
                    not in _PHASE_TRANSITIONS.get(
                        current.phase or "",
                        set(),
                    )
                )
                or effect not in {"not_sent", "acknowledged", "unknown"}
                or (current.effect != "not_sent" and effect == "not_sent")
                # Unknown is conservative aggregate history, never erased by a
                # later acknowledgement of only part of the operation sequence.
                or (current.effect == "unknown" and effect != "unknown")
            ):
                raise BrowserRunStoreError("browser_run_transition_invalid")
            status: RunStatus = "waiting_user" if phase == "waiting_user" else "running"
            result = await self._update(
                session, current, {"phase": phase, "status": status, "effect": effect}
            )
            if status != current.status:
                await self._transition_task(session, result, status, None)
        return result

    async def attach_lease(
        self,
        claim: RunSnapshot,
        *,
        provider_key: str,
        provider_manifest_digest: bytes,
        lease_epoch: int,
    ) -> RunSnapshot:
        checked_run_id(provider_key)
        checked_run_digest(provider_manifest_digest)
        if type(lease_epoch) is not int or not 1 <= lease_epoch <= MAX_RUN_REVISION:
            raise BrowserRunStoreError("browser_run_lease_invalid")
        async with self._transaction() as session:
            current = await self._locked(session, claim, "advance")
            if current.lease_epoch is not None or current.phase != "acquiring":
                raise BrowserRunStoreError("browser_run_lease_already_bound")
            # Authority checks the actual lease against these candidate values before persistence.
            candidate = replace(
                current,
                provider_key=provider_key,
                provider_manifest_digest=provider_manifest_digest,
                lease_epoch=lease_epoch,
            )
            await self._authority.check_run(session, candidate, "advance")
            result = await self._update(
                session,
                current,
                {
                    "provider_key": provider_key,
                    "provider_manifest_digest": provider_manifest_digest,
                    "lease_epoch": lease_epoch,
                },
            )
        return result

    async def persist_verified(
        self,
        claim: RunSnapshot,
        *,
        result: ProtectedRunEnvelope,
        result_digest: bytes,
        evidence_digest: bytes,
    ) -> RunSnapshot:
        result.__post_init__()
        checked_run_digest(result_digest)
        checked_run_digest(evidence_digest)
        if result.cipher_version != "aes256gcm-browser-result-v1":
            raise BrowserRunStoreError("browser_run_result_purpose_invalid")
        async with self._transaction() as session:
            current = await self._locked(session, claim, "advance")
            if current.verification == "verified" or current.phase not in {"running", "verifying"}:
                raise BrowserRunStoreError("browser_run_verification_stale")
            candidate = replace(
                current,
                verification="verified",
                verification_evidence_digest=evidence_digest,
                phase="verifying",
                result_digest=result_digest,
                protected_result=result,
            )
            # The trusted verifier must recognize this exact output, not merely
            # accept a digest-shaped byte string or a caller's verified flag.
            await self._authority.check_run(session, candidate, "verify")
            persisted = await self._update(
                session,
                current,
                {
                    "verification": "verified",
                    "verification_evidence_digest": evidence_digest,
                    "phase": "verifying",
                    "result_digest": result_digest,
                    "result_cipher_version": result.cipher_version,
                    "result_key_id": result.key_id,
                    "result_nonce": result.nonce,
                    "result_ciphertext": result.ciphertext,
                },
            )
        return persisted

    async def prepare_capture(self, claim: RunSnapshot) -> RunSnapshot:
        async with self._transaction() as session:
            # Durable operation identity alone never authorizes provider IO. A
            # later sent transition still requires the strict live-lease check.
            current = await self._locked(session, claim, "capture_prepare")
            if (
                current.capture_status != "not_requested"
                or current.lease_epoch is None
                or current.verification != "verified"
            ):
                raise BrowserRunStoreError("browser_run_capture_not_ready")
            result = await self._update(
                session,
                current,
                {
                    "capture_operation_id": uuid4().hex,
                    "capture_status": "prepared",
                },
            )
        return result

    async def transition_capture(
        self,
        claim: RunSnapshot,
        *,
        status: CaptureStatus,
        profile_generation_id: str | None = None,
    ) -> RunSnapshot:
        # Successful capture transitions belong exclusively to the Profile store's
        # atomic generation/proof/head transaction. A reference is not that proof.
        if status in {"validated", "promoted"} or profile_generation_id is not None:
            raise BrowserRunStoreError("browser_run_profile_transaction_required")
        # Settling capture debt can outlive a held live lease. This action never
        # authorizes a send, validation or promotion, and retains worker fencing.
        action: RunAction = "capture_settle" if status in {"failed", "quarantined"} else "capture"
        async with self._transaction() as session:
            current = await self._locked(session, claim, action)
            if status not in _CAPTURE_TRANSITIONS.get(current.capture_status, set()):
                raise BrowserRunStoreError("browser_run_capture_transition_invalid")
            result = await self._update(session, current, {"capture_status": status})
        return result

    async def request_cancel(
        self,
        owner: BrowserOwner,
        task_id: str,
        run_id: str,
    ) -> RunSnapshot:
        async with self._transaction() as session:
            initial = await self._read_run(session, owner, task_id, run_id)
            current = await self._locked(session, initial, "cancel", worker=False, terminal_ok=True)
            if current.status not in _ACTIVE or current.cancel_requested:
                return current
            result = await self._update(session, current, {"cancel_requested": True})
        return result

    async def acknowledge_cancel(self, claim: RunSnapshot) -> RunSnapshot:
        async with self._transaction() as session:
            current = await self._locked(session, claim, "cancel")
            if not current.cancel_requested:
                raise BrowserRunStoreError("browser_run_cancel_not_requested")
            # Authority verifies real cancellation barrier/termination, never an HTTP 202.
            candidate = replace(current, cancel_acknowledged=True)
            await self._authority.check_run(session, candidate, "cancel")
            result = await self._update(session, current, {"cancel_acknowledged": True})
        return result

    async def _transition_task(
        self,
        session: AsyncSession,
        run: RunSnapshot,
        status: RunStatus,
        error_code: str | None,
    ) -> None:
        updated = await transition_owned_task(
            session,
            tenant_id=run.owner.tenant_id,
            ai_user_id=run.owner.user_id,
            session_id=run.owner.session_id,
            task_id=run.task_id,
            expected_statuses=("running", "waiting_user"),
            status=status,
            error_code=error_code,
        )
        if updated is None:
            raise BrowserRunStoreError("browser_run_task_stale")

    async def finalize(
        self,
        claim: RunSnapshot,
        *,
        status: Literal["completed", "failed", "cancelled"],
        error_code: str | None = None,
        verification: RunVerification | None = None,
        dispatch_failure_code: DispatchFailureCode | None = None,
    ) -> RunSnapshot:
        _code(error_code)
        async with self._transaction() as session:
            current = await self._locked(session, claim, "finalize")
            final_verification = current.verification if verification is None else verification
            if (
                status not in {"completed", "failed", "cancelled"}
                or current.capture_status not in _CAPTURE_TERMINAL
                or (status == "completed") != (final_verification == "verified")
                or (current.verification == "verified" and final_verification != "verified")
                or (
                    status == "completed"
                    and (
                        error_code is not None
                        or current.protected_result is None
                        or current.result_digest is None
                        or current.verification_evidence_digest is None
                    )
                )
                or (status == "failed" and error_code is None)
                or (
                    status == "cancelled"
                    and (error_code != "browser_cancelled" or not current.cancel_acknowledged)
                )
                or (
                    current.effect == "unknown"
                    and status != "completed"
                    and (status != "failed" or error_code != "browser_effect_unknown")
                )
            ):
                raise BrowserRunStoreError("browser_run_terminal_invalid")
            event_id = uuid5(NAMESPACE_URL, f"browser-terminal-v1:{current.run_id}").hex
            await append_owned_event(
                session,
                tenant_id=current.owner.tenant_id,
                ai_user_id=current.owner.user_id,
                session_id=current.owner.session_id,
                task_id=current.task_id,
                event=TaskEventRecord(
                    event_id=event_id,
                    task_id=current.task_id,
                    event_type="browser_run_terminal",
                    timestamp=await self._now(session),
                    payload={
                        "run_id": current.run_id,
                        "status": status,
                        "error_code": error_code,
                        "state_revision": current.state_revision + 1,
                    },
                ),
            )
            result = await self._update(
                session,
                current,
                {
                    "status": status,
                    "phase": None,
                    "verification": final_verification,
                    "error_code": error_code,
                    "dispatch_failure_code": dispatch_failure_code,
                    "terminal_revision": current.state_revision + 1,
                    "terminal_event_id": event_id,
                },
            )
            await self._transition_task(session, result, status, error_code)
        return result

    async def record_cleanup(self, claim: RunSnapshot, *, cleanup: RunCleanup) -> RunSnapshot:
        if cleanup not in {"released", "terminated", "quarantined", "failed"}:
            raise BrowserRunStoreError("browser_run_cleanup_invalid")
        async with self._transaction() as session:
            # This is independent recovery authority, allowed after user auth expiry/revocation.
            current = await self._locked(
                session, claim, "cleanup", worker=False, terminal_ok=True, cleanup_outcome=cleanup,
            )
            if current.cleanup in {"released", "terminated"}:
                if current.cleanup == cleanup:
                    return current
                raise BrowserRunStoreError("browser_run_cleanup_final")
            result = await self._update(session, current, {"cleanup": cleanup})
        return result
