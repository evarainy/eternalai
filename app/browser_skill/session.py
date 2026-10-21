"""Pure Profile/lease decisions, not authorization or provider-proof authorities.

Snapshots and proof facts must come from trusted current store/provider reads.
The future persistence adapter must repeat the predicates and CAS atomically;
these functions perform no IO and provide no durability or remote-death proof.
"""

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from app.browser_skill.models import (
    Contract,
    Digest,
    Epoch,
    OpaqueId,
    ProfileRef,
    ResourceOutcome,
    ScopeBinding,
)


def require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("browser_aware_time_required")


class LeaseSnapshot(Contract):
    binding: ScopeBinding = Field(repr=False)
    holder_id: OpaqueId
    deadline: datetime
    state: Literal["held", "quarantined", "released"]

    @model_validator(mode="after")
    def aware_deadline(self) -> "LeaseSnapshot":
        require_aware(self.deadline)
        return self


BindingDecision = Literal["current", "unauthorized", "binding_stale", "lease_stale"]


def current_binding_decision(
    expected: ScopeBinding,
    current: ScopeBinding,
    lease: LeaseSnapshot,
    *,
    holder_id: str,
    now: datetime,
    authorized_now: bool,
) -> BindingDecision:
    """Compare every binding field; a true authorization fact is not verified here."""
    require_aware(now)
    if not authorized_now:
        return "unauthorized"
    if expected != current:
        return "binding_stale"
    if (
        lease.binding != current
        or lease.holder_id != holder_id
        or lease.state != "held"
        or lease.deadline <= now
    ):
        return "lease_stale"
    return "current"


class CaptureProof(Contract):
    """Trusted provider facts; this model does not inspect a captured Profile.

    Origin and projection digests must be produced by actual bounded validation.
    The unique-generation fact must include provider allocation and the store's
    full generation history, not merely comparison with the active generation.
    """

    profile: ProfileRef = Field(repr=False)
    base_profile_revision: Epoch
    allocated_new_generation: bool
    generation_absent_from_history: bool
    verified_subject_digest: Digest
    origin_digest: Digest
    expected_origin_digest: Digest
    projection_digest: Digest
    expected_projection_digest: Digest
    capture_complete: bool
    captured_bytes: int = Field(ge=0)
    maximum_bytes: int = Field(gt=0)


class ProfilePromotion(Contract):
    status: Literal["promote", "orphan", "reject"]
    reason: Literal[
        "current", "unauthorized", "binding_stale", "lease_stale",
        "profile_stale", "generation_reused", "subject_mismatch", "capture_invalid",
    ]
    next_profile: ProfileRef | None = Field(default=None, repr=False)


def promote_profile(
    current_profile: ProfileRef | None,
    proof: CaptureProof,
    current_binding: ScopeBinding,
    lease: LeaseSnapshot,
    *,
    holder_id: str,
    now: datetime,
    authorized_now: bool,
    current_verified_subject_digest: Digest,
) -> ProfilePromotion:
    """Return a proposed CAS result; only proven new generations may become orphans.

    A reused generation is rejected without tagging an existing/shared resource
    for orphan cleanup. Neither result authorizes provider deletion.
    """
    def rejected(reason: str) -> ProfilePromotion:
        is_new = (
            proof.allocated_new_generation
            and proof.generation_absent_from_history
            and (current_profile is None
                 or current_profile.generation_ref != proof.profile.generation_ref)
        )
        return ProfilePromotion.model_validate({
            "status": "orphan" if is_new else "reject", "reason": reason,
        })

    check = current_binding_decision(
        proof.profile.binding, current_binding, lease,
        holder_id=holder_id, now=now, authorized_now=authorized_now,
    )
    if check != "current":
        return rejected(check)
    revision = current_profile.profile_revision if current_profile else 0
    if (
        proof.base_profile_revision != revision
        or proof.profile.profile_revision != revision + 1
        or (
            current_profile is not None
            and (
                current_profile.binding.owner != current_binding.owner
                or current_profile.binding.binding_id != current_binding.binding_id
                or current_profile.binding.binding_revision != current_binding.binding_revision
            )
        )
    ):
        return rejected("profile_stale")
    if (
        not proof.allocated_new_generation
        or not proof.generation_absent_from_history
        or (current_profile and proof.profile.generation_ref == current_profile.generation_ref)
    ):
        return rejected("generation_reused")
    if (
        proof.profile.subject_digest != current_verified_subject_digest
        or proof.verified_subject_digest != current_verified_subject_digest
        or (current_profile and current_profile.subject_digest != current_verified_subject_digest)
    ):
        return rejected("subject_mismatch")
    if (
        not proof.capture_complete
        or proof.captured_bytes > proof.maximum_bytes
        or proof.origin_digest != proof.expected_origin_digest
        or proof.projection_digest != proof.expected_projection_digest
    ):
        return rejected("capture_invalid")
    return ProfilePromotion(status="promote", reason="current", next_profile=proof.profile)


class ResourceFacts(Contract):
    """Acquisition/lifecycle facts bound by the caller to one resource claim.

    A missing resource reference is not proof that acquisition had no effect.
    outcome_matches_claim includes provider resource identity and claim epoch;
    ResourceOutcome itself intentionally carries neither.
    """

    acquisition_phase: Literal["reservation_only", "acquiring", "acquired", "unknown"]
    acquisition_send_started: bool
    reservation_only_proven: bool
    lease_expired: bool
    outcome: ResourceOutcome | None = None
    outcome_matches_claim: bool = False


class ResourceDisposition(Contract):
    capacity: Literal["keep", "release"]
    quarantine: bool


def resource_disposition(facts: ResourceFacts) -> ResourceDisposition:
    """Only proven no-acquire or matching lifecycle evidence releases capacity."""
    if facts.outcome is not None and facts.outcome_matches_claim:
        if facts.outcome.status in {"released", "terminated"}:
            return ResourceDisposition(capacity="release", quarantine=False)
        return ResourceDisposition(capacity="keep", quarantine=True)
    if (
        facts.acquisition_phase == "reservation_only"
        and facts.reservation_only_proven
        and not facts.acquisition_send_started
        and facts.outcome is None
    ):
        return ResourceDisposition(capacity="release", quarantine=False)
    return ResourceDisposition(
        capacity="keep",
        quarantine=(
            facts.lease_expired
            or facts.acquisition_phase != "acquired"
            or facts.outcome is not None
        ),
    )
