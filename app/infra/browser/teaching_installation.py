"""PG backed teaching review for an explicitly installed browser publication.

The HumanGate request is created through the existing HumanGate authority. This
adapter only reads its persisted decision; it cannot manufacture an approval.
The replay proof loader must read the owner scoped proof written with the Run.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Literal, cast

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.browser_skill.models import BrowserOwner
from app.browser_skill.publication_contracts import (
    BrowserPublicationManifest,
    canonical_json,
)
from app.browser_skill.registry import ReviewedPublicationAuthority, TeachingReviewFact
from app.browser_skill.teaching import TeachingReplayProof
from app.infra.browser.composition import BrowserPublicationGrants, BrowserVerticalComponents
from app.infra.human_gate.postgresql import PostgreSQLHumanGate
from app.infra.persistence.browser.runs import PostgreSQLBrowserRunStore
from app.ports.browser_publication_store import BrowserPublicationReviewPort
from app.ports.browser_run_store import checked_run_id
from app.ports.human_gate import (
    HumanGateConflictError,
    HumanGateDecisionRecord,
    HumanGateRequest,
    VersionBinding,
    build_task_version_binding_manifest,
)

ReplayProofLookup = Callable[
    [AsyncSession, BrowserOwner, str], Awaitable[TeachingReplayProof | None]
]


class InstalledReplayProofLookup:
    """Fail closed until the exact same-session Run store is composed."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions
        self._runs: PostgreSQLBrowserRunStore | None = None

    def install_runs(self, runs: PostgreSQLBrowserRunStore) -> None:
        if (type(runs) is not PostgreSQLBrowserRunStore
                or runs._sessions is not self._sessions
                or self._runs is not None):
            raise ValueError("browser_teaching_replay_installation_invalid")
        self._runs = runs

    async def __call__(
        self, session: AsyncSession, owner: BrowserOwner, replay_digest: str,
    ) -> TeachingReplayProof | None:
        runs = self._runs
        if runs is None:
            return None
        return await runs.load_replay_proof_in_session(session, owner, replay_digest)


def teaching_review_action_digest(
    owner: BrowserOwner, manifest: BrowserPublicationManifest,
    proof: TeachingReplayProof,
) -> str:
    """Exact value free action shown to the current HumanGate reviewer."""
    if (owner != proof.binding.owner
            or manifest.digest != proof.publication_digest):
        raise ValueError("browser_teaching_review_reference_invalid")
    payload = {
        "owner": [owner.tenant_id, owner.user_id, owner.session_id],
        "binding": [proof.binding.binding_id, proof.binding.binding_revision],
        "draft": proof.draft_digest,
        "replay": [proof.digest, proof.replay_task_id, proof.replay_run_id,
                   proof.verifier_evidence_digest],
        "publication": [manifest.digest, manifest.skill.skill_id,
                        manifest.skill.version, manifest.skill.digest],
        "site": [manifest.site.site_id, manifest.site.version,
                 manifest.site.digest, manifest.site.source.fixture_digest],
        "schema": hashlib.sha256(manifest.output.output_schema_json.encode()).hexdigest(),
        "capability": [manifest.capability.capability_id,
                       manifest.capability_digest,
                       manifest.capability.target_system],
    }
    return hashlib.sha256((
        "browser_teaching_review_action.v1\n" + canonical_json(payload)
    ).encode()).hexdigest()


def _evidence(row: RowMapping) -> str:
    payload = {key: (
        value.isoformat() if hasattr(value, "isoformat") else value
    ) for key, value in row.items() if key in {
        "request_id", "task_id", "requested_for_ai_user_id",
        "requested_session_id", "requested_tenant_id", "action_digest",
        "request_digest", "binding_manifest_digest", "requested_at",
        "expires_at", "decision", "decided_by_ai_user_id",
        "decided_session_id", "decided_tenant_id", "decided_at",
    }}
    return hashlib.sha256((
        "browser_teaching_review_record.v1\n" + canonical_json(payload)
    ).encode()).hexdigest()


