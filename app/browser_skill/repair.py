"""Bounded read-only exploration and review-only locator repair proposals.

Model suggestions identify registered primitives only. Actual actions must use
the sole Run/Executor; a repair never mutates a draft, business object, grant or
Verifier and cannot publish itself.
"""

from __future__ import annotations

import json
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Literal, Never, Self, SupportsIndex

from pydantic import Field, TypeAdapter, model_validator

from app.browser_skill.compiler import (
    DurableTeachingCompilerAuthority,
    DurableTeachingDraft,
    TeachingContext,
    TeachingError,
    _check_whitelist,
    canonical_step_id,
    compile_authorized_run,
    validate_draft_route_shape,
    validate_teaching_source,
)
from app.browser_skill.models import (
    BrowserOperationError,
    Contract,
    Coverage,
    Digest,
    Epoch,
    LocatorHint,
    OpaqueId,
    Role,
    SafeText,
    ScopeBinding,
    ScopeStamp,
)
from app.browser_skill.publication_contracts import BrowserPublicationManifest
from app.browser_skill.registry import Publication, canonical_digest
from app.browser_skill.site_rules import FrozenSiteAdapter, RegisteredSitePlan, SiteStepRule
from app.browser_skill.trajectory import (
    BindingRevisions,
    NeutralTrajectory,
    PublicationReference,
    RunTeachingStep,
    SkillDraft,
    TrajectoryEvent,
    _binding_copy,
)


class RepairError(ValueError):
    """Fixed codes only; no callback payload, selector or owner in diagnostics."""


RepairFailureReason = Literal["target_stale", "locator_not_found", "ambiguous_target"]


@dataclass(frozen=True, slots=True, repr=False)
class FailedStepEvidence:
    """Trusted failed-scope lookup result, never reconstructed from page text."""

    trajectory_id: str
    event_id: str
    binding: ScopeBinding
    publication: PublicationReference
    scope: ScopeStamp
    reason: RepairFailureReason
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


@dataclass(frozen=True, slots=True, repr=False)
class FormalRepairObservation:
    """One unique candidate from a fresh, same-region, read-only probe."""

    replacement: LocatorHint
    scope: ScopeStamp
    target_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "replacement", LocatorHint.model_validate_json(
            self.replacement.model_dump_json()
        ))
        object.__setattr__(self, "scope", ScopeStamp.model_validate_json(
            self.scope.model_dump_json()
        ))
        TypeAdapter(Digest).validate_python(self.target_digest, strict=True)

    @property
    def digest(self) -> str:
        return canonical_digest("browser_repair_observation.v1", {
            "replacement": self.replacement.model_dump(mode="json"),
            "scope": self.scope.model_dump(mode="json"),
            "target_digest": self.target_digest,
        })


@dataclass(frozen=True, slots=True, repr=False)
class FormalFailedStep:
    """Owner-scoped Run audit fact, never inferred from a page/model response."""

    binding: ScopeBinding
    run_id: str
    publication_digest: str
    step_id: str
    reason: RepairFailureReason
    dispatch_state: Literal["not_sent"]
    evidence_digest: str
    observations: tuple[FormalRepairObservation, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "binding", _binding_copy(self.binding))
        for value in (self.run_id, self.step_id):
            TypeAdapter(OpaqueId).validate_python(value, strict=True)
        for value in (self.publication_digest, self.evidence_digest):
            TypeAdapter(Digest).validate_python(value, strict=True)
        if self.reason not in {"target_stale", "locator_not_found", "ambiguous_target"}:
            raise RepairError("repair_failure_unsupported")
        if self.dispatch_state != "not_sent":
            raise RepairError("repair_effect_unknown")
        if (
            type(self.observations) is not tuple or len(self.observations) > 32
            or any(not isinstance(item, FormalRepairObservation)
                   for item in self.observations)
            or len({item.replacement for item in self.observations})
            != len(self.observations)
        ):
            raise RepairError("repair_observation_invalid")

    def __repr__(self) -> str:
        return "<FormalFailedStep: process-local>"

    def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
        raise TypeError("repair_private_serialization_forbidden")

    def __getstate__(self) -> Never:
        raise TypeError("repair_private_serialization_forbidden")


