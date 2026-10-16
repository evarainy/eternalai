"""Frozen, registered Site facts. Construction is a trusted composition boundary.

These records are not public requests. A digest or a read_only label alone is
not evidence: composition installs audited facts and their immutable proof refs.
DOM selectors and private parameter values remain in the adapter/input stores.
"""

import json
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
    operation: Literal[
        "click", "fill", "select_option", "read", "navigate", "check",
        "press_enter", "wait",
    ]
    actual_effect: Literal["read_only", "may_write", "unknown"]
    proof_ref: OpaqueId
    evidence_digest: Digest


class ExpectedPopup(Contract):
    """Audited Site sidecar, bound to one published read-only action step."""

    step_id: OpaqueId
    origin: str
    path_prefix: Annotated[str, Field(min_length=1, max_length=512)]
    object_type: OpaqueId
    maximum_pages: Annotated[int, Field(ge=1, le=4)] = 1
    wait_ms: Annotated[int, Field(ge=100, le=15_000)] = 5_000

    @model_validator(mode="after")
    def closed_location(self) -> Self:
        canonical_origin(self.origin)
        if (not self.path_prefix.startswith("/")
                or self.path_prefix.startswith("//")
                or any(c in self.path_prefix for c in "?#\\%*\r\n\t")):
            raise ValueError("site_popup_path_invalid")
        return self


class ExpectedDownload(Contract):
    """A single frozen read-only click may produce one bounded private file."""

    step_id: OpaqueId
    object_type: OpaqueId
    media_type: Literal["application/pdf", "text/plain", "text/csv", "image/png"]
    max_bytes: Annotated[int, Field(ge=1, le=16 * 1024 * 1024)]
    ttl_seconds: Annotated[int, Field(ge=1, le=86_400)]
    content_rule_digest: Digest
    wait_ms: Annotated[int, Field(ge=100, le=15_000)] = 5_000


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
        if self.step.operation == "wait" and self.effect.actual_effect != "read_only":
            raise ValueError("site_wait_effect_invalid")
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


class QueryField(Contract):
    field_id: OpaqueId


class RegisteredQueryReadRule(Contract):
    """Detail lookup; dynamic output is validated, never supplied as an oracle."""

    contract: Literal["independent_query_detail_v1"] = "independent_query_detail_v1"
    object_type: OpaqueId
    key_ref: ParameterRef
    fields: Annotated[tuple[QueryField, ...], Field(min_length=1, max_length=32)]
    output_schema_json: Annotated[str, Field(min_length=2, max_length=65_536, repr=False)]
    maximum_result_bytes: Annotated[int, Field(ge=1, le=65_536)] = 16_384

    @model_validator(mode="after")
    def query_shape(self) -> Self:
        schema = json.loads(self.output_schema_json)
        if (
            len({f.field_id for f in self.fields}) != len(self.fields)
            or not isinstance(schema, dict)
            or json.dumps(schema, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False) != self.output_schema_json
        ):
            raise ValueError("site_query_read_invalid")
        return self


ReadRule = RegisteredReadRule | RegisteredQueryReadRule


class RegisteredTraversal(Contract):
    """Frozen collection coverage budget over audited read-only advances.

    Selector definitions stay in the trusted local DOM registration. These
    digests must match that registration exactly before dispatch begins.
    """

    region_id: OpaqueId
    object_type: OpaqueId
    mode: Literal["paged", "virtualized"]
    advance_mode: Literal["action", "scroll"] = "action"
    advance_step_id: OpaqueId
    max_pages: Annotated[int, Field(ge=2, le=64)]
    max_rows: Annotated[int, Field(ge=1, le=65_536)]
    page_key_rule_digest: Digest
    row_key_rule_digest: Digest
    ordinal_rule_digest: Digest
    total_rule_digest: Digest
    terminal_rule_digest: Digest

    @model_validator(mode="after")
    def scroll_shape(self) -> Self:
        if self.advance_mode == "scroll" and self.mode != "virtualized":
            raise ValueError("site_traversal_scroll_mode_invalid")
        return self


class RegisteredExploration(Contract):
    """One frozen, read-only primitive catalog with a bounded in-Run choice budget."""

    allowed_regions: Annotated[tuple[OpaqueId, ...], Field(min_length=1, max_length=32)]
    max_steps: Annotated[int, Field(ge=1, le=32)]
    max_seconds: Annotated[int, Field(ge=1, le=300)]

    @model_validator(mode="after")
    def unique_regions(self) -> Self:
        if len(set(self.allowed_regions)) != len(self.allowed_regions):
            raise ValueError("site_exploration_region_duplicate")
        return self


