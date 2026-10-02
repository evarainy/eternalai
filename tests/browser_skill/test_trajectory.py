from __future__ import annotations

import json
import pickle
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest
from pydantic import ValidationError

from app.browser_skill.models import BrowserOwner, ScopeBinding, ScopeStamp, TargetRef
from app.browser_skill.registry import Publication, canonical_digest
from app.browser_skill.site_rules import EffectFact
from app.browser_skill.trajectory import (
    CapturePermission,
    CaptureStepPermission,
    DraftManifest,
    NeutralTrajectory,
    ParameterApproval,
    PublicationReference,
    RecordingContext,
    SkillDraft,
    TrajectoryError,
    TrajectoryEvent,
    TrajectoryManifest,
    TrajectoryRecorder,
)
from tests.browser_skill.factories import DIGEST, binding, policy, scope


class SyntheticCapture:
    """Generated values only; callbacks check actual lookup keys, never source labels."""

    def __init__(self, skill_name: str = "query_todos") -> None:
        path = (
            Path(__file__).resolve().parents[2] / "skills/browser/ecology9" / f"{skill_name}.json"
        )
        self.publication = Publication.model_validate_json(path.read_bytes())
        self.expected = binding()
        self.current = binding()
        self.stamp = scope()
        self.target = TargetRef(target_id="synthetic_target", candidate_epoch=1, scope=self.stamp)
        self.targets = (self.target,)
        self.authorized = True
        self.permission_enabled = True
        self.authorized_events: list[str | None] = []
        step = self.publication.skill.steps[0]
        self.proof = EffectFact(
            operation=step.operation,
            actual_effect=step.effect,
            proof_ref="synthetic_effect_fact",
            evidence_digest="b" * 64,
        )
        self.permission = CapturePermission(
            trajectory_id="synthetic_trajectory",
            source_ref="synthetic_source",
            binding=self.expected,
            publication=PublicationReference.from_publication(self.publication),
            authorization_digest=DIGEST,
            base_revision=4,
            steps=(
                CaptureStepPermission(step_id=step.step_id, effect=self.proof, policy=policy()),
            ),
        )

    def lookup(
        self,
        trajectory_id: str,
        source_ref: str,
        who: ScopeBinding,
        publication: PublicationReference,
    ) -> CapturePermission | None:
        if not self.permission_enabled or (trajectory_id, source_ref, who, publication) != (
            "synthetic_trajectory",
            "synthetic_source",
            self.expected,
            PublicationReference.from_publication(self.publication),
        ):
            return None
        return self.permission

    def authorize(
        self,
        who: ScopeBinding,
        source_ref: str,
        publication: PublicationReference,
        event: TrajectoryEvent | None,
    ) -> bool:
        self.authorized_events.append(event.event_id if event else None)
        return self.authorized and (who, source_ref, publication) == (
            self.expected,
            "synthetic_source",
            PublicationReference.from_publication(self.publication),
        )

    def current_scope(self, source_ref: str) -> ScopeStamp:
        assert source_ref == "synthetic_source"
        return self.stamp

    def current_targets(self, source_ref: str) -> tuple[TargetRef, ...]:
        assert source_ref == "synthetic_source"
        return self.targets

    def context(self) -> RecordingContext:
        return RecordingContext(
            lambda: self.current,
            self.lookup,
            self.authorize,
            self.current_scope,
            self.current_targets,
        )

    def recorder(self) -> TrajectoryRecorder:
        return TrajectoryRecorder(
            trajectory_id="synthetic_trajectory",
            source_ref="synthetic_source",
            binding=self.expected,
            publication=self.publication,
            base_revision=4,
            context=self.context(),
        )

    def event(self, sequence: int = 1) -> TrajectoryEvent:
        step = self.publication.skill.steps[0]
        return TrajectoryEvent(
            sequence=sequence,
            event_id=f"event_{sequence}",
            step_id=step.step_id,
            scope=self.stamp,
            target=self.target,
            operation=step.operation,
            effect=step.effect,
            effect_proof=self.proof,
            value_ref=step.value_ref,
            url_ref=step.url_ref,
            option_ref=step.option_ref,
            policy_digest=policy().digest,
            approved_labels=("Open",),
        )


