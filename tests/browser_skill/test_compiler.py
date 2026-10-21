"""Generated synthetic teaching inputs only; no page, model or service calls."""

from __future__ import annotations

import json
import pickle
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.browser_skill.compiler import (
    TeachingContext,
    TeachingError,
    canonical_step_id,
    compile_trajectory,
    validate_draft_route_shape,
)
from app.browser_skill.models import (
    BrowserOwner,
    DecisionBudget,
    DecisionSource,
    LocatorHint,
    ObservationPolicy,
    ObservationRequest,
    ParameterRef,
    ScopeBinding,
    TargetRef,
)
from app.browser_skill.registry import Publication, canonical_digest
from app.browser_skill.site_rules import (
    EffectFact,
    ExpectedField,
    FrozenSiteAdapter,
    RegisteredReadRule,
    RegisteredSitePlan,
    SiteStepRule,
)
from app.browser_skill.trajectory import (
    CapturePermission,
    CaptureStepPermission,
    DraftManifest,
    NeutralTrajectory,
    ParameterApproval,
    PublicationReference,
    RecordingContext,
    SkillDraft,
    TrajectoryEvent,
    TrajectoryManifest,
    TrajectoryRecorder,
)
from tests.browser_skill.factories import DIGEST, binding, context, scope


def synthetic_publication() -> Publication:
    path = Path(__file__).resolve().parents[2] / "skills/browser/ecology9/search_contact.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    skill = payload["skill"]
    skill["parameters"] = ["contact_query", "business_key", "expected_field"]
    skill["digest"] = canonical_digest("browser_skill.v1", skill)
    descriptor = payload["descriptor"]
    descriptor["skill_digest"] = skill["digest"]
    descriptor["digest"] = canonical_digest("browser_descriptor.v1", descriptor)
    payload["dependency_digest"] = canonical_digest(
        "browser_dependencies.v1",
        {name: payload[name] for name in ("site", "verifier", "descriptor")},
    )
    payload["digest"] = canonical_digest("browser_publication.v1", payload)
    return Publication.model_validate_json(json.dumps(payload))