class StepPrimitiveBinding(Contract):
    """One compiled step maps to one audited Site primitive, including repeats."""

    step_id: OpaqueId
    primitive_id: OpaqueId


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
    primitive_rules: Annotated[tuple[SiteStepRule, ...], Field(max_length=64)] = ()
    step_primitives: Annotated[tuple[StepPrimitiveBinding, ...], Field(max_length=64)] = ()
    read_rule: ReadRule
    decision_manifest: ModelManifest
    decision_budget: DecisionBudget
    traversal: RegisteredTraversal | None = None
    exploration: RegisteredExploration | None = None

    @model_validator(mode="after")
    def registered_shape(self) -> Self:
        if len({r.step.step_id for r in self.steps}) != len(self.steps):
            raise ValueError("site_duplicate_step")
        if self.primitive_rules:
            by_id = {rule.step.step_id: rule for rule in self.primitive_rules}
            mapping = {binding.step_id: binding.primitive_id
                       for binding in self.step_primitives}
            if (len(by_id) != len(self.primitive_rules)
                    or len(mapping) != len(self.step_primitives)
                    or (mapping and set(mapping) != {
                        rule.step.step_id for rule in self.steps
                    })
                    or any(
                        by_id.get(mapping.get(rule.step.step_id, rule.step.step_id))
                        != rule.model_copy(update={"step": rule.step.model_copy(
                            update={"step_id": mapping.get(
                                rule.step.step_id, rule.step.step_id,
                            )},
                        )})
                        for rule in self.steps
                    )
                    or any(rule.step.effect != "read_only"
                           or rule.effect.actual_effect != "read_only"
                           for rule in self.primitive_rules)):
                raise ValueError("site_primitive_catalog_invalid")
        elif self.step_primitives:
            raise ValueError("site_primitive_catalog_invalid")
        if len(set(self.navigation_origins)) != len(self.navigation_origins):
            raise ValueError("site_duplicate_origin")
        canonical_origin(self.source.origin)
        for origin in self.navigation_origins:
            canonical_origin(origin)
        if self.source.origin not in self.navigation_origins:
            raise ValueError("site_source_origin_unregistered")
        if self.traversal is not None:
            traversal = self.traversal
            if (traversal.object_type != self.read_rule.object_type
                    or not any(
                        rule.step.step_id == traversal.advance_step_id
                        and rule.observation.region_id == traversal.region_id
                        and rule.step.operation in (
                            {"read"} if traversal.advance_mode == "scroll"
                            else {"click", "navigate"}
                        )
                        and rule.step.effect == "read_only"
                        and rule.effect.actual_effect == "read_only"
                        and (traversal.advance_mode != "scroll"
                             or rule.step.locator.kind != "business_key")
                        for rule in self.steps
                    )):
                raise ValueError("site_traversal_unregistered")
        if self.exploration is not None:
            explorer = self.exploration
            if (self.traversal is not None or self.step_primitives
                    or not self.primitive_rules
                    or self.steps != self.primitive_rules
                    or any(
                        rule.step.operation not in {"read", "click"}
                        or rule.step.effect != "read_only"
                        or rule.effect.actual_effect != "read_only"
                        or rule.observation.region_id not in explorer.allowed_regions
                        for rule in self.steps
                    )):
                raise ValueError("site_exploration_catalog_invalid")
        return self

    def primitive_rule(self, step_id: str) -> SiteStepRule | None:
        """Trusted catalog lookup for teaching; never authorizes dispatch alone."""
        return next(
            (rule for rule in self.primitive_rules if rule.step.step_id == step_id),
            None,
        )

    def primitive_for_step(self, step_id: str) -> str:
        for binding in self.step_primitives:
            if binding.step_id == step_id:
                return binding.primitive_id
        return step_id

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
            or (
                isinstance(self.read_rule, RegisteredReadRule)
                and any(f.value_ref.name not in skill.parameters for f in self.read_rule.fields)
            )
        ):
            raise ValueError("site_skill_registration_mismatch")

    def validate_context(self, context: ExecutionContext) -> None:
        self.validate_skill(context.skill)
        if self.source != context.source or self.navigation_origins != context.navigation_origins:
            raise ValueError("site_execution_source_mismatch")

    def validate_read(self, spec: ReadSpec) -> None:
        mode = (
            "independent_query_detail_v1" if isinstance(self.read_rule, RegisteredQueryReadRule)
            else "independent_confirmed_key_v1"
        )
        if (
            spec.mode != mode
            or (spec.verifier_id, spec.verifier_digest) != (self.verifier_id, self.verifier_digest)
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
            and rule.step.effect in {"read_only", "may_write"}
            and rule.effect.actual_effect == rule.step.effect
            for rule in plan.steps
        )