class FormalLocatorProposal(Contract):
    schema_version: Literal["browser_formal_locator_proposal.v1"] = (
        "browser_formal_locator_proposal.v1"
    )
    proposal_id: OpaqueId
    source_kind: Literal["published_skill", "teaching_draft"] = "teaching_draft"
    draft_digest: Digest
    source_digest: Digest
    publication_digest: Digest
    failed_run_id: OpaqueId
    step_id: OpaqueId
    primitive_id: OpaqueId
    original_step_digest: Digest
    failure_evidence_digest: Digest
    observation_evidence_digest: Digest
    replacement: LocatorHint
    digest: Digest

    @model_validator(mode="after")
    def integrity(self) -> Self:
        if canonical_digest(self.schema_version, self.model_dump(mode="json")) != self.digest:
            raise ValueError("repair_formal_proposal_digest_mismatch")
        return self


@dataclass(frozen=True, slots=True, repr=False)
class FormalRepairAuthority:
    compiler: DurableTeachingCompilerAuthority | None
    current_binding: Callable[[], ScopeBinding]
    failed_step: Callable[[str, str, ScopeBinding], FormalFailedStep | None]
    approved_hints: Callable[
        [BrowserPublicationManifest, str, ScopeBinding], tuple[LocatorHint, ...]
    ]
    authorize: Callable[
        [DurableTeachingDraft | BrowserPublicationManifest, ScopeBinding], bool
    ]

    def __post_init__(self) -> None:
        if (self.compiler is not None
            and not isinstance(self.compiler, DurableTeachingCompilerAuthority)) or not all(
            callable(value) for value in (
                self.current_binding, self.failed_step,
                self.approved_hints, self.authorize,
            )
        ):
            raise RepairError("repair_formal_authority_required")

    def __repr__(self) -> str:
        return "<FormalRepairAuthority: process-local>"

    def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
        raise TypeError("repair_private_serialization_forbidden")

    def __getstate__(self) -> Never:
        raise TypeError("repair_private_serialization_forbidden")


def _formal_hint(
    hint: LocatorHint, publication: BrowserPublicationManifest,
    business_key_approved: bool,
) -> None:
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
        policy = publication.site.policy
        if value not in (*policy.allowed_names, *policy.allowed_context,
                         *policy.allowed_row_labels, *policy.allowed_column_labels):
            raise RepairError("repair_hint_unapproved")
    elif hint.kind == "business_key":
        if (value != publication.site.read_rule.key_ref.name
                or not business_key_approved):
            raise RepairError("repair_hint_unapproved")
    else:
        raise RepairError("repair_hint_unsupported")


