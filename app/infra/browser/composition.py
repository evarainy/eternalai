"""Explicit, default-off wiring for the single registered READ_ONLY vertical.

Construction performs no database/provider IO, publication or catalog mutation.
Dependencies are trusted server installations, never request DTOs or environment
labels. Persisted keys and real proof implementations are mandatory when enabled.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import TypeAdapter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.browser_skill.chat import BrowserChatService
from app.browser_skill.compiler import (
    initial_validation_scope_digest,
    teaching_grant_scope_digest,
)
from app.browser_skill.models import (
    BrowserOwner,
    LocatorHint,
    OpaqueId,
    ScopeBinding,
    TeachingPlan,
    TeachingPlanStep,
)
from app.browser_skill.publication_contracts import (
    BrowserPublicationManifest,
    BrowserPublicationRecord,
    canonical_json,
)
from app.browser_skill.registry import (
    ReviewedPublicationAuthority,
    canonical_digest,
    publish_reviewed_candidate,
)
from app.browser_skill.repair import (
    FormalLocatorProposal,
    FormalRepairAuthority,
    RepairError,
    _formal_hint,
    propose_formal_locator,
)
from app.browser_skill.run_contracts import BrowserArtifactView
from app.browser_skill.runtime import BrowserReadWorker
from app.browser_skill.submission import BrowserSubmissionService, _validate_schema
from app.browser_skill.teaching import (
    TeachingReplayAuthority,
    TeachingReplayProof,
    replay_draft,
)
from app.browser_skill.trajectory import ParameterApproval
from app.browser_skill.write_workflow import _owner_digest
from app.infra.auth.crypto import PrincipalSessionBinder
from app.infra.browser.local_file_artifacts import (
    FileArtifactOwner,
    LocalFileArtifactError,
    LocalFileArtifacts,
)
from app.infra.browser.read_execution import (
    BrowserReadExecutionFactory,
    BrowserReadLifecycle,
    VerifiedBrowserReadExecution,
)
from app.infra.human_gate.postgresql import PostgreSQLHumanGate
from app.infra.persistence.browser.authorization import (
    CleanupAuthorize,
    CleanupCheck,
    PostgreSQLBrowserBindingReader,
    PostgreSQLBrowserCurrentAuth,
    PostgreSQLBrowserRunAuthority,
    RunProofCheck,
)
from app.infra.persistence.browser.payload_crypto import (
    BrowserPayloadCipher,
    BrowserRunCryptoIdentity,
)
from app.infra.persistence.browser.profiles import PostgreSQLBrowserProfileStore
from app.infra.persistence.browser.publications import PostgreSQLBrowserPublicationStore
from app.infra.persistence.browser.runs import PostgreSQLBrowserRunStore
from app.infra.persistence.capability_registry.repository import PostgreSQLCapabilityRegistry
from app.ports.auth import Principal
from app.ports.browser_chat import (
    BrowserChatBindingResolverPort,
    BrowserChatError,
    BrowserChatInputIdentity,
    BrowserChatParserPort,
)
from app.ports.browser_profile_store import (
    BrowserProfileCaptureProofPort,
    BrowserProfileCleanupAuthorityPort,
    BrowserProfileCleanupProofPort,
)
from app.ports.browser_publication_store import (
    BrowserPublicationError,
    BrowserPublicationReviewPort,
    BrowserPublicationStorePort,
    PublicationOperation,
)
from app.ports.browser_run_store import (
    BrowserRunStoreError,
    BrowserRunStorePort,
    ProtectedRunEnvelope,
    RunAdmission,
    RunSnapshot,
    checked_run_id,
)
from app.ports.capability_registry import CapabilitySpec
from app.ports.human_gate import HumanGateDecisionRecord, HumanGateRequest
from app.ports.policy_guard import PolicyGuardPort
from app.ports.task_store import SessionStorePort
from app.ports.trace import TracePort
from app.version_binding import capability_version_bindings


class BrowserPublicationGrants(Protocol):
    async def check(
        self, owner: BrowserOwner, skill_id: str, operation: PublicationOperation,
        publication_digest: str,
    ) -> bool:
        """Verify current server-bound actor/service grant for this exact tenant.

        Read/execute require a current user/service grant too. Owner strings,
        client flags, a digest and a matching request ContextVar are not grants.
        This implementation must consult trusted current local/DB facts only.
        """
        ...


class BrowserRegisteredSourceVerifier(Protocol):
    async def verify(self, manifest: BrowserPublicationManifest) -> bool:
        """Match the installed source, DOM/projector/verifier and effect evidence.

        Verify all frozen definitions against the actual registered execution
        implementation; a declared source kind/origin or digest alone is no proof.
        Bounded local/DB check only; no provider or model IO in transactions.
        """
        ...


class SingleSeedManifestAuthority:
    """One tenant and one complete immutable audited seed, with live grant checks."""

    def __init__(
        self, *, tenant_id: str, seed: BrowserPublicationManifest,
        grants: BrowserPublicationGrants, source_verifier: BrowserRegisteredSourceVerifier,
    ) -> None:
        TypeAdapter(OpaqueId).validate_python(tenant_id, strict=True)
        self._tenant_id = tenant_id
        self._seed = BrowserPublicationManifest.model_validate_json(seed.model_dump_json())
        if not callable(getattr(grants, "check", None)) or not callable(
            getattr(source_verifier, "verify", None)
        ):
            raise ValueError("browser_publication_authority_required")
        self._grants, self._source = grants, source_verifier

    async def authorize(
        self, owner: BrowserOwner, skill_id: str, operation: PublicationOperation,
    ) -> bool:
        if (owner.tenant_id != self._tenant_id or skill_id != self._seed.skill.skill_id
                or operation not in {"prepare", "activate", "deactivate", "read", "execute",
                                     "teach"}):
            return False
        detached_owner = BrowserOwner(tenant_id=owner.tenant_id, user_id=owner.user_id,
                                      session_id=owner.session_id)
        return await self._grants.check(
            detached_owner, skill_id, operation, self._seed.digest,
        ) is True

    async def authorize_in_session(
        self, session: AsyncSession, owner: BrowserOwner, skill_id: str,
        operation: PublicationOperation,
    ) -> bool:
        if (owner.tenant_id != self._tenant_id or skill_id != self._seed.skill.skill_id
                or operation not in {"prepare", "activate", "deactivate", "read", "execute",
                                     "teach"}):
            return False
        # Explicit optional DB-only adapter; generic grant implementations keep their checks.
        check = getattr(self._grants, "check_in_session", None)
        if not callable(check):
            return await self.authorize(owner, skill_id, operation)
        detached_owner = BrowserOwner(
            tenant_id=owner.tenant_id, user_id=owner.user_id, session_id=owner.session_id,
        )
        return await check(session, detached_owner, skill_id, operation, self._seed.digest) is True

    async def verify_manifest(
        self, owner: BrowserOwner, manifest: BrowserPublicationManifest,
    ) -> bool:
        checked = BrowserPublicationManifest.model_validate_json(manifest.model_dump_json())
        if owner.tenant_id != self._tenant_id or checked != self._seed:
            return False
        return await self._source.verify(checked) is True


class ActiveBrowserPublications(PostgreSQLBrowserPublicationStore):
    """Frozen reads retain history; execution additionally requires its active head."""

    async def assert_current(
        self, owner: BrowserOwner, manifest: BrowserPublicationManifest,
    ) -> None:
        async with self._transaction() as session:
            await self.assert_current_in_session(session, owner, manifest, require_active=True)


class BrowserChatPayloadCipher:
    """Infrastructure conversion keeps durable AEAD types out of Chat ports."""

    def __init__(self, cipher: BrowserPayloadCipher) -> None:
        self._cipher = cipher

    def encrypt_input(
        self, identity: BrowserChatInputIdentity, value: Mapping[str, object],
    ) -> ProtectedRunEnvelope:
        return self._cipher.encrypt_input(BrowserRunCryptoIdentity(
            owner=identity.owner, task_id=identity.task_id, run_id=identity.run_id,
            target_system=identity.target_system, binding_id=identity.binding_id,
            binding_revision=identity.binding_revision,
            auth_fingerprint=identity.auth_fingerprint, auth_expires_at=identity.auth_expires_at,
            publication_digest=identity.publication_digest, input_revision=identity.input_revision,
            input_digest=identity.input_digest,
            auth_evidence_version=identity.auth_evidence_version,
        ), value)

    def decrypt_result(self, run: RunSnapshot) -> dict[str, Any]:
        return self._cipher.decrypt_result(run)


@dataclass(frozen=True, slots=True, repr=False)
class BrowserVerticalDependencies:
    """Trusted explicit installation. Every provider/proof dependency is mandatory.

    Keyrings must be durable, retain historical IDs and be supplied by the existing
    secret authority. No startup key generation, environment loading or rotation.
    Lifecycle cancellation/cleanup callbacks must verify exact provider facts;
    never inject an empty coroutine or infer proof from requested outcome enums.
    """

    session_factory: async_sessionmaker[AsyncSession]
    capability_registry: PostgreSQLCapabilityRegistry
    session_binder: PrincipalSessionBinder
    policy: PolicyGuardPort
    tenant_id: str
    seed: BrowserPublicationManifest
    publication_grants: BrowserPublicationGrants
    source_verifier: BrowserRegisteredSourceVerifier
    payload_keys: Mapping[str, bytes] = field(repr=False)
    active_payload_key_id: str
    request_digest_keys: Mapping[str, bytes] = field(repr=False)
    active_request_digest_key_id: str
    input_digest_key: bytes = field(repr=False)
    result_digest_key: bytes = field(repr=False)
    execution_factory: BrowserReadExecutionFactory
    lifecycle: BrowserReadLifecycle
    profile_capture_proof: BrowserProfileCaptureProofPort
    profile_cleanup_proof: BrowserProfileCleanupProofPort
    profile_cleanup_authority: BrowserProfileCleanupAuthorityPort
    cancel_check: RunProofCheck
    cleanup_authorize: CleanupAuthorize
    cleanup_check: CleanupCheck
    chat_parser: BrowserChatParserPort | None
    chat_bindings: BrowserChatBindingResolverPort
    trace: TracePort
    sessions: SessionStorePort
    worker_id: str
    enabled: bool = False
    worker_ttl_seconds: int = 60
    profile_retention_seconds: int | None = None
    business_ready: Callable[[RunSnapshot], Awaitable[bool]] | None = None
    business_ready_in_session: Callable[
        [AsyncSession, RunSnapshot], Awaitable[bool]
    ] | None = None
    cancel_check_in_session: CleanupAuthorize | None = None
    publication_review: BrowserPublicationReviewPort | None = None
    teaching_base: BrowserPublicationManifest | None = None
    confirmed_key_digest: Callable[[bytes, str], str] | None = None
    file_artifacts: LocalFileArtifacts | None = None
    teaching_review_request: Callable[
        [BrowserOwner, BrowserPublicationManifest, str, str, str],
        Awaitable[HumanGateRequest],
    ] | None = None
    teaching_review_decide: Callable[
        [BrowserOwner, BrowserPublicationManifest, str, str, str,
         Literal["confirmed", "rejected"]],
        Awaitable[HumanGateDecisionRecord],
    ] | None = None
    initial_review_request: Callable[
        [BrowserOwner, BrowserPublicationManifest, str, str, str],
        Awaitable[HumanGateRequest],
    ] | None = None
    initial_review_decide: Callable[
        [BrowserOwner, BrowserPublicationManifest, str, str, str,
         Literal["confirmed", "rejected"]],
        Awaitable[HumanGateDecisionRecord],
    ] | None = None
    locator_repair_hints: Mapping[str, tuple[LocatorHint, ...]] = field(
        default_factory=dict, repr=False,
    )


@dataclass(slots=True, repr=False)
class _OwnerScan:
    """Finite scheduling cycle, never an owner authorization cache."""

    upper: tuple[str, str] | None = None
    after: tuple[str, str] | None = None
    cutoff: datetime | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass(frozen=True, slots=True, repr=False)
class BrowserVerticalComponents:
    """Explicit service references; installing routes/scheduling is the caller's job."""

    publications: ActiveBrowserPublications
    cipher: BrowserPayloadCipher
    current_auth: PostgreSQLBrowserCurrentAuth
    binding_reader: PostgreSQLBrowserBindingReader
    profiles: PostgreSQLBrowserProfileStore
    runs: PostgreSQLBrowserRunStore
    execution: VerifiedBrowserReadExecution
    worker: BrowserReadWorker
    chat: BrowserChatService | None
    submission: BrowserSubmissionService
    _sessions: async_sessionmaker[AsyncSession]
    _tenant_id: str
    _seed: BrowserPublicationManifest
    _owner_scan: _OwnerScan = field(default_factory=_OwnerScan, compare=False)
    _active_owners: set[tuple[str, str, str]] = field(
        default_factory=set, compare=False, repr=False,
    )
    _cleanup_owners: set[tuple[str, str, str]] = field(
        default_factory=set, compare=False, repr=False,
    )
    _start_resources: Callable[[], Awaitable[None]] | None = field(
        default=None, compare=False, repr=False,
    )
    _stop_resources: Callable[[], Awaitable[None]] | None = field(
        default=None, compare=False, repr=False,
    )
    reviewed_publication: ReviewedPublicationAuthority | None = field(
        default=None, compare=False, repr=False,
    )
    _repair_hints: tuple[tuple[str, tuple[LocatorHint, ...]], ...] = field(
        default=(), compare=False, repr=False,
    )

    async def start_resources(self) -> None:
        if self._start_resources is not None:
            await self._start_resources()

    async def stop_resources(self) -> None:
        if self._stop_resources is not None:
            await self._stop_resources()

    async def prepare_seed(self, owner: BrowserOwner) -> BrowserPublicationRecord:
        """Explicit service operation; constructing components never calls this."""
        return await self.publications.prepare(owner, self._seed)

    async def activate_seed(self, owner: BrowserOwner) -> BrowserPublicationRecord:
        """Explicit fixed-digest activation after trusted operator authorization."""
        return await self.publications.activate(
            owner, self._seed.skill.skill_id, self._seed.digest, expected_revision=0,
        )

    async def activate_initial(
        self, owner: BrowserOwner, *, task_id: str, run_id: str,
        approval_ref: str,
    ) -> BrowserPublicationRecord:
        if self._seed.source_kind != "registered_site":
            raise BrowserPublicationError("browser_publication_initial_invalid")
        return await self.publications.activate_initial(
            owner, self._seed, task_id=task_id, run_id=run_id,
            approval_ref=approval_ref,
        )

    async def publish_teaching_candidate(
        self, owner: BrowserOwner, *, draft_digest: str, replay_digest: str,
    ) -> BrowserPublicationRecord:
        authority = self.reviewed_publication
        if authority is None or self._seed.source_kind != "registered_site":
            raise BrowserPublicationError("browser_teaching_publication_unavailable")
        return await publish_reviewed_candidate(
            owner, self._seed, draft_digest=draft_digest,
            replay_digest=replay_digest, authority=authority,
        )

    async def replay_teaching_candidate(
        self, principal: Principal, bound_session: str, *,
        source_task_id: str, source_run_id: str,
        parameter_whitelist: tuple[ParameterApproval, ...],
        heldout_arguments: dict[str, Any], client_request_id: str,
        timeout_seconds: int = 300,
    ) -> TeachingReplayProof:
        """Submit a distinct sealed input through the installed worker and Verifier."""
        if (self._seed.source_kind != "registered_site"
                or type(heldout_arguments) is not dict
                or type(timeout_seconds) is not int
                or not 1 <= timeout_seconds <= 300):
            raise BrowserPublicationError("browser_teaching_replay_unavailable")
        draft, compiler, source_run = await self.submission._compile_teaching_context(
            principal, bound_session, source_task_id, source_run_id,
            parameter_whitelist,
        )
        binding = draft.binding
        owner = binding.owner
        _validate_schema(heldout_arguments, draft.publication.capability.input_schema)
        original = self.cipher.decrypt_input(source_run.admission).get("arguments")
        if (type(original) is not dict or original == heldout_arguments
                or set(heldout_arguments) != set(draft.publication.skill.parameters)):
            raise BrowserPublicationError("browser_teaching_replay_not_heldout")
        ref_payload = canonical_json({
            "owner": [owner.tenant_id, owner.user_id, owner.session_id],
            "binding": [binding.binding_id, binding.binding_revision],
            "publication": draft.publication.digest,
            "arguments": heldout_arguments,
        }).encode("utf-8")
        heldout_ref = hmac.digest(
            self.submission._input_key,
            b"browser-heldout-parameters.v1\x00" + ref_payload,
            hashlib.sha256,
        ).hex()
        deadline = time.monotonic() + timeout_seconds

        async def authorize_replay(candidate: object, scope: ScopeBinding) -> bool:
            if candidate != draft or scope != binding:
                return False
            refreshed, _, current_run = await self.submission._compile_teaching_context(
                principal, bound_session, source_task_id, source_run_id,
                parameter_whitelist,
            )
            return refreshed == draft and current_run == source_run

        async def heldout_set(candidate: object, ref: str, scope: ScopeBinding) -> bool:
            return (candidate == draft and scope == binding and ref == heldout_ref
                    and original != heldout_arguments)

        async def execute_draft(
            candidate: object, ref: str, scope: ScopeBinding,
        ) -> tuple[str, str]:
            if candidate != draft or ref != heldout_ref or scope != binding:
                raise BrowserPublicationError("browser_teaching_replay_denied")
            envelope = await self.submission.submit_teaching(
                principal=principal, bound_session=bound_session,
                skill_id=draft.publication.skill.skill_id,
                skill_version=draft.publication.skill.version,
                arguments=heldout_arguments, client_request_id=client_request_id,
                replay_source=(source_task_id, source_run_id, draft.digest),
            )
            data = envelope.data
            if not isinstance(data, dict):
                raise BrowserPublicationError("browser_teaching_replay_admission_unavailable")
            task_id, run_id = data.get("task_id"), data.get("run_id")
            if type(task_id) is not str or type(run_id) is not str:
                raise BrowserPublicationError("browser_teaching_replay_admission_unavailable")
            while time.monotonic() < deadline:
                current = await self.runs.get(owner, task_id, run_id)
                if current.status in {"completed", "failed", "cancelled"}:
                    return task_id, run_id
                await asyncio.sleep(0.25)
            raise BrowserPublicationError("browser_teaching_replay_timeout")

        async def read_run(
            task_id: str, run_id: str, scope: ScopeBinding,
        ) -> RunSnapshot:
            if scope != binding:
                raise BrowserPublicationError("browser_teaching_replay_denied")
            return await self.runs.get(owner, task_id, run_id)

        async def same_subject(
            first_run_id: str, second_run_id: str, scope: ScopeBinding,
        ) -> bool:
            if scope != binding or first_run_id != source_run_id:
                return False
            source_current = await self.submission._read_teaching_source(
                owner, source_task_id, first_run_id,
            )
            replay_current = await self.runs.get(
                owner, replay_task_id, second_run_id,
            )
            return (
                source_current == source_run
                and replay_current.owner == source_current.owner
                and replay_current.admission.binding_id
                == source_current.admission.binding_id
                and replay_current.admission.binding_revision
                == source_current.admission.binding_revision
                and replay_current.admission.target_system
                == source_current.admission.target_system
            )

        replay_task_id = ""
        async def execute_and_bind(
            candidate: object, ref: str, scope: ScopeBinding,
        ) -> tuple[str, str]:
            nonlocal replay_task_id
            replay_task_id, replay_run_id = await execute_draft(candidate, ref, scope)
            return replay_task_id, replay_run_id

        authority = TeachingReplayAuthority(
            compiler=compiler, current_binding=lambda: binding,
            authorize_replay=authorize_replay, heldout_set=heldout_set,
            execute_draft=execute_and_bind, read_run=read_run,
            same_subject=same_subject, record_proof=self.runs.record_replay_proof,
            deadline_monotonic=deadline, clock=time.monotonic,
        )
        return await replay_draft(
            draft, binding=binding, heldout_parameter_set_ref=heldout_ref,
            authority=authority,
        )

    async def propose_locator_repair(
        self, principal: Principal, bound_session: str, *,
        failed_task_id: str, failed_run_id: str,
        step_id: str, replacement: LocatorHint,
        source_task_id: str | None = None, source_run_id: str | None = None,
        parameter_whitelist: tuple[ParameterApproval, ...] = (),
    ) -> FormalLocatorProposal:
        """Trusted internal entry for one review-only failed Run proposal."""
        if self._seed.source_kind != "registered_site" or not self._repair_hints:
            raise RepairError("repair_formal_unavailable")
        invocation = self.submission._invocation(principal, bound_session)
        owner = invocation.owner
        hints = dict(self._repair_hints).get(step_id, ())
        if not hints:
            raise RepairError("repair_hints_unavailable")
        if source_task_id is None and source_run_id is None:
            if parameter_whitelist:
                raise RepairError("repair_request_invalid")
            failed = await self.runs.get(owner, failed_task_id, failed_run_id)
            evidence = await self.runs.read_failed_step(
                owner, failed_task_id, failed_run_id, step_id,
            )
            current = await self.publications.get_active(
                owner, self._seed.skill.skill_id,
            )
            plan = await self.runs.load_teaching_plan(
                owner, failed_task_id, failed_run_id,
            )
            if (
                current is None or current.manifest != self._seed
                or failed.status != "failed" or failed.cancel_requested
                or failed.effect == "unknown" or failed.verification == "verified"
                or failed.admission.publication_digest.hex() != self._seed.digest
                or plan is not None or evidence is None
            ):
                raise RepairError("repair_failure_scope_mismatch")
            await self.publications.assert_current(owner, self._seed)
            binding = evidence.binding
            authority = FormalRepairAuthority(
                compiler=None, current_binding=lambda: binding,
                failed_step=lambda requested, step, scope: evidence
                if (requested, step, scope) == (failed_run_id, step_id, binding)
                else None,
                approved_hints=lambda publication, step, scope: hints
                if (publication, step, scope)
                == (self._seed, step_id, binding) else (),
                authorize=lambda candidate, scope:
                    candidate == self._seed and scope == binding,
            )
            proposal = propose_formal_locator(
                self._seed, binding=binding, failed_run_id=failed_run_id,
                step_id=step_id, replacement=replacement, authority=authority,
            )
            if (
                await self.runs.get(owner, failed_task_id, failed_run_id) != failed
                or await self.runs.read_failed_step(
                    owner, failed_task_id, failed_run_id, step_id,
                ) != evidence
                or await self.runs.load_teaching_plan(
                    owner, failed_task_id, failed_run_id,
                ) is not None
            ):
                raise RepairError("repair_evidence_stale")
            await self.publications.assert_current(owner, self._seed)
            self.submission._recheck(invocation)
            await self.runs.record_locator_proposal(
                owner, failed_task_id, failed_run_id, proposal,
            )
            return proposal
        if source_task_id is None or source_run_id is None:
            raise RepairError("repair_request_invalid")
        draft, compiler, source_run = await self.submission._compile_teaching_context(
            principal, bound_session, source_task_id, source_run_id,
            parameter_whitelist,
        )
        binding = draft.binding
        owner = binding.owner
        failed = await self.runs.get(owner, failed_task_id, failed_run_id)
        plan = await self.runs.load_teaching_plan(owner, failed_task_id, failed_run_id)
        evidence = await self.runs.read_failed_step(
            owner, failed_task_id, failed_run_id, step_id,
        )
        sealed = self.cipher.decrypt_input(failed.admission)
        source_ref = sealed.get("replay_source")
        step_pairs = tuple(
            (item.step_id, item.primitive_id) for item in draft.publication.site.step_primitives
        )
        if (
            draft.publication != self._seed
            or failed.status != "failed" or failed.cancel_requested
            or failed.effect == "unknown" or failed.verification == "verified"
            or failed.owner != source_run.owner
            or failed.admission.binding_id != binding.binding_id
            or failed.admission.binding_revision != binding.binding_revision
            or failed.admission.target_system != source_run.admission.target_system
            or failed.admission.publication_digest.hex() != draft.publication.digest
            or plan is None or plan.purpose != "teaching"
            or plan.publication_digest != draft.publication.digest
            or plan.source_publication_digest
            != source_run.admission.publication_digest.hex()
            or plan.sealed_input_digest != failed.admission.input_digest.hex()
            or tuple((item.step_id, item.primitive_id) for item in plan.steps)
            != step_pairs
            or source_ref != {
                "task_id": source_task_id, "run_id": source_run_id,
                "draft_digest": draft.digest,
            }
            or evidence is None
        ):
            raise RepairError("repair_failure_scope_mismatch")
        authority = FormalRepairAuthority(
            compiler=compiler, current_binding=lambda: binding,
            failed_step=lambda requested, step, scope: evidence
            if (requested, step, scope) == (failed_run_id, step_id, binding)
            else None,
            approved_hints=lambda publication, step, scope: hints
            if (publication, step, scope)
            == (draft.publication, step_id, binding) else (),
            authorize=lambda candidate, scope: candidate == draft and scope == binding,
        )
        proposal = propose_formal_locator(
            draft, binding=binding, failed_run_id=failed_run_id,
            step_id=step_id, replacement=replacement, authority=authority,
        )
        refreshed, _, current_source = await self.submission._compile_teaching_context(
            principal, bound_session, source_task_id, source_run_id,
            parameter_whitelist,
        )
        if (
            refreshed != draft or current_source != source_run
            or await self.runs.get(owner, failed_task_id, failed_run_id) != failed
            or await self.runs.load_teaching_plan(owner, failed_task_id, failed_run_id)
            != plan
            or await self.runs.read_failed_step(
                owner, failed_task_id, failed_run_id, step_id,
            ) != evidence
        ):
            raise RepairError("repair_evidence_stale")
        self.submission._recheck(invocation)
        await self.runs.record_locator_proposal(
            owner, failed_task_id, failed_run_id, proposal,
        )
        return proposal

    async def read_locator_repair_proposals(
        self, principal: Principal, bound_session: str, *,
        task_id: str, run_id: str,
    ) -> tuple[FormalLocatorProposal, ...]:
        """Internal owner-scoped review read; proposals never activate a Skill."""
        invocation = self.submission._invocation(principal, bound_session)
        run = await self.runs.get(invocation.owner, task_id, run_id)
        if run.admission.publication_digest.hex() != self._seed.digest:
            raise RepairError("repair_failure_scope_mismatch")
        await self.publications.assert_current(invocation.owner, self._seed)
        proposals = await self.runs.read_locator_proposals(
            invocation.owner, task_id, run_id,
        )
        await self.publications.assert_current(invocation.owner, self._seed)
        self.submission._recheck(invocation)
        return proposals

    async def _journal_published_locator_proposals(self, run: RunSnapshot) -> None:
        """Worker continuation from a terminal failed Run, with no model or DOM IO."""
        if (
            run.status != "failed" or run.effect == "unknown"
            or run.cancel_requested or not self._repair_hints
            or run.admission.publication_digest.hex() != self._seed.digest
            or await self.runs.load_teaching_plan(
                run.owner, run.task_id, run.run_id,
            ) is not None
        ):
            return
        current = await self.publications.get_active(
            run.owner, self._seed.skill.skill_id,
        )
        if current is None or current.manifest != self._seed:
            return
        await self.publications.assert_current(run.owner, self._seed)
        for step_id, hints in self._repair_hints:
            evidence = await self.runs.read_failed_step(
                run.owner, run.task_id, run.run_id, step_id,
            )
            if evidence is None:
                continue
            binding = evidence.binding
            authority = FormalRepairAuthority(
                compiler=None, current_binding=lambda: binding,
                failed_step=lambda requested, step, scope: evidence
                if (requested, step, scope) == (run.run_id, step_id, binding)
                else None,
                approved_hints=lambda publication, step, scope: hints
                if (publication, step, scope)
                == (self._seed, step_id, binding) else (),
                authorize=lambda candidate, scope:
                    candidate == self._seed and scope == binding,
            )
            step = next(item for item in self._seed.skill.steps
                        if item.step_id == step_id)
            for hint in hints:
                if (hint == step.locator or not any(
                    item.replacement == hint for item in evidence.observations
                )):
                    continue
                proposal = propose_formal_locator(
                    self._seed, binding=binding, failed_run_id=run.run_id,
                    step_id=step_id, replacement=hint, authority=authority,
                )
                await self.runs.record_locator_proposal(
                    run.owner, run.task_id, run.run_id, proposal,
                )
        await self.publications.assert_current(run.owner, self._seed)

    async def run_business_ready(self) -> tuple[RunSnapshot, ...]:
        # One business lane for the installed single-binding factory. Busy owners
        # can be skipped through the existing finite discovery cycle.
        return await self.run_ready(maximum_owners=1, include_cleanup=False, maximum_runs=1)

    async def run_ready(
        self, *, maximum_owners: int = 16, include_cleanup: bool = True,
        maximum_runs: int | None = None,
    ) -> tuple[RunSnapshot, ...]:
        """One bounded supervisor pass over durable owner identities, no implicit loop.

        The database supplies owners, never a client owner list. Worker claim/current
        checks still verify each exact protected Run and its current authorization.
        Closing this discovery transaction precedes all worker/provider operations.
        """
        if (type(maximum_owners) is not int or not 1 <= maximum_owners <= 64
                or type(include_cleanup) is not bool
                or (maximum_runs is not None and (
                    type(maximum_runs) is not int or not 1 <= maximum_runs <= maximum_owners
                ))):
            raise ValueError("browser_worker_batch_invalid")
        eligible = (
            "tenant_id=:tenant AND publication_digest=:publication"
            " AND status IN ('running','waiting_user')"
            " AND (worker_deadline IS NULL OR worker_deadline<=clock_timestamp())"
            " AND created_at<=:cycle_time"
        )
        params: dict[str, object] = {
            "tenant": self._tenant_id, "publication": bytes.fromhex(self._seed.digest),
            "limit": maximum_owners,
        }
        scan = self._owner_scan
        # Serialize only discovery/cursor movement, never provider execution. The
        # upper key is fixed for the cycle so arriving owners cannot defer wrap.
        async with scan.lock, self._sessions() as session:
            if scan.upper is None:
                scan.cutoff = (await session.execute(text("SELECT clock_timestamp()"))).scalar_one()
                params["cycle_time"] = scan.cutoff
                last = (await session.execute(text(
                    "SELECT ai_user_id,session_id FROM browser_runs WHERE " + eligible
                    + " GROUP BY ai_user_id,session_id"
                    " ORDER BY ai_user_id DESC,session_id DESC LIMIT 1",
                ), params)).mappings().one_or_none()
                if last is not None:
                    scan.upper = (last["ai_user_id"], last["session_id"])
            params["cycle_time"] = scan.cutoff
            rows = []
            if scan.upper is not None:
                params.update(upper_user=scan.upper[0], upper_session=scan.upper[1])
                after_clause = ""
                if scan.after is not None:
                    params.update(after_user=scan.after[0], after_session=scan.after[1])
                    after_clause = " AND (ai_user_id,session_id)>(:after_user,:after_session)"
                rows = list((await session.execute(text(
                    "SELECT tenant_id,ai_user_id,session_id"
                    " FROM browser_runs WHERE " + eligible
                    + " AND (ai_user_id,session_id)<=(:upper_user,:upper_session)" + after_clause
                    + " GROUP BY tenant_id,ai_user_id,session_id"
                    " ORDER BY ai_user_id,session_id LIMIT :limit",
                ), params)).mappings().all())
                if rows:
                    scan.after = (rows[-1]["ai_user_id"], rows[-1]["session_id"])
                if not rows or scan.after == scan.upper:
                    scan.after = scan.upper = None
                    scan.cutoff = None
        completed: list[RunSnapshot] = []
        failed_owners = 0
        for row in rows:
            owner = BrowserOwner(tenant_id=row["tenant_id"], user_id=row["ai_user_id"],
                                 session_id=row["session_id"])
            owner_key = (owner.tenant_id, owner.user_id, owner.session_id)
            if owner_key in self._active_owners or owner_key in self._cleanup_owners:
                continue
            try:
                # Readiness is checked on each exact claim candidate in the
                # store; one recovering Run cannot exempt its owner's queue.
                self._active_owners.add(owner_key)
                try:
                    result = await self.worker.run_next(owner)
                finally:
                    self._active_owners.discard(owner_key)
            except Exception:
                # One revoked/stale owner must not block unrelated authorized
                # owners. Report the pass failure without retaining identity,
                # exception details, protected payloads or claiming success.
                failed_owners += 1
                continue
            if result is not None:
                try:
                    await self._journal_published_locator_proposals(result)
                except Exception:
                    failed_owners += 1
                    continue
                completed.append(result)
                if maximum_runs is not None and len(completed) >= maximum_runs:
                    break
        if include_cleanup:
            try:
                await self.cleanup_ready(maximum_owners=maximum_owners)
            except Exception:
                failed_owners += 1
        if failed_owners:
            raise BrowserRunStoreError("browser_worker_pass_failed")
        return tuple(completed)

    async def cleanup_ready(self, *, maximum_owners: int = 16) -> None:
        if type(maximum_owners) is not int or not 1 <= maximum_owners <= 64:
            raise ValueError("browser_worker_batch_invalid")
        failed_owners = 0
        # Historical cleanup has independent service authority. A revoked or
        # expired business session must not prevent exact resource reconciliation.
        async with self._sessions() as session:
            cleanup_rows = (await session.execute(text(
                "SELECT br.tenant_id,br.ai_user_id,br.session_id,br.task_id,br.run_id"
                " FROM browser_runs br WHERE br.tenant_id=:tenant"
                " AND br.publication_digest=:publication"
                " AND br.cleanup NOT IN ('released','terminated')"
                " AND (br.status IN ('completed','failed','cancelled') OR"
                " ((br.worker_deadline IS NULL OR br.worker_deadline<=clock_timestamp())"
                " AND (br.auth_expires_at<=clock_timestamp() OR EXISTS"
                " (SELECT 1 FROM auth_session_revocations rv"
                " WHERE rv.token_fingerprint=br.auth_fingerprint) OR NOT EXISTS"
                " (SELECT 1 FROM oa_session_credentials c WHERE c.tenant_id=br.tenant_id"
                " AND c.ai_user_id=br.ai_user_id AND c.target_system=br.target_system"
                " AND c.binding_id=br.binding_id AND c.binding_revision=br.binding_revision"
                " AND c.binding_state='active' AND c.revoked_at IS NULL))))"
                " ORDER BY br.updated_at,br.run_id LIMIT :limit"
            ), {"tenant": self._tenant_id, "publication": bytes.fromhex(self._seed.digest),
                "limit": maximum_owners})).mappings().all()
        for row in cleanup_rows:
            owner = BrowserOwner(tenant_id=row["tenant_id"], user_id=row["ai_user_id"],
                                 session_id=row["session_id"])
            owner_key = (owner.tenant_id, owner.user_id, owner.session_id)
            if owner_key in self._active_owners or owner_key in self._cleanup_owners:
                continue
            self._cleanup_owners.add(owner_key)
            try:
                historical = await self.runs.get_for_cleanup(owner, row["task_id"], row["run_id"])
                await self.worker.cleanup(historical)
            except Exception:
                failed_owners += 1
            finally:
                self._cleanup_owners.discard(owner_key)
        if failed_owners:
            raise BrowserRunStoreError("browser_worker_pass_failed")