def changed_owner(original: ScopeBinding, field: str) -> ScopeBinding:
    identity = {
        "tenant_id": original.owner.tenant_id,
        "user_id": original.owner.user_id,
        "session_id": original.owner.session_id,
    }
    identity[field] = "another_owner"
    return original.model_copy(update={"owner": BrowserOwner(**identity)})


def draft_manifest(capture: SyntheticCapture, **changes: Any) -> DraftManifest:
    skill = capture.publication.skill
    approvals = [
        ParameterApproval(name=name, purpose="fill_value", format="text").model_dump(mode="json")
        for name in skill.parameters
    ]
    data = dict(
        schema_version="skill_draft.v1",
        draft_id="draft",
        base_revision=4,
        draft_revision=1,
        base_publication=PublicationReference.from_publication(capture.publication).model_dump(
            mode="json"
        ),
        parameter_whitelist=approvals,
        skill=skill.model_dump(mode="json"),
        state="draft",
        validation_digest=None,
        rejection=None,
    )
    data.update(changes)
    data["digest"] = canonical_digest("skill_draft.v1", data)
    return DraftManifest.model_validate_json(json.dumps(data))


@pytest.mark.parametrize(
    "skill_name,effect",
    [("query_todos", "read_only"), ("open_todo", "may_write"), ("search_contact", "may_write")],
)
def test_metadata_records_proven_effects_without_execution(skill_name: str, effect: str) -> None:
    capture = SyntheticCapture(skill_name)
    recorder = capture.recorder()
    empty = recorder.snapshot(binding=capture.expected)
    result = recorder.append(capture.event(), binding=capture.expected, expected_revision=0)
    assert empty.manifest.events == ()
    assert empty.manifest.revision == 0
    assert result.manifest.revision == 1
    assert result.manifest.events[0].effect == effect
    assert result.manifest.base_revision == 4
    assert result.manifest.base_publication.skill_digest == capture.publication.skill.digest
    assert capture.authorized_events == [None, None, "event_1", "event_1"]
    assert not hasattr(recorder, "execute")
    assert not hasattr(result, "publish")


def test_private_owner_and_callbacks_are_not_wire_or_representations() -> None:
    capture = SyntheticCapture()
    recorder = capture.recorder()
    snapshot = recorder.append(capture.event(), binding=capture.expected, expected_revision=0)
    draft = SkillDraft(capture.expected, draft_manifest(capture))
    for value in (capture.permission, capture.context(), recorder, snapshot, draft):
        assert "process-local" in repr(value)
        assert "session" not in repr(value)
        with pytest.raises(TypeError, match="trajectory_private_serialization_forbidden"):
            pickle.dumps(value)
        with pytest.raises(TypeError, match="trajectory_private_serialization_forbidden"):
            value.__getstate__()
        with pytest.raises(TypeError):
            json.dumps(value)
        with pytest.raises(TypeError):
            vars(value)
    wire = snapshot.manifest.model_dump_json()
    decoded = json.loads(wire)
    assert set(decoded["binding"]) == {
        "binding_id",
        "binding_revision",
        "authorization_revision",
        "lease_epoch",
    }
    assert not {"owner", "tenant_id", "user_id", "session_id"}.intersection(decoded["binding"])
    assert "https://" not in wire
    assert "origin" not in decoded["base_publication"]
    assert snapshot.binding.owner.session_id == capture.expected.owner.session_id


@pytest.mark.parametrize(
    "field", ["raw_dom", "vendor_wire", "url", "value", "cookies", "har", "password"]
)
def test_raw_material_is_rejected_before_recording(field: str) -> None:
    capture = SyntheticCapture()
    data = capture.event().model_dump(mode="json")
    data[field] = "synthetic_forbidden_marker"
    with pytest.raises(ValidationError, match="Extra inputs"):
        TrajectoryEvent.model_validate_json(json.dumps(data))