def propose_formal_locator(
    source: DurableTeachingDraft | BrowserPublicationManifest,
    *,
    binding: ScopeBinding,
    failed_run_id: str,
    step_id: str,
    replacement: LocatorHint,
    authority: FormalRepairAuthority,
) -> FormalLocatorProposal:
    """Propose one locator change for a proven failed published or replay step."""
    try:
        binding = _binding_copy(binding)
        if isinstance(source, DurableTeachingDraft):
            checked_draft = DurableTeachingDraft(
                source.binding, source.source, source.publication,
                source.parameter_whitelist, source.digest,
            )
            draft: DurableTeachingDraft | None = checked_draft
            publication = checked_draft.publication
            source_kind: Literal["published_skill", "teaching_draft"] = "teaching_draft"
            source_digest = checked_draft.source.manifest.digest
            proposal_source_digest = checked_draft.digest
            business_key_approved = any(
                item.name == publication.site.read_rule.key_ref.name
                and item.purpose == "business_key"
                for item in checked_draft.parameter_whitelist
            )
        elif isinstance(source, BrowserPublicationManifest):
            draft = None
            publication = BrowserPublicationManifest.model_validate_json(
                source.model_dump_json()
            )
            source_kind = "published_skill"
            source_digest = proposal_source_digest = publication.digest
            business_key_approved = (
                publication.site.read_rule.key_ref.name
                in publication.skill.parameters
            )
        else:
            raise RepairError("repair_formal_unavailable")
        candidate_source: DurableTeachingDraft | BrowserPublicationManifest = (
            draft if draft is not None else publication
        )
        TypeAdapter(OpaqueId).validate_python(failed_run_id, strict=True)
        TypeAdapter(OpaqueId).validate_python(step_id, strict=True)
        replacement = LocatorHint.model_validate_json(replacement.model_dump_json())
        if (
            publication.source_kind != "registered_site"
            or (draft is not None and binding != draft.binding)
            or authority.current_binding() != binding
            or authority.authorize(candidate_source, binding) is not True
        ):
            raise RepairError("repair_denied")
        if draft is not None:
            if authority.compiler is None:
                raise RepairError("repair_formal_authority_required")
            current = compile_authorized_run(
                draft.source, binding=binding,
                target_skill_id=publication.skill.skill_id,
                version=publication.skill.version,
                site_version=publication.site.version,
                parameter_whitelist=draft.parameter_whitelist,
                authority=authority.compiler,
            )
            if current != draft:
                raise RepairError("repair_draft_stale")
        index = next((i for i, step in enumerate(publication.skill.steps)
                      if step.step_id == step_id), None)
        if index is None:
            raise RepairError("repair_step_unknown")
        step = publication.skill.steps[index]
        primitive_id = next((
            item.primitive_id for item in publication.site.step_primitives
            if item.step_id == step_id
        ), step_id if not publication.site.step_primitives else None)
        if primitive_id is None:
            raise RepairError("repair_step_unknown")
        failure = authority.failed_step(failed_run_id, step_id, binding)
        if failure is None:
            raise RepairError("repair_failure_unavailable")
        failure = FormalFailedStep(
            failure.binding, failure.run_id, failure.publication_digest,
            failure.step_id, failure.reason, failure.dispatch_state,
            failure.evidence_digest,
        )
        if (
            failure.binding.owner != binding.owner
            or failure.binding.binding_id != binding.binding_id
            or failure.binding.binding_revision != binding.binding_revision
            or failure.binding.authorization_run_id != failed_run_id
            or failure.binding.evidence_version != "verified-session-v1"
            or failure.run_id != failed_run_id
            or failure.publication_digest != publication.digest
            or failure.step_id != step_id
        ):
            raise RepairError("repair_failure_scope_mismatch")
        hints = authority.approved_hints(publication, step_id, binding)
        if type(hints) is not tuple or not hints or len(hints) > 32:
            raise RepairError("repair_hints_unavailable")
        checked_hints = tuple(LocatorHint.model_validate_json(item.model_dump_json())
                              for item in hints)
        if len(set(checked_hints)) != len(checked_hints):
            raise RepairError("repair_hints_duplicate")
        for hint in checked_hints:
            _formal_hint(hint, publication, business_key_approved)
        if replacement not in checked_hints or replacement == step.locator:
            raise RepairError("repair_hint_unapproved")
        observation = next((item for item in failure.observations
                            if item.replacement == replacement), None)
        rule = next((item for item in publication.site.steps
                     if item.step.step_id == step_id), None)
        if (observation is None or rule is None
                or observation.scope.region_id != rule.observation.region_id):
            raise RepairError("repair_observation_unavailable")
        observation_digest = observation.digest
        seed = canonical_digest("browser_formal_locator_id.v1", {
            "source_kind": source_kind, "source_digest": proposal_source_digest,
            "step_id": step_id,
            "failure_evidence_digest": failure.evidence_digest,
            "replacement": replacement.model_dump(mode="json"),
        })
        payload: dict[str, object] = dict(
            schema_version="browser_formal_locator_proposal.v1",
            proposal_id=f"proposal_{seed}", source_kind=source_kind,
            draft_digest=proposal_source_digest,
            source_digest=source_digest,
            publication_digest=publication.digest,
            failed_run_id=failed_run_id, step_id=step_id,
            primitive_id=primitive_id,
            original_step_digest=canonical_digest(
                "browser_skill_step.v1", step.model_dump(mode="json"),
            ),
            failure_evidence_digest=failure.evidence_digest,
            observation_evidence_digest=observation_digest,
            replacement=replacement.model_dump(mode="json"),
        )
        payload["digest"] = canonical_digest(
            "browser_formal_locator_proposal.v1", payload,
        )
        proposal = FormalLocatorProposal.model_validate_json(json.dumps(payload))
        if (
            authority.current_binding() != binding
            or authority.authorize(candidate_source, binding) is not True
            or authority.failed_step(failed_run_id, step_id, binding) != failure
            or authority.approved_hints(publication, step_id, binding) != hints
        ):
            raise RepairError("repair_evidence_stale")
        return proposal
    except RepairError:
        raise
    except Exception:
        raise RepairError("repair_formal_unavailable") from None


