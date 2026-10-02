"""Compile authorized neutral events into a constrained, unpublished SkillDraft.

The trusted composition supplies current owner, publication, source grant and
registered Site facts. This module neither observes a page nor runs a step.
Historical event targets are checked against their capture grant; a moved page
does not retroactively turn a recorded event into a current target.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Never, SupportsIndex

from pydantic import TypeAdapter

from app.browser_skill.models import (
    BrowserSkill,
    Epoch,
    OpaqueId,
    ParameterPurpose,
    ScopeBinding,
    SkillStep,
)
from app.browser_skill.registry import Publication, canonical_digest
from app.browser_skill.site_rules import FrozenSiteAdapter, RegisteredSitePlan
from app.browser_skill.trajectory import (
    BindingRevisions,
    CapturePermission,
    DraftManifest,
    NeutralTrajectory,
    ParameterApproval,
    PublicationReference,
    RecordingContext,
    SkillDraft,
    TrajectoryEvent,
    _binding_copy,
)


class TeachingError(ValueError):
    """Fixed codes only; never include private callback data in diagnostics."""


@dataclass(frozen=True, slots=True, repr=False)
class TeachingContext:
    """Process-local, trusted composition callbacks; not a public request.

    ``active_base`` reads a publication and its revision together. ``source_head``
    must read the owner-scoped stored trajectory revision and digest. These
    callbacks are rechecked through compilation, but cannot provide a durable
    cross-process transaction at this source-only checkpoint.
    """

    recording: RecordingContext
    active_base: Callable[[], tuple[Publication, int]]
    source_head: Callable[[str, ScopeBinding], tuple[int, str]]
    deadline_monotonic: float
    clock: Callable[[], float]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.recording, RecordingContext)
            or not all(callable(cb) for cb in (self.active_base, self.source_head, self.clock))
            or not math.isfinite(self.deadline_monotonic)
        ):
            raise TeachingError("teaching_context_invalid")

    def __repr__(self) -> str:
        return "<TeachingContext: process-local>"

    def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
        raise TypeError("teaching_private_serialization_forbidden")

    def __getstate__(self) -> Never:
        raise TypeError("teaching_private_serialization_forbidden")


def canonical_step_id(event: TrajectoryEvent) -> str:
    """A stable one-to-one source event key, including repeated source steps."""
    checked = TrajectoryEvent.model_validate_json(event.model_dump_json())
    digest = canonical_digest("teaching_step.v1", checked.model_dump(mode="json"))
    return TypeAdapter(OpaqueId).validate_python(f"event_{checked.sequence:03d}_{digest}")


def _read_current(
    context: TeachingContext,
    source: NeutralTrajectory,
    binding: ScopeBinding,
    publication: Publication,
    base_revision: int,
    source_revision: int,
) -> None:
    try:
        now = context.clock()
        if (
            type(now) not in (int, float)
            or not math.isfinite(now)
            or now >= context.deadline_monotonic
        ):
            raise TeachingError("teaching_authorization_expired")
        if _binding_copy(context.recording.current_binding()) != binding:
            raise TeachingError("teaching_binding_stale")
        active, revision = context.active_base()
        active = Publication.model_validate_json(active.model_dump_json())
        if type(revision) is not int or revision != base_revision or active != publication:
            raise TeachingError("teaching_base_stale")
        current_source_revision, current_source_digest = context.source_head(
            source.manifest.trajectory_id, _binding_copy(binding)
        )
        if (
            type(current_source_revision) is not int
            or current_source_revision != source_revision
            or current_source_digest != source.manifest.digest
        ):
            raise TeachingError("teaching_source_stale")
    except TeachingError:
        raise
    except Exception:
        raise TeachingError("teaching_authorization_unavailable") from None


def _grant(
    context: TeachingContext,
    source: NeutralTrajectory,
    binding: ScopeBinding,
    reference: PublicationReference,
    event: TrajectoryEvent | None,
) -> CapturePermission:
    manifest = source.manifest
    try:
        raw = context.recording.source_permission(
            manifest.trajectory_id, manifest.source_ref, _binding_copy(binding), reference
        )
        if raw is None:
            raise TeachingError("teaching_source_denied")
        permission = CapturePermission(
            raw.trajectory_id,
            raw.source_ref,
            raw.binding,
            raw.publication,
            raw.authorization_digest,
            raw.base_revision,
            raw.steps,
        )
        if (
            permission.trajectory_id != manifest.trajectory_id
            or permission.source_ref != manifest.source_ref
            or permission.binding != binding
            or permission.publication != reference
            or permission.authorization_digest != manifest.source_authorization_digest
            or permission.base_revision != manifest.base_revision
        ):
            raise TeachingError("teaching_source_permission_stale")
        if (
            context.recording.authorize(
                _binding_copy(binding), manifest.source_ref, reference, event
            )
            is not True
        ):
            raise TeachingError("teaching_denied")
        return permission
    except TeachingError:
        raise
    except Exception:
        raise TeachingError("teaching_authorization_unavailable") from None


def _check_event(
    event: TrajectoryEvent,
    publication: Publication,
    plan: RegisteredSitePlan,
    permission: CapturePermission,
) -> SkillStep:
    step = next((item for item in publication.skill.steps if item.step_id == event.step_id), None)
    rule = next((item for item in plan.steps if item.step.step_id == event.step_id), None)
    grant = next((item for item in permission.steps if item.step_id == event.step_id), None)
    if step is None or rule is None or grant is None or not event.matches_step(step):
        raise TeachingError("teaching_step_unregistered")
    if (
        rule.step != step
        or rule.observation.region_id != event.scope.region_id
        or rule.effect != event.effect_proof
        or grant.effect != rule.effect
        or grant.policy != plan.policy
        or grant.effect.actual_effect == "unknown"
    ):
        raise TeachingError("teaching_step_evidence_mismatch")
    if event.policy_digest != grant.policy.digest or not set(event.approved_labels) <= set(
        (*grant.policy.allowed_names, *grant.policy.allowed_context)
    ):
        raise TeachingError("teaching_labels_unapproved")
    return step


def _required_purposes(
    plan: RegisteredSitePlan, skill: BrowserSkill
) -> dict[str, ParameterPurpose]:
    required: dict[str, ParameterPurpose] = {}
    refs: list[tuple[str, ParameterPurpose]] = [
        (plan.read_rule.key_ref.name, "business_key"),
        *((field.value_ref.name, "expected_field") for field in plan.read_rule.fields),
    ]
    for step in skill.steps:
        if step.value_ref is not None:
            refs.append((step.value_ref.name, "fill_value"))
        if step.url_ref is not None:
            refs.append((step.url_ref.name, "navigation_url"))
        if step.option_ref is not None:
            refs.append((step.option_ref.name, "option_values"))
    for name, purpose in refs:
        if name in required and required[name] != purpose:
            raise TeachingError("teaching_parameter_purpose_conflict")
        required[name] = purpose
    return required


def _check_whitelist(
    approvals: tuple[ParameterApproval, ...], plan: RegisteredSitePlan, skill: BrowserSkill
) -> tuple[ParameterApproval, ...]:
    if type(approvals) is not tuple:
        raise TeachingError("teaching_whitelist_invalid")
    try:
        checked = tuple(
            ParameterApproval.model_validate_json(item.model_dump_json()) for item in approvals
        )
    except Exception:
        raise TeachingError("teaching_whitelist_invalid") from None
    if tuple(item.name for item in checked) != skill.parameters:
        raise TeachingError("teaching_whitelist_invalid")
    required = _required_purposes(plan, skill)
    if any(required.get(item.name, item.purpose) != item.purpose for item in checked):
        raise TeachingError("teaching_parameter_purpose_mismatch")
    return checked


def validate_draft_route_shape(
    source: NeutralTrajectory,
    draft: SkillDraft,
    plan: RegisteredSitePlan,
) -> None:
    """Prove a one-to-one same-Executor rule shape, without registering it.

    Repeated captured events get distinct draft IDs, so the original Site plan
    cannot bootstrap the draft. This candidate mapping is intentionally local:
    it does not install rules, approve hints or make a draft executable.
    """
    try:
        events = source.manifest.events
        steps = draft.manifest.skill.steps
        if len(events) != len(steps):
            raise TeachingError("teaching_route_mismatch")
        rules = {rule.step.step_id: rule for rule in plan.steps}
        mapped = []
        for event, step in zip(events, steps, strict=True):
            rule = rules.get(event.step_id)
            if rule is None or step.step_id != canonical_step_id(event):
                raise TeachingError("teaching_route_mismatch")
            original = rule.step
            if (
                step.operation != original.operation
                or step.effect != original.effect
                or step.value_ref != original.value_ref
                or step.url_ref != original.url_ref
                or step.option_ref != original.option_ref
            ):
                raise TeachingError("teaching_route_mismatch")
            mapped.append(rule.model_copy(update={"step": step}))
        candidate = plan.model_copy(
            update={
                "skill_version": draft.manifest.skill.version,
                "skill_digest": draft.manifest.skill.digest,
                "steps": tuple(mapped),
            }
        )
        candidate = RegisteredSitePlan.model_validate_json(candidate.model_dump_json())
        candidate.validate_skill(draft.manifest.skill)
    except TeachingError:
        raise
    except Exception:
        raise TeachingError("teaching_route_mismatch") from None


def validate_teaching_source(
    source: NeutralTrajectory,
    *,
    binding: ScopeBinding,
    publication: Publication,
    base_revision: int,
    expected_source_revision: int,
    site: FrozenSiteAdapter,
    context: TeachingContext,
) -> tuple[NeutralTrajectory, Publication, RegisteredSitePlan]:
    """Revalidate full private ownership, grant, site rules and every event."""
    try:
        checked_binding = _binding_copy(binding)
        checked_source = NeutralTrajectory(source.binding, source.manifest)
        checked_publication = Publication.model_validate_json(publication.model_dump_json())
        TypeAdapter(Epoch).validate_python(base_revision, strict=True)
        TypeAdapter(Epoch).validate_python(expected_source_revision, strict=True)
        if checked_source.binding != checked_binding or checked_source.manifest.binding != (
            BindingRevisions.from_binding(checked_binding)
        ):
            raise TeachingError("teaching_owner_or_binding_mismatch")
        manifest = checked_source.manifest
        reference = PublicationReference.from_publication(checked_publication)
        if manifest.base_publication != reference or manifest.base_revision != base_revision:
            raise TeachingError("teaching_base_mismatch")
        if manifest.revision != expected_source_revision or not manifest.events:
            raise TeachingError("teaching_source_revision_invalid")
        if len(manifest.events) > 64:
            raise TeachingError("teaching_too_many_steps")
        plan = site.bootstrap(checked_publication.skill)
        _read_current(
            context,
            checked_source,
            checked_binding,
            checked_publication,
            base_revision,
            expected_source_revision,
        )
        for event in manifest.events:
            permission = _grant(context, checked_source, checked_binding, reference, event)
            _check_event(event, checked_publication, plan, permission)
            _read_current(
                context,
                checked_source,
                checked_binding,
                checked_publication,
                base_revision,
                expected_source_revision,
            )
        _grant(context, checked_source, checked_binding, reference, None)
        _read_current(
            context,
            checked_source,
            checked_binding,
            checked_publication,
            base_revision,
            expected_source_revision,
        )
        return checked_source, checked_publication, plan
    except TeachingError:
        raise
    except Exception:
        raise TeachingError("teaching_source_unsupported") from None


def compile_trajectory(
    source: NeutralTrajectory,
    *,
    binding: ScopeBinding,
    publication: Publication,
    base_revision: int,
    expected_source_revision: int,
    parameter_whitelist: tuple[ParameterApproval, ...],
    site: FrozenSiteAdapter,
    context: TeachingContext,
) -> SkillDraft:
    """Produce a review-only draft from the exact registered source event order."""
    source, publication, plan = validate_teaching_source(
        source,
        binding=binding,
        publication=publication,
        base_revision=base_revision,
        expected_source_revision=expected_source_revision,
        site=site,
        context=context,
    )
    approvals = _check_whitelist(parameter_whitelist, plan, publication.skill)
    base_steps = {step.step_id: step for step in publication.skill.steps}
    steps = tuple(
        SkillStep.model_validate_json(
            base_steps[event.step_id]
            .model_copy(update={"step_id": canonical_step_id(event)})
            .model_dump_json()
        )
        for event in source.manifest.events
    )
    version = f"draft_{source.manifest.digest}"
    skill_payload = publication.skill.model_dump(mode="json")
    skill_payload.update(
        version=version,
        parameters=[item.name for item in approvals],
        steps=[step.model_dump(mode="json") for step in steps],
    )
    skill_payload["digest"] = canonical_digest("browser_skill.v1", skill_payload)
    skill = BrowserSkill.model_validate_json(json.dumps(skill_payload))
    payload: dict[str, object] = dict(
        schema_version="skill_draft.v1",
        draft_id=f"draft_{source.manifest.digest}",
        base_publication=source.manifest.base_publication.model_dump(mode="json"),
        base_revision=base_revision,
        draft_revision=1,
        parameter_whitelist=[item.model_dump(mode="json") for item in approvals],
        skill=skill.model_dump(mode="json"),
        state="draft",
        validation_digest=None,
        rejection=None,
    )
    payload["digest"] = canonical_digest("skill_draft.v1", payload)
    draft = SkillDraft(binding, DraftManifest.model_validate_json(json.dumps(payload)))
    validate_draft_route_shape(source, draft, plan)
    _read_current(
        context, source, draft.binding, publication, base_revision, expected_source_revision
    )
    _grant(context, source, draft.binding, source.manifest.base_publication, None)
    _read_current(
        context, source, draft.binding, publication, base_revision, expected_source_revision
    )
    return draft