class SyntheticTeaching:
    def __init__(self) -> None:
        self.publication = synthetic_publication()
        self.active_publication = self.publication
        self.binding = binding()
        self.current_binding = self.binding
        self.base_revision = 4
        self.current_base_revision = 4
        self.current_source_revision = 0
        self.current_source_digest = DIGEST
        self.now = 0.0
        self.allowed = True
        self.permission_enabled = True
        self.authorize_calls = 0
        self.revoke_after: int | None = None
        self.stamp = scope()
        self.target = TargetRef(target_id="synthetic_target", candidate_epoch=1, scope=self.stamp)
        self.policy = ObservationPolicy(
            policy_id="teaching_synthetic_policy",
            digest=DIGEST,
            allowed_names=("Open", "Alternative"),
            allowed_roles=("textbox", "button"),
        )
        self.step = self.publication.skill.steps[0]
        self.effect = EffectFact(
            operation=self.step.operation,
            actual_effect=self.step.effect,
            proof_ref="synthetic_proof",
            evidence_digest=DIGEST,
        )
        self.permission = CapturePermission(
            trajectory_id="synthetic_trajectory",
            source_ref="synthetic_source",
            binding=self.binding,
            publication=PublicationReference.from_publication(self.publication),
            authorization_digest=DIGEST,
            base_revision=4,
            steps=(
                CaptureStepPermission(
                    step_id=self.step.step_id, effect=self.effect, policy=self.policy
                ),
            ),
        )
        self.plan = RegisteredSitePlan(
            site_id=self.publication.skill.site_id,
            site_digest=self.publication.skill.site_digest,
            skill_id=self.publication.skill.skill_id,
            skill_version=self.publication.skill.version,
            skill_digest=self.publication.skill.digest,
            verifier_id=self.publication.skill.verifier_id,
            verifier_digest=self.publication.skill.verifier_digest,
            source=DecisionSource(
                source_id="synthetic_source",
                origin="https://ecology9.invalid",
                fixture_digest=DIGEST,
            ),
            navigation_origins=("https://ecology9.invalid",),
            policy=self.policy,
            steps=tuple(
                SiteStepRule(
                    step=registered_step,
                    observation=ObservationRequest(region_id=self.stamp.region_id),
                    target_criteria=("Open",),
                    effect=EffectFact(
                        operation=registered_step.operation,
                        actual_effect=registered_step.effect,
                        proof_ref="synthetic_proof",
                        evidence_digest=DIGEST,
                    ),
                )
                for registered_step in self.publication.skill.steps
            ),
            read_rule=RegisteredReadRule(
                object_type="synthetic_contact",
                key_ref=ParameterRef(name="business_key"),
                fields=(
                    ExpectedField(
                        field_id="synthetic_state", value_ref=ParameterRef(name="expected_field")
                    ),
                ),
            ),
            decision_manifest=context().manifest,
            decision_budget=DecisionBudget(),
        )
        self.site = FrozenSiteAdapter((self.plan,))
        self.approvals = (
            ParameterApproval(name="contact_query", purpose="fill_value", format="text"),
            ParameterApproval(name="business_key", purpose="business_key", format="opaque_id"),
            ParameterApproval(name="expected_field", purpose="expected_field", format="text"),
        )
        self.snapshot: NeutralTrajectory | None = None

    def lookup(
        self,
        trajectory_id: str,
        source_ref: str,
        who: ScopeBinding,
        publication: PublicationReference,
    ) -> CapturePermission | None:
        if self.permission_enabled and (trajectory_id, source_ref, who, publication) == (
            "synthetic_trajectory",
            "synthetic_source",
            self.binding,
            PublicationReference.from_publication(self.publication),
        ):
            return self.permission
        return None

    def authorize(
        self,
        who: ScopeBinding,
        source_ref: str,
        publication: PublicationReference,
        event: TrajectoryEvent | None,
    ) -> bool:
        self.authorize_calls += 1
        return (
            self.allowed
            and (self.revoke_after is None or self.authorize_calls <= self.revoke_after)
            and (who, source_ref, publication)
            == (
                self.binding,
                "synthetic_source",
                PublicationReference.from_publication(self.publication),
            )
        )

    def recording_context(self) -> RecordingContext:
        return RecordingContext(
            lambda: self.current_binding,
            self.lookup,
            self.authorize,
            lambda _source: self.stamp,
            lambda _source: (self.target,),
        )

    def teaching_context(self) -> TeachingContext:
        return TeachingContext(
            recording=self.recording_context(),
            active_base=lambda: (self.active_publication, self.current_base_revision),
            source_head=lambda _source, _owner: (
                self.current_source_revision,
                self.current_source_digest,
            ),
            deadline_monotonic=10.0,
            clock=lambda: self.now,
        )

    def event(self, sequence: int) -> TrajectoryEvent:
        return TrajectoryEvent(
            sequence=sequence,
            event_id=f"event_{sequence}",
            step_id=self.step.step_id,
            scope=self.stamp,
            target=self.target,
            operation=self.step.operation,
            effect=self.step.effect,
            effect_proof=self.effect,
            value_ref=self.step.value_ref,
            url_ref=self.step.url_ref,
            option_ref=self.step.option_ref,
            policy_digest=self.policy.digest,
            approved_labels=("Open",),
        )

    def record(self, count: int = 1) -> NeutralTrajectory:
        recorder = TrajectoryRecorder(
            trajectory_id="synthetic_trajectory",
            source_ref="synthetic_source",
            binding=self.binding,
            publication=self.publication,
            base_revision=self.base_revision,
            context=self.recording_context(),
        )
        snapshot = recorder.snapshot(binding=self.binding)
        for sequence in range(1, count + 1):
            snapshot = recorder.append(
                self.event(sequence), binding=self.binding, expected_revision=sequence - 1
            )
        self.current_source_revision = count
        self.current_source_digest = snapshot.manifest.digest
        self.snapshot = snapshot
        return snapshot

    def compile(self, source: NeutralTrajectory | None = None, **changes: Any):
        actual = source or self.snapshot
        assert actual is not None
        args = dict(
            binding=self.binding,
            publication=self.publication,
            base_revision=self.base_revision,
            expected_source_revision=actual.manifest.revision,
            parameter_whitelist=self.approvals,
            site=self.site,
            context=self.teaching_context(),
        )
        args.update(changes)
        return compile_trajectory(actual, **args)


def reseal_source(source: NeutralTrajectory, **changes: Any) -> NeutralTrajectory:
    payload = source.manifest.model_dump(mode="json")
    payload.update(changes)
    payload["digest"] = canonical_digest("neutral_trajectory.v1", payload)
    return NeutralTrajectory(
        source.binding, TrajectoryManifest.model_validate_json(json.dumps(payload))
    )


