from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.browser_skill.models import ProfileRef, ResourceOutcome
from app.browser_skill.session import (
    CaptureProof,
    LeaseSnapshot,
    ResourceFacts,
    current_binding_decision,
    promote_profile,
    resource_disposition,
)
from tests.browser_skill.factories import DIGEST, binding

NOW = datetime(2026, 10, 2, tzinfo=UTC)


def lease() -> LeaseSnapshot:
    return LeaseSnapshot(
        binding=binding(), holder_id="worker", deadline=NOW + timedelta(seconds=10), state="held",
    )


def profile(generation: str = "old", revision: int = 1) -> ProfileRef:
    return ProfileRef(
        generation_ref=generation, binding=binding(), profile_revision=revision,
        subject_digest=DIGEST,
    )


def capture() -> CaptureProof:
    return CaptureProof(
        profile=profile("new", 2), base_profile_revision=1,
        allocated_new_generation=True, generation_absent_from_history=True,
        verified_subject_digest=DIGEST, origin_digest=DIGEST, expected_origin_digest=DIGEST,
        projection_digest=DIGEST, expected_projection_digest=DIGEST,
        capture_complete=True, captured_bytes=10, maximum_bytes=10,
    )


def test_profile_promotion_requires_current_facts_and_proposes_exact_next_version() -> None:
    decision = promote_profile(
        profile(), capture(), binding(), lease(), holder_id="worker", now=NOW,
        authorized_now=True, current_verified_subject_digest=DIGEST,
    )
    assert decision.status == "promote"
    assert decision.next_profile == profile("new", 2)
    assert profile().generation_ref == "old"
    first = capture().model_copy(update={
        "profile": profile("first", 1), "base_profile_revision": 0,
    })
    initial = promote_profile(
        None, first, binding(), lease(), holder_id="worker", now=NOW,
        authorized_now=True, current_verified_subject_digest=DIGEST,
    )
    assert initial.next_profile == profile("first", 1)


@pytest.mark.parametrize("field", ["tenant_id", "user_id", "session_id"])
def test_binding_rejects_each_owner_dimension(field: str) -> None:
    changed = binding().model_copy(update={
        "owner": binding().owner.model_copy(update={field: "other"}),
    })
    assert current_binding_decision(
        binding(), changed, lease(), holder_id="worker", now=NOW, authorized_now=True,
    ) == "binding_stale"


@pytest.mark.parametrize(
    "field,value", [("binding_id", "other"), ("binding_revision", 2),
                    ("authorization_revision", 3), ("lease_epoch", 4)],
)
def test_binding_requires_all_revision_and_identity_fields(field: str, value: object) -> None:
    changed = binding().model_copy(update={field: value})
    assert current_binding_decision(
        binding(), changed, lease(), holder_id="worker", now=NOW, authorized_now=True,
    ) == "binding_stale"


@pytest.mark.parametrize(
    "change", [{"deadline": NOW}, {"holder_id": "other"}, {"state": "quarantined"},
               {"state": "released"}, {"binding": binding().model_copy(update={"lease_epoch": 4})}],
)
def test_lease_fences_expiry_holder_and_state(change: dict[str, object]) -> None:
    assert current_binding_decision(
        binding(), binding(), lease().model_copy(update=change),
        holder_id="worker", now=NOW, authorized_now=True,
    ) == "lease_stale"