@pytest.mark.parametrize("field", ["tenant_id", "user_id", "session_id"])
def test_exact_owner_checked_on_append_snapshot_and_restore(field: str) -> None:
    capture = SyntheticCapture()
    recorder = capture.recorder()
    snapshot = recorder.snapshot(binding=capture.expected)
    other = changed_owner(capture.expected, field)
    with pytest.raises(TrajectoryError, match="owner_or_binding_mismatch"):
        recorder.append(capture.event(), binding=other, expected_revision=0)
    with pytest.raises(TrajectoryError, match="owner_or_binding_mismatch"):
        recorder.snapshot(binding=other)
    with pytest.raises(TrajectoryError, match="owner_or_binding_mismatch"):
        TrajectoryRecorder.restore(
            snapshot, binding=other, publication=capture.publication, context=capture.context()
        )
    assert recorder.snapshot(binding=capture.expected).manifest.revision == 0


@pytest.mark.parametrize(
    "field", ["binding_id", "binding_revision", "authorization_revision", "lease_epoch"]
)
def test_each_current_binding_version_is_rechecked(field: str) -> None:
    capture = SyntheticCapture()
    recorder = capture.recorder()
    capture.current = capture.current.model_copy(
        update={field: "changed" if field == "binding_id" else 99}
    )
    with pytest.raises(TrajectoryError, match="trajectory_binding_stale"):
        recorder.append(capture.event(), binding=capture.expected, expected_revision=0)


@pytest.mark.parametrize(
    "change,error",
    [
        ("auth", "trajectory_denied"),
        ("source", "trajectory_source_denied"),
        ("digest", "trajectory_source_permission_stale"),
        ("owner", "trajectory_source_permission_mismatch"),
        ("source_ref", "trajectory_source_permission_mismatch"),
        ("publication", "trajectory_source_permission_mismatch"),
    ],
)
def test_current_authorization_and_source_permission_fail_closed(change: str, error: str) -> None:
    capture = SyntheticCapture()
    recorder = capture.recorder()
    if change == "auth":
        capture.authorized = False
    elif change == "source":
        capture.permission_enabled = False
    elif change == "digest":
        capture.permission = replace(capture.permission, authorization_digest="c" * 64)
    elif change == "owner":
        capture.permission = replace(
            capture.permission, binding=changed_owner(capture.expected, "session_id")
        )
    elif change == "source_ref":
        capture.permission = replace(capture.permission, source_ref="another_source")
    else:
        reference = capture.permission.publication.model_copy(
            update={"publication_digest": "c" * 64}
        )
        capture.permission = replace(capture.permission, publication=reference)
    with pytest.raises(TrajectoryError, match=error):
        recorder.append(capture.event(), binding=capture.expected, expected_revision=0)


@pytest.mark.parametrize("change", ["page", "frame", "region", "target", "candidate_epoch"])
def test_live_scope_and_target_freshness(change: str) -> None:
    capture = SyntheticCapture()
    recorder, event = capture.recorder(), capture.event()
    if change == "page":
        capture.stamp = capture.stamp.model_copy(update={"page_epoch": 99})
    elif change == "frame":
        frames = capture.stamp.frame_path
        capture.stamp = capture.stamp.model_copy(
            update={"frame_path": (frames[0], frames[1].model_copy(update={"frame_epoch": 99}))}
        )
    elif change == "region":
        capture.stamp = capture.stamp.model_copy(update={"region_digest": "b" * 64})
    elif change == "target":
        capture.targets = ()
    else:
        capture.targets = (capture.target.model_copy(update={"candidate_epoch": 99}),)
    with pytest.raises(TrajectoryError, match="trajectory_scope_stale"):
        recorder.append(event, binding=capture.expected, expected_revision=0)


