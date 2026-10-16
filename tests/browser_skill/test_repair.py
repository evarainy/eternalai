"""Synthetic failed-scope and hint registry checks; no real Site exploration."""

from __future__ import annotations

import json
import pickle
from typing import Any

import pytest
from pydantic import ValidationError

from app.browser_skill.models import FrameHop, LocatorHint, ParameterRef, ScopeBinding
from app.browser_skill.registry import canonical_digest
from app.browser_skill.repair import (
    FailedStepEvidence,
    LocatorProposal,
    RepairContext,
    RepairError,
    propose_locator,
    validate_locator_proposal,
)
from app.browser_skill.trajectory import DraftManifest, PublicationReference, SkillDraft
from tests.browser_skill.factories import DIGEST
from tests.browser_skill.test_compiler import SyntheticTeaching


class SyntheticRepair:
    def __init__(self, count: int = 1) -> None:
        self.teaching = SyntheticTeaching()
        self.source = self.teaching.record(count)
        self.draft = self.teaching.compile()
        self.event = self.source.manifest.events[0]
        self.hint = LocatorHint(kind="role_name", value="Alternative")
        self.hints = (self.hint,)
        self.draft_revision = 1
        self.draft_digest = self.draft.manifest.digest
        self.failure_enabled = True
        self.evidence = FailedStepEvidence(
            trajectory_id=self.source.manifest.trajectory_id,
            event_id=self.event.event_id,
            binding=self.teaching.binding,
            publication=PublicationReference.from_publication(self.teaching.publication),
            scope=self.event.scope,
            reason="locator_not_found",
            evidence_digest=DIGEST,
        )

    def failure_lookup(
        self,
        trajectory_id: str,
        event_id: str,
        who: ScopeBinding,
        publication: PublicationReference,
    ) -> FailedStepEvidence | None:
        if self.failure_enabled and (trajectory_id, event_id, who, publication) == (
            self.source.manifest.trajectory_id,
            self.event.event_id,
            self.teaching.binding,
            PublicationReference.from_publication(self.teaching.publication),
        ):
            return self.evidence
        return None

    def approved_hints(
        self,
        trajectory_id: str,
        event: Any,
        who: ScopeBinding,
        publication: PublicationReference,
    ) -> tuple[LocatorHint, ...]:
        if (trajectory_id, event.event_id, who, publication) == (
            self.source.manifest.trajectory_id,
            self.event.event_id,
            self.teaching.binding,
            PublicationReference.from_publication(self.teaching.publication),
        ):
            return self.hints
        return ()

    def context(self) -> RepairContext:
        return RepairContext(
            teaching=self.teaching.teaching_context(),
            current_draft_head=lambda _draft, _owner: (self.draft_revision, self.draft_digest),
            failure_lookup=self.failure_lookup,
            approved_hints=self.approved_hints,
        )

    def propose(self, **changes: Any) -> LocatorProposal:
        args = dict(
            binding=self.teaching.binding,
            publication=self.teaching.publication,
            base_revision=self.teaching.base_revision,
            expected_source_revision=self.source.manifest.revision,
            expected_draft_revision=self.draft.manifest.draft_revision,
            event_id=self.event.event_id,
            replacement=self.hint,
            site=self.teaching.site,
            context=self.context(),
        )
        args.update(changes)
        return propose_locator(self.source, self.draft, **args)

    def validate(self, proposal: LocatorProposal, **changes: Any) -> None:
        args = dict(
            binding=self.teaching.binding,
            publication=self.teaching.publication,
            base_revision=self.teaching.base_revision,
            expected_source_revision=self.source.manifest.revision,
            expected_draft_revision=self.draft.manifest.draft_revision,
            site=self.teaching.site,
            context=self.context(),
        )
        args.update(changes)
        validate_locator_proposal(proposal, self.source, self.draft, **args)


def reseal_draft(
    draft: SkillDraft,
    *,
    steps: list[dict[str, Any]] | None = None,
    skill_updates: dict[str, Any] | None = None,
    draft_updates: dict[str, Any] | None = None,
) -> SkillDraft:
    payload = draft.manifest.model_dump(mode="json")
    skill = payload["skill"]
    if steps is not None:
        skill["steps"] = steps
    if skill_updates:
        skill.update(skill_updates)
    skill["digest"] = canonical_digest("browser_skill.v1", skill)
    if draft_updates:
        payload.update(draft_updates)
    payload["digest"] = canonical_digest("skill_draft.v1", payload)
    return SkillDraft(draft.binding, DraftManifest.model_validate_json(json.dumps(payload)))


