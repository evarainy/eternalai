"""Synthetic new-primitive teaching contract; written for the later test window."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast

import pytest

from app.browser_skill.compiler import (
    DurableTeachingCompilerAuthority,
    DurableTeachingDraft,
    TeachingError,
    compile_authorized_run,
)
from app.browser_skill.models import (
    BrowserOwner,
    Coverage,
    LocatorHint,
    ModelManifest,
    ObservationRequest,
    ScopeBinding,
    SkillStep,
    TargetRef,
)
from app.browser_skill.publication_contracts import (
    BrowserPublicationManifest,
    BrowserPublicationRecord,
    canonical_json,
    publication_digest,
    rollback_version_manifest,
)
from app.browser_skill.registry import (
    PublicationError,
    ReviewedPublicationAuthority,
    TeachingReviewFact,
    canonical_digest,
    publish_reviewed_candidate,
    rollback_reviewed_publication,
)
from app.browser_skill.repair import (
    ExplorationFeedback,
    ExplorationRegionFeedback,
    FormalFailedStep,
    FormalRepairAuthority,
    FormalRepairObservation,
    ReadOnlyExplorationAuthority,
    RepairError,
    explore_registered_reads,
    propose_formal_locator,
)
from app.browser_skill.site_rules import EffectFact, SiteStepRule
from app.browser_skill.teaching import (
    ReplayError,
    TeachingReplayAuthority,
    TeachingReplayProof,
    replay_draft,
)
from app.browser_skill.trajectory import (
    BindingRevisions,
    ParameterApproval,
    RunTeachingAuthority,
    RunTeachingStep,
    TrajectoryError,
    TrajectoryEvent,
    _route_teaching_plan,
    capture_authorized_run,
)
from app.infra.browser.composition import BrowserVerticalComponents
from app.infra.browser.fixed_synthetic_seed import build_fixed_synthetic_query_source
from app.ports.auth import Principal
from app.ports.browser_publication_store import BrowserPublicationStorePort
from app.ports.browser_run_store import (
    ProtectedRunEnvelope,
    RunAdmission,
    RunEffect,
    RunSnapshot,
)
from tests.browser_skill.factories import binding, scope
from tests.infra.browser.test_local_read_execution import registered_site


def _base() -> BrowserPublicationManifest:
    model = ModelManifest(
        request_model="synthetic_model", deployment_model="synthetic_pinned_v1",
        manifest_digest=hashlib.sha256(b"synthetic teaching").hexdigest(),
    )
    return build_fixed_synthetic_query_source(model).manifest


def _run(publication_digest: str, *, task_id: str = "task_source",
         run_id: str = "run_source", effect: RunEffect = "acknowledged") -> RunSnapshot:
    owner = binding().owner
    expiry = datetime.now(UTC) + timedelta(minutes=5)
    admission = RunAdmission(
        owner=owner, task_id=task_id, run_id=run_id, target_system="oa",
        binding_id=binding().binding_id, binding_revision=binding().binding_revision,
        auth_fingerprint=b"a" * 32, auth_expires_at=expiry,
        publication_digest=bytes.fromhex(publication_digest),
        input_revision=1, input_digest=b"i" * 32,
        protected_input=ProtectedRunEnvelope(
            "aes256gcm-browser-input-v1", "synthetic", b"n" * 12, b"c" * 16,
        ),
    )
    return RunSnapshot(
        admission=admission, state_revision=9, status="completed", phase=None,
        worker_id="synthetic", worker_epoch=1, worker_deadline=None,
        provider_key=None, provider_manifest_digest=None, lease_epoch=None,
        profile_generation_id=None, capture_operation_id=None,
        capture_status="not_requested", cancel_requested=False,
        cancel_acknowledged=False, effect=effect, verification="verified",
        verification_evidence_digest=b"v" * 32, cleanup="released",
        error_code=None, dispatch_failure_code=None, terminal_revision=9,
        terminal_event_id="terminal", result_digest=None, protected_result=None,
    )


class SyntheticRunTeaching:
    """Fake authoritative callbacks. This is not real PG/Executor evidence."""

    def __init__(self, *, base: BrowserPublicationManifest | None = None) -> None:
        self.base = _base() if base is None else base
        first = self.base.site.steps[0]
        second = SiteStepRule(
            step=SkillStep(
                step_id="inspect_collection", operation="click", effect="read_only",
                locator=LocatorHint(kind="test_id", value="fixture_detail"),
            ),
            observation=ObservationRequest(region_id="fixture_messages"),
            target_criteria=("Synthetic messages",),
            effect=EffectFact(
                operation="click", actual_effect="read_only",
                proof_ref="synthetic_click_read_only",
                evidence_digest=hashlib.sha256(b"synthetic click proof").hexdigest(),
            ),
        )
        self.rules = {first.step.step_id: first, second.step.step_id: second}
        if self.base.source_kind == "registered_site":
            data = self.base.model_dump(mode="json")
            data["site"]["primitive_rules"] = [
                rule.model_dump(mode="json") for rule in self.rules.values()
            ]
            data["site"]["digest"] = publication_digest(
                "browser_site.publication.v1", data["site"],
            )
            data["skill"]["site_digest"] = data["site"]["digest"]
            data["skill"]["digest"] = publication_digest("browser_skill.v1", data["skill"])
            data["digest"] = publication_digest("browser_publication.durable.v1", data)
            self.base = BrowserPublicationManifest.model_validate_json(canonical_json(data))
        self.source_run = _run(self.base.digest)
        stamp = scope().model_copy(update={"region_id": "fixture_messages"})
        target = TargetRef(target_id="synthetic_target", candidate_epoch=1, scope=stamp)
        self.steps = tuple(
            RunTeachingStep(
                run_id="run_source", skill_version=self.base.skill.version,
                skill_digest=self.base.skill.digest,
                binding=BindingRevisions.from_binding(binding()),
                primitive_id=rule.step.step_id,
                event=TrajectoryEvent(
                    sequence=index, event_id=f"event_{index}",
                    step_id=rule.step.step_id, scope=stamp, target=target,
                    operation=rule.step.operation, effect="read_only",
                    effect_proof=rule.effect,
                    policy_digest=self.base.site.policy.digest,
                    approved_labels=("Synthetic messages",),
                ),
                rule_digest=canonical_digest(
                    "browser_site_step.v1", rule.model_dump(mode="json"),
                ),
                dispatch_state="acknowledged",
                dispatch_evidence_digest=hashlib.sha256(
                    f"synthetic dispatch {index}".encode()
                ).hexdigest(),
            )
            for index, rule in enumerate((first, second), start=1)
        )
        self.allowed = True

    def run_authority(self) -> RunTeachingAuthority:
        return RunTeachingAuthority(
            current_binding=binding,
            read_run=lambda _id, _binding: self.source_run,
            read_steps=lambda _id, _binding: self.steps,
            read_parameter_set_ref=lambda _id, _binding: "training_set",
            frozen_publication=lambda _digest, _binding: self.base,
            primitive_rule=lambda primitive_id, _base: self.rules.get(primitive_id),
            authorize_capture=lambda _id, _binding: self.allowed,
        )

    def compiler_authority(self) -> DurableTeachingCompilerAuthority:
        return DurableTeachingCompilerAuthority(
            run=self.run_authority(),
            authorize_target=lambda _binding, _base, skill_id:
                self.allowed and skill_id == "synthetic_new_flow",
            unpublished_combination=lambda _binding, _skill, steps:
                self.allowed and steps == ("read_collection", "inspect_collection"),
        )

    def draft(self) -> DurableTeachingDraft:
        source = capture_authorized_run(
            "run_source", binding=binding(), authority=self.run_authority(),
        )
        return compile_authorized_run(
            source, binding=binding(), target_skill_id="synthetic_new_flow",
            version="v1", site_version="v2",
            parameter_whitelist=(ParameterApproval(
                name="business_key", purpose="business_key", format="opaque_id",
            ),), authority=self.compiler_authority(),
        )


def test_new_registered_combination_compiles_without_a_published_full_flow() -> None:
    sample = SyntheticRunTeaching()
    draft = sample.draft()
    assert tuple(step.operation for step in draft.publication.skill.steps) == (
        "read", "click",
    )
    assert draft.publication.site.digest != sample.base.site.digest
    assert draft.publication.verifier == sample.base.verifier
    assert draft.publication.output == sample.base.output
    assert draft.publication.skill.skill_id == "synthetic_new_flow"
    assert draft.publication.site_plan().steps[1].step.step_id != "inspect_collection"


def test_missing_current_grant_unknown_effect_and_old_flow_fail_closed() -> None:
    sample = SyntheticRunTeaching()
    sample.allowed = False
    with pytest.raises(TrajectoryError, match="teaching_run_denied"):
        capture_authorized_run(
            "run_source", binding=binding(), authority=sample.run_authority(),
        )
    sample.allowed = True
    sample.source_run = replace(sample.source_run, effect="unknown")
    with pytest.raises(TrajectoryError, match="teaching_run_not_verified"):
        capture_authorized_run(
            "run_source", binding=binding(), authority=sample.run_authority(),
        )
    sample.source_run = _run(sample.base.digest)
    sample.steps = sample.steps[:1]
    source = capture_authorized_run(
        "run_source", binding=binding(), authority=sample.run_authority(),
    )
    with pytest.raises(TeachingError, match="teaching_flow_already_published"):
        compile_authorized_run(
            source, binding=binding(), target_skill_id="synthetic_new_flow",
            version="v1", site_version="v2",
            parameter_whitelist=(ParameterApproval(
                name="business_key", purpose="business_key", format="opaque_id",
            ),), authority=sample.compiler_authority(),
        )


def test_heldout_replay_requires_same_subject_and_real_verified_run_fact() -> None:
    sample = SyntheticRunTeaching()
    draft = sample.draft()
    replay_run = _run(
        draft.publication.digest, task_id="task_replay", run_id="run_replay",
    )
    stored: list[TeachingReplayProof] = []

    async def yes(*_args: object) -> bool:
        return True

    async def execute(*_args: object) -> tuple[str, str]:
        return "task_replay", "run_replay"

    async def read(*_args: object) -> RunSnapshot:
        return replay_run

    async def record(proof: TeachingReplayProof) -> bool:
        stored.append(proof)
        return True

    authority = TeachingReplayAuthority(
        compiler=sample.compiler_authority(), current_binding=binding,
        authorize_replay=yes, heldout_set=yes, execute_draft=execute,
        read_run=read, same_subject=yes, record_proof=record,
        deadline_monotonic=10, clock=lambda: 0,
    )
    with pytest.raises(ReplayError, match="teaching_replay_not_heldout"):
        asyncio.run(replay_draft(
            draft, binding=binding(), heldout_parameter_set_ref="training_set",
            authority=authority,
        ))
    proof = asyncio.run(replay_draft(
        draft, binding=binding(), heldout_parameter_set_ref="holdout_set",
        authority=authority,
    ))
    assert stored == [proof]
    assert proof.verifier_evidence_digest == (b"v" * 32).hex()
    replay_run = replace(replay_run, verification="mismatch")
    with pytest.raises(ReplayError, match="teaching_replay_unverified"):
        asyncio.run(replay_draft(
            draft, binding=binding(), heldout_parameter_set_ref="second_holdout",
            authority=authority,
        ))


def test_reviewed_publication_reads_approval_and_switches_pg_head() -> None:
    sample = SyntheticRunTeaching()
    draft = sample.draft()
    owner = binding().owner
    replay_digest = "b" * 64
    prepared: BrowserPublicationRecord | None = None
    active: BrowserPublicationRecord | None = None

    class Store:
        async def prepare(
            self, who: BrowserOwner, manifest: BrowserPublicationManifest,
        ) -> BrowserPublicationRecord:
            nonlocal prepared
            assert who == owner and manifest == draft.publication
            prepared = BrowserPublicationRecord(
                manifest=manifest, state="prepared", activation_revision=0,
            )
            return prepared

        async def get_frozen(
            self, _who: BrowserOwner, _digest: str,
        ) -> BrowserPublicationRecord | None:
            return active

        async def get_active(
            self, _who: BrowserOwner, _skill: str,
        ) -> BrowserPublicationRecord | None:
            return active

        async def activate_reviewed(
            self, who: BrowserOwner, manifest: BrowserPublicationManifest, *,
            draft_digest: str, replay_digest: str, approval_ref: str,
            reviewer_ref: str, evidence_digest: str,
            expected_active_digest: str | None = None,
        ) -> BrowserPublicationRecord:
            nonlocal active
            assert prepared is not None and who == owner
            assert expected_active_digest is None
            assert (draft_digest, replay_digest, approval_ref, reviewer_ref,
                    evidence_digest) == (draft.digest, "b" * 64, "approval",
                                         "reviewer", "c" * 64)
            active = BrowserPublicationRecord(
                manifest=manifest, state="active", activation_revision=1,
            )
            return active

    async def yes(*_args: object) -> bool:
        return True

    async def review(*_args: object) -> TeachingReviewFact:
        return TeachingReviewFact(
            owner, draft.digest, replay_digest, draft.publication.digest,
            "approval", "reviewer", "c" * 64, True,
        )

    authority = ReviewedPublicationAuthority(
        store=cast(BrowserPublicationStorePort, Store()), authorize=yes, replay_current=yes,
        review_fact=review,
    )
    result = asyncio.run(publish_reviewed_candidate(
        owner, draft.publication, draft_digest=draft.digest,
        replay_digest=replay_digest, authority=authority,
    ))
    assert result == active
    assert result is not None and result.manifest == draft.publication

    async def no_review(*_args: object) -> None:
        return None

    denied = replace(authority, review_fact=no_review)
    with pytest.raises(PublicationError, match="teaching_review_required"):
        asyncio.run(publish_reviewed_candidate(
            owner, draft.publication, draft_digest=draft.digest,
            replay_digest=replay_digest, authority=denied,
        ))


def test_rollback_publishes_new_version_with_expected_head() -> None:
    source = registered_site().manifest
    current = rollback_version_manifest(
        source, skill_version="current_v2", site_version="current_site_v2",
    )
    expected = rollback_version_manifest(
        source, skill_version="restored_v3", site_version="restored_site_v3",
    )
    owner = binding().owner
    head = BrowserPublicationRecord(
        manifest=current, state="active", activation_revision=1,
    )
    old = BrowserPublicationRecord(
        manifest=source, state="inactive", activation_revision=2,
    )
    calls: list[tuple[str, str]] = []

    class Store:
        async def get_frozen(
            self, _who: BrowserOwner, digest: str,
        ) -> BrowserPublicationRecord | None:
            return old if digest == source.digest else head if digest == expected.digest else None

        async def get_active(
            self, _who: BrowserOwner, _skill: str,
        ) -> BrowserPublicationRecord:
            return head

        async def prepare(
            self, _who: BrowserOwner, manifest: BrowserPublicationManifest,
        ) -> BrowserPublicationRecord:
            assert manifest == expected
            calls.append(("prepare", manifest.digest))
            return BrowserPublicationRecord(
                manifest=manifest, state="prepared", activation_revision=0,
            )

        async def activate_reviewed(
            self, _who: BrowserOwner, manifest: BrowserPublicationManifest, *,
            draft_digest: str, replay_digest: str, approval_ref: str,
            reviewer_ref: str, evidence_digest: str,
            expected_active_digest: str | None = None,
        ) -> BrowserPublicationRecord:
            nonlocal head
            assert manifest == expected
            assert expected_active_digest == current.digest
            assert (draft_digest, replay_digest, approval_ref,
                    reviewer_ref, evidence_digest) == (
                "d" * 64, "b" * 64, "approval", "reviewer", "e" * 64,
            )
            calls.append(("activate", manifest.digest))
            head = BrowserPublicationRecord(
                manifest=manifest, state="active", activation_revision=1,
            )
            return head

    async def yes(*_args: object) -> bool:
        return True

    async def review(*_args: object) -> TeachingReviewFact:
        return TeachingReviewFact(
            owner, "d" * 64, "b" * 64, expected.digest,
            "approval", "reviewer", "e" * 64, True,
        )

    authority = ReviewedPublicationAuthority(
        store=cast(BrowserPublicationStorePort, Store()), authorize=yes,
        replay_current=yes, review_fact=review,
    )
    result = asyncio.run(rollback_reviewed_publication(
        owner, source.digest, skill_version="restored_v3",
        site_version="restored_site_v3", draft_digest="d" * 64,
        replay_digest="b" * 64, authority=authority,
    ))
    assert result.manifest == expected
    assert result.manifest.rollback_source_digest == source.digest
    assert calls == [("prepare", expected.digest), ("activate", expected.digest)]


def test_exploration_suggestions_are_registered_read_only_and_bounded() -> None:
    sample = SyntheticRunTeaching()
    emitted: list[str] = []

    async def yes(*_args: object) -> bool:
        return True

    async def suggest(
        _run: str, prior: tuple[str, ...], feedback: ExplorationFeedback,
    ) -> str | None:
        assert feedback.regions[0].region_id == "fixture_messages"
        return "inspect_collection" if not prior else None

    async def observe(
        _run: str, _binding: ScopeBinding, _base: BrowserPublicationManifest,
        prior: tuple[RunTeachingStep, ...],
    ) -> ExplorationFeedback:
        last = prior[-1] if prior else None
        return ExplorationFeedback(
            regions=(ExplorationRegionFeedback(
                region_id="fixture_messages",
                coverage=Coverage(state="complete", reason="complete"),
                candidate_count=1, actionable_count=1,
                roles=("button",), approved_names=("Synthetic messages",),
            ),),
            last_primitive_id=last.primitive_id if last else None,
            last_region_id=last.event.scope.region_id if last else None,
            last_dispatch_state=last.dispatch_state if last else None,
        )

    async def dispatch(
        _run: str, primitive: str, _binding: ScopeBinding,
    ) -> RunTeachingStep:
        assert primitive == "inspect_collection"
        emitted.append(primitive)
        return sample.steps[1]

    authority = ReadOnlyExplorationAuthority(
        current_binding=binding, authorize=yes,
        registered_rule=lambda primitive, _base: sample.rules.get(primitive),
        suggest_next=suggest, observe_current=observe, execute_step=dispatch,
        clock=lambda: 0, deadline_monotonic=10,
        maximum_steps=2, allowed_regions=("fixture_messages",),
    )
    result = asyncio.run(explore_registered_reads(
        "run_source", binding=binding(), publication=sample.base,
        authority=authority,
    ))
    assert emitted == ["inspect_collection"]
    assert result == (sample.steps[1],)
    sample.rules["inspect_collection"] = sample.rules["inspect_collection"].model_copy(
        update={"effect": EffectFact(
            operation="click", actual_effect="unknown",
            proof_ref="unknown", evidence_digest="d" * 64,
        )}
    )
    with pytest.raises(RepairError, match="exploration_effect_denied"):
        asyncio.run(explore_registered_reads(
            "run_source", binding=binding(), publication=sample.base,
            authority=authority,
        ))


def test_teaching_route_accepts_terminal_first_page_and_repeated_advance() -> None:
    expected = ("open", "advance", "read")
    assert _route_teaching_plan(
        expected, ("open", "read"), advance_id="advance",
        max_pages=3, terminal_after=1,
    ) == (0, 2)
    assert _route_teaching_plan(
        expected, ("open", "advance", "advance", "read"),
        advance_id="advance", max_pages=3, terminal_after=3,
    ) == (0, 1, 1, 2)
    with pytest.raises(TrajectoryError, match="teaching_plan_sequence_mismatch"):
        _route_teaching_plan(
            expected, ("open", "read"), advance_id="advance",
            max_pages=3, terminal_after=None,
        )
    with pytest.raises(TrajectoryError, match="teaching_plan_sequence_mismatch"):
        _route_teaching_plan(
            expected, ("open", "advance", "read"),
            advance_id="advance", max_pages=3, terminal_after=1,
        )


def test_formal_repair_only_proposes_a_locator_for_proven_not_sent_failure() -> None:
    sample = SyntheticRunTeaching(base=registered_site().manifest)
    draft = sample.draft()
    assert draft.publication.source_kind == "registered_site"
    step = draft.publication.skill.steps[0]
    failed_binding = ScopeBinding(
        owner=binding().owner, binding_id=binding().binding_id,
        binding_revision=binding().binding_revision,
        authorization_run_id="run_failed", evidence_version="verified-session-v1",
        lease_epoch=4,
    )
    alternative = LocatorHint(kind="test_id", value="fixture_row_alternate")
    observed = FormalRepairObservation(
        alternative,
        scope().model_copy(update={
            "region_id": draft.publication.site.steps[0].observation.region_id,
        }),
        "d" * 64,
    )
    failure = FormalFailedStep(
        failed_binding, "run_failed", draft.publication.digest, step.step_id,
        "locator_not_found", "not_sent", "e" * 64, (observed,),
    )
    holder: dict[str, FormalFailedStep | None] = {"failure": failure}
    authority = FormalRepairAuthority(
        compiler=sample.compiler_authority(), current_binding=binding,
        failed_step=lambda _run, _step, _binding: holder["failure"],
        approved_hints=lambda _manifest, _step, _binding: (alternative,),
        authorize=lambda candidate, who: candidate == draft and who == binding(),
    )
    proposal = propose_formal_locator(
        draft, binding=binding(), failed_run_id="run_failed",
        step_id=step.step_id, replacement=alternative, authority=authority,
    )
    assert proposal.replacement == alternative
    assert proposal.failure_evidence_digest == failure.evidence_digest
    assert proposal.observation_evidence_digest == observed.digest
    assert proposal.primitive_id == "read_collection"
    assert draft.publication.skill.steps[0] == step
    holder["failure"] = FormalFailedStep(
        binding(), "run_failed", draft.publication.digest, step.step_id,
        "locator_not_found", "not_sent", "e" * 64,
    )
    with pytest.raises(RepairError, match="repair_failure_scope_mismatch"):
        propose_formal_locator(
            draft, binding=binding(), failed_run_id="run_failed",
            step_id=step.step_id, replacement=alternative, authority=authority,
        )
    holder["failure"] = None
    with pytest.raises(RepairError, match="repair_failure_unavailable"):
        propose_formal_locator(
            draft, binding=binding(), failed_run_id="run_failed",
            step_id=step.step_id, replacement=alternative, authority=authority,
        )


def test_formal_repair_rejects_a_synthetic_teaching_draft() -> None:
    sample = SyntheticRunTeaching()
    draft = sample.draft()
    assert draft.publication.source_kind == "registered_synthetic_fixture"
    alternative = LocatorHint(kind="test_id", value="fixture_row_alternate")
    authority = FormalRepairAuthority(
        compiler=sample.compiler_authority(), current_binding=binding,
        failed_step=lambda _run, _step, _binding: None,
        approved_hints=lambda _manifest, _step, _binding: (alternative,),
        authorize=lambda candidate, who: candidate == draft and who == binding(),
    )
    with pytest.raises(RepairError, match="repair_denied"):
        propose_formal_locator(
            draft, binding=binding(), failed_run_id="run_failed",
            step_id=draft.publication.skill.steps[0].step_id,
            replacement=alternative, authority=authority,
        )


def test_published_registered_run_uses_the_same_review_only_repair_contract() -> None:
    publication = registered_site().manifest
    step = publication.skill.steps[0]
    failed_binding = ScopeBinding(
        owner=binding().owner, binding_id=binding().binding_id,
        binding_revision=binding().binding_revision,
        authorization_run_id="run_published_failed",
        evidence_version="verified-session-v1", lease_epoch=5,
    )
    failure = FormalFailedStep(
        failed_binding, "run_published_failed", publication.digest,
        step.step_id, "target_stale", "not_sent", "e" * 64,
        (FormalRepairObservation(
            LocatorHint(kind="test_id", value="fixture_row_alternate"),
            scope().model_copy(update={
                "region_id": publication.site.steps[0].observation.region_id,
            }),
            "d" * 64,
        ),),
    )
    hint = LocatorHint(kind="test_id", value="fixture_row_alternate")
    authority = FormalRepairAuthority(
        compiler=None, current_binding=lambda: failed_binding,
        failed_step=lambda _run, _step, _binding: failure,
        approved_hints=lambda _manifest, _step, _binding: (hint,),
        authorize=lambda candidate, scope:
            candidate == publication and scope == failed_binding,
    )
    proposal = propose_formal_locator(
        publication, binding=failed_binding,
        failed_run_id="run_published_failed", step_id=step.step_id,
        replacement=hint, authority=authority,
    )
    assert proposal.source_kind == "published_skill"
    assert proposal.publication_digest == publication.digest
    assert proposal.failure_evidence_digest == failure.evidence_digest
    assert proposal.observation_evidence_digest == failure.observations[0].digest
    assert proposal.original_step_digest == canonical_digest(
        "browser_skill_step.v1", step.model_dump(mode="json"),
    )
    assert publication.skill.steps[0] == step
    unobserved = replace(failure, observations=())
    without_probe = replace(authority, failed_step=lambda _run, _step, _binding: unobserved)
    with pytest.raises(RepairError, match="repair_observation_unavailable"):
        propose_formal_locator(
            publication, binding=failed_binding,
            failed_run_id="run_published_failed", step_id=step.step_id,
            replacement=hint, authority=without_probe,
        )


def test_installed_published_repair_entry_requires_the_failed_run_audit() -> None:
    async def scenario() -> None:
        publication = registered_site().manifest
        step = publication.skill.steps[0]
        owner = binding().owner
        failed = replace(
            _run(publication.digest, run_id="run_published_failed"),
            status="failed", verification=None,
            verification_evidence_digest=None,
            error_code="browser_verification_failed", lease_epoch=5,
        )
        failed_binding = ScopeBinding(
            owner=owner, binding_id=binding().binding_id,
            binding_revision=binding().binding_revision,
            authorization_run_id=failed.run_id,
            evidence_version="verified-session-v1", lease_epoch=5,
        )
        evidence = FormalFailedStep(
            failed_binding, failed.run_id, publication.digest, step.step_id,
            "locator_not_found", "not_sent", "e" * 64,
            (FormalRepairObservation(
                LocatorHint(kind="test_id", value="fixture_row_alternate"),
                scope().model_copy(update={
                    "region_id": publication.site.steps[0].observation.region_id,
                }),
                "d" * 64,
            ),),
        )
        hint = LocatorHint(kind="test_id", value="fixture_row_alternate")

        class Runs:
            failure: FormalFailedStep | None = evidence
            recorded: list[object] = []

            async def get(self, requested_owner, task_id, run_id):
                assert (requested_owner, task_id, run_id) == (
                    owner, failed.task_id, failed.run_id,
                )
                return failed

            async def read_failed_step(self, requested_owner, task_id, run_id, step_id):
                assert (requested_owner, task_id, run_id, step_id) == (
                    owner, failed.task_id, failed.run_id, step.step_id,
                )
                return self.failure

            async def load_teaching_plan(self, requested_owner, task_id, run_id):
                assert (requested_owner, task_id, run_id) == (
                    owner, failed.task_id, failed.run_id,
                )
                return None

            async def record_locator_proposal(
                self, requested_owner, task_id, run_id, proposal,
            ) -> None:
                assert (requested_owner, task_id, run_id) == (
                    owner, failed.task_id, failed.run_id,
                )
                self.recorded.append(proposal)

        class Publications:
            checks = 0

            async def get_active(self, requested_owner, skill_id):
                assert (requested_owner, skill_id) == (owner, publication.skill.skill_id)
                return SimpleNamespace(manifest=publication)

            async def assert_current(self, requested_owner, candidate):
                assert (requested_owner, candidate) == (owner, publication)
                self.checks += 1

        runs, publications = Runs(), Publications()
        installed = SimpleNamespace(
            _seed=publication,
            _repair_hints=((step.step_id, (hint,)),),
            runs=runs, publications=publications,
            submission=SimpleNamespace(
                _invocation=lambda _principal, _session: SimpleNamespace(owner=owner),
                _recheck=lambda _invocation: None,
            ),
        )
        kwargs = dict(
            failed_task_id=failed.task_id, failed_run_id=failed.run_id,
            step_id=step.step_id, replacement=hint,
        )
        principal = cast(Principal, object())
        proposal = await BrowserVerticalComponents.propose_locator_repair(
            cast(BrowserVerticalComponents, installed), principal, owner.session_id,
            **kwargs,
        )
        assert proposal.source_kind == "published_skill"
        assert publications.checks == 2
        assert runs.recorded == [proposal]
        await BrowserVerticalComponents._journal_published_locator_proposals(
            cast(BrowserVerticalComponents, installed), failed,
        )
        assert runs.recorded == [proposal, proposal]
        runs.failure = None
        with pytest.raises(RepairError, match="repair_failure_scope_mismatch"):
            await BrowserVerticalComponents.propose_locator_repair(
                cast(BrowserVerticalComponents, installed), principal,
                owner.session_id, **kwargs,
            )

    asyncio.run(scenario())
