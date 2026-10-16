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
import logging
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

from app.browser_skill.models import (
    ActionCommand,
    BrowserOwner,
    DispatchReceipt,
    LocatorHint,
    PageEvidence,
    ReadSpec,
    ScopeBinding,
    ScopeStamp,
    TeachingPlan,
)
from app.browser_skill.publication_contracts import BrowserPublicationManifest, canonical_json
from app.browser_skill.registry import canonical_digest
from app.browser_skill.repair import (
    FormalFailedStep,
    FormalLocatorProposal,
    FormalRepairObservation,
    RepairFailureReason,
)
from app.browser_skill.run_contracts import BrowserRunRef, CommitFact, _mint_commit_fact
from app.browser_skill.site_rules import SiteStepRule
from app.browser_skill.teaching import TeachingReplayProof
from app.browser_skill.trajectory import (
    BindingRevisions,
    RunTeachingStep,
    TrajectoryEvent,
)
from app.browser_skill.verifier import IndependentVerifier
from app.ports.browser_fill_stop import FillStopReport
from app.browser_skill.write_workflow import (
    BrowserWritePreparation,
    _owner_digest,
    _write_steps_digest,
)
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
_STEP_AUDIT_VERSION = "browser.run_step_audit.v1"
_FAILED_STEP_AUDIT_VERSION = "browser.run_failed_step_audit.v1"
_LOCATOR_PROPOSAL_AUDIT_VERSION = "browser.run_locator_proposal.v1"
_TRAVERSAL_TERMINAL_VERSION = "browser.traversal_terminal.v1"
_WRITE_PREPARED_VERSION = "browser.write_prepared.v1"
_WRITE_CONSUMED_VERSION = "browser.write_consumed.v1"
_WRITE_SEND_VERSION = "browser.write_send_reserved.v1"
_WRITE_BARRIER_VERSION = "browser.write_final_barrier.v1"
_REPLAY_PROOF_VERSION = "browser.teaching_replay_proof.v1"
_TEACHING_PLAN_VERSION = "browser.teaching_plan_record.v1"
_MAX_STEP_AUDITS = 126
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
        self._replay_source_reader: (
            Callable[[AsyncSession, RunSnapshot], Awaitable[None]] | None
        ) = None
        # Retain progress for outstanding owners; discard entries once no due
        # work remains. A fixed cycle upper prevents new arrivals delaying wrap.
        self._claim_scans: dict[tuple[str, str, str], _ClaimScan] = {}

    def install_replay_source_reader(
        self, reader: Callable[[AsyncSession, RunSnapshot], Awaitable[None]],
    ) -> None:
        """One exact installed source's read authority; never an execution fallback."""
        if self._replay_source_reader is not None or not callable(reader):
            raise BrowserRunStoreError("browser_teaching_source_installation_invalid")
        self._replay_source_reader = reader

    async def _check_replay_source(self, session: AsyncSession, source: RunSnapshot) -> None:
        reader = self._replay_source_reader
        if reader is None:
            await self._authority.check_run(session, source, "read")
        else:
            await self._authority.check_owner(session, source.owner)
            await reader(session, source)

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

    async def lookup_request(
        self, owner: BrowserOwner, client_request_id: str, semantic_input: bytes,
        *, settle_expired: bool = False,
    ) -> CanonicalRequest | None:
        checked_run_id(client_request_id)
        if type(semantic_input) is not bytes or not 1 <= len(semantic_input) <= 1_048_576:
            raise BrowserRunStoreError("browser_request_input_invalid")
        async with self._transaction() as session:
            await self._authority.check_owner(session, owner)
            if settle_expired:
                row = await self._request_row(session, owner, client_request_id)
            else:
                result = await session.execute(text(
                    f"SELECT * FROM tasks WHERE {_OWNER} AND client_request_id=:client_request_id"
                    " FOR SHARE",
                ), {**_owner(owner), "client_request_id": client_request_id})
                row = result.mappings().one_or_none()
            if row is None:
                return None
            digest = self._digest(
                owner, client_request_id, semantic_input, cast(str, row["request_digest_key_id"]),
            )
            if (row["request_schema_version"] != _REQUEST_VERSION
                    or not hmac.compare_digest(digest, cast(bytes, row["request_digest"]))):
                raise BrowserRunStoreError("request_key_conflict")
            run = await self._task_run(session, owner, cast(str, row["task_id"]))
            if run is not None:
                await self._authority.check_run(session, run, "read")
            elif (settle_expired and row["status"] == "created" and (
                row["processing_deadline"] is None
                or cast(datetime, row["processing_deadline"]) <= await self._now(session)
            )):
                # Close only this locked, MAC-matched original claim. No new
                # processing owner, parsing or Run allocation is permitted.
                row = (await session.execute(text(
                    "UPDATE tasks SET status='failed',error_code='browser_request_abandoned',"
                    "processing_owner=NULL,processing_deadline=NULL"
                    f" WHERE {_TASK} AND status='created' RETURNING *",
                ), _identity(owner, cast(str, row["task_id"])))).mappings().one()
            return self._request(owner, row, parse_winner=False, run=run)

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
        return await self._accept(request, admission, teaching_plan=None)

    async def accept_teaching(
        self, request: CanonicalRequest, admission: RunAdmission,
        plan: TeachingPlan,
    ) -> CommitFact:
        """Admit a prepared candidate and its signed plan in one transaction."""
        if not isinstance(plan, TeachingPlan):
            raise BrowserRunStoreError("browser_teaching_plan_invalid")
        return await self._accept(request, admission, teaching_plan=plan)

    async def _accept(
        self, request: CanonicalRequest, admission: RunAdmission,
        *, teaching_plan: TeachingPlan | None,
    ) -> CommitFact:
        if request.owner != admission.owner or request.task_id != admission.task_id:
            raise BrowserRunStoreError("browser_request_owner_mismatch")
        # Revalidate immutable scalar invariants even for an unchecked object construction.
        admission.__post_init__()
        async with self._transaction() as session:
            await self._authority.check_owner(session, request.owner)
            await self._lock_request(session, request)
            if teaching_plan is None:
                await self._authority.check_admission(session, request, admission)
            else:
                await self._authority.check_teaching_admission(
                    session, request, admission, teaching_plan,
                )
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
            if teaching_plan is not None:
                run = await self._read_run(
                    session, admission.owner, admission.task_id, admission.run_id,
                )
                manifest = await self._frozen_publication(session, run)
                state = (await session.execute(text(
                    "SELECT state FROM browser_publications WHERE tenant_id=:tenant"
                    " AND publication_digest=:digest"
                ), {"tenant": admission.owner.tenant_id,
                    "digest": admission.publication_digest})).scalar_one_or_none()
                if state != "prepared":
                    raise BrowserRunStoreError("browser_teaching_candidate_not_prepared")
                plan_data: dict[str, object] = {
                    "plan": teaching_plan.model_dump(mode="json"),
                }
                await self._append_write_event(
                    session, run, kind=_TEACHING_PLAN_VERSION,
                    event_id=self._write_event_id("teaching-plan", run.run_id),
                    data=plan_data,
                    revision_delta=0,
                )
                if await self._teaching_plan_in_session(
                    session, run, manifest,
                ) != teaching_plan:
                    raise BrowserRunStoreError("browser_teaching_plan_mismatch")
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

    async def _frozen_publication(
        self, session: AsyncSession, run: RunSnapshot,
    ) -> BrowserPublicationManifest:
        row = (await session.execute(text(
            "SELECT manifest FROM browser_publications WHERE tenant_id=:tenant"
            " AND publication_digest=:digest"
        ), {"tenant": run.owner.tenant_id,
            "digest": run.admission.publication_digest})).mappings().one_or_none()
        if row is None:
            raise BrowserRunStoreError("browser_step_publication_unavailable")
        try:
            manifest = BrowserPublicationManifest.model_validate_json(
                canonical_json(row["manifest"]),
            )
        except ValueError:
            logging.getLogger(__name__).error(
                'browser_run_store_diagnostic {"stage":"frozen_publication_validation",'
                '"exception_type":"BrowserRunStoreError",'
                '"trusted_error_code":"browser_step_publication_invalid"}'
            )
            raise BrowserRunStoreError("browser_step_publication_invalid") from None
        if manifest.digest != run.admission.publication_digest.hex():
            logging.getLogger(__name__).error(
                'browser_run_store_diagnostic {"stage":"frozen_publication_digest",'
                '"exception_type":"BrowserRunStoreError",'
                '"trusted_error_code":"browser_step_publication_invalid"}'
            )
            raise BrowserRunStoreError("browser_step_publication_invalid")
        return manifest

    async def _audited_steps(
        self, session: AsyncSession, run: RunSnapshot,
    ) -> tuple[RunTeachingStep, ...]:
        rows = (await session.execute(text(
            "SELECT payload FROM task_events WHERE task_id=:task"
            " AND event_type='browser_run_step' AND payload->>'run_id'=:run"
            " ORDER BY (payload->>'sequence')::integer"
        ), {"task": run.task_id, "run": run.run_id})).mappings().all()
        if len(rows) > _MAX_STEP_AUDITS:
            raise BrowserRunStoreError("browser_step_audit_overflow")
        result: list[RunTeachingStep] = []
        for sequence, row in enumerate(rows, start=1):
            payload = row["payload"]
            if not isinstance(payload, dict):
                raise BrowserRunStoreError("browser_step_audit_invalid")
            unsigned = {key: value for key, value in payload.items() if key != "mac"}
            key_id = payload.get("key_id")
            mac = payload.get("mac")
            if (
                type(key_id) is not str or type(mac) is not str
                or payload.get("schema_version") != _STEP_AUDIT_VERSION
                or payload.get("run_id") != run.run_id
                or payload.get("sequence") != sequence
                or type(payload.get("run_revision")) is not int
                or payload["run_revision"] < 1
                or payload["run_revision"] > run.state_revision
                or not hmac.compare_digest(mac, self._step_mac(unsigned, key_id))
            ):
                raise BrowserRunStoreError("browser_step_audit_invalid")
            try:
                step = RunTeachingStep.model_validate_json(canonical_json(payload["step"]))
            except (KeyError, ValueError):
                raise BrowserRunStoreError("browser_step_audit_invalid") from None
            if step.run_id != run.run_id or step.event.sequence != sequence:
                raise BrowserRunStoreError("browser_step_audit_invalid")
            result.append(step)
        return tuple(result)

    @staticmethod
    def _check_step_route(
        manifest: BrowserPublicationManifest,
        step_ids: tuple[str, ...], *, complete: bool,
        terminal_after: int | None = None,
    ) -> None:
        """Match the frozen sequence, allowing only its registered page advance to repeat."""
        exploration = manifest.site.exploration
        if exploration is not None:
            catalog = {rule.step.step_id for rule in manifest.site.primitive_rules}
            if (
                len(step_ids) > exploration.max_steps
                or (complete and not step_ids)
                or any(step_id not in catalog for step_id in step_ids)
            ):
                raise BrowserRunStoreError("browser_step_audit_sequence_conflict")
            return
        expected = manifest.skill.steps
        traversal = manifest.site.traversal
        if terminal_after is not None and (
            traversal is None or type(terminal_after) is not int
            or not 0 <= terminal_after <= len(step_ids)
        ):
            raise BrowserRunStoreError("browser_step_audit_sequence_conflict")
        advance_id = traversal.advance_step_id if traversal is not None else None
        cursor = 0
        advances = 0
        used_terminal = False
        for index, step_id in enumerate(step_ids):
            if cursor >= len(expected):
                raise BrowserRunStoreError("browser_step_audit_sequence_conflict")
            if step_id == expected[cursor].step_id:
                if step_id == advance_id:
                    if terminal_after is not None and index >= terminal_after:
                        raise BrowserRunStoreError("browser_step_audit_sequence_conflict")
                    advances += 1
                    if traversal is None or advances >= traversal.max_pages:
                        raise BrowserRunStoreError("browser_step_audit_sequence_conflict")
                else:
                    cursor += 1
            elif (
                expected[cursor].step_id == advance_id
                and terminal_after == index and cursor + 1 < len(expected)
                and step_id == expected[cursor + 1].step_id
            ):
                used_terminal = True
                cursor += 2
            else:
                raise BrowserRunStoreError("browser_step_audit_sequence_conflict")
        if complete and not (
            cursor == len(expected)
            or (cursor == len(expected) - 1 and terminal_after == len(step_ids)
                and expected[cursor].step_id == advance_id)
        ):
            raise BrowserRunStoreError("browser_step_audit_incomplete")
        if terminal_after is not None and not used_terminal and not (
            cursor == len(expected) - 1 and terminal_after == len(step_ids)
            and expected[cursor].step_id == advance_id
        ):
            raise BrowserRunStoreError("browser_step_audit_sequence_conflict")

    @staticmethod
    def _primitive_id(manifest: BrowserPublicationManifest, step_id: str) -> str:
        for binding in manifest.site.step_primitives:
            if binding.step_id == step_id:
                return binding.primitive_id
        return step_id

    @staticmethod
    def _primitive_rule(
        manifest: BrowserPublicationManifest, primitive_id: str,
    ) -> SiteStepRule | None:
        return next((item for item in manifest.site.primitive_rules
                     if item.step.step_id == primitive_id), None)

    def _step_mac(self, data: dict[str, object], key_id: str) -> str:
        key = self._digest_keys.get(key_id)
        if key is None:
            raise BrowserRunStoreError("browser_step_audit_key_unavailable")
        encoded = json.dumps(data, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True, allow_nan=False).encode("ascii")
        return hmac.new(key, b"browser-run-step-audit-v1\x00" + encoded,
                        hashlib.sha256).hexdigest()

    def _proposal_mac(self, data: dict[str, object], key_id: str) -> str:
        key = self._digest_keys.get(key_id)
        if key is None:
            raise BrowserRunStoreError("browser_repair_proposal_key_unavailable")
        encoded = json.dumps(data, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True, allow_nan=False).encode("ascii")
        return hmac.new(key, b"browser-run-locator-proposal-v1\x00" + encoded,
                        hashlib.sha256).hexdigest()

    def _traversal_mac(self, data: dict[str, object], key_id: str) -> str:
        key = self._digest_keys.get(key_id)
        if key is None:
            raise BrowserRunStoreError("browser_traversal_audit_key_unavailable")
        encoded = json.dumps(data, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True, allow_nan=False).encode("ascii")
        return hmac.new(key, b"browser-traversal-terminal-v1\x00" + encoded,
                        hashlib.sha256).hexdigest()

    async def _traversal_terminal_in_session(
        self, session: AsyncSession, run: RunSnapshot,
        manifest: BrowserPublicationManifest,
    ) -> int | None:
        event_id = self._write_event_id("traversal-terminal", run.run_id)
        row = (await session.execute(text(
            "SELECT event_type,payload FROM task_events WHERE task_id=:task"
            " AND event_id=:event"
        ), {"task": run.task_id, "event": event_id})).mappings().one_or_none()
        if row is None:
            return None
        payload = row["payload"]
        if row["event_type"] != _TRAVERSAL_TERMINAL_VERSION or not isinstance(payload, dict):
            raise BrowserRunStoreError("browser_traversal_audit_invalid")
        unsigned = {key: value for key, value in payload.items() if key != "mac"}
        key_id, mac = payload.get("key_id"), payload.get("mac")
        if (
            payload.get("schema_version") != _TRAVERSAL_TERMINAL_VERSION
            or type(key_id) is not str or type(mac) is not str
            or not hmac.compare_digest(mac, self._traversal_mac(unsigned, key_id))
            or payload.get("run_id") != run.run_id
            or payload.get("publication_digest") != manifest.digest
            or payload.get("worker_epoch") != run.worker_epoch
            or payload.get("lease_epoch") != run.lease_epoch
            or type(payload.get("sequence")) is not int
            or not 0 <= payload["sequence"] <= _MAX_STEP_AUDITS
            or type(payload.get("page_count")) is not int
            or manifest.site.traversal is None
            or not 1 <= payload["page_count"] <= manifest.site.traversal.max_pages
            or type(payload.get("page_chain_digest")) is not str
            or len(payload["page_chain_digest"]) != 64
        ):
            raise BrowserRunStoreError("browser_traversal_audit_invalid")
        return int(payload["sequence"])

    async def record_traversal_terminal(
        self, claim: RunSnapshot, *, pages: tuple[PageEvidence, ...],
        sequence: int,
    ) -> RunSnapshot:
        if (type(sequence) is not int or not 0 <= sequence <= _MAX_STEP_AUDITS
                or type(pages) is not tuple):
            raise BrowserRunStoreError("browser_traversal_audit_invalid")
        async with self._transaction() as session:
            current = await self._locked(session, claim, "advance")
            if (current.phase != "running" or current.cancel_requested
                    or current.verification is not None or current.effect == "unknown"
                    or current.lease_epoch is None):
                raise BrowserRunStoreError("browser_traversal_audit_stale")
            manifest = await self._frozen_publication(session, current)
            traversal = manifest.site.traversal
            digest = (
                IndependentVerifier._page_chain(traversal, pages)
                if traversal is not None else None
            )
            if digest is None or not pages[-1].terminal_seen:
                raise BrowserRunStoreError("browser_traversal_audit_incomplete")
            previous = await self._audited_steps(session, current)
            ids = tuple(item.event.step_id for item in previous)
            if len(ids) != sequence or traversal is None:
                raise BrowserRunStoreError("browser_traversal_audit_sequence_conflict")
            expected = tuple(item.step_id for item in manifest.skill.steps)
            try:
                position = expected.index(traversal.advance_step_id)
            except ValueError:
                raise BrowserRunStoreError("browser_traversal_audit_sequence_conflict") from None
            if (ids[:position] != expected[:position]
                    or any(item != traversal.advance_step_id for item in ids[position:])
                    or len(ids) - position >= traversal.max_pages):
                raise BrowserRunStoreError("browser_traversal_audit_sequence_conflict")
            if await self._traversal_terminal_in_session(session, current, manifest) is not None:
                raise BrowserRunStoreError("browser_traversal_audit_reused")
            payload: dict[str, object] = {
                "schema_version": _TRAVERSAL_TERMINAL_VERSION,
                "run_id": current.run_id,
                "publication_digest": manifest.digest,
                "worker_epoch": current.worker_epoch,
                "lease_epoch": current.lease_epoch,
                "sequence": sequence,
                "page_count": len(pages),
                "page_chain_digest": digest,
                "key_id": self._active_digest_key_id,
            }
            payload["mac"] = self._traversal_mac(payload, self._active_digest_key_id)
            await append_owned_event(
                session, tenant_id=current.owner.tenant_id,
                ai_user_id=current.owner.user_id,
                session_id=current.owner.session_id,
                task_id=current.task_id,
                event=TaskEventRecord(
                    event_id=self._write_event_id("traversal-terminal", current.run_id),
                    task_id=current.task_id,
                    event_type=_TRAVERSAL_TERMINAL_VERSION,
                    timestamp=await self._now(session), payload=payload,
                ),
            )
            return current

    def _write_mac(self, data: dict[str, object], key_id: str) -> str:
        key = self._digest_keys.get(key_id)
        if key is None:
            raise BrowserRunStoreError("browser_write_audit_key_unavailable")
        encoded = json.dumps(data, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True, allow_nan=False).encode("ascii")
        return hmac.new(key, b"browser-write-audit-v1\x00" + encoded,
                        hashlib.sha256).hexdigest()

    @staticmethod
    def _write_event_id(kind: str, identity: str) -> str:
        return uuid5(NAMESPACE_URL, f"browser-{kind}-v1:{identity}").hex

    async def _write_event(
        self, session: AsyncSession, run: RunSnapshot, kind: str,
        event_id: str,
    ) -> dict[str, object] | None:
        row = (await session.execute(text(
            "SELECT event_type,payload FROM task_events WHERE task_id=:task"
            " AND event_id=:event_id"
        ), {"task": run.task_id, "event_id": event_id})).mappings().one_or_none()
        if row is None:
            return None
        payload = row["payload"]
        if not isinstance(payload, dict) or row["event_type"] != kind:
            raise BrowserRunStoreError("browser_write_audit_invalid")
        unsigned = {key: value for key, value in payload.items() if key != "mac"}
        key_id = payload.get("key_id")
        mac = payload.get("mac")
        if (
            type(key_id) is not str or type(mac) is not str
            or not hmac.compare_digest(mac, self._write_mac(unsigned, key_id))
            or payload.get("run_id") != run.run_id
        ):
            raise BrowserRunStoreError("browser_write_audit_invalid")
        return payload

    async def _append_write_event(
        self, session: AsyncSession, run: RunSnapshot,
        *, kind: str, event_id: str, data: dict[str, object],
        revision_delta: int = 1,
    ) -> None:
        if revision_delta not in {0, 1}:
            raise BrowserRunStoreError("browser_write_audit_revision_invalid")
        payload: dict[str, object] = {
            "schema_version": kind, "run_id": run.run_id,
            "run_revision": run.state_revision + revision_delta,
            "key_id": self._active_digest_key_id, **data,
        }
        payload["mac"] = self._write_mac(payload, self._active_digest_key_id)
        await append_owned_event(
            session, tenant_id=run.owner.tenant_id,
            ai_user_id=run.owner.user_id,
            session_id=run.owner.session_id, task_id=run.task_id,
            event=TaskEventRecord(
                event_id=event_id, task_id=run.task_id, event_type=kind,
                timestamp=await self._now(session), payload=payload,
            ),
        )

    async def _gate_request(
        self, session: AsyncSession, run: RunSnapshot,
        preparation: BrowserWritePreparation,
        *, confirmed: bool,
    ) -> None:
        row = (await session.execute(text(
            "SELECT * FROM human_gate_requests WHERE request_id=:request_id FOR SHARE"
        ), {"request_id": preparation.request_id})).mappings().one_or_none()
        if row is None:
            raise BrowserRunStoreError("browser_write_confirmation_missing")
        expected = preparation.gate_request(run.owner)
        if any(row[key] != value for key, value in expected.model_dump().items()):
            raise BrowserRunStoreError("browser_write_confirmation_mismatch")
        now = await self._now(session)
        if row["expires_at"] <= now:
            raise BrowserRunStoreError("browser_write_confirmation_expired")
        if confirmed and (
            row["decision"] != "confirmed"
            or row["decided_by_ai_user_id"] != run.owner.user_id
            or row["decided_tenant_id"] != run.owner.tenant_id
            or row["decided_session_id"] != run.owner.session_id
            or row["decided_at"] is None
            or row["decided_at"] < row["requested_at"]
            or row["decided_at"] >= row["expires_at"]
        ):
            raise BrowserRunStoreError("browser_write_confirmation_unapproved")

    async def _prepared_write(
        self, session: AsyncSession, run: RunSnapshot,
    ) -> BrowserWritePreparation:
        event_id = self._write_event_id("write-prepared", run.run_id)
        event = await self._write_event(session, run, _WRITE_PREPARED_VERSION, event_id)
        if event is None or event.get("schema_version") != _WRITE_PREPARED_VERSION:
            raise BrowserRunStoreError("browser_write_preparation_missing")
        try:
            preparation = BrowserWritePreparation.model_validate_json(
                json.dumps(event["preparation"])
            )
        except (KeyError, ValueError):
            raise BrowserRunStoreError("browser_write_preparation_invalid") from None
        if (
            preparation.task_id != run.task_id
            or preparation.run_id != run.run_id
            or preparation.owner_digest != _owner_digest(run.owner)
            or preparation.publication_digest != run.admission.publication_digest.hex()
            or preparation.binding_id != run.admission.binding_id
            or preparation.binding_revision != run.admission.binding_revision
            or preparation.input_revision != run.admission.input_revision
            or preparation.parameter_set_digest != run.admission.input_digest.hex()
        ):
            raise BrowserRunStoreError("browser_write_preparation_mismatch")
        return preparation

    async def _teaching_plan_in_session(
        self, session: AsyncSession, run: RunSnapshot,
        manifest: BrowserPublicationManifest,
    ) -> TeachingPlan | None:
        event = await self._write_event(
            session, run, _TEACHING_PLAN_VERSION,
            self._write_event_id("teaching-plan", run.run_id),
        )
        if event is None:
            return None
        if event.get("schema_version") != _TEACHING_PLAN_VERSION:
            raise BrowserRunStoreError("browser_teaching_plan_invalid")
        try:
            plan = TeachingPlan.model_validate_json(canonical_json(event["plan"]))
        except (KeyError, ValueError):
            raise BrowserRunStoreError("browser_teaching_plan_invalid") from None
        if (
            plan.task_id != run.task_id or plan.run_id != run.run_id
            or plan.owner_digest != _owner_digest(run.owner)
            or plan.binding_id != run.admission.binding_id
            or plan.binding_revision != run.admission.binding_revision
            or plan.publication_digest != manifest.digest
            or plan.skill_digest != manifest.skill.digest
            or plan.site_digest != manifest.site.digest
            or plan.policy_digest != manifest.site.policy.digest
            or plan.verifier_digest != manifest.verifier.digest
            or plan.sealed_input_digest != run.admission.input_digest.hex()
            or (plan.purpose == "teaching" and not manifest.site.primitive_rules)
            or tuple(item.step_id for item in plan.steps) != tuple(
                item.step_id for item in manifest.skill.steps
            )
            or (plan.purpose == "initial_validation" and (
                plan.source_publication_digest != manifest.digest
                or manifest.source_kind != "registered_site"
                or manifest.effect not in {"read_only", "controlled_write"}
                or manifest.rollback_source_digest is not None
            ))
        ):
            raise BrowserRunStoreError("browser_teaching_plan_mismatch")
        source_row = (await session.execute(text(
            "SELECT manifest FROM browser_publications WHERE tenant_id=:tenant"
            " AND publication_digest=:digest"
            " AND ((:initial AND state IN ('prepared','active','inactive'))"
            " OR (NOT :initial AND state IN ('active','inactive')))"
        ), {"tenant": run.owner.tenant_id,
            "digest": bytes.fromhex(plan.source_publication_digest),
            "initial": plan.purpose == "initial_validation"})).mappings().one_or_none()
        if source_row is None:
            raise BrowserRunStoreError("browser_teaching_source_unavailable")
        try:
            source = BrowserPublicationManifest.model_validate_json(
                canonical_json(source_row["manifest"]),
            )
        except ValueError:
            raise BrowserRunStoreError("browser_teaching_source_invalid") from None
        if source.digest != plan.source_publication_digest:
            raise BrowserRunStoreError("browser_teaching_source_invalid")
        if plan.purpose == "initial_validation" and source != manifest:
            raise BrowserRunStoreError("browser_teaching_source_invalid")
        source_rules = {
            item.step.step_id: item for item in (
                *source.site.steps, *source.site.primitive_rules,
            )
        }
        by_id = {item.step.step_id: item for item in (
            manifest.site.steps if plan.purpose == "initial_validation"
            else manifest.site.primitive_rules or manifest.site.steps
        )}
        candidate_rules = {item.step.step_id: item for item in manifest.site.steps}
        for item in plan.steps:
            primitive = by_id.get(item.primitive_id)
            candidate = candidate_rules.get(item.step_id)
            if (
                primitive is None or candidate is None
                or source_rules.get(item.primitive_id) != primitive
                or (plan.purpose != "initial_validation"
                    and self._primitive_id(manifest, item.step_id) != item.primitive_id)
                or candidate != primitive.model_copy(update={
                    "step": primitive.step.model_copy(update={"step_id": item.step_id}),
                })
                or primitive.step.effect != primitive.effect.actual_effect
                or (primitive.step.effect != "read_only" and not (
                    plan.purpose == "initial_validation"
                    and manifest.effect == "controlled_write"
                    and primitive.step.effect == "may_write"
                ))
                or item.rule_digest != canonical_digest(
                    "browser_site_step.v1", primitive.model_dump(mode="json"),
                )
            ):
                raise BrowserRunStoreError("browser_teaching_primitive_mismatch")
        return plan

    async def record_teaching_plan(
        self, claim: RunSnapshot, plan: TeachingPlan,
    ) -> RunSnapshot:
        """Sign a trusted queued Run's exact approved primitive sequence."""
        if not isinstance(plan, TeachingPlan):
            raise BrowserRunStoreError("browser_teaching_plan_invalid")
        async with self._transaction() as session:
            current = await self._locked(session, claim, "read", worker=False)
            if (
                current.phase != "queued" or current.worker_id is not None
                or current.lease_epoch is not None or current.cancel_requested
                or current.effect != "not_sent" or current.verification is not None
                or await self._write_event(
                    session, current, _TEACHING_PLAN_VERSION,
                    self._write_event_id("teaching-plan", current.run_id),
                ) is not None
            ):
                raise BrowserRunStoreError("browser_teaching_plan_stale")
            manifest = await self._frozen_publication(session, current)
            # Reuse the read validator before writing; a catalog name is not
            # authority unless the exact frozen rule and input are bound.
            payload: dict[str, object] = {"plan": plan.model_dump(mode="json")}
            await self._append_write_event(
                session, current, kind=_TEACHING_PLAN_VERSION,
                event_id=self._write_event_id("teaching-plan", current.run_id),
                data=payload,
            )
            try:
                checked = await self._teaching_plan_in_session(
                    session, current, manifest,
                )
            except BrowserRunStoreError:
                raise
            if checked != plan:
                raise BrowserRunStoreError("browser_teaching_plan_mismatch")
            return await self._update(session, current, {"effect": current.effect})

    async def load_teaching_plan(
        self, owner: BrowserOwner, task_id: str, run_id: str,
    ) -> TeachingPlan | None:
        async with self._transaction() as session:
            run = await self._read_run(session, owner, task_id, run_id)
            await self._authority.check_run(session, run, "read")
            manifest = await self._frozen_publication(session, run)
            return await self._teaching_plan_in_session(session, run, manifest)

    async def load_traversal_terminal(
        self, owner: BrowserOwner, task_id: str, run_id: str,
    ) -> int | None:
        async with self._transaction() as session:
            run = await self._read_run(session, owner, task_id, run_id)
            await self._authority.check_run(session, run, "read")
            manifest = await self._frozen_publication(session, run)
            return await self._traversal_terminal_in_session(session, run, manifest)

    async def load_teaching_plan_in_session(
        self, transaction: object, admission: RunAdmission,
    ) -> TeachingPlan | None:
        """Read the exact Run's MAC plan inside an existing authority transaction."""
        if not isinstance(transaction, AsyncSession):
            raise BrowserRunStoreError("browser_teaching_transaction_invalid")
        run = await self._read_run(
            transaction, admission.owner, admission.task_id, admission.run_id,
        )
        if run.admission != admission:
            raise BrowserRunStoreError("browser_teaching_admission_mismatch")
        manifest = await self._frozen_publication(transaction, run)
        return await self._teaching_plan_in_session(transaction, run, manifest)

    async def record_write_preparation(
        self, claim: RunSnapshot, preparation: BrowserWritePreparation,
    ) -> RunSnapshot:
        """Persist only a HumanGate-created exact preview on a queued Run."""
        async with self._transaction() as session:
            current = await self._locked(session, claim, "read", worker=False)
            existing = await self._write_event(
                session, current, _WRITE_PREPARED_VERSION,
                self._write_event_id("write-prepared", current.run_id),
            )
            if existing is not None:
                recorded = await self._prepared_write(session, current)
                if recorded != preparation:
                    raise BrowserRunStoreError("browser_write_preparation_mismatch")
                return current
            if (
                current.phase != "queued" or current.worker_id is not None
                or current.lease_epoch is not None or current.cancel_requested
                or current.effect != "not_sent" or current.verification is not None
            ):
                raise BrowserRunStoreError("browser_write_preparation_stale")
            manifest = await self._frozen_publication(session, current)
            if (
                preparation.task_id != current.task_id
                or preparation.run_id != current.run_id
                or preparation.owner_digest != _owner_digest(current.owner)
                or preparation.publication_digest != manifest.digest
                or preparation.skill_id != manifest.skill.skill_id
                or preparation.skill_version != manifest.skill.version
                or preparation.skill_digest != manifest.skill.digest
                or preparation.site_id != manifest.site.site_id
                or preparation.site_digest != manifest.site.digest
                or preparation.verifier_id != manifest.verifier.verifier_id
                or preparation.verifier_digest != manifest.verifier.digest
                or preparation.binding_id != current.admission.binding_id
                or preparation.binding_revision != current.admission.binding_revision
                or preparation.input_revision != current.admission.input_revision
                or preparation.parameter_set_digest != current.admission.input_digest.hex()
                or preparation.object_type != manifest.site.read_rule.object_type
                or preparation.write_steps_digest != _write_steps_digest(manifest)
            ):
                raise BrowserRunStoreError("browser_write_preparation_invalid")
            binding_row = (await session.execute(text(
                "SELECT bindings FROM task_version_binding_manifests"
                " WHERE task_id=:task AND manifest_digest=:digest"
            ), {"task": current.task_id,
                "digest": preparation.binding_manifest_digest})).mappings().one_or_none()
            if binding_row is None or not any(
                item.get("resource_type") == "skill"
                and item.get("resource_id") == preparation.skill_id
                and item.get("version") == preparation.skill_version
                and item.get("digest") == preparation.skill_digest
                for item in binding_row["bindings"].get("bindings", ())
            ):
                raise BrowserRunStoreError("browser_write_task_binding_invalid")
            await self._gate_request(session, current, preparation, confirmed=False)
            await self._append_write_event(
                session, current, kind=_WRITE_PREPARED_VERSION,
                event_id=self._write_event_id("write-prepared", current.run_id),
                data={"preparation": preparation.model_dump(mode="json")},
            )
            return await self._update(session, current, {"effect": current.effect})

    async def load_write_preparation(
        self, owner: BrowserOwner, task_id: str, run_id: str,
    ) -> BrowserWritePreparation | None:
        """Resolve an uncertain post-accept request without creating a second gate."""
        async with self._transaction() as session:
            run = await self._read_run(session, owner, task_id, run_id)
            await self._authority.check_run(session, run, "read")
            try:
                return await self._prepared_write(session, run)
            except BrowserRunStoreError as error:
                if error.code == "browser_write_preparation_missing":
                    return None
                raise

    async def write_confirmation_ready(
        self, run: RunSnapshot,
    ) -> bool:
        """Claim readiness hint; reserve still rechecks everything under a fence."""
        async with self._transaction() as session:
            return await self.write_confirmation_ready_in_session(session, run)

    async def write_confirmation_ready_in_session(
        self, transaction: object, run: RunSnapshot,
    ) -> bool:
        """The same bounded hint in an existing claim transaction."""
        if not isinstance(transaction, AsyncSession):
            raise BrowserRunStoreError("browser_write_transaction_invalid")
        session = transaction
        current = await self._read_run(session, run.owner, run.task_id, run.run_id)
        await self._authority.check_run(session, current, "read")
        if current != run or current.phase != "queued" or current.cancel_requested:
            return False
        try:
            preparation = await self._prepared_write(session, current)
            await self._gate_request(session, current, preparation, confirmed=True)
        except BrowserRunStoreError as error:
            if error.code in {
                "browser_write_preparation_missing",
                "browser_write_confirmation_missing",
                "browser_write_confirmation_unapproved",
                "browser_write_confirmation_expired",
            }:
                return False
            raise
        return True

    async def record_fill_stop(self, claim: RunSnapshot, report: FillStopReport) -> None:
        async with self._transaction() as session:
            current = await self._locked(session, claim, "advance")
            manifest = await self._frozen_publication(session, current)
            if manifest.execution_mode != "d_form_fill_stop_v1":
                raise BrowserRunStoreError("browser_fill_stop_receipt_denied")
            checked = FillStopReport.model_validate_json(report.model_dump_json())
            event_id = self._write_event_id("fill-stop-receipt", current.run_id)
            existing = await self._write_event(session, current, "browser.fill_stop.receipt.v1", event_id)
            if existing is not None:
                if existing.get("report") != checked.model_dump(mode="json"):
                    raise BrowserRunStoreError("browser_fill_stop_receipt_conflict")
                return
            await self._append_write_event(session, current, kind="browser.fill_stop.receipt.v1",
                event_id=event_id, revision_delta=0, data={"report": checked.model_dump(mode="json")})

    async def load_fill_stop(self, owner: BrowserOwner, task_id: str, run_id: str) -> FillStopReport | None:
        async with self._transaction() as session:
            current = await self._read_run(session, owner, task_id, run_id)
            await self._authority.check_run(session, current, "read")
            value = await self._write_event(session, current, "browser.fill_stop.receipt.v1",
                                           self._write_event_id("fill-stop-receipt", current.run_id))
            if value is None:
                return None
            report = FillStopReport.model_validate_json(json.dumps(value.get("report")))
            # The immutable business receipt predates terminal resource cleanup.
            # Only this owner-checked Run's canonical cleanup outcome updates its display.
            cleanup_confirmed = current.cleanup in {"released", "terminated"}
            if report.execution_status == "filled_stopped" and not cleanup_confirmed:
                # The client accepts a successful finite receipt only after cleanup.
                # Keep the immutable receipt and canonical business outcome intact.
                return None
            return report.model_copy(update={
                "cleanup_confirmed": cleanup_confirmed,
            })

    async def reserve_fill_stop(self, claim: RunSnapshot) -> RunSnapshot:
        """One whole finite D flow; no fabricated v4.2 per-control receipts."""
        async with self._transaction() as session:
            current = await self._locked(session, claim, "advance")
            manifest = await self._frozen_publication(session, current)
            if (manifest.execution_mode != "d_form_fill_stop_v1"
                    or manifest.effect != "controlled_write"
                    or current.phase != "running" or current.cancel_requested
                    or current.lease_epoch is None or current.effect != "not_sent"
                    or current.verification is not None):
                raise BrowserRunStoreError("browser_fill_stop_dispatch_stale")
            preparation = await self._prepared_write(session, current)
            await self._gate_request(session, current, preparation, confirmed=True)
            if preparation.write_steps_digest != _write_steps_digest(manifest):
                raise BrowserRunStoreError("browser_fill_stop_dispatch_mismatch")
            consumed_id = self._write_event_id("write-consumed", preparation.request_id)
            send_id = self._write_event_id("fill-stop-send", current.run_id)
            if (await self._write_event(session, current, _WRITE_CONSUMED_VERSION, consumed_id)
                    is not None or await self._write_event(session, current, _WRITE_SEND_VERSION, send_id)
                    is not None):
                raise BrowserRunStoreError("browser_write_send_reused")
            data = {"request_id": preparation.request_id,
                    "action_digest": preparation.action_digest,
                    "worker_epoch": current.worker_epoch, "lease_epoch": current.lease_epoch}
            await self._append_write_event(session, current, kind=_WRITE_CONSUMED_VERSION,
                                           event_id=consumed_id, data=data)
            await self._append_write_event(session, current, kind=_WRITE_SEND_VERSION,
                                           event_id=send_id, data={**data, "mode": "FILL_STOP",
                                                                "write_steps_digest": preparation.write_steps_digest})
            return await self._update(session, current, {"effect": current.effect})

    async def assert_fill_stop_reservation(self, claim: RunSnapshot) -> None:
        async with self._transaction() as session:
            current = await self._locked(session, claim, "advance")
            manifest = await self._frozen_publication(session, current)
            if (manifest.execution_mode != "d_form_fill_stop_v1"
                    or current.phase != "running" or current.cancel_requested
                    or current.lease_epoch is None or current.effect != "not_sent"):
                raise BrowserRunStoreError("browser_fill_stop_dispatch_stale")
            preparation = await self._prepared_write(session, current)
            await self._gate_request(session, current, preparation, confirmed=True)
            send = await self._write_event(session, current, _WRITE_SEND_VERSION,
                                          self._write_event_id("fill-stop-send", current.run_id))
            if (send is None or send.get("mode") != "FILL_STOP"
                    or send.get("request_id") != preparation.request_id
                    or send.get("action_digest") != preparation.action_digest
                    or send.get("write_steps_digest") != preparation.write_steps_digest
                    or send.get("worker_epoch") != current.worker_epoch
                    or send.get("lease_epoch") != current.lease_epoch):
                raise BrowserRunStoreError("browser_write_send_unreserved")

    async def reserve_write_dispatch(
        self, claim: RunSnapshot, *, command: ActionCommand,
        rule: SiteStepRule, spec: ReadSpec, sequence: int,
    ) -> RunSnapshot:
        """Atomically consume one HumanGate decision and reserve this send.

        Both events and the Run CAS commit together. A lost send receipt leaves
        phase=running, which recovery already marks UNKNOWN without replay.
        """
        if type(sequence) is not int or not 1 <= sequence <= _MAX_STEP_AUDITS:
            raise BrowserRunStoreError("browser_write_sequence_invalid")
        async with self._transaction() as session:
            current = await self._locked(session, claim, "advance")
            if (
                current.phase != "running" or current.cancel_requested
                or current.lease_epoch is None or current.effect == "unknown"
                or current.verification is not None
                or command.binding.owner != current.owner
                or command.binding.binding_id != current.admission.binding_id
                or command.binding.binding_revision != current.admission.binding_revision
                or command.binding.authorization_run_id != current.run_id
                or command.binding.lease_epoch != current.lease_epoch
            ):
                raise BrowserRunStoreError("browser_write_dispatch_stale")
            manifest = await self._frozen_publication(session, current)
            preparation = await self._prepared_write(session, current)
            rules = {item.step.step_id: item for item in manifest.site.steps}
            expected_steps = {item.step_id: item for item in manifest.skill.steps}
            if (
                expected_steps.get(command.step.step_id) != command.step
                or rules.get(command.step.step_id) != rule
                or command.skill_digest != manifest.skill.digest
                or rule.step.effect != "may_write"
                or rule.effect.actual_effect != "may_write"
                or spec.binding != command.binding
                or spec.business_key.object_type != preparation.object_type
                or spec.business_key.key_digest != preparation.business_key_digest
                or spec.business_key.confirmation_ref
                != preparation.business_key_confirmation_ref
                or preparation.write_steps_digest != _write_steps_digest(manifest)
            ):
                raise BrowserRunStoreError("browser_write_dispatch_mismatch")
            await self._gate_request(session, current, preparation, confirmed=True)
            previous = await self._audited_steps(session, current)
            if len(previous) != sequence - 1:
                raise BrowserRunStoreError("browser_write_sequence_conflict")
            self._check_step_route(
                manifest, tuple(item.event.step_id for item in previous)
                + (command.step.step_id,), complete=False,
                terminal_after=await self._traversal_terminal_in_session(
                    session, current, manifest,
                ),
            )
            consumed_id = self._write_event_id(
                "write-consumed", preparation.request_id,
            )
            consumed = await self._write_event(
                session, current, _WRITE_CONSUMED_VERSION, consumed_id,
            )
            if consumed is None:
                previous_writes = {
                    item.event.step_id for item in previous
                    if item.event.effect == "may_write"
                }
                if previous_writes:
                    raise BrowserRunStoreError("browser_write_confirmation_not_consumed")
                await self._append_write_event(
                    session, current, kind=_WRITE_CONSUMED_VERSION,
                    event_id=consumed_id,
                    data={
                        "request_id": preparation.request_id,
                        "action_digest": preparation.action_digest,
                        "worker_epoch": current.worker_epoch,
                        "lease_epoch": current.lease_epoch,
                    },
                )
            elif (
                consumed.get("request_id") != preparation.request_id
                or consumed.get("action_digest") != preparation.action_digest
                or consumed.get("worker_epoch") != current.worker_epoch
                or consumed.get("lease_epoch") != current.lease_epoch
            ):
                raise BrowserRunStoreError("browser_write_confirmation_reused")
            send_id = self._write_event_id(
                "write-send", f"{current.run_id}:{sequence}",
            )
            if await self._write_event(
                session, current, _WRITE_SEND_VERSION, send_id,
            ) is not None:
                raise BrowserRunStoreError("browser_write_send_reused")
            await self._append_write_event(
                session, current, kind=_WRITE_SEND_VERSION,
                event_id=send_id,
                data={
                    "request_id": preparation.request_id,
                    "action_digest": preparation.action_digest,
                    "sequence": sequence,
                    "step_id": command.step.step_id,
                    "worker_epoch": current.worker_epoch,
                    "lease_epoch": current.lease_epoch,
                },
            )
            return await self._update(session, current, {"effect": current.effect})

    async def assert_write_reservation(
        self, claim: RunSnapshot, *, command: ActionCommand,
        sequence: int,
    ) -> None:
        """Final DB side of the adapter's dispatch barrier; no permission cache."""
        async with self._transaction() as session:
            current = await self._locked(session, claim, "advance")
            if (
                current.phase != "running" or current.cancel_requested
                or current.lease_epoch is None or current.effect == "unknown"
                or command.binding.owner != current.owner
                or command.binding.authorization_run_id != current.run_id
                or command.binding.lease_epoch != current.lease_epoch
            ):
                raise BrowserRunStoreError("browser_write_dispatch_stale")
            preparation = await self._prepared_write(session, current)
            await self._gate_request(session, current, preparation, confirmed=True)
            send = await self._write_event(
                session, current, _WRITE_SEND_VERSION,
                self._write_event_id(
                    "write-send", f"{current.run_id}:{sequence}",
                ),
            )
            if (
                send is None or send.get("schema_version") != _WRITE_SEND_VERSION
                or send.get("request_id") != preparation.request_id
                or send.get("action_digest") != preparation.action_digest
                or send.get("step_id") != command.step.step_id
                or send.get("sequence") != sequence
                or send.get("worker_epoch") != current.worker_epoch
                or send.get("lease_epoch") != current.lease_epoch
            ):
                raise BrowserRunStoreError("browser_write_send_unreserved")
            previous = await self._audited_steps(session, current)
            if len(previous) != sequence - 1:
                raise BrowserRunStoreError("browser_write_sequence_conflict")
            manifest = await self._frozen_publication(session, current)
            self._check_step_route(
                manifest, tuple(item.event.step_id for item in previous)
                + (command.step.step_id,), complete=False,
                terminal_after=await self._traversal_terminal_in_session(
                    session, current, manifest,
                ),
            )
            barrier_id = self._write_event_id(
                "write-barrier", f"{current.run_id}:{sequence}",
            )
            if await self._write_event(
                session, current, _WRITE_BARRIER_VERSION, barrier_id,
            ) is not None:
                raise BrowserRunStoreError("browser_write_barrier_reused")
            await self._append_write_event(
                session, current, kind=_WRITE_BARRIER_VERSION,
                event_id=barrier_id, revision_delta=0,
                data={
                    "sequence": sequence,
                    "step_id": command.step.step_id,
                    "worker_epoch": current.worker_epoch,
                    "lease_epoch": current.lease_epoch,
                },
            )

    async def record_replay_proof(self, proof: TeachingReplayProof) -> bool:
        """Keep the independently verified replay fact with its existing Task."""
        if type(proof) is not TeachingReplayProof:
            raise BrowserRunStoreError("browser_replay_proof_invalid")
        owner = proof.binding.owner
        async with self._transaction() as session:
            replay = await self._read_run(
                session, owner, proof.replay_task_id, proof.replay_run_id,
            )
            source_row = (await session.execute(text(
                "SELECT * FROM browser_runs WHERE run_id=:run AND tenant_id=:tenant"
                " AND ai_user_id=:user AND session_id=:session"
            ), {"run": proof.source_run_id, "tenant": owner.tenant_id,
                "user": owner.user_id,
                "session": owner.session_id})).mappings().one_or_none()
            if source_row is None:
                raise BrowserRunStoreError("browser_replay_source_unavailable")
            source = _snapshot(source_row)
            await self._authority.check_run(session, replay, "read")
            await self._check_replay_source(session, source)
            if (
                replay.status != "completed" or source.status != "completed"
                or replay.verification != "verified"
                or source.verification != "verified"
                or replay.effect != "acknowledged"
                or source.effect != "acknowledged"
                or replay.verification_evidence_digest is None
                or source.verification_evidence_digest is None
                or replay.verification_evidence_digest.hex()
                != proof.verifier_evidence_digest
                or replay.admission.publication_digest.hex()
                != proof.publication_digest
                or replay.owner != source.owner
                or replay.admission.binding_id != proof.binding.binding_id
                or source.admission.binding_id != proof.binding.binding_id
                or replay.admission.binding_revision
                != proof.binding.binding_revision
                or source.admission.binding_revision
                != proof.binding.binding_revision
                or replay.cancel_requested or source.cancel_requested
                or replay.error_code is not None or source.error_code is not None
                or replay.protected_result is None or source.protected_result is None
            ):
                raise BrowserRunStoreError("browser_replay_proof_unverified")
            event_id = self._write_event_id("replay-proof", replay.run_id)
            if await self._write_event(
                session, replay, _REPLAY_PROOF_VERSION, event_id,
            ) is not None:
                raise BrowserRunStoreError("browser_replay_proof_reused")
            await self._append_write_event(
                session, replay, kind=_REPLAY_PROOF_VERSION,
                event_id=event_id,
                revision_delta=0,
                data={
                    "digest": proof.digest,
                    "draft_digest": proof.draft_digest,
                    "publication_digest": proof.publication_digest,
                    "source_run_id": proof.source_run_id,
                    "replay_task_id": proof.replay_task_id,
                    "replay_run_id": proof.replay_run_id,
                    "heldout_parameter_set_ref": proof.heldout_parameter_set_ref,
                    "verifier_evidence_digest": proof.verifier_evidence_digest,
                    "binding": BindingRevisions.from_binding(
                        proof.binding,
                    ).model_dump(mode="json"),
                    "input_digest": replay.admission.input_digest.hex(),
                    "source_input_digest": source.admission.input_digest.hex(),
                },
            )
            return True

    async def load_replay_proof_in_session(
        self, transaction: object, owner: BrowserOwner, replay_digest: str,
    ) -> TeachingReplayProof | None:
        """Reload a signed proof and both immutable verified Runs in one tx."""
        if not isinstance(transaction, AsyncSession):
            raise BrowserRunStoreError("browser_replay_transaction_invalid")
        session = transaction
        rows = (await session.execute(text(
            "SELECT e.event_type,e.payload FROM task_events e JOIN tasks t"
            " ON t.task_id=e.task_id WHERE t.tenant_id=:tenant"
            " AND t.ai_user_id=:user AND t.session_id=:session"
            " AND e.event_type=:kind AND e.payload->>'digest'=:digest LIMIT 2"
        ), {"tenant": owner.tenant_id, "user": owner.user_id,
            "session": owner.session_id, "kind": _REPLAY_PROOF_VERSION,
            "digest": replay_digest})).mappings().all()
        if not rows:
            return None
        if len(rows) != 1:
            raise BrowserRunStoreError("browser_replay_proof_ambiguous")
        payload = rows[0]["payload"]
        if not isinstance(payload, dict):
            raise BrowserRunStoreError("browser_replay_proof_invalid")
        unsigned = {key: value for key, value in payload.items() if key != "mac"}
        key_id = payload.get("key_id")
        mac = payload.get("mac")
        if (
            type(key_id) is not str or type(mac) is not str
            or payload.get("schema_version") != _REPLAY_PROOF_VERSION
            or not hmac.compare_digest(mac, self._write_mac(unsigned, key_id))
        ):
            raise BrowserRunStoreError("browser_replay_proof_invalid")
        try:
            binding = ScopeBinding.model_validate({
                "owner": owner, **payload["binding"],
            })
            proof = TeachingReplayProof(
                binding=binding,
                draft_digest=payload["draft_digest"],
                publication_digest=payload["publication_digest"],
                source_run_id=payload["source_run_id"],
                replay_task_id=payload["replay_task_id"],
                replay_run_id=payload["replay_run_id"],
                heldout_parameter_set_ref=payload["heldout_parameter_set_ref"],
                verifier_evidence_digest=payload["verifier_evidence_digest"],
                digest=payload["digest"],
            )
        except (KeyError, TypeError, ValueError):
            raise BrowserRunStoreError("browser_replay_proof_invalid") from None
        replay = await self._read_run(
            session, owner, proof.replay_task_id, proof.replay_run_id,
        )
        source_row = (await session.execute(text(
            "SELECT * FROM browser_runs WHERE run_id=:run AND tenant_id=:tenant"
            " AND ai_user_id=:user AND session_id=:session"
        ), {"run": proof.source_run_id, "tenant": owner.tenant_id,
            "user": owner.user_id, "session": owner.session_id})).mappings().one_or_none()
        if source_row is None:
            raise BrowserRunStoreError("browser_replay_source_unavailable")
        source = _snapshot(source_row)
        await self._authority.check_run(session, replay, "read")
        await self._check_replay_source(session, source)
        if (
            proof.digest != replay_digest
            or replay.status != "completed" or source.status != "completed"
            or replay.verification != "verified"
            or source.verification != "verified"
            or replay.effect != "acknowledged"
            or source.effect != "acknowledged"
            or replay.verification_evidence_digest is None
            or replay.verification_evidence_digest.hex()
            != proof.verifier_evidence_digest
            or replay.admission.publication_digest.hex()
            != proof.publication_digest
            or replay.admission.input_digest.hex() != payload.get("input_digest")
            or source.admission.input_digest.hex()
            != payload.get("source_input_digest")
            or replay.admission.binding_id != proof.binding.binding_id
            or source.admission.binding_id != proof.binding.binding_id
            or replay.admission.binding_revision
            != proof.binding.binding_revision
            or source.admission.binding_revision
            != proof.binding.binding_revision
            or replay.cancel_requested or source.cancel_requested
            or replay.error_code is not None or source.error_code is not None
        ):
            raise BrowserRunStoreError("browser_replay_proof_stale")
        return proof

    async def record_step(
        self, claim: RunSnapshot, *, command: ActionCommand,
        rule: SiteStepRule, scope: ScopeStamp, receipt: DispatchReceipt,
        policy_digest: str, sequence: int,
    ) -> tuple[RunSnapshot, RunTeachingStep]:
        """Fence and sign one Executor receipt in the existing Task event journal.

        This is after the actual send. Failure makes the caller's effect unknown;
        no later step may be sent or taught without this committed fact.
        """
        if type(sequence) is not int or not 1 <= sequence <= _MAX_STEP_AUDITS:
            raise BrowserRunStoreError("browser_step_sequence_invalid")
        try:
            receipt.validate_for(command)
        except ValueError:
            raise BrowserRunStoreError("browser_step_receipt_invalid") from None
        if (
            receipt.state != "acknowledged" or receipt.evidence_digest is None
            or command.step != rule.step
            or command.step.effect != rule.effect.actual_effect
            or command.step.effect not in {"read_only", "may_write"}
            or command.binding.owner != claim.owner
            or command.binding.binding_id != claim.admission.binding_id
            or command.binding.binding_revision != claim.admission.binding_revision
            or command.binding.authorization_run_id != claim.run_id
            or command.binding.evidence_version != claim.admission.auth_evidence_version
            or command.binding.lease_epoch != claim.lease_epoch
            or scope.region_id != rule.observation.region_id
        ):
            raise BrowserRunStoreError("browser_step_audit_invalid")
        async with self._transaction() as session:
            current = await self._locked(session, claim, "advance")
            if (
                current.phase != "running" or current.cancel_requested
                or current.effect == "unknown" or current.verification is not None
                or current.lease_epoch is None
            ):
                raise BrowserRunStoreError("browser_step_audit_stale")
            manifest = await self._frozen_publication(session, current)
            publication_state = (await session.execute(text(
                "SELECT state FROM browser_publications WHERE tenant_id=:tenant"
                " AND publication_digest=:digest"
            ), {"tenant": current.owner.tenant_id,
                "digest": current.admission.publication_digest})).scalar_one_or_none()
            if publication_state == "prepared" and await self._teaching_plan_in_session(
                session, current, manifest,
            ) is None:
                raise BrowserRunStoreError("browser_teaching_plan_required")
            rules = {item.step.step_id: item for item in manifest.site.steps}
            expected_steps = {item.step_id: item for item in manifest.skill.steps}
            if (
                expected_steps.get(command.step.step_id) != command.step
                or rules.get(command.step.step_id) != rule
                or manifest.skill.digest != command.skill_digest
                or manifest.site.policy.digest != policy_digest
            ):
                raise BrowserRunStoreError("browser_step_audit_publication_mismatch")
            primitive_id = self._primitive_id(manifest, command.step.step_id)
            primitive_rule = self._primitive_rule(manifest, primitive_id)
            if primitive_rule is None:
                if primitive_id != command.step.step_id:
                    raise BrowserRunStoreError("browser_step_primitive_unregistered")
                primitive_rule = rule
            previous = await self._audited_steps(session, current)
            if len(previous) != sequence - 1:
                raise BrowserRunStoreError("browser_step_audit_sequence_conflict")
            self._check_step_route(
                manifest, tuple(item.event.step_id for item in previous)
                + (command.step.step_id,), complete=False,
                terminal_after=await self._traversal_terminal_in_session(
                    session, current, manifest,
                ),
            )
            if command.step.effect == "may_write":
                send = await self._write_event(
                    session, current, _WRITE_SEND_VERSION,
                    self._write_event_id(
                        "write-send", f"{current.run_id}:{sequence}",
                    ),
                )
                if (
                    send is None or send.get("schema_version") != _WRITE_SEND_VERSION
                    or send.get("sequence") != sequence
                    or send.get("step_id") != command.step.step_id
                    or send.get("worker_epoch") != current.worker_epoch
                    or send.get("lease_epoch") != current.lease_epoch
                ):
                    raise BrowserRunStoreError("browser_write_send_unreserved")
                barrier = await self._write_event(
                    session, current, _WRITE_BARRIER_VERSION,
                    self._write_event_id(
                        "write-barrier", f"{current.run_id}:{sequence}",
                    ),
                )
                if (
                    barrier is None
                    or barrier.get("schema_version") != _WRITE_BARRIER_VERSION
                    or barrier.get("sequence") != sequence
                    or barrier.get("step_id") != command.step.step_id
                    or barrier.get("worker_epoch") != current.worker_epoch
                    or barrier.get("lease_epoch") != current.lease_epoch
                ):
                    raise BrowserRunStoreError("browser_write_barrier_missing")
            event = TrajectoryEvent(
                sequence=sequence,
                event_id=uuid5(NAMESPACE_URL,
                               f"browser-run-step-v1:{current.run_id}:{sequence}").hex,
                step_id=command.step.step_id, scope=scope,
                target=command.target, option=command.option,
                operation=command.step.operation, effect=command.step.effect,
                effect_proof=rule.effect,
                value_ref=command.step.value_ref,
                url_ref=command.step.url_ref,
                option_ref=command.step.option_ref,
                policy_digest=policy_digest, approved_labels=(),
            )
            step = RunTeachingStep(
                run_id=current.run_id, skill_version=manifest.skill.version,
                skill_digest=manifest.skill.digest,
                binding=BindingRevisions.from_binding(command.binding),
                primitive_id=primitive_id,
                event=event,
                rule_digest=canonical_digest(
                    "browser_site_step.v1", primitive_rule.model_dump(mode="json"),
                ),
                dispatch_state="acknowledged",
                dispatch_evidence_digest=receipt.evidence_digest,
            )
            payload: dict[str, object] = {
                "schema_version": _STEP_AUDIT_VERSION,
                "run_id": current.run_id,
                "sequence": sequence,
                "run_revision": current.state_revision + 1,
                "step": step.model_dump(mode="json"),
                "key_id": self._active_digest_key_id,
            }
            payload["mac"] = self._step_mac(payload, self._active_digest_key_id)
            await append_owned_event(
                session, tenant_id=current.owner.tenant_id,
                ai_user_id=current.owner.user_id,
                session_id=current.owner.session_id,
                task_id=current.task_id,
                event=TaskEventRecord(
                    event_id=event.event_id, task_id=current.task_id,
                    event_type="browser_run_step", timestamp=await self._now(session),
                    payload=payload,
                ),
            )
            updated = await self._update(session, current, {"effect": "acknowledged"})
            return updated, step

    async def record_failed_step(
        self, claim: RunSnapshot, *, rule: SiteStepRule, scope: ScopeStamp,
        policy_digest: str, sequence: int, reason: RepairFailureReason,
        observations: tuple[FormalRepairObservation, ...],
    ) -> tuple[RunSnapshot, FormalFailedStep]:
        """Commit a value-free pre-send failure from the installed Executor only."""
        if (
            type(sequence) is not int or not 1 <= sequence <= _MAX_STEP_AUDITS
            or reason not in {"target_stale", "locator_not_found", "ambiguous_target"}
            or scope.region_id != rule.observation.region_id
            or type(observations) is not tuple or len(observations) > 32
            or any(not isinstance(item, FormalRepairObservation)
                   or item.scope.region_id != scope.region_id
                   or item.replacement == rule.step.locator for item in observations)
            or len({item.replacement for item in observations}) != len(observations)
        ):
            raise BrowserRunStoreError("browser_failed_step_audit_invalid")
        async with self._transaction() as session:
            current = await self._locked(session, claim, "advance")
            if (
                current.phase != "running" or current.cancel_requested
                or current.effect == "unknown" or current.verification is not None
                or current.lease_epoch is None
            ):
                raise BrowserRunStoreError("browser_failed_step_audit_stale")
            manifest = await self._frozen_publication(session, current)
            registered = next((item for item in manifest.site.steps
                               if item.step.step_id == rule.step.step_id), None)
            if (
                manifest.source_kind != "registered_site"
                or registered != rule or manifest.site.policy.digest != policy_digest
                or rule.step.effect != rule.effect.actual_effect
            ):
                raise BrowserRunStoreError("browser_failed_step_audit_publication_mismatch")
            previous = await self._audited_steps(session, current)
            if len(previous) != sequence - 1:
                raise BrowserRunStoreError("browser_failed_step_audit_sequence_conflict")
            self._check_step_route(
                manifest, tuple(item.event.step_id for item in previous)
                + (rule.step.step_id,), complete=False,
                terminal_after=await self._traversal_terminal_in_session(
                    session, current, manifest,
                ),
            )
            existing = (await session.execute(text(
                "SELECT 1 FROM task_events WHERE task_id=:task"
                " AND event_type='browser_run_failed_step'"
                " AND payload->>'run_id'=:run LIMIT 1"
            ), {"task": current.task_id, "run": current.run_id})).scalar_one_or_none()
            if existing is not None:
                raise BrowserRunStoreError("browser_failed_step_audit_duplicate")
            if rule.step.effect == "may_write":
                send = await self._write_event(
                    session, current, _WRITE_SEND_VERSION,
                    self._write_event_id(
                        "write-send", f"{current.run_id}:{sequence}",
                    ),
                )
                if send is not None:
                    raise BrowserRunStoreError("browser_failed_step_effect_uncertain")
            payload: dict[str, object] = {
                "schema_version": _FAILED_STEP_AUDIT_VERSION,
                "run_id": current.run_id,
                "sequence": sequence,
                "run_revision": current.state_revision + 1,
                "publication_digest": manifest.digest,
                "step_id": rule.step.step_id,
                "scope": scope.model_dump(mode="json"),
                "policy_digest": policy_digest,
                "binding_id": current.admission.binding_id,
                "binding_revision": current.admission.binding_revision,
                "lease_epoch": current.lease_epoch,
                "worker_epoch": current.worker_epoch,
                "reason": reason,
                "dispatch_state": "not_sent",
                "observations": [
                    {"replacement": item.replacement.model_dump(mode="json"),
                     "scope": item.scope.model_dump(mode="json"),
                     "target_digest": item.target_digest}
                    for item in observations
                ],
                "key_id": self._active_digest_key_id,
            }
            payload["mac"] = self._step_mac(payload, self._active_digest_key_id)
            await append_owned_event(
                session, tenant_id=current.owner.tenant_id,
                ai_user_id=current.owner.user_id,
                session_id=current.owner.session_id,
                task_id=current.task_id,
                event=TaskEventRecord(
                    event_id=uuid5(
                        NAMESPACE_URL,
                        f"browser-run-failed-step-v1:{current.run_id}:{sequence}",
                    ).hex,
                    task_id=current.task_id, event_type="browser_run_failed_step",
                    timestamp=await self._now(session), payload=payload,
                ),
            )
            updated = await self._update(session, current, {"effect": current.effect})
            binding = ScopeBinding(
                owner=current.owner, binding_id=current.admission.binding_id,
                binding_revision=current.admission.binding_revision,
                authorization_run_id=current.run_id,
                evidence_version=current.admission.auth_evidence_version,
                lease_epoch=current.lease_epoch,
            )
            return updated, FormalFailedStep(
                binding, current.run_id, manifest.digest, rule.step.step_id,
                reason, "not_sent", str(payload["mac"]), observations,
            )

    async def read_failed_step(
        self, owner: BrowserOwner, task_id: str, run_id: str, step_id: str,
    ) -> FormalFailedStep | None:
        """Read only an authenticated terminal failure with its exact MAC."""
        checked_run_id(step_id)
        async with self._transaction() as session:
            run = await self._read_run(session, owner, task_id, run_id)
            await self._authority.check_run(session, run, "read")
            if (
                run.status != "failed" or run.verification == "verified"
                or run.effect == "unknown" or run.cancel_requested
                or run.lease_epoch is None
            ):
                return None
            manifest = await self._frozen_publication(session, run)
            if manifest.source_kind != "registered_site":
                return None
            rows = (await session.execute(text(
                "SELECT payload FROM task_events WHERE task_id=:task"
                " AND event_type='browser_run_failed_step'"
                " AND payload->>'run_id'=:run"
            ), {"task": task_id, "run": run_id})).mappings().all()
            if not rows:
                return None
            if len(rows) != 1 or not isinstance(rows[0]["payload"], dict):
                raise BrowserRunStoreError("browser_failed_step_audit_invalid")
            payload = rows[0]["payload"]
            unsigned = {key: value for key, value in payload.items() if key != "mac"}
            key_id, mac = payload.get("key_id"), payload.get("mac")
            if (
                type(key_id) is not str or type(mac) is not str
                or payload.get("schema_version") != _FAILED_STEP_AUDIT_VERSION
                or payload.get("run_id") != run_id
                or payload.get("publication_digest") != manifest.digest
                or type(payload.get("step_id")) is not str
                or type(payload.get("sequence")) is not int
                or type(payload.get("run_revision")) is not int
                or not 1 <= payload["run_revision"] <= run.state_revision
                or payload.get("binding_id") != run.admission.binding_id
                or payload.get("binding_revision") != run.admission.binding_revision
                or payload.get("lease_epoch") != run.lease_epoch
                or payload.get("worker_epoch") != run.worker_epoch
                or payload.get("dispatch_state") != "not_sent"
                or payload.get("policy_digest") != manifest.site.policy.digest
                or payload.get("reason") not in {
                    "target_stale", "locator_not_found", "ambiguous_target",
                }
                or not hmac.compare_digest(mac, self._step_mac(unsigned, key_id))
            ):
                raise BrowserRunStoreError("browser_failed_step_audit_invalid")
            audited_step_id = payload["step_id"]
            rule = next((item for item in manifest.site.steps
                         if item.step.step_id == audited_step_id), None)
            try:
                scope = ScopeStamp.model_validate(payload["scope"])
            except (KeyError, ValueError):
                raise BrowserRunStoreError("browser_failed_step_audit_invalid") from None
            raw_observations = payload.get("observations")
            if type(raw_observations) is not list or len(raw_observations) > 32:
                raise BrowserRunStoreError("browser_failed_step_audit_invalid")
            try:
                observations = tuple(FormalRepairObservation(
                    LocatorHint.model_validate(item["replacement"]),
                    ScopeStamp.model_validate(item["scope"]),
                    item["target_digest"],
                ) for item in raw_observations)
            except (KeyError, TypeError, ValueError):
                raise BrowserRunStoreError("browser_failed_step_audit_invalid") from None
            previous = await self._audited_steps(session, run)
            if (
                rule is None or scope.region_id != rule.observation.region_id
                or len(previous) != payload["sequence"] - 1
                or any(item.scope.region_id != scope.region_id
                       or item.replacement == rule.step.locator
                       for item in observations)
                or len({item.replacement for item in observations}) != len(observations)
            ):
                raise BrowserRunStoreError("browser_failed_step_audit_invalid")
            self._check_step_route(
                manifest, tuple(item.event.step_id for item in previous)
                + (audited_step_id,), complete=False,
                terminal_after=await self._traversal_terminal_in_session(
                    session, run, manifest,
                ),
            )
            binding = ScopeBinding(
                owner=run.owner, binding_id=run.admission.binding_id,
                binding_revision=run.admission.binding_revision,
                authorization_run_id=run_id,
                evidence_version=run.admission.auth_evidence_version,
                lease_epoch=run.lease_epoch,
            )
            if audited_step_id != step_id:
                return None
            return FormalFailedStep(
                binding, run_id, manifest.digest, audited_step_id,
                payload["reason"], "not_sent", mac, observations,
            )

    async def record_locator_proposal(
        self, owner: BrowserOwner, task_id: str, run_id: str,
        proposal: FormalLocatorProposal,
    ) -> None:
        try:
            checked = FormalLocatorProposal.model_validate_json(
                proposal.model_dump_json()
            )
        except ValueError:
            raise BrowserRunStoreError("browser_repair_proposal_invalid") from None
        failure = await self.read_failed_step(
            owner, task_id, run_id, checked.step_id,
        )
        if (
            failure is None or checked.failed_run_id != run_id
            or checked.publication_digest != failure.publication_digest
            or checked.failure_evidence_digest != failure.evidence_digest
            or not any(
                item.replacement == checked.replacement
                and item.digest == checked.observation_evidence_digest
                for item in failure.observations
            )
        ):
            raise BrowserRunStoreError("browser_repair_proposal_stale")
        async with self._transaction() as session:
            run = await self._read_run(session, owner, task_id, run_id)
            await self._authority.check_run(session, run, "read")
            if (
                run.status != "failed" or run.effect == "unknown"
                or run.cancel_requested or run.verification == "verified"
                or run.admission.publication_digest.hex() != checked.publication_digest
            ):
                raise BrowserRunStoreError("browser_repair_proposal_stale")
            manifest = await self._frozen_publication(session, run)
            plan = await self._teaching_plan_in_session(session, run, manifest)
            step = next((item for item in manifest.skill.steps
                         if item.step_id == checked.step_id), None)
            if (
                step is None or checked.original_step_digest != canonical_digest(
                    "browser_skill_step.v1", step.model_dump(mode="json"),
                ) or checked.primitive_id != self._primitive_id(
                    manifest, checked.step_id,
                )
                or (
                    checked.source_kind == "published_skill"
                    and (plan is not None or checked.draft_digest != manifest.digest
                         or checked.source_digest != manifest.digest)
                )
                or (
                    checked.source_kind == "teaching_draft"
                    and (plan is None or plan.purpose != "teaching"
                         or plan.publication_digest != manifest.digest
                         or checked.source_digest != plan.source_publication_digest)
                )
            ):
                raise BrowserRunStoreError("browser_repair_proposal_invalid")
            event_id = uuid5(
                NAMESPACE_URL, f"browser-run-locator-proposal-v1:{checked.proposal_id}",
            ).hex
            existing = (await session.execute(text(
                "SELECT event_type,payload FROM task_events WHERE task_id=:task"
                " AND event_id=:event"
            ), {"task": task_id, "event": event_id})).mappings().one_or_none()
            if existing is not None:
                old = existing["payload"]
                if (
                    existing["event_type"] != "browser_locator_proposal"
                    or not isinstance(old, dict)
                    or old.get("schema_version") != _LOCATOR_PROPOSAL_AUDIT_VERSION
                    or old.get("run_id") != run_id
                    or old.get("proposal") != checked.model_dump(mode="json")
                    or type(old.get("key_id")) is not str
                    or type(old.get("mac")) is not str
                    or not hmac.compare_digest(
                        old["mac"], self._proposal_mac(
                            {key: value for key, value in old.items() if key != "mac"},
                            old["key_id"],
                        ),
                    )
                ):
                    raise BrowserRunStoreError("browser_repair_proposal_invalid")
                return
            payload: dict[str, object] = {
                "schema_version": _LOCATOR_PROPOSAL_AUDIT_VERSION,
                "run_id": run_id,
                "proposal": checked.model_dump(mode="json"),
                "key_id": self._active_digest_key_id,
            }
            payload["mac"] = self._proposal_mac(payload, self._active_digest_key_id)
            await append_owned_event(
                session, tenant_id=owner.tenant_id, ai_user_id=owner.user_id,
                session_id=owner.session_id, task_id=task_id,
                event=TaskEventRecord(
                    event_id=event_id, task_id=task_id,
                    event_type="browser_locator_proposal",
                    timestamp=await self._now(session), payload=payload,
                ),
            )

    async def read_locator_proposals(
        self, owner: BrowserOwner, task_id: str, run_id: str,
    ) -> tuple[FormalLocatorProposal, ...]:
        async with self._transaction() as session:
            run = await self._read_run(session, owner, task_id, run_id)
            await self._authority.check_run(session, run, "read")
            rows = (await session.execute(text(
                "SELECT payload FROM task_events WHERE task_id=:task"
                " AND event_type='browser_locator_proposal'"
                " AND payload->>'run_id'=:run ORDER BY event_id"
            ), {"task": task_id, "run": run_id})).mappings().all()
        if len(rows) > 32:
            raise BrowserRunStoreError("browser_repair_proposal_overflow")
        proposals: list[FormalLocatorProposal] = []
        for row in rows:
            payload = row["payload"]
            if not isinstance(payload, dict):
                raise BrowserRunStoreError("browser_repair_proposal_invalid")
            key_id, mac = payload.get("key_id"), payload.get("mac")
            if (
                payload.get("schema_version") != _LOCATOR_PROPOSAL_AUDIT_VERSION
                or payload.get("run_id") != run_id
                or type(key_id) is not str or type(mac) is not str
                or not hmac.compare_digest(
                    mac, self._proposal_mac(
                        {key: value for key, value in payload.items() if key != "mac"},
                        key_id,
                    ),
                )
            ):
                raise BrowserRunStoreError("browser_repair_proposal_invalid")
            try:
                proposal = FormalLocatorProposal.model_validate(payload["proposal"])
            except (KeyError, ValueError):
                raise BrowserRunStoreError("browser_repair_proposal_invalid") from None
            evidence = await self.read_failed_step(
                owner, task_id, run_id, proposal.step_id,
            )
            if (
                evidence is None or proposal.failed_run_id != run_id
                or proposal.publication_digest != evidence.publication_digest
                or proposal.failure_evidence_digest != evidence.evidence_digest
                or not any(
                    item.replacement == proposal.replacement
                    and item.digest == proposal.observation_evidence_digest
                    for item in evidence.observations
                )
            ):
                raise BrowserRunStoreError("browser_repair_proposal_stale")
            proposals.append(proposal)
        return tuple(proposals)

    async def read_teaching_steps(
        self, owner: BrowserOwner, task_id: str, run_id: str,
    ) -> tuple[RunTeachingStep, ...]:
        """Owner-scoped, MAC-checked facts from a completed verified Run only."""
        async with self._transaction() as session:
            run = await self._read_run(session, owner, task_id, run_id)
            await self._authority.check_run(session, run, "read")
            if (
                run.status != "completed" or run.verification != "verified"
                or run.verification_evidence_digest is None
                or run.effect != "acknowledged" or run.cancel_requested
            ):
                raise BrowserRunStoreError("browser_step_source_unverified")
            manifest = await self._frozen_publication(session, run)
            if any(
                rule.step.effect != "read_only"
                or rule.effect.actual_effect != "read_only"
                for rule in manifest.site.steps
            ):
                raise BrowserRunStoreError("browser_step_source_not_read_only")
            result = await self._audited_steps(session, run)
            self._check_step_route(
                manifest, tuple(step.event.step_id for step in result),
                complete=True,
                terminal_after=await self._traversal_terminal_in_session(
                    session, run, manifest,
                ),
            )
            rules = {rule.step.step_id: rule for rule in manifest.site.steps}
            expected = {step.step_id: step for step in manifest.skill.steps}
            for sequence, step in enumerate(result, start=1):
                candidate_id = step.event.step_id
                rule = rules.get(candidate_id)
                primitive = self._primitive_rule(manifest, step.primitive_id)
                if primitive is None and step.primitive_id == candidate_id:
                    primitive = rule
                if rule is None or primitive is None:
                    raise BrowserRunStoreError("browser_step_audit_invalid")
                if (
                    step.run_id != run_id
                    or step.event.sequence != sequence
                    or expected.get(candidate_id) != rule.step
                    or step.primitive_id != self._primitive_id(manifest, candidate_id)
                    or not step.event.matches_step(rule.step)
                    or step.event.scope.region_id != rule.observation.region_id
                    or step.event.effect_proof != rule.effect
                    or step.event.policy_digest != manifest.site.policy.digest
                    or step.rule_digest != canonical_digest(
                        "browser_site_step.v1", primitive.model_dump(mode="json"),
                    )
                    or step.skill_digest != manifest.skill.digest
                    or step.skill_version != manifest.skill.version
                    or step.binding.binding_id != run.admission.binding_id
                    or step.binding.binding_revision != run.admission.binding_revision
                    or step.binding.authorization_run_id != run_id
                    or step.binding.evidence_version
                    != run.admission.auth_evidence_version
                    or step.binding.lease_epoch != run.lease_epoch
                ):
                    raise BrowserRunStoreError("browser_step_audit_invalid")
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