def test_proposal_binds_failed_scope_fixed_step_and_review_only_hint() -> None:
    repair = SyntheticRepair()
    proposal = repair.propose()
    repair.validate(proposal)
    assert proposal.source_trajectory_digest == repair.source.manifest.digest
    assert proposal.base_publication == repair.source.manifest.base_publication
    assert proposal.draft_digest == repair.draft.manifest.digest
    assert proposal.draft_revision == repair.draft.manifest.draft_revision
    assert proposal.failed_scope == repair.event.scope
    assert proposal.step_id == repair.draft.manifest.skill.steps[0].step_id
    assert proposal.replacement == repair.hint
    assert repair.draft.manifest.skill.steps[0].locator == repair.teaching.step.locator
    assert not hasattr(proposal, "execute") and not hasattr(proposal, "publish")
    wire = proposal.model_dump_json()
    assert "session" not in wire and "https://" not in wire
    assert "raw_dom" not in wire and "private_value" not in wire


def test_failure_and_repair_context_stay_process_local() -> None:
    repair = SyntheticRepair()
    for value in (repair.evidence, repair.context()):
        assert "session" not in repr(value)
        with pytest.raises(TypeError, match="repair_private_serialization_forbidden"):
            pickle.dumps(value)


def test_changed_draft_head_and_failure_evidence_reject_replay() -> None:
    repair = SyntheticRepair()
    proposal = repair.propose()
    repair.draft_revision = 2
    with pytest.raises(RepairError, match="repair_draft_stale"):
        repair.validate(proposal)
    repair.draft_revision = 1
    repair.draft_digest = "c" * 64
    with pytest.raises(RepairError, match="repair_draft_stale"):
        repair.validate(proposal)
    repair.draft_digest = repair.draft.manifest.digest
    repair.evidence = FailedStepEvidence(
        trajectory_id=repair.evidence.trajectory_id,
        event_id=repair.evidence.event_id,
        binding=repair.evidence.binding,
        publication=repair.evidence.publication,
        scope=repair.evidence.scope,
        reason=repair.evidence.reason,
        evidence_digest="b" * 64,
    )
    with pytest.raises(RepairError, match="repair_proposal_stale"):
        repair.validate(proposal)


def test_repair_requires_current_binding_base_source_and_expiry() -> None:
    repair = SyntheticRepair()
    repair.teaching.current_binding = repair.teaching.binding.model_copy(
        update={"authorization_revision": 9}
    )
    with pytest.raises(ValueError, match="teaching_binding_stale"):
        repair.propose()
    repair.teaching.current_binding = repair.teaching.binding
    repair.teaching.current_base_revision = 5
    with pytest.raises(ValueError, match="teaching_base_stale"):
        repair.propose()
    repair.teaching.current_base_revision = 4
    repair.teaching.current_source_digest = "c" * 64
    with pytest.raises(ValueError, match="teaching_source_stale"):
        repair.propose()
    repair.teaching.current_source_digest = repair.source.manifest.digest
    repair.teaching.now = 10.0
    with pytest.raises(ValueError, match="teaching_authorization_expired"):
        repair.propose()


def test_failed_scope_must_match_full_frame_path_and_owner() -> None:
    repair = SyntheticRepair()
    changed_scope = repair.event.scope.model_copy(
        update={"frame_path": (FrameHop(frame_id="other", frame_epoch=1),)}
    )
    repair.evidence = FailedStepEvidence(
        trajectory_id=repair.evidence.trajectory_id,
        event_id=repair.evidence.event_id,
        binding=repair.evidence.binding,
        publication=repair.evidence.publication,
        scope=changed_scope,
        reason=repair.evidence.reason,
        evidence_digest=repair.evidence.evidence_digest,
    )
    with pytest.raises(RepairError, match="repair_failure_scope_mismatch"):
        repair.propose()
    repair = SyntheticRepair()
    other = repair.teaching.binding.model_copy(update={"binding_id": "other"})
    with pytest.raises(ValueError, match="teaching_owner_or_binding_mismatch"):
        repair.propose(binding=other)


def test_missing_failure_or_unapproved_hint_does_not_become_proposal() -> None:
    repair = SyntheticRepair()
    repair.failure_enabled = False
    with pytest.raises(RepairError, match="repair_failure_unavailable"):
        repair.propose()
    repair.failure_enabled = True
    with pytest.raises(RepairError, match="repair_hint_unapproved"):
        repair.propose(replacement=LocatorHint(kind="role_name", value="Open"))
    with pytest.raises(RepairError, match="repair_event_unknown"):
        repair.propose(event_id="unknown_event")