def _validate_dependencies(deps: BrowserVerticalDependencies) -> None:
    if (not isinstance(deps.capability_registry, PostgreSQLCapabilityRegistry)
            or deps.capability_registry._session_factory is not deps.session_factory
            or not isinstance(deps.session_binder, PrincipalSessionBinder)):
        raise ValueError("browser_vertical_authority_configuration_invalid")
    TypeAdapter(OpaqueId).validate_python(deps.tenant_id, strict=True)
    for key in (deps.input_digest_key, deps.result_digest_key):
        if type(key) is not bytes or len(key) != 32:
            raise ValueError("browser_vertical_digest_key_invalid")
    required = (
        (deps.publication_grants, "check"), (deps.source_verifier, "verify"),
        (deps.policy, "decide"), (deps.execution_factory, "open"),
        (deps.lifecycle, "lookup_capture"), (deps.lifecycle, "send_capture"),
        (deps.lifecycle, "stop"), (deps.lifecycle, "cleanup"),
        (deps.profile_capture_proof, "verify_capture"),
        (deps.profile_capture_proof, "check_live_subject"),
        (deps.profile_cleanup_proof, "verify_cleanup"),
        (deps.profile_cleanup_authority, "check_cleanup"),
        (deps.chat_bindings, "resolve"),
        (deps.sessions, "get_session"), (deps.sessions, "create_session"),
    )
    if (any(not callable(getattr(dependency, method, None)) for dependency, method in required)
            or (deps.chat_parser is not None
                and not callable(getattr(deps.chat_parser, "parse", None)))
            or not all(callable(callback) for callback in (
                deps.cancel_check, deps.cleanup_authorize, deps.cleanup_check,
            )) or deps.trace is None):
        raise ValueError("browser_vertical_trusted_dependency_required")
    if (deps.seed.source_kind == "registered_site"
            and not callable(getattr(deps.publication_review, "check_in_session", None))):
        raise ValueError("browser_publication_review_authority_required")
    if (deps.seed.effect == "controlled_write"
            and (deps.seed.source_kind != "registered_site"
                 or not callable(deps.confirmed_key_digest))):
        raise ValueError("browser_write_confirmation_authority_required")
    if (deps.teaching_base is not None
            and (deps.seed.source_kind != "registered_site"
                 or deps.seed.effect != "read_only"
                 or deps.teaching_base.source_kind != "registered_site"
                 or deps.teaching_base.effect != "read_only"
                 or deps.teaching_base.digest == deps.seed.digest
                 or not callable(getattr(deps.publication_grants,
                                         "check_in_session", None)))):
        raise ValueError("browser_teaching_installation_invalid")
    hints = deps.locator_repair_hints
    steps = {item.step_id for item in deps.seed.skill.steps}
    if (
        not isinstance(hints, Mapping) or len(hints) > len(steps)
        or (bool(hints) and deps.seed.source_kind != "registered_site")
    ):
        raise ValueError("browser_repair_installation_invalid")
    for step_id, values in hints.items():
        if (
            type(step_id) is not str or step_id not in steps
            or type(values) is not tuple or not 1 <= len(values) <= 32
            or any(not isinstance(value, LocatorHint) for value in values)
            or len(set(values)) != len(values)
        ):
            raise ValueError("browser_repair_installation_invalid")
        original = next(item for item in deps.seed.skill.steps
                        if item.step_id == step_id)
        if all(value == original.locator for value in values):
            raise ValueError("browser_repair_installation_invalid")
        try:
            for value in values:
                _formal_hint(
                    value, deps.seed,
                    deps.seed.site.read_rule.key_ref.name
                    in deps.seed.skill.parameters,
                )
        except RepairError:
            raise ValueError("browser_repair_installation_invalid") from None