@pytest.mark.parametrize(
    "change,reason", [
        ({"base_profile_revision": 0}, "profile_stale"),
        ({"profile": profile("new", 3)}, "profile_stale"),
        ({"profile": profile("old", 2)}, "generation_reused"),
        ({"allocated_new_generation": False}, "generation_reused"),
        ({"generation_absent_from_history": False}, "generation_reused"),
        ({"verified_subject_digest": "b" * 64}, "subject_mismatch"),
        ({"origin_digest": "b" * 64}, "capture_invalid"),
        ({"projection_digest": "b" * 64}, "capture_invalid"),
        ({"capture_complete": False}, "capture_invalid"),
        ({"captured_bytes": 11}, "capture_invalid"),
    ],
)
def test_stale_or_invalid_capture_becomes_orphan(
    change: dict[str, object], reason: str,
) -> None:
    result = promote_profile(
        profile(), capture().model_copy(update=change), binding(), lease(),
        holder_id="worker", now=NOW, authorized_now=True, current_verified_subject_digest=DIGEST,
    )
    expected_status = "reject" if reason == "generation_reused" else "orphan"
    assert (result.status, result.reason, result.next_profile) == (expected_status, reason, None)


def test_profile_owner_subject_and_current_authority_are_independent_checks() -> None:
    other = profile().model_copy(update={
        "binding": binding().model_copy(update={
            "owner": binding().owner.model_copy(update={"session_id": "another_chat"}),
        }),
    })
    for current, authorized, subject, reason in (
        (other, True, DIGEST, "profile_stale"),
        (profile(), False, DIGEST, "unauthorized"),
        (profile(), True, "b" * 64, "subject_mismatch"),
    ):
        outcome = promote_profile(
            current, capture(), binding(), lease(), holder_id="worker", now=NOW,
            authorized_now=authorized, current_verified_subject_digest=subject,
        )
        assert outcome.reason == reason
        assert outcome.next_profile is None


@pytest.mark.parametrize("phase", ["acquiring", "unknown"])
def test_missing_acquire_response_keeps_capacity_even_without_expired_lease(phase: str) -> None:
    facts = ResourceFacts.model_validate({
        "acquisition_phase": phase, "acquisition_send_started": True,
        "reservation_only_proven": False, "lease_expired": False,
    })
    result = resource_disposition(facts)
    assert (result.capacity, result.quarantine) == ("keep", True)


def test_only_proven_no_acquire_or_matching_termination_releases_capacity() -> None:
    facts = ResourceFacts(
        acquisition_phase="reservation_only", acquisition_send_started=False,
        reservation_only_proven=True, lease_expired=True,
    )
    assert resource_disposition(facts).capacity == "release"
    for change in ({"reservation_only_proven": False}, {"acquisition_send_started": True}):
        decision = resource_disposition(facts.model_copy(update=change))
        assert decision.capacity == "keep"
        assert decision.quarantine is True
    unknown = facts.model_copy(update={"acquisition_phase": "unknown"})
    outcome = ResourceOutcome(status="terminated", evidence_digest=DIGEST)
    mismatched = unknown.model_copy(update={"outcome": outcome})
    assert resource_disposition(mismatched).capacity == "keep"
    matched = mismatched.model_copy(update={"outcome_matches_claim": True})
    assert resource_disposition(matched).capacity == "release"
    quarantined = matched.model_copy(update={
        "outcome": ResourceOutcome(status="quarantined", evidence_digest=DIGEST),
    })
    assert resource_disposition(quarantined).capacity == "keep"


def test_live_and_expired_resources_both_hold_capacity_with_different_quarantine() -> None:
    facts = ResourceFacts(
        acquisition_phase="acquired", acquisition_send_started=True,
        reservation_only_proven=False, lease_expired=False,
    )
    assert resource_disposition(facts).model_dump() == {"capacity": "keep", "quarantine": False}
    assert resource_disposition(facts.model_copy(update={"lease_expired": True})).model_dump() == {
        "capacity": "keep", "quarantine": True,
    }


def test_naive_deadline_and_unknown_acquisition_states_are_rejected() -> None:
    with pytest.raises(ValidationError, match="browser_aware_time_required"):
        LeaseSnapshot(binding=binding(), holder_id="worker", deadline=datetime(2026, 1, 1),
                      state="held")
    with pytest.raises(ValidationError):
        ResourceFacts.model_validate({
            "acquisition_phase": "assumed_dead", "acquisition_send_started": False,
            "reservation_only_proven": True, "lease_expired": True,
        })