@pytest.mark.parametrize(
    "hint",
    [
        LocatorHint(kind="role_name", value="https://synthetic.invalid"),
        LocatorHint(kind="test_id", value="javascript:synthetic()"),
        LocatorHint(kind="test_id", value="value;synthetic"),
    ],
)
def test_even_registered_unsafe_url_or_script_hint_is_rejected(hint: LocatorHint) -> None:
    repair = SyntheticRepair()
    repair.hints = (hint,)
    with pytest.raises(RepairError, match="repair_hint_unsafe"):
        repair.propose(replacement=hint)


def test_changed_operation_parameter_and_reordering_are_rejected() -> None:
    repair = SyntheticRepair(count=2)
    steps = [step.model_dump(mode="json") for step in repair.draft.manifest.skill.steps]
    steps[0]["operation"] = "read"
    steps[0]["value_ref"] = None
    changed = reseal_draft(repair.draft, steps=steps)
    repair.draft_digest = changed.manifest.digest
    with pytest.raises(RepairError, match="repair_fixed_step_changed"):
        propose_locator(
            repair.source,
            changed,
            binding=repair.teaching.binding,
            publication=repair.teaching.publication,
            base_revision=4,
            expected_source_revision=2,
            expected_draft_revision=1,
            event_id=repair.event.event_id,
            replacement=repair.hint,
            site=repair.teaching.site,
            context=repair.context(),
        )
    reordered = reseal_draft(
        repair.draft,
        steps=list(
            reversed([step.model_dump(mode="json") for step in repair.draft.manifest.skill.steps])
        ),
    )
    repair.draft_digest = reordered.manifest.digest
    with pytest.raises(RepairError, match="repair_fixed_step_changed"):
        propose_locator(
            repair.source,
            reordered,
            binding=repair.teaching.binding,
            publication=repair.teaching.publication,
            base_revision=4,
            expected_source_revision=2,
            expected_draft_revision=1,
            event_id=repair.event.event_id,
            replacement=repair.hint,
            site=repair.teaching.site,
            context=repair.context(),
        )


def test_changed_parameter_ref_and_verifier_cannot_pass_draft_contract() -> None:
    repair = SyntheticRepair()
    steps = [step.model_dump(mode="json") for step in repair.draft.manifest.skill.steps]
    steps[0]["value_ref"] = ParameterRef(name="expected_field").model_dump(mode="json")
    with pytest.raises(ValidationError, match="draft_parameter_purpose_mismatch"):
        reseal_draft(repair.draft, steps=steps)
    with pytest.raises(ValidationError, match="draft_dependencies_changed"):
        reseal_draft(repair.draft, skill_updates={"verifier_id": "other_verifier"})


def test_proposal_rejects_tampered_digest_revision_and_unknown_fields() -> None:
    repair = SyntheticRepair()
    proposal = repair.propose()
    with pytest.raises(RepairError, match="repair_proposal_invalid"):
        repair.validate(proposal.model_copy(update={"draft_revision": 9}))
    with pytest.raises(ValidationError, match="Extra inputs"):
        LocatorProposal.model_validate({**proposal.model_dump(mode="json"), "script": "synthetic"})
    with pytest.raises(RepairError, match="repair_draft_mismatch"):
        repair.propose(expected_draft_revision=2)


def test_validated_draft_remains_review_only_and_cannot_expand_hint_scope() -> None:
    repair = SyntheticRepair()
    validated = reseal_draft(
        repair.draft,
        draft_updates={"state": "validated", "validation_digest": "d" * 64},
    )
    repair.draft_digest = validated.manifest.digest
    proposal = propose_locator(
        repair.source,
        validated,
        binding=repair.teaching.binding,
        publication=repair.teaching.publication,
        base_revision=4,
        expected_source_revision=1,
        expected_draft_revision=1,
        event_id=repair.event.event_id,
        replacement=repair.hint,
        site=repair.teaching.site,
        context=repair.context(),
    )
    assert proposal.draft_digest == validated.manifest.digest
    assert not hasattr(validated, "publish")
    repair.draft = validated
    repair.hints = (LocatorHint(kind="role_name", value="Open"),)
    with pytest.raises(RepairError, match="repair_hint_unapproved"):
        repair.validate(proposal)