@pytest.mark.parametrize(
    "change,error",
    [
        ("label", "labels_unapproved"),
        ("policy", "labels_unapproved"),
        ("proof", "effect_unproved"),
        ("unknown_effect", "effect_unproved"),
        ("step", "step_unregistered"),
        ("operation", "step_unregistered"),
    ],
)
def test_events_must_match_registered_skill_site_facts_and_approved_projection(
    change: str, error: str
) -> None:
    capture = SyntheticCapture()
    recorder, event = capture.recorder(), capture.event()
    if change == "label":
        event = event.model_copy(update={"approved_labels": ("unapproved generated label",)})
    elif change == "policy":
        event = event.model_copy(update={"policy_digest": "b" * 64})
    elif change == "proof":
        event = event.model_copy(
            update={"effect_proof": capture.proof.model_copy(update={"evidence_digest": "c" * 64})}
        )
    elif change == "unknown_effect":
        grant = capture.permission.steps[0]
        capture.permission = replace(
            capture.permission,
            steps=(
                grant.model_copy(
                    update={"effect": capture.proof.model_copy(update={"actual_effect": "unknown"})}
                ),
            ),
        )
    elif change == "step":
        event = event.model_copy(update={"step_id": "unregistered"})
    else:
        event = event.model_copy(
            update={
                "operation": "click",
                "effect_proof": capture.proof.model_copy(update={"operation": "click"}),
            }
        )
    with pytest.raises(TrajectoryError, match=error):
        recorder.append(event, binding=capture.expected, expected_revision=0)


def test_model_copy_cannot_hide_unknown_effect_or_wrong_target_scope() -> None:
    capture = SyntheticCapture()
    recorder, event = capture.recorder(), capture.event()
    unproved = event.model_copy(
        update={"effect_proof": capture.proof.model_copy(update={"actual_effect": "unknown"})}
    )
    with pytest.raises(ValidationError, match="effect_unproved"):
        recorder.append(unproved, binding=capture.expected, expected_revision=0)
    stale = event.model_copy(
        update={
            "target": capture.target.model_copy(
                update={"scope": scope().model_copy(update={"page_epoch": 5})}
            )
        }
    )
    with pytest.raises(ValidationError, match="scope_mismatch"):
        recorder.append(stale, binding=capture.expected, expected_revision=0)


def test_cas_sequence_duplicate_and_immutable_snapshots() -> None:
    capture = SyntheticCapture()
    recorder = capture.recorder()
    first = recorder.append(capture.event(), binding=capture.expected, expected_revision=0)
    with pytest.raises(TrajectoryError, match="revision_conflict"):
        recorder.append(capture.event(2), binding=capture.expected, expected_revision=0)
    with pytest.raises(TrajectoryError, match="sequence_invalid"):
        recorder.append(capture.event(3), binding=capture.expected, expected_revision=1)
    duplicate = capture.event(2).model_copy(update={"event_id": "event_1"})
    with pytest.raises(ValidationError, match="duplicate_event"):
        recorder.append(duplicate, binding=capture.expected, expected_revision=1)
    second = recorder.append(capture.event(2), binding=capture.expected, expected_revision=1)
    assert first.manifest.revision == 1
    assert second.manifest.revision == 2
    object.__setattr__(second.manifest.events[0], "event_id", "tampered_return")
    assert recorder.snapshot(binding=capture.expected).manifest.events[0].event_id == "event_1"
    with pytest.raises(FrozenInstanceError):
        first.binding = binding()  # type: ignore[misc]


def test_competing_append_has_one_cas_winner() -> None:
    capture = SyntheticCapture()
    recorder, barrier = capture.recorder(), Barrier(2)

    def append(event_id: str) -> str:
        event = capture.event().model_copy(update={"event_id": event_id})
        barrier.wait()
        try:
            recorder.append(event, binding=capture.expected, expected_revision=0)
            return event_id
        except TrajectoryError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(append, ("first", "second")))
    assert results.count("trajectory_revision_conflict") == 1
    snapshot = recorder.snapshot(binding=capture.expected)
    assert snapshot.manifest.revision == 1
    assert snapshot.manifest.events[0].event_id in results