class PostgreSQLTeachingPublicationReview(BrowserPublicationReviewPort):
    """Check a real, unexpired HumanGate row and independently verified Run.

    ``load_proof`` is a required trusted Run adapter, never a request DTO. It
    must read a proof persisted with the replay Run and recheck its owner. The
    SQL below separately confirms the replay Run's terminal Verifier result,
    current binding revision and the human decision. Missing facts deny.
    """

    def __init__(
        self, sessions: async_sessionmaker[AsyncSession],
        load_proof: ReplayProofLookup,
    ) -> None:
        if not callable(load_proof):
            raise ValueError("browser_teaching_replay_store_required")
        self._sessions, self._load_proof = sessions, load_proof

    async def request_review(
        self, owner: BrowserOwner, manifest: BrowserPublicationManifest,
        draft_digest: str, replay_digest: str, request_id: str,
    ) -> HumanGateRequest:
        """Ask the same verified subject to review one proven replay version."""
        checked_run_id(request_id)
        if manifest.source_kind != "registered_site":
            raise ValueError("browser_teaching_review_publication_invalid")
        async with self._sessions() as session, session.begin():
            proof = await self._proof(
                session, owner, manifest, draft_digest, replay_digest,
            )
        if proof is None:
            raise ValueError("browser_teaching_replay_unverified")
        now = datetime.now(UTC)
        binding = VersionBinding(
            resource_type="skill", resource_id=manifest.skill.skill_id,
            version=manifest.skill.version, digest=manifest.skill.digest,
        )
        gate = PostgreSQLHumanGate(self._sessions)
        locked = await gate.get_task_binding(proof.replay_task_id)
        if locked is None:
            try:
                locked = await gate.bind_task(build_task_version_binding_manifest(
                    task_id=proof.replay_task_id, bindings=(binding,), locked_at=now,
                ))
            except HumanGateConflictError:
                locked = await gate.get_task_binding(proof.replay_task_id)
                if locked is None:
                    raise
        if locked.bindings != (binding,):
            raise ValueError("browser_teaching_review_binding_invalid")
        action = teaching_review_action_digest(owner, manifest, proof)
        request_digest = hashlib.sha256((
            "browser_teaching_review_request.v1\n" + canonical_json({
                "request_id": request_id,
                "action_digest": action,
                "binding_manifest_digest": locked.manifest_digest,
            })
        ).encode()).hexdigest()
        existing = await gate.get_request(request_id)
        if existing is not None:
            if (existing.task_id != proof.replay_task_id
                    or existing.requested_tenant_id != owner.tenant_id
                    or existing.requested_for_ai_user_id != owner.user_id
                    or existing.requested_session_id != owner.session_id
                    or existing.action_digest != action
                    or existing.binding_manifest_digest != locked.manifest_digest
                    or existing.request_digest != request_digest
                    or existing.expires_at <= now):
                raise ValueError("browser_teaching_review_request_conflict")
            return existing
        request = HumanGateRequest(
            request_id=request_id, task_id=proof.replay_task_id,
            requested_for_ai_user_id=owner.user_id,
            requested_session_id=owner.session_id,
            requested_tenant_id=owner.tenant_id,
            action_digest=action, request_digest=request_digest,
            binding_manifest_digest=locked.manifest_digest,
            requested_at=now, expires_at=now + timedelta(minutes=15),
        )
        return await gate.create_request(request)

    async def decide_review(
        self, owner: BrowserOwner, manifest: BrowserPublicationManifest,
        draft_digest: str, replay_digest: str, request_id: str,
        decision: Literal["confirmed", "rejected"],
    ) -> HumanGateDecisionRecord:
        """Record an exact same-subject decision; publication rechecks it later."""
        checked_run_id(request_id)
        async with self._sessions() as session, session.begin():
            proof = await self._proof(
                session, owner, manifest, draft_digest, replay_digest,
            )
        if proof is None:
            raise ValueError("browser_teaching_replay_unverified")
        gate = PostgreSQLHumanGate(self._sessions)
        request = await gate.get_request(request_id)
        now = datetime.now(UTC)
        if (request is None or request.task_id != proof.replay_task_id
                or request.requested_tenant_id != owner.tenant_id
                or request.requested_for_ai_user_id != owner.user_id
                or request.requested_session_id != owner.session_id
                or request.action_digest != teaching_review_action_digest(
                    owner, manifest, proof,
                )
                or request.expires_at <= now):
            raise ValueError("browser_teaching_review_request_stale")
        record = HumanGateDecisionRecord(
            request_id=request_id, task_id=proof.replay_task_id,
            decided_by_ai_user_id=owner.user_id,
            decided_session_id=owner.session_id,
            decided_tenant_id=owner.tenant_id,
            decision=decision,
            request_digest=request.request_digest,
            binding_manifest_digest=request.binding_manifest_digest,
            decided_at=now,
        )
        existing = await gate.get_decision(request_id)
        if existing is None:
            try:
                existing = await gate.record_decision(record)
            except HumanGateConflictError:
                existing = await gate.get_decision(request_id)
        if (existing is None
                or existing.model_dump(exclude={"decided_at"})
                != record.model_dump(exclude={"decided_at"})):
            raise ValueError("browser_teaching_review_decision_conflict")
        return existing

    async def _proof(
        self, session: AsyncSession, owner: BrowserOwner,
        manifest: BrowserPublicationManifest, draft_digest: str,
        replay_digest: str,
    ) -> TeachingReplayProof | None:
        proof = await self._load_proof(session, owner, replay_digest)
        if (type(proof) is not TeachingReplayProof or proof.digest != replay_digest
                or proof.binding.owner != owner or proof.draft_digest != draft_digest
                or proof.publication_digest != manifest.digest):
            return None
        run = (await session.execute(text(
            "SELECT 1 FROM browser_runs WHERE tenant_id=:tenant AND ai_user_id=:user"
            " AND session_id=:session AND task_id=:task AND run_id=:run"
            " AND binding_id=:binding AND binding_revision=:revision"
            " AND target_system=:target AND publication_digest=:publication"
            " AND status='completed' AND effect='acknowledged'"
            " AND verification='verified' AND error_code IS NULL"
            " AND cancel_requested=FALSE AND cancel_acknowledged=FALSE"
            " AND verification_evidence_digest=:evidence FOR SHARE"
        ), {
            "tenant": owner.tenant_id, "user": owner.user_id,
            "session": owner.session_id, "task": proof.replay_task_id,
            "run": proof.replay_run_id, "binding": proof.binding.binding_id,
            "revision": proof.binding.binding_revision,
            "target": manifest.capability.target_system,
            "publication": bytes.fromhex(manifest.digest),
            "evidence": bytes.fromhex(proof.verifier_evidence_digest),
        })).scalar_one_or_none()
        source = (await session.execute(text(
            "SELECT 1 FROM browser_runs WHERE tenant_id=:tenant AND ai_user_id=:user"
            " AND session_id=:session AND run_id=:run"
            " AND binding_id=:binding AND binding_revision=:revision"
            " AND status='completed' AND effect='acknowledged'"
            " AND verification='verified' AND error_code IS NULL"
            " AND cancel_requested=FALSE AND cancel_acknowledged=FALSE FOR SHARE"
        ), {
            "tenant": owner.tenant_id, "user": owner.user_id,
            "session": owner.session_id, "run": proof.source_run_id,
            "binding": proof.binding.binding_id,
            "revision": proof.binding.binding_revision,
        })).scalar_one_or_none()
        binding = (await session.execute(text(
            "SELECT 1 FROM oa_session_credentials WHERE tenant_id=:tenant"
            " AND ai_user_id=:user AND target_system=:target"
            " AND binding_id=:binding AND binding_revision=:revision"
            " AND binding_state='active' AND revoked_at IS NULL FOR SHARE"
        ), {
            "tenant": owner.tenant_id, "user": owner.user_id,
            "target": manifest.capability.target_system,
            "binding": proof.binding.binding_id,
            "revision": proof.binding.binding_revision,
        })).scalar_one_or_none()
        return proof if run == 1 and source == 1 and binding == 1 else None

    async def _row(
        self, session: AsyncSession, owner: BrowserOwner,
        manifest: BrowserPublicationManifest, draft_digest: str,
        replay_digest: str, approval_ref: str | None,
    ) -> tuple[TeachingReplayProof, RowMapping] | None:
        proof = await self._proof(
            session, owner, manifest, draft_digest, replay_digest,
        )
        if proof is None:
            return None
        params = {
            "tenant": owner.tenant_id, "user": owner.user_id,
            "session": owner.session_id, "task": proof.replay_task_id,
            "action": teaching_review_action_digest(owner, manifest, proof),
        }
        if approval_ref is not None:
            params["approval"] = approval_ref
        query = (
            "SELECT * FROM human_gate_requests WHERE requested_tenant_id=:tenant"
            " AND requested_for_ai_user_id=:user AND requested_session_id=:session"
            " AND task_id=:task AND action_digest=:action"
            " AND decision='confirmed' AND decided_tenant_id=:tenant"
            " AND decided_by_ai_user_id=:user AND decided_session_id=:session"
            " AND requested_at<=decided_at AND decided_at<expires_at"
            " AND expires_at>clock_timestamp()"
        )
        if approval_ref is not None:
            query += " AND request_id=:approval"
        query += " ORDER BY decided_at DESC LIMIT 2 FOR SHARE"
        rows = (await session.execute(text(query), params)).mappings().all()
        if len(rows) != 1:
            return None
        row = rows[0]
        return proof, row

    async def read_fact(
        self, owner: BrowserOwner, manifest: BrowserPublicationManifest,
        draft_digest: str, replay_digest: str,
    ) -> TeachingReviewFact | None:
        async with self._sessions() as session, session.begin():
            current = await self._row(
                session, owner, manifest, draft_digest, replay_digest, None,
            )
            if current is None:
                return None
            _, row = current
            return TeachingReviewFact(
                owner=owner, draft_digest=draft_digest,
                replay_digest=replay_digest, publication_digest=manifest.digest,
                approval_ref=cast(str, row["request_id"]),
                reviewer_ref=cast(str, row["decided_by_ai_user_id"]),
                evidence_digest=_evidence(row), approved=True,
            )

    async def replay_current(
        self, owner: BrowserOwner, manifest: BrowserPublicationManifest,
        draft_digest: str, replay_digest: str,
    ) -> bool:
        async with self._sessions() as session, session.begin():
            return await self._proof(
                session, owner, manifest, draft_digest, replay_digest,
            ) is not None

    async def check_in_session(
        self, session: object, owner: BrowserOwner,
        manifest: BrowserPublicationManifest, *, draft_digest: str,
        replay_digest: str, approval_ref: str, reviewer_ref: str,
        evidence_digest: str,
    ) -> bool:
        if not isinstance(session, AsyncSession):
            return False
        current = await self._row(
            session, owner, manifest, draft_digest, replay_digest, approval_ref,
        )
        if current is None:
            return False
        _, row = current
        return (
            reviewer_ref == row["decided_by_ai_user_id"]
            and evidence_digest == _evidence(row)
        )


