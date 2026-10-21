"""Compile authorized neutral events into constrained unpublished drafts.

The durable candidate path composes registered new primitives under the current
Run, Site and permission facts. It neither observes a page nor runs a step.
The older synthetic draft compiler remains for its existing contract callers.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Never, SupportsIndex

from pydantic import TypeAdapter

from app.browser_skill.models import (
    BrowserOwner,
    BrowserSkill,
    Epoch,
    OpaqueId,
    ParameterPurpose,
    ScopeBinding,
    SkillStep,
)
from app.browser_skill.publication_contracts import (
    BrowserPublicationManifest,
    BrowserSiteDefinition,
    publication_digest,
)
from app.browser_skill.registry import Publication, canonical_digest
from app.browser_skill.site_rules import (
    FrozenSiteAdapter,
    RegisteredReadRule,
    RegisteredSitePlan,
    StepPrimitiveBinding,
)
from app.browser_skill.trajectory import (
    BindingRevisions,
    CapturePermission,
    DraftManifest,
    NeutralTrajectory,
    ParameterApproval,
    PublicationReference,
    RecordingContext,
    RunTeachingAuthority,
    RunTeachingSource,
    RunTeachingStep,
    SkillDraft,
    TrajectoryEvent,
    _binding_copy,
    capture_authorized_run,
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
    if not isinstance(plan.read_rule, RegisteredReadRule):
        raise TeachingError("teaching_query_read_unsupported")
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


@dataclass(frozen=True, slots=True, repr=False)
class DurableTeachingDraft:
    """Review-only immutable candidate using the real PG publication contract."""

    binding: ScopeBinding
    source: RunTeachingSource
    publication: BrowserPublicationManifest
    parameter_whitelist: tuple[ParameterApproval, ...]
    digest: str

    def __post_init__(self) -> None:
        checked = _binding_copy(self.binding)
        source = RunTeachingSource(self.source.binding, self.source.manifest)
        publication = BrowserPublicationManifest.model_validate_json(
            self.publication.model_dump_json()
        )
        approvals = tuple(ParameterApproval.model_validate_json(item.model_dump_json())
                          for item in self.parameter_whitelist)
        if checked != source.binding or tuple(item.name for item in approvals) != (
            publication.skill.parameters
        ):
            raise TeachingError("teaching_draft_binding_invalid")
        expected = canonical_digest("browser_durable_teaching_draft.v1", {
            "source_digest": source.manifest.digest,
            "publication_digest": publication.digest,
            "parameter_whitelist": [item.model_dump(mode="json") for item in approvals],
        })
        if self.digest != expected:
            raise TeachingError("teaching_draft_digest_invalid")
        object.__setattr__(self, "binding", checked)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "publication", publication)
        object.__setattr__(self, "parameter_whitelist", approvals)

    def __repr__(self) -> str:
        return "<DurableTeachingDraft: process-local>"

    def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
        raise TypeError("teaching_private_serialization_forbidden")

    def __getstate__(self) -> Never:
        raise TypeError("teaching_private_serialization_forbidden")


@dataclass(frozen=True, slots=True, repr=False)
class DurableTeachingCompilerAuthority:
    """Trusted active catalog/grant checks, never external request assertions."""

    run: RunTeachingAuthority
    authorize_target: Callable[[ScopeBinding, BrowserPublicationManifest, str], bool]
    unpublished_combination: Callable[[ScopeBinding, str, tuple[str, ...]], bool]

    def __post_init__(self) -> None:
        if not isinstance(self.run, RunTeachingAuthority) or not callable(
            self.authorize_target
        ) or not callable(self.unpublished_combination):
            raise TeachingError("teaching_compiler_authority_required")

    def __repr__(self) -> str:
        return "<DurableTeachingCompilerAuthority: process-local>"

    def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
        raise TypeError("teaching_private_serialization_forbidden")

    def __getstate__(self) -> Never:
        raise TypeError("teaching_private_serialization_forbidden")


def _formal_required_purposes(
    base: BrowserPublicationManifest, steps: tuple[SkillStep, ...],
) -> dict[str, ParameterPurpose]:
    required: dict[str, ParameterPurpose] = {
        base.site.read_rule.key_ref.name: "business_key",
    }
    if isinstance(base.site.read_rule, RegisteredReadRule):
        for field in base.site.read_rule.fields:
            name = field.value_ref.name
            if name in required and required[name] != "expected_field":
                raise TeachingError("teaching_parameter_purpose_conflict")
            required[name] = "expected_field"
    for step in steps:
        for ref, purpose in (
            (step.value_ref, "fill_value"),
            (step.url_ref, "navigation_url"),
            (step.option_ref, "option_values"),
        ):
            if ref is None:
                continue
            if ref.name in required and required[ref.name] != purpose:
                raise TeachingError("teaching_parameter_purpose_conflict")
            required[ref.name] = purpose  # type: ignore[assignment]
    return required


def teaching_grant_scope_digest(
    owner: BrowserOwner, base: BrowserPublicationManifest,
    candidate: BrowserPublicationManifest,
) -> str:
    """Bind a teaching grant request to exact owner and both frozen versions.

    This digest identifies the scope. Current grant checks remain mandatory.
    """
    return canonical_digest("browser.teaching_grant_scope.v1", {
        "owner": [owner.tenant_id, owner.user_id, owner.session_id],
        "base": [base.digest, base.skill.skill_id, base.skill.digest],
        "candidate": [candidate.digest, candidate.skill.skill_id,
                      candidate.skill.digest],
    })


def initial_validation_scope_digest(
    owner: BrowserOwner, publication: BrowserPublicationManifest,
) -> str:
    """Value-free identity for one prepared, first-version validation Run."""
    return canonical_digest("browser.initial_validation_scope.v1", {
        "owner": [owner.tenant_id, owner.user_id, owner.session_id],
        "publication": [publication.digest, publication.skill.skill_id,
                        publication.skill.digest],
    })


def build_registered_teaching_candidate(
    base: BrowserPublicationManifest, *, target_skill_id: str,
    version: str, site_version: str,
    steps: tuple[StepPrimitiveBinding, ...],
    parameter_whitelist: tuple[ParameterApproval, ...],
) -> BrowserPublicationManifest:
    """Build the actual candidate before a teaching Run is admitted.

    Only the audited base Site primitive catalog supplies action rules. The
    caller must separately prove current grants, unpublished sequence and
    owner-scoped input; this pure builder grants no execution authority.
    """
    try:
        base = BrowserPublicationManifest.model_validate_json(base.model_dump_json())
        target_skill_id = TypeAdapter(OpaqueId).validate_python(target_skill_id, strict=True)
        version = TypeAdapter(OpaqueId).validate_python(version, strict=True)
        site_version = TypeAdapter(OpaqueId).validate_python(site_version, strict=True)
        if (
            base.source_kind != "registered_site"
            or base.effect != "read_only"
            or not base.site.primitive_rules
            or type(steps) is not tuple or not 1 <= len(steps) <= 64
            or type(parameter_whitelist) is not tuple
            or site_version == base.site.version
            or (target_skill_id == base.skill.skill_id
                and version == base.skill.version)
        ):
            raise TeachingError("teaching_candidate_invalid")
        bindings = tuple(
            StepPrimitiveBinding.model_validate_json(item.model_dump_json())
            for item in steps
        )
        if len({item.step_id for item in bindings}) != len(bindings):
            raise TeachingError("teaching_candidate_duplicate_step")
        catalog = {rule.step.step_id: rule for rule in base.site.primitive_rules}
        if len(catalog) != len(base.site.primitive_rules):
            raise TeachingError("teaching_primitive_unregistered")
        rules = []
        for item in bindings:
            source_rule = catalog.get(item.primitive_id)
            if (
                source_rule is None
                or source_rule.step.effect != "read_only"
                or source_rule.effect.actual_effect != "read_only"
            ):
                raise TeachingError("teaching_primitive_unregistered")
            rules.append(source_rule.model_copy(update={
                "step": source_rule.step.model_copy(update={"step_id": item.step_id}),
            }))
        checked_rules = tuple(
            type(rule).model_validate_json(rule.model_dump_json()) for rule in rules
        )
        skill_steps = tuple(rule.step for rule in checked_rules)
        approvals = tuple(
            ParameterApproval.model_validate_json(item.model_dump_json())
            for item in parameter_whitelist
        )
        required = _formal_required_purposes(base, skill_steps)
        if ({item.name: item.purpose for item in approvals} != required
                or len(approvals) != len(required)):
            raise TeachingError("teaching_whitelist_invalid")
        site_payload = base.site.model_dump(mode="json")
        traversal = site_payload.get("traversal")
        if isinstance(traversal, dict):
            advance = next((
                item.step_id for item in bindings
                if item.primitive_id == traversal["advance_step_id"]
            ), None)
            site_payload["traversal"] = (
                {**traversal, "advance_step_id": advance}
                if advance is not None else None
            )
        site_payload.update(
            version=site_version,
            steps=[rule.model_dump(mode="json") for rule in checked_rules],
            step_primitives=[item.model_dump(mode="json") for item in bindings],
        )
        site_payload["digest"] = publication_digest(
            "browser_site.publication.v1", site_payload,
        )
        site = BrowserSiteDefinition.model_validate(site_payload)
        skill_payload = base.skill.model_dump(mode="json")
        skill_payload.update(
            skill_id=target_skill_id, version=version,
            site_digest=site.digest,
            parameters=[item.name for item in approvals],
            steps=[item.model_dump(mode="json") for item in skill_steps],
        )
        skill_payload["digest"] = canonical_digest("browser_skill.v1", skill_payload)
        skill = BrowserSkill.model_validate(skill_payload)
        candidate_payload = base.model_dump(mode="json")
        candidate_payload.update(
            skill=skill.model_dump(mode="json"),
            site=site.model_dump(mode="json"),
        )
        candidate_payload["digest"] = publication_digest(
            "browser_publication.durable.v1", candidate_payload,
        )
        return BrowserPublicationManifest.model_validate(candidate_payload)
    except TeachingError:
        raise
    except Exception:
        raise TeachingError("teaching_candidate_invalid") from None


def compile_authorized_run(
    source: RunTeachingSource,
    *,
    binding: ScopeBinding,
    target_skill_id: str,
    version: str,
    site_version: str,
    parameter_whitelist: tuple[ParameterApproval, ...],
    authority: DurableTeachingCompilerAuthority,
) -> DurableTeachingDraft:
    """Compile a genuinely new order of registered read-only primitives.

    Every rule is re-read from the trusted Site catalog. This only constructs an
    immutable candidate; the existing publication store remains sole authority.
    """
    try:
        binding = _binding_copy(binding)
        target_skill_id = TypeAdapter(OpaqueId).validate_python(target_skill_id, strict=True)
        version = TypeAdapter(OpaqueId).validate_python(version, strict=True)
        site_version = TypeAdapter(OpaqueId).validate_python(site_version, strict=True)
        source = RunTeachingSource(source.binding, source.manifest)
        if source.binding != binding:
            raise TeachingError("teaching_owner_or_binding_mismatch")
        current = capture_authorized_run(
            source.manifest.source_run_id, binding=binding, authority=authority.run,
        )
        if current != source:
            raise TeachingError("teaching_run_stale")
        base = BrowserPublicationManifest.model_validate_json(
            authority.run.frozen_publication(
                source.manifest.source_publication_digest, binding,
            ).model_dump_json()
        )
        if (
            base.digest != source.manifest.source_publication_digest
            or base.skill.skill_id != source.manifest.source_skill_id
            or base.skill.version != source.manifest.source_skill_version
            or base.skill.digest != source.manifest.source_skill_digest
            or base.skill.site_id != source.manifest.site_id
            or base.site.digest != source.manifest.site_digest
            or base.verifier.verifier_id != source.manifest.verifier_id
            or base.verifier.digest != source.manifest.verifier_digest
            or base.site.read_rule.object_type != source.manifest.object_type
        ):
            raise TeachingError("teaching_base_mismatch")
        if authority.authorize_target(binding, base, target_skill_id) is not True:
            raise TeachingError("teaching_target_denied")
        if site_version == base.site.version or (
            target_skill_id == base.skill.skill_id and version == base.skill.version
        ):
            raise TeachingError("teaching_version_not_new")
        logical_items = source.manifest.steps
        if source.manifest.execution_publication_digest is not None:
            read_plan = authority.run.read_plan
            plan = (read_plan(source.manifest.source_run_id, binding)
                    if read_plan is not None else None)
            if (plan is None
                    or plan.publication_digest
                    != source.manifest.execution_publication_digest):
                raise TeachingError("teaching_plan_unavailable")
            by_step: dict[str, RunTeachingStep] = {}
            for item in source.manifest.steps:
                by_step.setdefault(item.event.step_id, item)
            if set(by_step) != {item.step_id for item in plan.steps}:
                raise TeachingError("teaching_plan_sequence_mismatch")
            logical_items = tuple(by_step[item.step_id] for item in plan.steps)
        primitive_ids = tuple(item.primitive_id for item in logical_items)
        if (
            primitive_ids == tuple(item.step_id for item in base.skill.steps)
            or authority.unpublished_combination(
                binding, target_skill_id, primitive_ids,
            ) is not True
        ):
            raise TeachingError("teaching_flow_already_published")
        rules = []
        step_primitives: list[dict[str, str]] = []
        for item in logical_items:
            raw_rule = authority.run.primitive_rule(item.primitive_id, base)
            if raw_rule is None:
                raise TeachingError("teaching_primitive_unregistered")
            rule = type(raw_rule).model_validate_json(raw_rule.model_dump_json())
            if base.source_kind == "registered_site" and (
                not base.site.primitive_rules
                or rule != next(
                    (registered for registered in base.site.primitive_rules
                     if registered.step.step_id == item.primitive_id),
                    None,
                )
            ):
                raise TeachingError("teaching_primitive_unregistered")
            if (
                canonical_digest("browser_site_step.v1", rule.model_dump(mode="json"))
                != item.rule_digest or not item.event.model_copy(update={
                    "step_id": item.primitive_id,
                }).matches_step(rule.step)
                or rule.effect.actual_effect != "read_only"
                or rule.step.effect != "read_only"
            ):
                raise TeachingError("teaching_primitive_changed")
            new_step = SkillStep.model_validate_json(
                rule.step.model_copy(update={"step_id": (
                    item.event.step_id
                    if source.manifest.execution_publication_digest is not None
                    else canonical_step_id(item.event)
                )})
                .model_dump_json()
            )
            rules.append(rule.model_copy(update={"step": new_step}))
            step_primitives.append({
                "step_id": new_step.step_id, "primitive_id": item.primitive_id,
            })
        checked_rules = tuple(type(rule).model_validate_json(rule.model_dump_json())
                              for rule in rules)
        steps = tuple(rule.step for rule in checked_rules)
        required = _formal_required_purposes(base, steps)
        if type(parameter_whitelist) is not tuple:
            raise TeachingError("teaching_whitelist_invalid")
        approvals = tuple(ParameterApproval.model_validate_json(item.model_dump_json())
                          for item in parameter_whitelist)
        if (
            len(approvals) != len(required)
            or {item.name: item.purpose for item in approvals} != required
        ):
            raise TeachingError("teaching_whitelist_invalid")
        site_payload = base.site.model_dump(mode="json")
        site_payload.update(version=site_version,
                            steps=[rule.model_dump(mode="json") for rule in checked_rules],
                            step_primitives=(
                                step_primitives if base.source_kind == "registered_site" else []
                            ))
        # Exploration is a property of the source catalog. The compiled route
        # is a fixed publication and must execute its complete sealed sequence.
        site_payload["exploration"] = None
        if source.manifest.execution_publication_digest is not None:
            traversal = site_payload.get("traversal")
            if isinstance(traversal, dict):
                advance = next((
                    item["step_id"] for item in step_primitives
                    if item["primitive_id"] == traversal["advance_step_id"]
                ), None)
                site_payload["traversal"] = (
                    {**traversal, "advance_step_id": advance}
                    if advance is not None else None
                )
        site_payload["digest"] = publication_digest(
            "browser_site.publication.v1", site_payload,
        )
        site = BrowserSiteDefinition.model_validate_json(json.dumps(site_payload))
        skill_payload = base.skill.model_dump(mode="json")
        skill_payload.update(
            skill_id=target_skill_id, version=version, site_digest=site.digest,
            parameters=[item.name for item in approvals],
            steps=[step.model_dump(mode="json") for step in steps],
        )
        skill_payload["digest"] = canonical_digest("browser_skill.v1", skill_payload)
        skill = BrowserSkill.model_validate_json(json.dumps(skill_payload))
        manifest_payload = base.model_dump(mode="json")
        manifest_payload.update(skill=skill.model_dump(mode="json"),
                                site=site.model_dump(mode="json"))
        manifest_payload["digest"] = publication_digest(
            "browser_publication.durable.v1", manifest_payload,
        )
        candidate = BrowserPublicationManifest.model_validate_json(
            json.dumps(manifest_payload)
        )
        if (
            source.manifest.execution_publication_digest is not None
            and candidate.digest != source.manifest.execution_publication_digest
        ):
            raise TeachingError("teaching_execution_candidate_mismatch")
        digest = canonical_digest("browser_durable_teaching_draft.v1", {
            "source_digest": source.manifest.digest,
            "publication_digest": candidate.digest,
            "parameter_whitelist": [item.model_dump(mode="json") for item in approvals],
        })
        draft = DurableTeachingDraft(binding, source, candidate, approvals, digest)
        # Recheck after every catalog callback and candidate build. A later
        # replay/publish does this again; this is not a durable transaction.
        if capture_authorized_run(
            source.manifest.source_run_id, binding=binding, authority=authority.run,
        ) != source or authority.authorize_target(binding, base, target_skill_id) is not True or (
            authority.unpublished_combination(binding, target_skill_id, primitive_ids) is not True
        ):
            raise TeachingError("teaching_authorization_stale")
        for item in logical_items:
            current_rule = authority.run.primitive_rule(item.primitive_id, base)
            if current_rule is None or canonical_digest(
                "browser_site_step.v1", current_rule.model_dump(mode="json"),
            ) != item.rule_digest:
                raise TeachingError("teaching_primitive_changed")
        return draft
    except TeachingError:
        raise
    except Exception:
        raise TeachingError("teaching_compile_unavailable") from None