def test_compiles_repeated_authorized_events_into_ordered_traceable_draft() -> None:
    teaching = SyntheticTeaching()
    source = teaching.record(2)
    draft = teaching.compile()
    steps = draft.manifest.skill.steps
    assert tuple(step.step_id for step in steps) == tuple(
        canonical_step_id(event) for event in source.manifest.events
    )
    assert len({step.step_id for step in steps}) == 2
    assert all(step.locator == teaching.step.locator for step in steps)
    assert all(step.value_ref == teaching.step.value_ref for step in steps)
    assert draft.manifest.state == "draft"
    assert draft.manifest.base_publication == source.manifest.base_publication
    assert draft.manifest.base_revision == 4
    assert draft.manifest.draft_revision == 1
    assert not hasattr(draft, "publish")
    assert not hasattr(draft, "execute")
    wire = draft.manifest.model_dump_json()
    assert "session" not in wire and "https://" not in wire
    assert teaching.authorize_calls >= 2 * len(source.manifest.events)
    assert draft.manifest.draft_id == f"draft_{source.manifest.digest}"
    assert draft.manifest.skill.version == f"draft_{source.manifest.digest}"
    validate_draft_route_shape(source, draft, teaching.plan)
    with pytest.raises(ValueError, match="site_skill_unregistered"):
        teaching.site.bootstrap(draft.manifest.skill)


def test_route_mapping_rejects_relabelled_or_reordered_draft_steps() -> None:
    teaching = SyntheticTeaching()
    source = teaching.record(2)
    draft = teaching.compile()
    payload = draft.manifest.model_dump(mode="json")
    steps = payload["skill"]["steps"]
    steps[0]["step_id"] = "invented_step"
    payload["skill"]["digest"] = canonical_digest("browser_skill.v1", payload["skill"])
    payload["digest"] = canonical_digest("skill_draft.v1", payload)
    relabelled = SkillDraft(draft.binding, DraftManifest.model_validate_json(json.dumps(payload)))
    with pytest.raises(TeachingError, match="teaching_route_mismatch"):
        validate_draft_route_shape(source, relabelled, teaching.plan)


def test_teaching_context_is_process_local() -> None:
    teaching = SyntheticTeaching()
    context_value = teaching.teaching_context()
    assert "session" not in repr(context_value)
    with pytest.raises(TypeError, match="teaching_private_serialization_forbidden"):
        pickle.dumps(context_value)


@pytest.mark.parametrize("field", ["tenant_id", "user_id", "session_id"])
def test_cross_owner_is_denied_even_if_manifest_digest_matches(field: str) -> None:
    teaching = SyntheticTeaching()
    source = teaching.record()
    owner = teaching.binding.owner
    values = dict(tenant_id=owner.tenant_id, user_id=owner.user_id, session_id=owner.session_id)
    values[field] = "other"
    other = teaching.binding.model_copy(update={"owner": BrowserOwner(**values)})
    with pytest.raises(TeachingError, match="teaching_owner_or_binding_mismatch"):
        teaching.compile(source, binding=other)


def test_revocation_expiry_and_source_head_are_rechecked() -> None:
    teaching = SyntheticTeaching()
    teaching.record(2)
    teaching.allowed = False
    with pytest.raises(TeachingError, match="teaching_denied"):
        teaching.compile()
    teaching.allowed = True
    teaching.now = 10.0
    with pytest.raises(TeachingError, match="teaching_authorization_expired"):
        teaching.compile()
    teaching.now = 0.0
    teaching.current_source_revision = 3
    with pytest.raises(TeachingError, match="teaching_source_stale"):
        teaching.compile()
    teaching.current_source_revision = 2
    teaching.current_source_digest = "b" * 64
    with pytest.raises(TeachingError, match="teaching_source_stale"):
        teaching.compile()
    teaching.current_source_digest = teaching.snapshot.manifest.digest  # type: ignore[union-attr]
    teaching.current_binding = teaching.binding.model_copy(update={"lease_epoch": 4})
    with pytest.raises(TeachingError, match="teaching_binding_stale"):
        teaching.compile()


def test_mid_compile_revocation_and_base_change_fail_closed() -> None:
    teaching = SyntheticTeaching()
    teaching.record(2)
    teaching.revoke_after = teaching.authorize_calls + 1
    with pytest.raises(TeachingError, match="teaching_denied"):
        teaching.compile()
    teaching.revoke_after = None
    teaching.current_base_revision = 5
    with pytest.raises(TeachingError, match="teaching_base_stale"):
        teaching.compile()


