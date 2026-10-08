"""Review-only locator proposals for authorized neutral failed steps.

No page exploration, executable selector code, draft mutation or publication is
performed here. A future editor must revalidate a proposal against the current
draft and publication before applying it through its own approved workflow.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Literal, Never, Self, SupportsIndex

from pydantic import Field, TypeAdapter, model_validator

from app.browser_skill.compiler import (
    TeachingContext,
    TeachingError,
    _check_whitelist,
    canonical_step_id,
    validate_draft_route_shape,
    validate_teaching_source,
)
from app.browser_skill.models import (
    Contract,
    Digest,
    Epoch,
    LocatorHint,
    OpaqueId,
    ScopeBinding,
    ScopeStamp,
)
from app.browser_skill.registry import Publication, canonical_digest
from app.browser_skill.site_rules import FrozenSiteAdapter, RegisteredSitePlan
from app.browser_skill.trajectory import (
    NeutralTrajectory,
    PublicationReference,
    SkillDraft,
    TrajectoryEvent,
    _binding_copy,
)


class RepairError(ValueError):
    """Fixed codes only; no callback payload, selector or owner in diagnostics."""


@dataclass(frozen=True, slots=True, repr=False)
class FailedStepEvidence:
    """Trusted failed-scope lookup result, never reconstructed from page text."""

    trajectory_id: str
    event_id: str
    binding: ScopeBinding
    publication: PublicationReference
    scope: ScopeStamp
    reason: Literal["target_stale", "locator_not_found", "ambiguous_target"]
    evidence_digest: str

    def __post_init__(self) -> None:
        TypeAdapter(OpaqueId).validate_python(self.trajectory_id, strict=True)
        TypeAdapter(OpaqueId).validate_python(self.event_id, strict=True)
        TypeAdapter(Digest).validate_python(self.evidence_digest, strict=True)
        object.__setattr__(self, "binding", _binding_copy(self.binding))
        object.__setattr__(
            self,
            "publication",
            PublicationReference.model_validate_json(self.publication.model_dump_json()),
        )
        object.__setattr__(
            self, "scope", ScopeStamp.model_validate_json(self.scope.model_dump_json())
        )
        if self.reason not in {"target_stale", "locator_not_found", "ambiguous_target"}:
            raise RepairError("repair_failure_unsupported")

    def __repr__(self) -> str:
        return "<FailedStepEvidence: process-local>"

    def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
        raise TypeError("repair_private_serialization_forbidden")

    def __getstate__(self) -> Never:
        raise TypeError("repair_private_serialization_forbidden")


@dataclass(frozen=True, slots=True, repr=False)
class RepairContext:
    """Trusted owner-scoped failure and hint registries supplied by composition."""

    teaching: TeachingContext
    current_draft_head: Callable[[str, ScopeBinding], tuple[int, str]]
    failure_lookup: Callable[
        [str, str, ScopeBinding, PublicationReference], FailedStepEvidence | None
    ]
    approved_hints: Callable[
        [str, TrajectoryEvent, ScopeBinding, PublicationReference], tuple[LocatorHint, ...]
    ]

    def __post_init__(self) -> None:
        if not isinstance(self.teaching, TeachingContext) or not all(
            callable(cb)
            for cb in (self.current_draft_head, self.failure_lookup, self.approved_hints)
        ):
            raise RepairError("repair_context_invalid")

    def __repr__(self) -> str:
        return "<RepairContext: process-local>"

    def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
        raise TypeError("repair_private_serialization_forbidden")

    def __getstate__(self) -> Never:
        raise TypeError("repair_private_serialization_forbidden")


class LocatorProposal(Contract):
    schema_version: Literal["locator_proposal.v1"] = "locator_proposal.v1"
    proposal_id: OpaqueId
    source_trajectory_digest: Digest
    source_revision: Epoch
    base_publication: PublicationReference
    base_revision: Epoch
    draft_id: OpaqueId
    draft_digest: Digest
    draft_revision: Annotated[int, Field(ge=1)]
    event_id: OpaqueId
    event_digest: Digest
    step_id: OpaqueId
    original_step_digest: Digest
    failed_scope: ScopeStamp
    failure_reason: Literal["target_stale", "locator_not_found", "ambiguous_target"]
    failure_evidence_digest: Digest
    approved_hints_digest: Digest
    target_criteria_digest: Digest
    replacement: LocatorHint
    digest: Digest

    @model_validator(mode="after")
    def integrity(self) -> Self:
        if canonical_digest(self.schema_version, self.model_dump(mode="json")) != self.digest:
            raise ValueError("repair_proposal_digest_mismatch")
        return self


def _safe_hint(hint: LocatorHint, plan: RegisteredSitePlan, draft: SkillDraft) -> None:
    value = hint.value
    if any(ord(c) < 32 for c in value) or any(
        token in value.casefold()
        for token in ("://", "javascript:", "data:", "<", ">", "`", "\\", ";", "${")
    ):
        raise RepairError("repair_hint_unsafe")
    if hint.kind == "test_id":
        try:
            TypeAdapter(OpaqueId).validate_python(value, strict=True)
        except Exception:
            raise RepairError("repair_hint_unsafe") from None
    elif hint.kind == "role_name":
        if value not in (
            *plan.policy.allowed_names,
            *plan.policy.allowed_context,
            *plan.policy.allowed_row_labels,
            *plan.policy.allowed_column_labels,
        ):
            raise RepairError("repair_hint_unapproved")
    elif hint.kind == "business_key":
        if value != plan.read_rule.key_ref.name or not any(
            approval.name == value and approval.purpose == "business_key"
            for approval in draft.manifest.parameter_whitelist
        ):
            raise RepairError("repair_hint_unapproved")
    else:
        raise RepairError("repair_hint_unsupported")


def _check_draft(
    source: NeutralTrajectory,
    draft: SkillDraft,
    binding: ScopeBinding,
    publication: Publication,
    base_revision: int,
    expected_draft_revision: int,
    plan: RegisteredSitePlan,
    context: RepairContext,
) -> SkillDraft:
    try:
        checked = SkillDraft(draft.binding, draft.manifest)
        checked.validate_base(binding, publication, base_revision)
        manifest = checked.manifest
        if (
            manifest.draft_id != f"draft_{source.manifest.digest}"
            or manifest.draft_revision != expected_draft_revision
            or manifest.state == "rejected"
            or manifest.skill.parameters != publication.skill.parameters
            or tuple(p.name for p in manifest.parameter_whitelist) != publication.skill.parameters
            or len(manifest.skill.steps) != len(source.manifest.events)
        ):
            raise RepairError("repair_draft_mismatch")
        _check_whitelist(manifest.parameter_whitelist, plan, publication.skill)
        current_revision, current_digest = context.current_draft_head(manifest.draft_id, binding)
        if (
            type(current_revision) is not int
            or current_revision != expected_draft_revision
            or current_digest != manifest.digest
        ):
            raise RepairError("repair_draft_stale")
        base_steps = {item.step_id: item for item in publication.skill.steps}
        for event, step in zip(source.manifest.events, manifest.skill.steps, strict=True):
            base = base_steps[event.step_id]
            if (
                step.step_id != canonical_step_id(event)
                or step.operation != base.operation
                or step.effect != base.effect
                or step.value_ref != base.value_ref
                or step.url_ref != base.url_ref
                or step.option_ref != base.option_ref
            ):
                raise RepairError("repair_fixed_step_changed")
            if step.locator != base.locator:
                hints = _hints(source, event, binding, plan, checked, context)
                if step.locator not in hints:
                    raise RepairError("repair_existing_hint_unapproved")
        validate_draft_route_shape(source, checked, plan)
        return checked
    except (RepairError, TeachingError):
        raise
    except Exception:
        raise RepairError("repair_draft_unsupported") from None


def _hints(
    source: NeutralTrajectory,
    event: TrajectoryEvent,
    binding: ScopeBinding,
    plan: RegisteredSitePlan,
    draft: SkillDraft,
    context: RepairContext,
) -> tuple[LocatorHint, ...]:
    try:
        raw = context.approved_hints(
            source.manifest.trajectory_id,
            event,
            _binding_copy(binding),
            source.manifest.base_publication,
        )
        if type(raw) is not tuple or not raw or len(raw) > 32:
            raise RepairError("repair_hints_unavailable")
        checked = tuple(LocatorHint.model_validate_json(item.model_dump_json()) for item in raw)
        if len(set(checked)) != len(checked):
            raise RepairError("repair_hints_duplicate")
        for hint in checked:
            _safe_hint(hint, plan, draft)
        return checked
    except RepairError:
        raise
    except Exception:
        raise RepairError("repair_hints_unavailable") from None


def _failure(
    source: NeutralTrajectory,
    event: TrajectoryEvent,
    binding: ScopeBinding,
    context: RepairContext,
) -> FailedStepEvidence:
    try:
        raw = context.failure_lookup(
            source.manifest.trajectory_id,
            event.event_id,
            _binding_copy(binding),
            source.manifest.base_publication,
        )
        if raw is None:
            raise RepairError("repair_failure_unavailable")
        checked = FailedStepEvidence(
            raw.trajectory_id,
            raw.event_id,
            raw.binding,
            raw.publication,
            raw.scope,
            raw.reason,
            raw.evidence_digest,
        )
        if (
            checked.trajectory_id != source.manifest.trajectory_id
            or checked.event_id != event.event_id
            or checked.binding != binding
            or checked.publication != source.manifest.base_publication
            or checked.scope != event.scope
        ):
            raise RepairError("repair_failure_scope_mismatch")
        return checked
    except RepairError:
        raise
    except Exception:
        raise RepairError("repair_failure_unavailable") from None


def propose_locator(
    source: NeutralTrajectory,
    draft: SkillDraft,
    *,
    binding: ScopeBinding,
    publication: Publication,
    base_revision: int,
    expected_source_revision: int,
    expected_draft_revision: int,
    event_id: str,
    replacement: LocatorHint,
    site: FrozenSiteAdapter,
    context: RepairContext,
) -> LocatorProposal:
    """Bind one approved hint to one proven failed event and exact draft head."""
    source, publication, plan = validate_teaching_source(
        source,
        binding=binding,
        publication=publication,
        base_revision=base_revision,
        expected_source_revision=expected_source_revision,
        site=site,
        context=context.teaching,
    )
    try:
        TypeAdapter(Epoch).validate_python(expected_draft_revision, strict=True)
        TypeAdapter(OpaqueId).validate_python(event_id, strict=True)
        replacement = LocatorHint.model_validate_json(replacement.model_dump_json())
    except Exception:
        raise RepairError("repair_request_invalid") from None
    checked_draft = _check_draft(
        source,
        draft,
        binding,
        publication,
        base_revision,
        expected_draft_revision,
        plan,
        context,
    )
    event = next((item for item in source.manifest.events if item.event_id == event_id), None)
    if event is None:
        raise RepairError("repair_event_unknown")
    index = event.sequence - 1
    step = checked_draft.manifest.skill.steps[index]
    evidence = _failure(source, event, binding, context)
    hints = _hints(source, event, binding, plan, checked_draft, context)
    if replacement not in hints or replacement == step.locator:
        raise RepairError("repair_hint_unapproved")
    rule = next(rule for rule in plan.steps if rule.step.step_id == event.step_id)
    hint_digest = canonical_digest(
        "repair_approved_hints.v1", {"hints": [hint.model_dump(mode="json") for hint in hints]}
    )
    event_digest = canonical_digest("trajectory_event.v1", event.model_dump(mode="json"))
    original_step_digest = canonical_digest("browser_skill_step.v1", step.model_dump(mode="json"))
    target_criteria_digest = canonical_digest(
        "site_target_criteria.v1", {"criteria": rule.target_criteria}
    )
    proposal_seed = canonical_digest(
        "locator_proposal_id.v1",
        {
            "draft_digest": checked_draft.manifest.digest,
            "event_digest": event_digest,
            "replacement": replacement.model_dump(mode="json"),
            "failure_evidence_digest": evidence.evidence_digest,
        },
    )
    payload: dict[str, object] = dict(
        schema_version="locator_proposal.v1",
        proposal_id=f"proposal_{proposal_seed}",
        source_trajectory_digest=source.manifest.digest,
        source_revision=source.manifest.revision,
        base_publication=source.manifest.base_publication.model_dump(mode="json"),
        base_revision=base_revision,
        draft_id=checked_draft.manifest.draft_id,
        draft_digest=checked_draft.manifest.digest,
        draft_revision=checked_draft.manifest.draft_revision,
        event_id=event.event_id,
        event_digest=event_digest,
        step_id=step.step_id,
        original_step_digest=original_step_digest,
        failed_scope=evidence.scope.model_dump(mode="json"),
        failure_reason=evidence.reason,
        failure_evidence_digest=evidence.evidence_digest,
        approved_hints_digest=hint_digest,
        target_criteria_digest=target_criteria_digest,
        replacement=replacement.model_dump(mode="json"),
    )
    payload["digest"] = canonical_digest("locator_proposal.v1", payload)
    proposal = LocatorProposal.model_validate_json(json.dumps(payload))
    # Refresh every external fact after all proposal construction callbacks.
    validate_teaching_source(
        source,
        binding=binding,
        publication=publication,
        base_revision=base_revision,
        expected_source_revision=expected_source_revision,
        site=site,
        context=context.teaching,
    )
    _check_draft(
        source,
        checked_draft,
        binding,
        publication,
        base_revision,
        expected_draft_revision,
        plan,
        context,
    )
    if (
        _failure(source, event, binding, context) != evidence
        or _hints(source, event, binding, plan, checked_draft, context) != hints
    ):
        raise RepairError("repair_evidence_stale")
    return proposal


def validate_locator_proposal(
    proposal: LocatorProposal,
    source: NeutralTrajectory,
    draft: SkillDraft,
    *,
    binding: ScopeBinding,
    publication: Publication,
    base_revision: int,
    expected_source_revision: int,
    expected_draft_revision: int,
    site: FrozenSiteAdapter,
    context: RepairContext,
) -> None:
    """Freshly rederive a proposal; success is structural, never publication."""
    try:
        checked = LocatorProposal.model_validate_json(proposal.model_dump_json())
    except Exception:
        raise RepairError("repair_proposal_invalid") from None
    fresh = propose_locator(
        source,
        draft,
        binding=binding,
        publication=publication,
        base_revision=base_revision,
        expected_source_revision=expected_source_revision,
        expected_draft_revision=expected_draft_revision,
        event_id=checked.event_id,
        replacement=checked.replacement,
        site=site,
        context=context,
    )
    if fresh != checked:
        raise RepairError("repair_proposal_stale")