def build_browser_vertical(
    dependencies: BrowserVerticalDependencies | None = None,
) -> BrowserVerticalComponents | None:
    """Return configured services only after explicit enablement; default is absent.

    Does not register/advertise a Capability, prepare/activate a seed, launch a
    worker or contact any provider. Missing real dependencies fail construction.
    """
    if dependencies is None:
        return None
    if type(dependencies.enabled) is not bool:
        raise ValueError("browser_vertical_enablement_invalid")
    if not dependencies.enabled:
        return None
    deps = dependencies
    _validate_dependencies(deps)
    seed = BrowserPublicationManifest.model_validate_json(deps.seed.model_dump_json())
    authority = SingleSeedManifestAuthority(
        tenant_id=deps.tenant_id, seed=seed, grants=deps.publication_grants,
        source_verifier=deps.source_verifier,
    )
    async def locked_capability(session: AsyncSession, capability_id: str) -> CapabilitySpec | None:
        return await deps.capability_registry._get_in_session(
            session, capability_id, for_share=True,
        )

    publications = ActiveBrowserPublications(
        deps.session_factory, deps.capability_registry, authority,
        review_authority=deps.publication_review,
        authorize_in_session=authority.authorize_in_session,
        capability_in_session=locked_capability,
    )
    cipher = BrowserPayloadCipher(dict(deps.payload_keys), active_key_id=deps.active_payload_key_id)

    async def publication_check(
        owner: BrowserOwner, digest: bytes, capability_id: str, require_active: bool,
    ) -> CapabilitySpec:
        if (type(digest) is not bytes or len(digest) != 32
                or not hmac.compare_digest(digest, bytes.fromhex(seed.digest))
                or capability_id != seed.capability.capability_id
                or type(require_active) is not bool):
            raise BrowserPublicationError("browser_publication_reference_invalid")
        if require_active:
            await publications.assert_current(owner, seed)
        else:
            historical = await publications.get_frozen(owner, seed.digest)
            if historical is None or historical.manifest != seed:
                raise BrowserPublicationError("browser_publication_reference_invalid")
        current = await deps.capability_registry.get(capability_id)
        if (current is None or current.status != "active"
                or canonical_json(current.model_dump(mode="json")) != seed.capability_snapshot_json
                or capability_version_bindings(current) != seed.capability_bindings):
            raise BrowserPublicationError("browser_publication_capability_changed")
        # CurrentAuth evaluates current roles and Policy with protected arguments
        # immediately after this check; the snapshot itself never grants access.
        return current

    async def publication_check_in_session(
        session: AsyncSession, owner: BrowserOwner, digest: bytes,
        capability_id: str, require_active: bool,
    ) -> CapabilitySpec:
        if (type(digest) is not bytes or len(digest) != 32
                or not hmac.compare_digest(digest, bytes.fromhex(seed.digest))
                or capability_id != seed.capability.capability_id
                or type(require_active) is not bool):
            raise BrowserPublicationError("browser_publication_reference_invalid")
        return await publications.assert_current_in_session(
            session, owner, seed, require_active=require_active,
            operation="execute" if require_active else "read",
        )

    current_auth = PostgreSQLBrowserCurrentAuth(
        session_factory=deps.session_factory, cipher=cipher, session_binder=deps.session_binder,
        policy=deps.policy, publication_check=publication_check,
        publication_check_in_session=publication_check_in_session,
    )
    binding_reader = PostgreSQLBrowserBindingReader(deps.session_factory)
    profiles = PostgreSQLBrowserProfileStore(
        session_factory=deps.session_factory, session_binder=deps.session_binder,
        current_auth=current_auth, binding_reader=binding_reader,
        capture_proof=deps.profile_capture_proof, cleanup_proof=deps.profile_cleanup_proof,
        cleanup_authority=deps.profile_cleanup_authority,
        retention_seconds=deps.profile_retention_seconds,
    )
    async def record_read_diagnostic(run: RunSnapshot, attributes: dict[str, str | int]) -> None:
        # Use the already persisted task trace, without decoding protected input
        # or adding owner/subject values to diagnostic attributes.
        async with deps.session_factory() as session:
            trace_id = (await session.execute(text(
                "SELECT trace_id FROM tasks WHERE task_id=:task AND tenant_id=:tenant"
                " AND ai_user_id=:user AND session_id=:session"
            ), {"task": run.task_id, "tenant": run.owner.tenant_id,
                "user": run.owner.user_id, "session": run.owner.session_id})).scalar_one()
        if type(trace_id) is not str or not trace_id:
            raise ValueError("browser_read_diagnostic_trace_unavailable")
        succeeded = (attributes.get("browser_read_outcome") == "verified"
                     or attributes.get("browser_worker_outcome") == "completed")
        skipped = (attributes.get("browser_read_outcome") in {"deferred", "cancelled"}
                   or attributes.get("browser_worker_outcome") in {"deferred", "cancelled"})
        await deps.trace.record_step(
            trace_id, run.task_id, run.owner.session_id,
            tenant_id=run.owner.tenant_id, ai_user_id=run.owner.user_id,
            event_type=(
                "adapter_called" if succeeded or skipped else "adapter_error"
            ),
            status="ok" if succeeded else "skipped" if skipped else "failed",
            attributes=attributes,
        )

    execution = VerifiedBrowserReadExecution(
        deps.execution_factory, deps.lifecycle, publications, cipher,
        result_digest_key=deps.result_digest_key, record_diagnostic=record_read_diagnostic,
        repair_hints=deps.locator_repair_hints,
    )
    run_authority = PostgreSQLBrowserRunAuthority(
        current_auth=current_auth, verification_check=execution.check_verified_candidate,
        cancel_check=deps.cancel_check, cleanup_authorize=deps.cleanup_authorize,
        cancel_check_in_session=deps.cancel_check_in_session,
        cleanup_check=deps.cleanup_check,
    )
    async def claim_ready_in_session(session: AsyncSession, run: RunSnapshot) -> bool:
        if (seed.effect == "controlled_write"
                and run.admission.publication_digest.hex() == seed.digest
                and run.phase == "queued"
                and not await runs.write_confirmation_ready_in_session(session, run)):
            return False
        return (deps.business_ready_in_session is None
                or await deps.business_ready_in_session(session, run))

    runs = PostgreSQLBrowserRunStore(
        session_factory=deps.session_factory, authority=run_authority,
        digest_keys=dict(deps.request_digest_keys),
        active_digest_key_id=deps.active_request_digest_key_id,
        claim_ready=deps.business_ready,
        claim_ready_in_session=claim_ready_in_session,
    )

    async def teaching_publication_check(
        session: AsyncSession, admission: RunAdmission,
        plan: TeachingPlan, require_execution: bool,
    ) -> CapabilitySpec:
        owner = admission.owner
        initial = plan.purpose == "initial_validation"
        bindings = tuple(
            (item.step_id, item.primitive_id) for item in seed.site.step_primitives
        ) if not initial else tuple(
            (item.step.step_id, item.step.step_id) for item in seed.site.steps
        )
        if (
            seed.source_kind != "registered_site"
            or (seed.effect != "read_only" and not (
                initial and seed.effect == "controlled_write"
            ))
            or owner.tenant_id != deps.tenant_id
            or admission.publication_digest.hex() != seed.digest
            or plan.task_id != admission.task_id
            or plan.run_id != admission.run_id
            or plan.owner_digest != _owner_digest(owner)
            or plan.binding_id != admission.binding_id
            or plan.binding_revision != admission.binding_revision
            or plan.sealed_input_digest != admission.input_digest.hex()
            or plan.publication_digest != seed.digest
            or plan.skill_digest != seed.skill.digest
            or plan.site_digest != seed.site.digest
            or plan.policy_digest != seed.site.policy.digest
            or plan.verifier_digest != seed.verifier.digest
            or (not initial and not seed.site.primitive_rules)
            or len(plan.steps) != len(seed.skill.steps)
            or tuple(item.step_id for item in plan.steps)
            != tuple(item.step_id for item in seed.skill.steps)
            or tuple((item.step_id, item.primitive_id) for item in plan.steps)
            != bindings
            or (initial and (
                deps.teaching_base is not None
                or seed.rollback_source_digest is not None
                or plan.source_publication_digest != seed.digest
            ))
        ):
            raise BrowserPublicationError("browser_teaching_candidate_denied")
        rows = (await session.execute(text(
            "SELECT publication_digest,state,manifest FROM browser_publications"
            " WHERE tenant_id=:tenant AND publication_digest IN (:candidate,:base)"
            " FOR SHARE"
        ), {
            "tenant": owner.tenant_id,
            "candidate": bytes.fromhex(seed.digest),
            "base": bytes.fromhex(plan.source_publication_digest),
        })).mappings().all()
        by_digest = {bytes(row["publication_digest"]).hex(): row for row in rows}
        candidate_row = by_digest.get(seed.digest)
        base_row = by_digest.get(plan.source_publication_digest)
        if (
            candidate_row is None or base_row is None
            or candidate_row["state"] not in {"prepared", "active", "inactive"}
            or (require_execution and candidate_row["state"] == "inactive")
            or (initial and require_execution and candidate_row["state"] != "prepared")
            or (not initial and candidate_row["state"] == "prepared"
                and base_row["state"] != "active")
            or base_row["state"] not in (
                {"prepared", "active", "inactive"} if initial else {"active", "inactive"}
            )
        ):
            raise BrowserPublicationError("browser_teaching_candidate_denied")
        try:
            candidate = BrowserPublicationManifest.model_validate(candidate_row["manifest"])
            base = BrowserPublicationManifest.model_validate(base_row["manifest"])
        except ValueError:
            raise BrowserPublicationError("browser_teaching_candidate_denied") from None
        if (
            candidate != seed or base.digest != plan.source_publication_digest
            or base.source_kind != "registered_site"
            or (base.effect != "read_only" and not (
                initial and base.effect == "controlled_write"
            ))
            or (initial and base != seed)
            or (not initial and deps.teaching_base is not None
                and base != deps.teaching_base)
            or seed.site.primitive_rules != base.site.primitive_rules
            or base.site.read_rule != seed.site.read_rule
            or seed.site.source != base.site.source
            or plan.grant_digest != (
                initial_validation_scope_digest(owner, seed) if initial
                else teaching_grant_scope_digest(owner, base, seed)
            )
        ):
            raise BrowserPublicationError("browser_teaching_candidate_denied")
        if initial and require_execution and (await session.execute(text(
            "SELECT 1 FROM browser_publications WHERE tenant_id=:tenant"
            " AND skill_id=:skill AND state='active' FOR SHARE"
        ), {"tenant": owner.tenant_id,
            "skill": seed.skill.skill_id})).scalar_one_or_none() is not None:
            raise BrowserPublicationError("browser_initial_publication_exists")
        catalog = {rule.step.step_id: rule for rule in (
            base.site.primitive_rules if not initial else base.site.steps
        )}
        for index, item in enumerate(plan.steps):
            primitive = catalog.get(item.primitive_id)
            if (
                primitive is None
                or primitive.step.effect != primitive.effect.actual_effect
                or (primitive.step.effect != "read_only" and not (
                    initial and seed.effect == "controlled_write"
                    and primitive.step.effect == "may_write"
                ))
                or item.rule_digest != canonical_digest(
                    "browser_site_step.v1", primitive.model_dump(mode="json"),
                )
                or seed.site.steps[index] != primitive.model_copy(update={
                    "step": primitive.step.model_copy(update={"step_id": item.step_id}),
                })
            ):
                raise BrowserPublicationError("browser_teaching_primitive_denied")
        if not await authority.authorize_in_session(
            session, owner, seed.skill.skill_id,
            "execute" if initial else "teach",
        ):
            raise BrowserPublicationError("browser_teaching_grant_denied")
        check_in_session = getattr(deps.publication_grants, "check_in_session", None)
        if not callable(check_in_session):
            raise BrowserPublicationError("browser_teaching_grant_denied")
        base_granted = await check_in_session(
            session, owner, base.skill.skill_id, "execute", base.digest,
        )
        if base_granted is not True or await deps.source_verifier.verify(seed) is not True:
            raise BrowserPublicationError("browser_teaching_grant_denied")
        current = await locked_capability(session, seed.capability.capability_id)
        if (
            current is None or current.status != "active"
            or current.type != ("action" if seed.effect == "controlled_write" else "query")
            or canonical_json(current.model_dump(mode="json"))
            != seed.capability_snapshot_json
            or capability_version_bindings(current) != seed.capability_bindings
        ):
            raise BrowserPublicationError("browser_publication_capability_changed")
        return current

    current_auth.install_teaching_authority(
        teaching_publication_check, runs.load_teaching_plan_in_session,
    )

    async def resolve_teaching_publication(
        run: RunSnapshot,
    ) -> BrowserPublicationRecord | None:
        if (seed.source_kind != "registered_site"
                or seed.effect not in {"read_only", "controlled_write"}
                or run.admission.publication_digest.hex() != seed.digest):
            return None
        async with deps.session_factory() as session, session.begin():
            plan = await runs.load_teaching_plan_in_session(session, run.admission)
            if plan is None:
                return None
            await current_auth.validate(session, run.admission, teaching_plan=plan)
            row = (await session.execute(text(
                "SELECT state,activation_revision,manifest FROM browser_publications"
                " WHERE tenant_id=:tenant AND publication_digest=:digest FOR SHARE"
            ), {"tenant": run.owner.tenant_id,
                "digest": run.admission.publication_digest})).mappings().one_or_none()
            if row is None:
                raise BrowserPublicationError("browser_teaching_candidate_unavailable")
            record = BrowserPublicationRecord.model_validate(dict(row))
            if (record.manifest != seed or record.state == "inactive"):
                raise BrowserPublicationError("browser_teaching_candidate_unavailable")
            return record

    async def current_teaching_publication(
        run: RunSnapshot, manifest: BrowserPublicationManifest,
    ) -> bool:
        if (seed.source_kind != "registered_site"
                or seed.effect not in {"read_only", "controlled_write"}
                or run.admission.publication_digest.hex() != seed.digest):
            return False
        async with deps.session_factory() as session, session.begin():
            plan = await runs.load_teaching_plan_in_session(session, run.admission)
            if plan is None:
                return False
            if manifest != seed:
                raise BrowserPublicationError("browser_teaching_candidate_unavailable")
            await current_auth.validate(session, run.admission, teaching_plan=plan)
            return True

    execution.install_teaching_publication(
        resolve_teaching_publication, current_teaching_publication,
    )
    worker = BrowserReadWorker(
        runs, execution, profiles, worker_id=deps.worker_id, enabled=True,
        ttl_seconds=deps.worker_ttl_seconds,
        record_diagnostic=record_read_diagnostic,
    )
    def file_owner(run: RunSnapshot) -> FileArtifactOwner:
        admission = run.admission
        return FileArtifactOwner(
            tenant_id=run.owner.tenant_id, ai_user_id=run.owner.user_id,
            session_id=run.owner.session_id, target_system=admission.target_system,
            binding_id=admission.binding_id,
            binding_revision=admission.binding_revision,
            task_id=run.task_id, run_id=run.run_id,
        )

    async def list_file_views(run: RunSnapshot) -> tuple[BrowserArtifactView, ...]:
        artifacts = deps.file_artifacts
        if artifacts is None:
            return ()
        records = await artifacts.list_artifacts(file_owner(run), limit=64)
        return tuple(BrowserArtifactView(
            artifact_id=item.reference, kind="download", media_type=item.media_type,
            size_bytes=item.size_bytes, expires_at=item.expires_at,
            availability="available",
        ) for item in records)

    async def read_file(run: RunSnapshot, reference: str) -> tuple[str, bytes]:
        artifacts = deps.file_artifacts
        if artifacts is None:
            raise BrowserPublicationError("browser_file_unavailable")
        owner = file_owner(run)
        try:
            metadata = await artifacts._authority.authorize_read(reference, owner)
            payload = await artifacts.read(reference, owner)
        except LocalFileArtifactError as error:
            if error.code in {
                "browser_file_reference_invalid", "browser_file_access_denied",
                "browser_file_source_invalid",
            }:
                raise BrowserChatError("browser_file_unavailable", http_status=404) from None
            raise
        return metadata.media_type, payload

    async def prepared_teaching_publication(
        owner: BrowserOwner, skill_id: str, skill_version: str,
    ) -> BrowserPublicationRecord | None:
        if ((deps.teaching_base is None and (
                seed.source_kind != "registered_site"
                or seed.effect not in {"read_only", "controlled_write"}
                or seed.rollback_source_digest is not None))
                or owner.tenant_id != deps.tenant_id
                or skill_id != seed.skill.skill_id
                or skill_version != seed.skill.version):
            return None
        async with deps.session_factory() as session, session.begin():
            row = (await session.execute(text(
                "SELECT state,activation_revision,manifest FROM browser_publications"
                " WHERE tenant_id=:tenant AND publication_digest=:digest FOR SHARE"
            ), {"tenant": owner.tenant_id,
                "digest": bytes.fromhex(seed.digest)})).mappings().one_or_none()
            if row is None:
                return None
            record = BrowserPublicationRecord.model_validate(dict(row))
            return record if record.state == "prepared" and record.manifest == seed else None

    async def build_teaching_plan(
        admission: RunAdmission, manifest: BrowserPublicationManifest,
    ) -> TeachingPlan:
        base = deps.teaching_base
        if (base is None or manifest != seed
                or admission.publication_digest.hex() != seed.digest):
            raise BrowserPublicationError("browser_teaching_installation_invalid")
        catalog = {rule.step.step_id: rule for rule in base.site.primitive_rules}
        try:
            steps = tuple(TeachingPlanStep(
                step_id=item.step_id, primitive_id=item.primitive_id,
                rule_digest=canonical_digest(
                    "browser_site_step.v1",
                    catalog[item.primitive_id].model_dump(mode="json"),
                ),
            ) for item in seed.site.step_primitives)
        except KeyError:
            raise BrowserPublicationError("browser_teaching_primitive_denied") from None
        plan = TeachingPlan(
            task_id=admission.task_id, run_id=admission.run_id,
            owner_digest=_owner_digest(admission.owner),
            binding_id=admission.binding_id,
            binding_revision=admission.binding_revision,
            source_publication_digest=base.digest,
            publication_digest=seed.digest,
            skill_digest=seed.skill.digest,
            site_digest=seed.site.digest,
            policy_digest=seed.site.policy.digest,
            verifier_digest=seed.verifier.digest,
            grant_digest=teaching_grant_scope_digest(admission.owner, base, seed),
            sealed_input_digest=admission.input_digest.hex(),
            parameter_set_ref=admission.input_digest.hex(),
            steps=steps,
        )
        async with deps.session_factory() as session, session.begin():
            await teaching_publication_check(session, admission, plan, True)
        return plan

    async def build_initial_validation_plan(
        admission: RunAdmission, manifest: BrowserPublicationManifest,
    ) -> TeachingPlan:
        if (deps.teaching_base is not None or manifest != seed
                or seed.source_kind != "registered_site"
                or seed.effect not in {"read_only", "controlled_write"}
                or seed.rollback_source_digest is not None
                or admission.publication_digest.hex() != seed.digest):
            raise BrowserPublicationError("browser_initial_validation_unavailable")
        plan = TeachingPlan(
            purpose="initial_validation",
            task_id=admission.task_id, run_id=admission.run_id,
            owner_digest=_owner_digest(admission.owner),
            binding_id=admission.binding_id,
            binding_revision=admission.binding_revision,
            source_publication_digest=seed.digest,
            publication_digest=seed.digest,
            skill_digest=seed.skill.digest,
            site_digest=seed.site.digest,
            policy_digest=seed.site.policy.digest,
            verifier_digest=seed.verifier.digest,
            grant_digest=initial_validation_scope_digest(admission.owner, seed),
            sealed_input_digest=admission.input_digest.hex(),
            parameter_set_ref=admission.input_digest.hex(),
            steps=tuple(TeachingPlanStep(
                step_id=rule.step.step_id,
                primitive_id=rule.step.step_id,
                rule_digest=canonical_digest(
                    "browser_site_step.v1", rule.model_dump(mode="json"),
                ),
            ) for rule in seed.site.steps),
        )
        async with deps.session_factory() as session, session.begin():
            await teaching_publication_check(session, admission, plan, True)
        return plan

    async def teaching_capture_grant(owner: BrowserOwner, digest: str) -> bool:
        base = deps.teaching_base
        if (base is None or digest != seed.digest or owner.tenant_id != deps.tenant_id):
            return False
        return (await deps.publication_grants.check(
            owner, seed.skill.skill_id, "teach", seed.digest,
        ) is True and await deps.publication_grants.check(
            owner, base.skill.skill_id, "teach", base.digest,
        ) is True)

    # An exploration source has a different digest from the prepared candidate.
    # Its read authority is separately bound to the one configured base manifest;
    # the candidate's execution authority never accepts that digest.
    base_publications: ActiveBrowserPublications | None = None
    base_runs: PostgreSQLBrowserRunStore | None = None
    if deps.teaching_base is not None:
        base_manifest = BrowserPublicationManifest.model_validate_json(
            deps.teaching_base.model_dump_json(),
        )
        base_authority = SingleSeedManifestAuthority(
            tenant_id=deps.tenant_id, seed=base_manifest,
            grants=deps.publication_grants, source_verifier=deps.source_verifier,
        )
        base_publications = ActiveBrowserPublications(
            deps.session_factory, deps.capability_registry, base_authority,
            authorize_in_session=base_authority.authorize_in_session,
            capability_in_session=locked_capability,
        )
        source_publications = base_publications

        async def check_source_publication(
            session: AsyncSession, owner: BrowserOwner, digest: bytes,
            capability_id: str, require_active: bool,
        ) -> CapabilitySpec:
            if (require_active or digest != bytes.fromhex(base_manifest.digest)
                    or capability_id != base_manifest.capability.capability_id):
                raise BrowserPublicationError("browser_teaching_source_denied")
            return await source_publications.assert_current_in_session(
                session, owner, base_manifest, require_active=False, operation="read",
            )

        async def check_source_publication_outside(
            owner: BrowserOwner, digest: bytes, capability_id: str, require_active: bool,
        ) -> CapabilitySpec:
            async with deps.session_factory() as session, session.begin():
                return await check_source_publication(
                    session, owner, digest, capability_id, require_active,
                )

        source_auth = PostgreSQLBrowserCurrentAuth(
            session_factory=deps.session_factory, cipher=cipher,
            session_binder=deps.session_binder, policy=deps.policy,
            publication_check=check_source_publication_outside,
            publication_check_in_session=check_source_publication,
        )
        base_runs = PostgreSQLBrowserRunStore(
            session_factory=deps.session_factory,
            authority=PostgreSQLBrowserRunAuthority(
                current_auth=source_auth, verification_check=execution.check_verified_candidate,
                cancel_check=deps.cancel_check, cleanup_authorize=deps.cleanup_authorize,
                cancel_check_in_session=deps.cancel_check_in_session,
                cleanup_check=deps.cleanup_check,
            ),
            digest_keys=dict(deps.request_digest_keys),
            active_digest_key_id=deps.active_request_digest_key_id,
        )

        async def authorize_replay_source(session: AsyncSession, run: RunSnapshot) -> None:
            if run.owner.tenant_id != deps.tenant_id:
                raise BrowserPublicationError("browser_teaching_source_denied")
            digest = run.admission.publication_digest.hex()
            if digest == seed.digest:
                await runs._authority.check_run(session, run, "read")
            elif digest == base_manifest.digest:
                await source_auth.validate(session, run.admission, require_active=False)
            else:
                raise BrowserPublicationError("browser_teaching_source_denied")

        runs.install_replay_source_reader(authorize_replay_source)

    async def teaching_capture_stores(
        owner: BrowserOwner, task_id: str, run_id: str,
    ) -> tuple[BrowserRunStorePort, BrowserPublicationStorePort]:
        source_store, source_store_publications = base_runs, base_publications
        if (owner.tenant_id != deps.tenant_id or source_store is None
                or source_store_publications is None or deps.teaching_base is None):
            raise BrowserPublicationError("browser_teaching_source_denied")
        checked_run_id(task_id)
        checked_run_id(run_id)
        async with deps.session_factory() as session:
            digest = (await session.execute(text(
                "SELECT publication_digest FROM browser_runs WHERE tenant_id=:tenant"
                " AND ai_user_id=:user AND session_id=:session"
                " AND task_id=:task AND run_id=:run",
            ), {"tenant": owner.tenant_id, "user": owner.user_id,
                "session": owner.session_id, "task": task_id,
                "run": run_id})).scalar_one_or_none()
        if digest is None:
            raise BrowserPublicationError("browser_teaching_source_denied")
        if bytes(digest).hex() == seed.digest:
            return runs, source_store_publications
        if bytes(digest).hex() == deps.teaching_base.digest:
            return source_store, source_store_publications
        raise BrowserPublicationError("browser_teaching_source_denied")

    async def teaching_published_routes(
        owner: BrowserOwner, skill_id: str,
    ) -> frozenset[tuple[str, ...]]:
        if (owner.tenant_id != deps.tenant_id or skill_id != seed.skill.skill_id):
            raise BrowserPublicationError("browser_teaching_catalog_denied")
        async with deps.session_factory() as session, session.begin():
            rows = (await session.execute(text(
                "SELECT publication_digest,manifest FROM browser_publications"
                " WHERE tenant_id=:tenant AND skill_id=:skill"
                " AND state IN ('active','inactive') FOR SHARE"
            ), {"tenant": owner.tenant_id, "skill": skill_id})).mappings().all()
            routes: set[tuple[str, ...]] = set()
            for row in rows:
                manifest = BrowserPublicationManifest.model_validate(row["manifest"])
                if (manifest.skill.skill_id != skill_id
                        or manifest.digest != bytes(row["publication_digest"]).hex()):
                    raise BrowserPublicationError("browser_teaching_catalog_invalid")
                routes.add(tuple(
                    item.primitive_id for item in manifest.site.step_primitives
                ) if manifest.site.step_primitives else tuple(
                    item.step_id for item in manifest.skill.steps
                ))
            return frozenset(routes)

    submission = BrowserSubmissionService(
        runs, publications, deps.chat_bindings, BrowserChatPayloadCipher(cipher),
        deps.session_binder.bind, deps.trace, sessions=deps.sessions,
        skill_id=seed.skill.skill_id, input_digest_key=deps.input_digest_key, enabled=True,
        write_gate=(PostgreSQLHumanGate(deps.session_factory)
                    if seed.effect == "controlled_write" else None),
        confirmed_key_digest=deps.confirmed_key_digest,
        list_artifacts=list_file_views if deps.file_artifacts is not None else None,
        read_artifact=read_file if deps.file_artifacts is not None else None,
        teaching_base_digest=(deps.teaching_base.digest
                              if deps.teaching_base is not None else None),
        teaching_candidate_digest=(seed.digest if seed.source_kind == "registered_site"
                                   else None),
        teaching_candidate_version=(seed.skill.version
                                    if seed.source_kind == "registered_site" else None),
        teaching_publication=(prepared_teaching_publication
                              if seed.source_kind == "registered_site" else None),
        teaching_plan=(build_teaching_plan if deps.teaching_base is not None
                       else build_initial_validation_plan
                       if seed.source_kind == "registered_site" else None),
        teaching_capture_grant=(teaching_capture_grant
                                 if deps.teaching_base is not None else None),
        teaching_capture_stores=(teaching_capture_stores
                                 if deps.teaching_base is not None else None),
        teaching_published_routes=(teaching_published_routes
                                   if deps.teaching_base is not None else None),
        teaching_review_request=deps.teaching_review_request,
        teaching_review_decide=deps.teaching_review_decide,
        initial_review_request=deps.initial_review_request,
        initial_review_decide=deps.initial_review_decide,
    )
    chat = (
        BrowserChatService(submission, deps.chat_parser)
        if deps.chat_parser is not None else None
    )
    return BrowserVerticalComponents(
        publications, cipher, current_auth, binding_reader, profiles, runs, execution,
        worker, chat, submission, deps.session_factory, deps.tenant_id, seed,
        _repair_hints=tuple(
            (step_id, tuple(LocatorHint.model_validate_json(value.model_dump_json())
                            for value in values))
            for step_id, values in deps.locator_repair_hints.items()
        ),
    )
