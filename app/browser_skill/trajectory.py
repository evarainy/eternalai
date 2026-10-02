"""Authorized neutral teaching metadata; no browser calls, raw values or I/O.

Only generated synthetic demonstrations are exercised at this checkpoint. Actual
capture, Chat teaching, durable BR-DB-04 ownership and real-material permission
are deferred. Recording a proven may_write effect never grants execution rights.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from typing import Annotated, Literal, Never, Self, SupportsIndex

from pydantic import Field, TypeAdapter, model_validator

from app.browser_skill.models import (
    BrowserOwner,
    BrowserSkill,
    Contract,
    Digest,
    Epoch,
    ObservationPolicy,
    OpaqueId,
    ParameterPurpose,
    ParameterRef,
    SafeText,
    ScopeBinding,
    ScopeStamp,
    SkillStep,
    TargetRef,
)
from app.browser_skill.registry import Publication, canonical_digest
from app.browser_skill.site_rules import EffectFact


class TrajectoryError(ValueError):
    """Only fixed diagnostic codes; never report callback payloads."""


class _Private:
    __slots__ = ()

    def __repr__(self) -> str:
        return f"<{type(self).__name__}: process-local>"

    def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
        raise TypeError("trajectory_private_serialization_forbidden")

    def __getstate__(self) -> Never:
        raise TypeError("trajectory_private_serialization_forbidden")


def _binding_copy(binding: ScopeBinding) -> ScopeBinding:
    # session_id is excluded from model_dump. Preserve every owner dimension
    # explicitly, without hashing any potentially low-entropy private identifier.
    return ScopeBinding(
        owner=BrowserOwner(
            tenant_id=binding.owner.tenant_id,
            user_id=binding.owner.user_id,
            session_id=binding.owner.session_id,
        ),
        binding_id=binding.binding_id,
        binding_revision=binding.binding_revision,
        authorization_revision=binding.authorization_revision,
        lease_epoch=binding.lease_epoch,
    )


class BindingRevisions(Contract):
    binding_id: OpaqueId
    binding_revision: Epoch
    authorization_revision: Epoch
    lease_epoch: Epoch

    @classmethod
    def from_binding(cls, binding: ScopeBinding) -> Self:
        checked = _binding_copy(binding)
        return cls(
            binding_id=checked.binding_id,
            binding_revision=checked.binding_revision,
            authorization_revision=checked.authorization_revision,
            lease_epoch=checked.lease_epoch,
        )


class PublicationReference(Contract):
    """No embedded Publication: its Site origin must not enter trajectory wire."""

    publication_digest: Digest
    publication_version: OpaqueId
    skill_id: OpaqueId
    skill_version: OpaqueId
    skill_digest: Digest
    site_id: OpaqueId
    site_digest: Digest
    verifier_id: OpaqueId
    verifier_digest: Digest
    dependency_digest: Digest

    @classmethod
    def from_publication(cls, publication: Publication) -> Self:
        checked = Publication.model_validate_json(publication.model_dump_json())
        skill = checked.skill
        return cls(
            publication_digest=checked.digest,
            publication_version=checked.version,
            skill_id=skill.skill_id,
            skill_version=skill.version,
            skill_digest=skill.digest,
            site_id=skill.site_id,
            site_digest=skill.site_digest,
            verifier_id=skill.verifier_id,
            verifier_digest=skill.verifier_digest,
            dependency_digest=checked.dependency_digest,
        )


class ParameterApproval(Contract):
    name: OpaqueId
    purpose: ParameterPurpose
    format: Literal["text", "opaque_id", "enum_choices", "registered_url"]

    @model_validator(mode="after")
    def compatible_format(self) -> Self:
        formats = {
            "fill_value": {"text", "opaque_id"},
            "option_values": {"enum_choices"},
            "navigation_url": {"registered_url"},
            "business_key": {"opaque_id"},
            "expected_field": {"text", "opaque_id"},
        }
        if self.format not in formats[self.purpose]:
            raise ValueError("draft_parameter_format_mismatch")
        return self


class TrajectoryEvent(Contract):
    sequence: Annotated[int, Field(ge=1)]
    event_id: OpaqueId
    step_id: OpaqueId
    scope: ScopeStamp
    target: TargetRef | None = None
    option: TargetRef | None = None
    operation: Literal["click", "fill", "select_option", "read", "navigate"]
    effect: Literal["read_only", "may_write"]
    effect_proof: EffectFact
    value_ref: ParameterRef | None = None
    url_ref: ParameterRef | None = None
    option_ref: ParameterRef | None = None
    policy_digest: Digest
    approved_labels: Annotated[tuple[SafeText, ...], Field(max_length=16)] = ()

    @model_validator(mode="after")
    def shape(self) -> Self:
        if (self.operation == "fill") != (self.value_ref is not None):
            raise ValueError("trajectory_value_reference_mismatch")
        if (self.operation == "navigate") != (self.url_ref is not None):
            raise ValueError("trajectory_url_reference_mismatch")
        if (self.operation == "select_option") != (self.option_ref is not None):
            raise ValueError("trajectory_option_reference_mismatch")
        if (self.operation != "navigate") != (self.target is not None):
            raise ValueError("trajectory_target_required")
        if (self.operation == "select_option") != (self.option is not None):
            raise ValueError("trajectory_option_required")
        if any(ref.scope != self.scope for ref in (self.target, self.option) if ref is not None):
            raise ValueError("trajectory_scope_mismatch")
        if (self.effect_proof.operation, self.effect_proof.actual_effect) != (
            self.operation,
            self.effect,
        ):
            raise ValueError("trajectory_effect_unproved")
        if len(set(self.approved_labels)) != len(self.approved_labels):
            raise ValueError("trajectory_duplicate_label")
        return self

    def matches_step(self, step: SkillStep) -> bool:
        return (
            self.step_id,
            self.operation,
            self.effect,
            self.value_ref,
            self.url_ref,
            self.option_ref,
        ) == (
            step.step_id,
            step.operation,
            step.effect,
            step.value_ref,
            step.url_ref,
            step.option_ref,
        )


class CaptureStepPermission(Contract):
    step_id: OpaqueId
    effect: EffectFact
    policy: ObservationPolicy


@dataclass(frozen=True, slots=True, repr=False)
class CapturePermission(_Private):
    """Trusted callback result, never parsed from public input or source labels.

    Composition owns the lookup of trajectory/source owner, binding versions,
    registered Publication, effect proof and approved projection policy. A raw
    digest is merely a reference to that grant, not self-authenticating evidence.
    """

    trajectory_id: str
    source_ref: str
    binding: ScopeBinding
    publication: PublicationReference
    authorization_digest: str
    base_revision: int
    steps: tuple[CaptureStepPermission, ...]
    __getstate__ = _Private.__getstate__

    def __post_init__(self) -> None:
        TypeAdapter(OpaqueId).validate_python(self.trajectory_id, strict=True)
        TypeAdapter(OpaqueId).validate_python(self.source_ref, strict=True)
        TypeAdapter(Digest).validate_python(self.authorization_digest, strict=True)
        TypeAdapter(Epoch).validate_python(self.base_revision, strict=True)
        object.__setattr__(self, "binding", _binding_copy(self.binding))
        object.__setattr__(
            self,
            "publication",
            PublicationReference.model_validate_json(self.publication.model_dump_json()),
        )
        if type(self.steps) is not tuple or not self.steps or len(self.steps) > 64:
            raise ValueError("trajectory_permission_steps_invalid")
        checked = tuple(
            CaptureStepPermission.model_validate_json(step.model_dump_json()) for step in self.steps
        )
        if len({step.step_id for step in checked}) != len(checked):
            raise ValueError("trajectory_permission_steps_duplicate")
        object.__setattr__(self, "steps", checked)


@dataclass(frozen=True, slots=True, repr=False)
class RecordingContext(_Private):
    """Synchronous trusted composition callbacks. No callback executes actions.

    The source lookup must check exact trajectory ownership, even when two owners
    have otherwise equal public digests. Callbacks must not reenter the recorder.
    """

    current_binding: Callable[[], ScopeBinding]
    source_permission: Callable[
        [str, str, ScopeBinding, PublicationReference], CapturePermission | None
    ]
    authorize: Callable[[ScopeBinding, str, PublicationReference, TrajectoryEvent | None], bool]
    current_scope: Callable[[str], ScopeStamp]
    current_targets: Callable[[str], tuple[TargetRef, ...]]
    __getstate__ = _Private.__getstate__

    def __post_init__(self) -> None:
        if not all(
            callable(callback)
            for callback in (
                self.current_binding,
                self.source_permission,
                self.authorize,
                self.current_scope,
                self.current_targets,
            )
        ):
            raise ValueError("trajectory_callbacks_required")


class TrajectoryManifest(Contract):
    schema_version: Literal["neutral_trajectory.v1"] = "neutral_trajectory.v1"
    trajectory_id: OpaqueId
    source_ref: OpaqueId
    source_authorization_digest: Digest
    binding: BindingRevisions
    base_publication: PublicationReference
    base_revision: Epoch
    revision: Epoch
    events: Annotated[tuple[TrajectoryEvent, ...], Field(max_length=512)] = ()
    digest: Digest

    @model_validator(mode="after")
    def integrity(self) -> Self:
        if self.revision != len(self.events) or tuple(
            event.sequence for event in self.events
        ) != tuple(range(1, self.revision + 1)):
            raise ValueError("trajectory_sequence_invalid")
        if len({event.event_id for event in self.events}) != len(self.events):
            raise ValueError("trajectory_duplicate_event")
        if canonical_digest(self.schema_version, self.model_dump(mode="json")) != self.digest:
            raise ValueError("trajectory_digest_mismatch")
        return self


@dataclass(frozen=True, slots=True, repr=False)
class NeutralTrajectory(_Private):
    """Sealed local ownership plus serializable safe manifest, never execution authority."""

    binding: ScopeBinding
    manifest: TrajectoryManifest
    __getstate__ = _Private.__getstate__

    def __post_init__(self) -> None:
        object.__setattr__(self, "binding", _binding_copy(self.binding))
        checked = TrajectoryManifest.model_validate_json(self.manifest.model_dump_json())
        if checked.binding != BindingRevisions.from_binding(self.binding):
            raise ValueError("trajectory_binding_mismatch")
        object.__setattr__(self, "manifest", checked)


class TrajectoryRecorder(_Private):
    """Single-process CAS metadata recorder; no DB, capture I/O or publication writer."""

    __slots__ = (
        "_binding",
        "_publication",
        "_reference",
        "_context",
        "_lock",
        "_trajectory_id",
        "_source_ref",
        "_manifest",
        "_base_revision",
    )

    def __init__(
        self,
        *,
        trajectory_id: str,
        source_ref: str,
        binding: ScopeBinding,
        publication: Publication,
        base_revision: int,
        context: RecordingContext,
    ) -> None:
        self._binding = _binding_copy(binding)
        self._publication = Publication.model_validate_json(publication.model_dump_json())
        self._reference = PublicationReference.from_publication(self._publication)
        self._context = context
        self._lock = Lock()
        self._trajectory_id = TypeAdapter(OpaqueId).validate_python(trajectory_id, strict=True)
        self._source_ref = TypeAdapter(OpaqueId).validate_python(source_ref, strict=True)
        TypeAdapter(Epoch).validate_python(base_revision, strict=True)
        self._base_revision = base_revision
        permission = self._authorized(binding, None)
        payload = dict(
            schema_version="neutral_trajectory.v1",
            trajectory_id=trajectory_id,
            source_ref=source_ref,
            source_authorization_digest=permission.authorization_digest,
            binding=BindingRevisions.from_binding(binding).model_dump(mode="json"),
            base_publication=self._reference.model_dump(mode="json"),
            base_revision=base_revision,
            revision=0,
            events=[],
        )
        self._manifest = self._seal(payload)

    @staticmethod
    def _seal(payload: dict[str, object]) -> bytes:
        import json

        payload["digest"] = canonical_digest("neutral_trajectory.v1", payload)
        return (
            TrajectoryManifest.model_validate_json(json.dumps(payload)).model_dump_json().encode()
        )

    def _authorized(
        self, binding: ScopeBinding, event: TrajectoryEvent | None
    ) -> CapturePermission:
        checked = _binding_copy(binding)
        if checked != self._binding:
            raise TrajectoryError("trajectory_owner_or_binding_mismatch")
        try:
            current = _binding_copy(self._context.current_binding())
            if current != checked:
                raise TrajectoryError("trajectory_binding_stale")
            permission = self._context.source_permission(
                self._trajectory_id, self._source_ref, _binding_copy(checked), self._reference
            )
            if permission is None:
                raise TrajectoryError("trajectory_source_denied")
            # Reconstruct to reject forged dataclass and nested model_copy payloads.
            permission = CapturePermission(
                permission.trajectory_id,
                permission.source_ref,
                permission.binding,
                permission.publication,
                permission.authorization_digest,
                permission.base_revision,
                permission.steps,
            )
            if (
                permission.trajectory_id,
                permission.source_ref,
                permission.binding,
                permission.publication,
            ) != (self._trajectory_id, self._source_ref, checked, self._reference):
                raise TrajectoryError("trajectory_source_permission_mismatch")
            if permission.base_revision != self._base_revision:
                raise TrajectoryError("trajectory_base_revision_stale")
            if (
                self._context.authorize(
                    _binding_copy(checked), self._source_ref, self._reference, event
                )
                is not True
            ):
                raise TrajectoryError("trajectory_denied")
        except TrajectoryError:
            raise
        except Exception:
            raise TrajectoryError("trajectory_authorization_unavailable") from None
        return permission

    def _check_event(
        self, event: TrajectoryEvent, permission: CapturePermission, *, check_current: bool = True
    ) -> None:
        base = next(
            (step for step in self._publication.skill.steps if step.step_id == event.step_id), None
        )
        rule = next((step for step in permission.steps if step.step_id == event.step_id), None)
        if base is None or rule is None or not event.matches_step(base):
            raise TrajectoryError("trajectory_step_unregistered")
        if rule.effect != event.effect_proof or rule.effect.actual_effect == "unknown":
            raise TrajectoryError("trajectory_effect_unproved")
        if event.policy_digest != rule.policy.digest or not set(event.approved_labels) <= set(
            (*rule.policy.allowed_names, *rule.policy.allowed_context)
        ):
            raise TrajectoryError("trajectory_labels_unapproved")
        if not check_current:
            return
        try:
            scope = ScopeStamp.model_validate_json(
                self._context.current_scope(self._source_ref).model_dump_json()
            )
            targets = tuple(
                TargetRef.model_validate_json(target.model_dump_json())
                for target in self._context.current_targets(self._source_ref)
            )
        except Exception:
            raise TrajectoryError("trajectory_scope_unavailable") from None
        if event.scope != scope or any(
            ref not in targets for ref in (event.target, event.option) if ref is not None
        ):
            raise TrajectoryError("trajectory_scope_stale")

    def append(
        self, event: TrajectoryEvent, *, binding: ScopeBinding, expected_revision: int
    ) -> NeutralTrajectory:
        checked = TrajectoryEvent.model_validate_json(event.model_dump_json())
        with self._lock:
            current = TrajectoryManifest.model_validate_json(self._manifest)
            permission = self._authorized(binding, checked)
            if permission.authorization_digest != current.source_authorization_digest:
                raise TrajectoryError("trajectory_source_permission_stale")
            if type(expected_revision) is not int or expected_revision != current.revision:
                raise TrajectoryError("trajectory_revision_conflict")
            if checked.sequence != current.revision + 1:
                raise TrajectoryError("trajectory_sequence_invalid")
            self._check_event(checked, permission)
            # Scope callbacks may observe an authorization change. Recheck just
            # before the local append; this is not a durable transaction claim.
            if self._authorized(binding, checked) != permission:
                raise TrajectoryError("trajectory_source_permission_stale")
            payload = current.model_dump(mode="json")
            payload["events"] = [
                *(item.model_dump(mode="json") for item in current.events),
                checked.model_dump(mode="json"),
            ]
            payload["revision"] = current.revision + 1
            self._manifest = self._seal(payload)
            return NeutralTrajectory(
                self._binding, TrajectoryManifest.model_validate_json(self._manifest)
            )

    def snapshot(self, *, binding: ScopeBinding) -> NeutralTrajectory:
        with self._lock:
            current = TrajectoryManifest.model_validate_json(self._manifest)
            permission = self._authorized(binding, None)
            if permission.authorization_digest != current.source_authorization_digest:
                raise TrajectoryError("trajectory_source_permission_stale")
            return NeutralTrajectory(self._binding, current)

    @classmethod
    def restore(
        cls,
        snapshot: NeutralTrajectory,
        *,
        binding: ScopeBinding,
        publication: Publication,
        context: RecordingContext,
    ) -> Self:
        """Restore only with sealed stored ownership AND fresh source authorization.

        JSON alone is insufficient: trusted persistence must recover the original
        owner binding separately. Current source lookup must bind trajectory ID to
        that exact owner. Equal public digests never substitute for this check.
        Historical targets may be stale; only newly appended events use live refs.
        """
        checked = NeutralTrajectory(snapshot.binding, snapshot.manifest)
        if _binding_copy(binding) != checked.binding:
            raise TrajectoryError("trajectory_owner_or_binding_mismatch")
        manifest = checked.manifest
        if PublicationReference.from_publication(publication) != manifest.base_publication:
            raise TrajectoryError("trajectory_publication_mismatch")
        recorder = cls(
            trajectory_id=manifest.trajectory_id,
            source_ref=manifest.source_ref,
            binding=binding,
            publication=publication,
            base_revision=manifest.base_revision,
            context=context,
        )
        fresh = TrajectoryManifest.model_validate_json(recorder._manifest)
        if fresh.source_authorization_digest != manifest.source_authorization_digest:
            raise TrajectoryError("trajectory_source_permission_stale")
        for event in manifest.events:
            permission = recorder._authorized(binding, event)
            if permission.authorization_digest != manifest.source_authorization_digest:
                raise TrajectoryError("trajectory_source_permission_stale")
            recorder._check_event(event, permission, check_current=False)
        recorder._manifest = manifest.model_dump_json().encode()
        return recorder


class DraftManifest(Contract):
    schema_version: Literal["skill_draft.v1"] = "skill_draft.v1"
    draft_id: OpaqueId
    base_publication: PublicationReference
    base_revision: Epoch
    draft_revision: Annotated[int, Field(ge=1)]
    parameter_whitelist: tuple[ParameterApproval, ...]
    skill: BrowserSkill
    state: Literal["draft", "validated", "rejected"] = "draft"
    validation_digest: Digest | None = None
    rejection: Literal["invalid_pattern", "unsafe_reference", "dependency_mismatch"] | None = None
    digest: Digest

    @model_validator(mode="after")
    def integrity(self) -> Self:
        if (self.state == "validated") != (self.validation_digest is not None):
            raise ValueError("draft_validation_state_mismatch")
        if (self.state == "rejected") != (self.rejection is not None):
            raise ValueError("draft_rejection_state_mismatch")
        names = [parameter.name for parameter in self.parameter_whitelist]
        if len(set(names)) != len(names) or set(self.skill.parameters) != set(names):
            raise ValueError("draft_parameter_whitelist_mismatch")
        purposes = {parameter.name: parameter.purpose for parameter in self.parameter_whitelist}
        for step in self.skill.steps:
            for ref, purpose in (
                (step.value_ref, "fill_value"),
                (step.url_ref, "navigation_url"),
                (step.option_ref, "option_values"),
            ):
                if ref is not None and purposes.get(ref.name) != purpose:
                    raise ValueError("draft_parameter_purpose_mismatch")
        base = self.base_publication
        if (
            self.skill.skill_id,
            self.skill.site_id,
            self.skill.site_digest,
            self.skill.verifier_id,
            self.skill.verifier_digest,
        ) != (
            base.skill_id,
            base.site_id,
            base.site_digest,
            base.verifier_id,
            base.verifier_digest,
        ):
            raise ValueError("draft_dependencies_changed")
        if (
            canonical_digest("browser_skill.v1", self.skill.model_dump(mode="json"))
            != self.skill.digest
        ):
            raise ValueError("draft_skill_digest_mismatch")
        if canonical_digest(self.schema_version, self.model_dump(mode="json")) != self.digest:
            raise ValueError("draft_digest_mismatch")
        return self


@dataclass(frozen=True, slots=True, repr=False)
class SkillDraft(_Private):
    """Teaching value object. Even validated means schema-only, never execution.

    Compiler/repair must validate current authorization and base revision before
    consuming it. It has no publish, activate or execute operation. Only the
    existing publication authority can subsequently publish approved content.
    """

    binding: ScopeBinding
    manifest: DraftManifest
    __getstate__ = _Private.__getstate__

    def __post_init__(self) -> None:
        object.__setattr__(self, "binding", _binding_copy(self.binding))
        object.__setattr__(
            self, "manifest", DraftManifest.model_validate_json(self.manifest.model_dump_json())
        )

    def validate_base(self, binding: ScopeBinding, publication: Publication, revision: int) -> None:
        checked = DraftManifest.model_validate_json(self.manifest.model_dump_json())
        if _binding_copy(binding) != self.binding:
            raise TrajectoryError("draft_owner_or_binding_mismatch")
        if type(revision) is not int or revision != checked.base_revision:
            raise TrajectoryError("draft_base_revision_stale")
        if PublicationReference.from_publication(publication) != checked.base_publication:
            raise TrajectoryError("draft_publication_mismatch")