class ExplorationRegionFeedback(Contract):
    """Policy-projected region summary; no DOM, values, refs or URLs."""

    region_id: OpaqueId
    coverage: Coverage
    candidate_count: Annotated[int, Field(ge=0, le=255)]
    actionable_count: Annotated[int, Field(ge=0, le=255)]
    roles: Annotated[tuple[Role, ...], Field(max_length=16)] = ()
    approved_names: Annotated[tuple[SafeText, ...], Field(max_length=16)] = ()

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if (self.actionable_count > self.candidate_count
                or len(set(self.roles)) != len(self.roles)
                or len(set(self.approved_names)) != len(self.approved_names)):
            raise ValueError("exploration_feedback_invalid")
        return self


class ExplorationFeedback(Contract):
    regions: Annotated[tuple[ExplorationRegionFeedback, ...], Field(min_length=1, max_length=32)]
    last_primitive_id: OpaqueId | None = None
    last_region_id: OpaqueId | None = None
    last_dispatch_state: Literal["acknowledged"] | None = None

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if (len({item.region_id for item in self.regions}) != len(self.regions)
                or (self.last_primitive_id is None) != (self.last_region_id is None)
                or (self.last_primitive_id is None) != (self.last_dispatch_state is None)):
            raise ValueError("exploration_feedback_invalid")
        return self


@dataclass(frozen=True, slots=True, repr=False)
class ReadOnlyExplorationAuthority:
    """Only a bounded next-primitive proposal; execution stays in the sole Run."""

    current_binding: Callable[[], ScopeBinding]
    authorize: Callable[[str, ScopeBinding, BrowserPublicationManifest], Awaitable[bool]]
    registered_rule: Callable[[str, BrowserPublicationManifest], SiteStepRule | None]
    suggest_next: Callable[
        [str, tuple[str, ...], ExplorationFeedback], Awaitable[str | None]
    ]
    observe_current: Callable[
        [str, ScopeBinding, BrowserPublicationManifest, tuple[RunTeachingStep, ...]],
        Awaitable[ExplorationFeedback],
    ]
    execute_step: Callable[[str, str, ScopeBinding], Awaitable[RunTeachingStep]]
    clock: Callable[[], float]
    deadline_monotonic: float
    maximum_steps: int
    allowed_regions: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            not all(callable(value) for value in (
                self.current_binding, self.authorize, self.registered_rule,
                self.suggest_next, self.observe_current, self.execute_step, self.clock,
            ))
            or type(self.maximum_steps) is not int
            or not 1 <= self.maximum_steps <= 32
            or type(self.allowed_regions) is not tuple
            or not 1 <= len(self.allowed_regions) <= 32
            or len(set(self.allowed_regions)) != len(self.allowed_regions)
            or type(self.deadline_monotonic) not in (int, float)
            or not math.isfinite(self.deadline_monotonic)
        ):
            raise RepairError("exploration_authority_invalid")
        for region in self.allowed_regions:
            TypeAdapter(OpaqueId).validate_python(region, strict=True)

    def __repr__(self) -> str:
        return "<ReadOnlyExplorationAuthority: process-local>"

    def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
        raise TypeError("repair_private_serialization_forbidden")

    def __getstate__(self) -> Never:
        raise TypeError("repair_private_serialization_forbidden")