def reviewed_publication_authority(
    components: BrowserVerticalComponents, manifest: BrowserPublicationManifest,
    grants: BrowserPublicationGrants, review: PostgreSQLTeachingPublicationReview,
) -> ReviewedPublicationAuthority:
    """Bind T's application contract to the installed PG and HumanGate facts."""
    if (manifest.source_kind != "registered_site"
            or components.publications._review is not review):
        raise ValueError("browser_teaching_publication_installation_invalid")

    async def authorize(owner: BrowserOwner, skill_id: str, operation: str) -> bool:
        if (owner.tenant_id != components._tenant_id
                or skill_id != manifest.skill.skill_id
                or operation not in {"publish", "rollback"}):
            return False
        if await grants.check(owner, skill_id, "activate", manifest.digest) is not True:
            return False
        if operation == "rollback" and await grants.check(
            owner, skill_id, "deactivate", manifest.digest,
        ) is not True:
            return False
        return True

    async def replay_current(
        owner: BrowserOwner, draft: str, replay: str, publication: str,
    ) -> bool:
        return (
            owner.tenant_id == components._tenant_id
            and publication == manifest.digest
            and await review.replay_current(owner, manifest, draft, replay)
        )

    async def review_fact(
        owner: BrowserOwner, draft: str, replay: str,
    ) -> TeachingReviewFact | None:
        if owner.tenant_id != components._tenant_id:
            return None
        return await review.read_fact(owner, manifest, draft, replay)

    return ReviewedPublicationAuthority(
        store=components.publications, authorize=authorize,
        replay_current=replay_current, review_fact=review_fact,
    )