def test_restore_reauthorizes_source_and_each_event_without_replaying_stale_targets() -> None:
    capture = SyntheticCapture()
    recorder = capture.recorder()
    snapshot = recorder.append(capture.event(), binding=capture.expected, expected_revision=0)
    capture.targets = ()
    restored = TrajectoryRecorder.restore(
        snapshot,
        binding=capture.expected,
        publication=capture.publication,
        context=capture.context(),
    )
    assert restored.snapshot(binding=capture.expected).manifest == snapshot.manifest
    assert capture.authorized_events.count("event_1") == 3
    with pytest.raises(TrajectoryError, match="scope_stale"):
        restored.append(capture.event(2), binding=capture.expected, expected_revision=1)
    capture.permission_enabled = False
    with pytest.raises(TrajectoryError, match="source_denied"):
        TrajectoryRecorder.restore(
            snapshot,
            binding=capture.expected,
            publication=capture.publication,
            context=capture.context(),
        )


def test_manifest_digest_and_restore_registration_cannot_be_bypassed() -> None:
    capture = SyntheticCapture()
    snapshot = capture.recorder().append(
        capture.event(), binding=capture.expected, expected_revision=0
    )
    corrupted = snapshot.manifest.model_copy(update={"digest": "0" * 64})
    with pytest.raises(ValidationError, match="digest_mismatch"):
        NeutralTrajectory(capture.expected, corrupted)
    payload = snapshot.manifest.model_dump(mode="json")
    payload["events"][0]["approved_labels"] = ["unapproved synthetic label"]
    payload["digest"] = canonical_digest("neutral_trajectory.v1", payload)
    forged = NeutralTrajectory(
        capture.expected, TrajectoryManifest.model_validate_json(json.dumps(payload))
    )
    with pytest.raises(TrajectoryError, match="labels_unapproved"):
        TrajectoryRecorder.restore(
            forged,
            binding=capture.expected,
            publication=capture.publication,
            context=capture.context(),
        )


def test_authorization_callbacks_are_required_and_errors_do_not_expose_payload() -> None:
    capture = SyntheticCapture()
    with pytest.raises(ValueError, match="callbacks_required"):
        replace(capture.context(), authorize=None)  # type: ignore[arg-type]

    def broken() -> ScopeBinding:
        raise RuntimeError("synthetic_provider_detail")

    context = replace(capture.context(), current_binding=broken)
    with pytest.raises(TrajectoryError, match="^trajectory_authorization_unavailable$") as error:
        TrajectoryRecorder(
            trajectory_id="synthetic_trajectory",
            source_ref="synthetic_source",
            binding=capture.expected,
            publication=capture.publication,
            base_revision=4,
            context=context,
        )
    assert "synthetic_provider_detail" not in str(error.value)


@pytest.mark.parametrize(
    "state,validation,rejection",
    [("draft", None, None), ("validated", DIGEST, None), ("rejected", None, "unsafe_reference")],
)
def test_draft_states_preserve_base_and_have_no_execution_or_publication(
    state: str, validation: str | None, rejection: str | None
) -> None:
    capture = SyntheticCapture("search_contact")
    draft = SkillDraft(
        capture.expected,
        draft_manifest(capture, state=state, validation_digest=validation, rejection=rejection),
    )
    draft.validate_base(capture.expected, capture.publication, 4)
    assert draft.manifest.state == state
    assert draft.manifest.parameter_whitelist[0].name == "contact_query"
    assert not hasattr(draft, "execute")
    assert not hasattr(draft, "publish")
    with pytest.raises(TrajectoryError, match="base_revision_stale"):
        draft.validate_base(capture.expected, capture.publication, 5)
    with pytest.raises(TrajectoryError, match="owner_or_binding_mismatch"):
        draft.validate_base(changed_owner(capture.expected, "session_id"), capture.publication, 4)