async def explore_registered_reads(
    run_id: str,
    *,
    binding: ScopeBinding,
    publication: BrowserPublicationManifest,
    authority: ReadOnlyExplorationAuthority,
) -> tuple[RunTeachingStep, ...]:
    """Bound read-only suggestions to registered primitives and one controlled Run.

    The returned facts are not a draft or source. The Run must independently
    finish verified before capture_authorized_run can use its audit.
    """
    try:
        run_id = TypeAdapter(OpaqueId).validate_python(run_id, strict=True)
        binding = _binding_copy(binding)
        publication = BrowserPublicationManifest.model_validate_json(
            publication.model_dump_json()
        )
        emitted: list[RunTeachingStep] = []
        for _ in range(authority.maximum_steps):
            now = authority.clock()
            if type(now) not in (int, float) or not math.isfinite(now) or now >= (
                authority.deadline_monotonic
            ):
                raise RepairError("exploration_deadline")
            if authority.current_binding() != binding or await authority.authorize(
                run_id, binding, publication,
            ) is not True:
                raise RepairError("exploration_denied")
            feedback = ExplorationFeedback.model_validate(await authority.observe_current(
                run_id, binding, publication, tuple(emitted),
            ))
            if (tuple(item.region_id for item in feedback.regions)
                    != authority.allowed_regions
                    or feedback.last_primitive_id
                    != (emitted[-1].primitive_id if emitted else None)
                    or feedback.last_region_id != (
                        emitted[-1].event.scope.region_id if emitted else None
                    )):
                raise RepairError("exploration_feedback_invalid")
            if authority.current_binding() != binding or await authority.authorize(
                run_id, binding, publication,
            ) is not True:
                raise RepairError("exploration_denied")
            primitive_id = await authority.suggest_next(
                run_id, tuple(item.primitive_id for item in emitted), feedback,
            )
            if primitive_id is None:
                return tuple(emitted)
            primitive_id = TypeAdapter(OpaqueId).validate_python(
                primitive_id, strict=True,
            )
            raw_rule = authority.registered_rule(primitive_id, publication)
            if raw_rule is None:
                raise RepairError("exploration_primitive_unregistered")
            rule = SiteStepRule.model_validate_json(raw_rule.model_dump_json())
            if (
                rule.step.step_id != primitive_id
                or rule.step.effect != "read_only"
                or rule.effect.actual_effect != "read_only"
                or rule.step.operation not in {"read", "click"}
                or rule.observation.region_id not in authority.allowed_regions
            ):
                raise RepairError("exploration_effect_denied")
            if authority.current_binding() != binding or await authority.authorize(
                run_id, binding, publication,
            ) is not True:
                raise RepairError("exploration_denied")
            fact = RunTeachingStep.model_validate_json((await authority.execute_step(
                run_id, primitive_id, binding,
            )).model_dump_json())
            if (
                fact.run_id != run_id
                or fact.skill_version != publication.skill.version
                or fact.skill_digest != publication.skill.digest
                or fact.binding != BindingRevisions.from_binding(binding)
                or fact.primitive_id != primitive_id
                or not fact.event.matches_step(rule.step)
                or fact.event.effect_proof != rule.effect
                or fact.rule_digest != canonical_digest(
                    "browser_site_step.v1", rule.model_dump(mode="json"),
                )
            ):
                raise RepairError("exploration_audit_mismatch")
            emitted.append(fact)
        return tuple(emitted)
    except RepairError:
        raise
    except BrowserOperationError:
        raise
    except Exception:
        raise RepairError("exploration_unavailable") from None
