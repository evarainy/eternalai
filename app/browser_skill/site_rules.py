"""Frozen, registered Site facts. Construction is a trusted composition boundary.

These records are not public requests. A digest or a read_only label alone is
not evidence: composition installs audited facts and their immutable proof refs.
DOM selectors and private parameter values remain in the adapter/input stores.
"""

from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from app.browser_skill.models import (
    ActionCommand,
    BrowserSkill,
    Contract,
    DecisionBudget,
    DecisionSource,
    Digest,
    ExecutionContext,
    ModelManifest,
    ObservationPolicy,
    ObservationRequest,
    OpaqueId,
    ParameterRef,
    ReadSpec,
    SafeText,
    SkillStep,
)


def canonical_origin(origin: str) -> str:
    """Accept an exact canonical HTTP(S) origin, never a host suffix/pattern."""
    try:
        parsed = urlsplit(origin)
        port = parsed.port
    except ValueError:
        raise ValueError("site_origin_invalid") from None
    host = parsed.hostname or ""
    host_text = f"[{host}]" if ":" in host else host
    suffix = "" if port is None else f":{port}"
    if (
        not origin.isascii()
        or any(char.isspace() or char in "\\%*" for char in origin)
        or parsed.scheme not in {"http", "https"}
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or origin != f"{parsed.scheme}://{host_text}{suffix}"
        or port == (443 if parsed.scheme == "https" else 80)
    ):
        raise ValueError("site_origin_invalid")
    return origin


def navigation_allowed(url: str, origins: tuple[str, ...]) -> bool:
    """Check every actual navigation hop before dispatch; never log the URL."""
    try:
        if not url.isascii() or any(c.isspace() or c == "\\" for c in url):
            return False
        parsed = urlsplit(url)
        origin = canonical_origin(f"{parsed.scheme}://{parsed.netloc}")
    except ValueError:
        return False
    return origin in origins


class EffectFact(Contract):
    operation: Literal["click", "fill", "select_option", "read", "navigate"]
    actual_effect: Literal["read_only", "may_write", "unknown"]
    proof_ref: OpaqueId
    evidence_digest: Digest


class SiteStepRule(Contract):
    step: SkillStep
    observation: ObservationRequest
    target_criteria: Annotated[tuple[SafeText, ...], Field(min_length=1, max_length=16)]
    option_criteria: Annotated[tuple[SafeText, ...], Field(max_length=16)] = ()
    effect: EffectFact

    @model_validator(mode="after")
    def fixed_step(self) -> Self:
        if self.effect.operation != self.step.operation:
            raise ValueError("site_effect_operation_mismatch")
        if self.observation.expected_scope is not None:
            raise ValueError("site_cannot_register_live_scope")
        if bool(self.option_criteria) != (self.step.operation == "select_option"):
            raise ValueError("site_option_criteria_mismatch")
        return self


class ExpectedField(Contract):
    field_id: OpaqueId
    value_ref: ParameterRef


class RegisteredReadRule(Contract):
    object_type: OpaqueId
    key_ref: ParameterRef
    fields: Annotated[tuple[ExpectedField, ...], Field(min_length=1, max_length=32)]

    @model_validator(mode="after")
    def unique_fields(self) -> Self:
        if len({f.field_id for f in self.fields}) != len(self.fields):
            raise ValueError("site_duplicate_read_field")
        return self


class RegisteredSitePlan(Contract):
    site_id: OpaqueId
    site_digest: Digest
    skill_id: OpaqueId
    skill_version: OpaqueId
    skill_digest: Digest
    verifier_id: OpaqueId
    verifier_digest: Digest
    source: DecisionSource
    navigation_origins: Annotated[tuple[str, ...], Field(max_length=32)]
    policy: ObservationPolicy
    steps: Annotated[tuple[SiteStepRule, ...], Field(min_length=1, max_length=64)]
    read_rule: RegisteredReadRule
    decision_manifest: ModelManifest
    decision_budget: DecisionBudget

    @model_validator(mode="after")
    def registered_shape(self) -> Self:
        if len({r.step.step_id for r in self.steps}) != len(self.steps):
            raise ValueError("site_duplicate_step")
        if len(set(self.navigation_origins)) != len(self.navigation_origins):
            raise ValueError("site_duplicate_origin")
        canonical_origin(self.source.origin)
        for origin in self.navigation_origins:
            canonical_origin(origin)
        if self.source.origin not in self.navigation_origins:
            raise ValueError("site_source_origin_unregistered")
        return self

    def validate_skill(self, skill: BrowserSkill) -> None:
        if (
            (
                self.site_id,
                self.site_digest,
                self.skill_id,
                self.skill_version,
                self.skill_digest,
                self.verifier_id,
                self.verifier_digest,
            )
            != (
                skill.site_id,
                skill.site_digest,
                skill.skill_id,
                skill.version,
                skill.digest,
                skill.verifier_id,
                skill.verifier_digest,
            )
            or tuple(rule.step for rule in self.steps) != skill.steps
            or self.read_rule.key_ref.name not in skill.parameters
            or any(f.value_ref.name not in skill.parameters for f in self.read_rule.fields)
        ):
            raise ValueError("site_skill_registration_mismatch")

    def validate_context(self, context: ExecutionContext) -> None:
        self.validate_skill(context.skill)
        if self.source != context.source or self.navigation_origins != context.navigation_origins:
            raise ValueError("site_execution_source_mismatch")

    def validate_read(self, spec: ReadSpec) -> None:
        if (
            (spec.verifier_id, spec.verifier_digest) != (self.verifier_id, self.verifier_digest)
            or spec.business_key.object_type != self.read_rule.object_type
            or spec.business_key.value_ref != self.read_rule.key_ref
            or spec.fields != tuple(f.field_id for f in self.read_rule.fields)
        ):
            raise ValueError("site_read_registration_mismatch")


class FrozenSiteAdapter:
    """An explicit immutable registry; no fallback, discovery, or environment label."""

    __slots__ = ("_plans",)

    def __init__(self, plans: tuple[RegisteredSitePlan, ...]) -> None:
        keys = [(p.skill_id, p.skill_version, p.skill_digest) for p in plans]
        if type(plans) is not tuple or len(keys) != len(set(keys)):
            raise ValueError("site_registry_invalid")
        self._plans = plans

    def bootstrap(self, skill: BrowserSkill) -> RegisteredSitePlan:
        for plan in self._plans:
            if (plan.skill_id, plan.skill_version, plan.skill_digest) == (
                skill.skill_id,
                skill.version,
                skill.digest,
            ):
                plan.validate_skill(skill)
                return plan
        raise ValueError("site_skill_unregistered")

    def observation_policy(self, skill: BrowserSkill) -> ObservationPolicy:
        return self.bootstrap(skill).policy

    def permits(self, skill: BrowserSkill, command: ActionCommand) -> bool:
        plan = self.bootstrap(skill)
        return command.skill_digest == skill.digest and any(
            rule.step == command.step
            and rule.step.effect == "read_only"
            and rule.effect.actual_effect == "read_only"
            for rule in plan.steps
        )