@pytest.mark.parametrize(
    "changes",
    [
        {"state": "published"},
        {"state": "validated"},
        {"state": "rejected"},
        {"state": "draft", "validation_digest": DIGEST},
        {"draft_revision": 0},
        {"parameter_whitelist": []},
    ],
)
def test_draft_rejects_bad_states_revisions_and_missing_parameter_approval(
    changes: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        draft_manifest(SyntheticCapture("search_contact"), **changes)


@pytest.mark.parametrize(
    "purpose,format_value",
    [
        ("fill_value", "registered_url"),
        ("navigation_url", "text"),
        ("option_values", "text"),
        ("business_key", "regex"),
        ("execute_script", "text"),
    ],
)
def test_parameter_approval_uses_closed_purpose_and_format(purpose: str, format_value: str) -> None:
    with pytest.raises(ValidationError):
        ParameterApproval.model_validate(
            {"name": "parameter", "purpose": purpose, "format": format_value}
        )


def test_draft_digest_and_dependency_integrity_survive_model_copy() -> None:
    capture = SyntheticCapture()
    manifest = draft_manifest(capture)
    with pytest.raises(ValidationError, match="draft_digest_mismatch"):
        SkillDraft(capture.expected, manifest.model_copy(update={"digest": "0" * 64}))
    with pytest.raises(ValidationError, match="draft_dependencies_changed"):
        SkillDraft(
            capture.expected,
            manifest.model_copy(
                update={"skill": manifest.skill.model_copy(update={"verifier_digest": "b" * 64})}
            ),
        )


def test_draft_parameter_reference_cannot_change_purpose() -> None:
    capture = SyntheticCapture("search_contact")
    wrong = ParameterApproval(name="contact_query", purpose="business_key", format="opaque_id")
    with pytest.raises(ValidationError, match="purpose_mismatch"):
        draft_manifest(capture, parameter_whitelist=[wrong.model_dump(mode="json")])


def test_base_revision_must_come_from_registered_source_permission() -> None:
    capture = SyntheticCapture()
    recorder = capture.recorder()
    capture.permission = replace(capture.permission, base_revision=5)
    with pytest.raises(TrajectoryError, match="trajectory_base_revision_stale"):
        recorder.append(capture.event(), binding=capture.expected, expected_revision=0)
    with pytest.raises(TrajectoryError, match="trajectory_base_revision_stale"):
        capture.recorder()


def test_authorization_change_during_scope_checks_cannot_append() -> None:
    capture = SyntheticCapture()

    def revoke_while_observing(source_ref: str) -> tuple[TargetRef, ...]:
        assert source_ref == "synthetic_source"
        capture.current = capture.current.model_copy(update={"authorization_revision": 99})
        return capture.targets

    context = replace(capture.context(), current_targets=revoke_while_observing)
    recorder = TrajectoryRecorder(
        trajectory_id="synthetic_trajectory",
        source_ref="synthetic_source",
        binding=capture.expected,
        publication=capture.publication,
        base_revision=4,
        context=context,
    )
    with pytest.raises(TrajectoryError, match="trajectory_binding_stale"):
        recorder.append(capture.event(), binding=capture.expected, expected_revision=0)
    capture.current = capture.expected
    assert recorder.snapshot(binding=capture.expected).manifest.revision == 0


@pytest.mark.parametrize("revision", [True, -1, "0"])
def test_invalid_cas_revision_cannot_append(revision: Any) -> None:
    capture = SyntheticCapture()
    recorder = capture.recorder()
    with pytest.raises(TrajectoryError, match="trajectory_revision_conflict"):
        recorder.append(capture.event(), binding=capture.expected, expected_revision=revision)
    assert recorder.snapshot(binding=capture.expected).manifest.revision == 0