def test_permission_digest_and_unregistered_step_are_rejected() -> None:
    teaching = SyntheticTeaching()
    source = teaching.record()
    stale = reseal_source(source, source_authorization_digest="b" * 64)
    teaching.current_source_digest = stale.manifest.digest
    with pytest.raises(TeachingError, match="teaching_source_permission_stale"):
        teaching.compile(stale)
    event = source.manifest.events[0].model_copy(update={"step_id": "invented"})
    unknown = reseal_source(source, events=[event.model_dump(mode="json")])
    teaching.current_source_digest = unknown.manifest.digest
    teaching.current_source_revision = 1
    with pytest.raises(TeachingError, match="teaching_step_unregistered"):
        teaching.compile(unknown)


def test_unapproved_parameter_ref_and_whitelist_are_rejected() -> None:
    teaching = SyntheticTeaching()
    source = teaching.record()
    event = source.manifest.events[0].model_copy(
        update={"value_ref": ParameterRef(name="secret_ref")}
    )
    unsupported = reseal_source(source, events=[event.model_dump(mode="json")])
    teaching.current_source_digest = unsupported.manifest.digest
    with pytest.raises(TeachingError, match="teaching_step_unregistered"):
        teaching.compile(unsupported)
    teaching.current_source_digest = source.manifest.digest
    with pytest.raises(TeachingError, match="teaching_whitelist_invalid"):
        teaching.compile(parameter_whitelist=teaching.approvals[:-1])
    wrong = ParameterApproval(name="contact_query", purpose="business_key", format="opaque_id")
    with pytest.raises(TeachingError, match="teaching_parameter_purpose_mismatch"):
        teaching.compile(parameter_whitelist=(wrong, *teaching.approvals[1:]))


@pytest.mark.parametrize("field", ["value", "url", "script", "raw_dom", "private_input"])
def test_event_cannot_carry_raw_values_or_script(field: str) -> None:
    teaching = SyntheticTeaching()
    payload = teaching.event(1).model_dump(mode="json")
    payload[field] = "synthetic_forbidden"
    with pytest.raises(ValidationError, match="Extra inputs"):
        TrajectoryEvent.model_validate(payload)


def test_unregistered_site_and_invalid_source_revision_do_not_compile() -> None:
    teaching = SyntheticTeaching()
    source = teaching.record()
    with pytest.raises(TeachingError, match="teaching_source_unsupported"):
        teaching.compile(site=FrozenSiteAdapter(()))
    with pytest.raises(TeachingError, match="teaching_source_revision_invalid"):
        teaching.compile(expected_source_revision=2)
    empty = reseal_source(source, revision=0, events=[])
    teaching.current_source_revision = 0
    teaching.current_source_digest = empty.manifest.digest
    with pytest.raises(TeachingError, match="teaching_source_revision_invalid"):
        teaching.compile(empty)


def test_changed_base_publication_and_duplicate_event_are_not_teachable() -> None:
    teaching = SyntheticTeaching()
    source = teaching.record(2)
    path = Path(__file__).resolve().parents[2] / "skills/browser/ecology9/query_todos.json"
    other = Publication.model_validate_json(path.read_bytes())
    with pytest.raises(TeachingError, match="teaching_base_mismatch"):
        teaching.compile(publication=other)
    duplicate = source.manifest.events[1].model_copy(
        update={"event_id": source.manifest.events[0].event_id}
    )
    with pytest.raises(ValidationError, match="trajectory_duplicate_event"):
        reseal_source(
            source,
            events=[
                source.manifest.events[0].model_dump(mode="json"),
                duplicate.model_dump(mode="json"),
            ],
        )


def test_failed_forged_event_effect_is_not_teachable() -> None:
    teaching = SyntheticTeaching()
    source = teaching.record()
    event = source.manifest.events[0]
    proof = event.effect_proof.model_copy(update={"actual_effect": "read_only"})
    forged = event.model_copy(update={"effect": "read_only", "effect_proof": proof})
    changed = reseal_source(source, events=[forged.model_dump(mode="json")])
    teaching.current_source_digest = changed.manifest.digest
    with pytest.raises(TeachingError, match="teaching_step_unregistered"):
        teaching.compile(changed)


def test_published_locator_is_copied_not_taken_from_event_label() -> None:
    teaching = SyntheticTeaching()
    source = teaching.record()
    event = source.manifest.events[0].model_copy(update={"approved_labels": ("Alternative",)})
    changed = reseal_source(source, events=[event.model_dump(mode="json")])
    teaching.current_source_digest = changed.manifest.digest
    draft = teaching.compile(changed)
    assert draft.manifest.skill.steps[0].locator == teaching.step.locator
    assert draft.manifest.skill.steps[0].locator != LocatorHint(
        kind="role_name", value="Alternative"
    )
