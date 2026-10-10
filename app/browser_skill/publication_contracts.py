"""Closed immutable manifests for the controlled READ_ONLY browser publication.

These records carry evidence, never authority. Registered source installation and
the current Capability/Policy remain trusted composition and registry decisions.
The older synthetic publication contract is deliberately independent.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal, Self

from pydantic import BaseModel, Field, model_validator

from app.browser_skill.models import (
    BrowserSkill,
    Contract,
    DecisionBudget,
    DecisionSource,
    Digest,
    ModelManifest,
    ObservationPolicy,
    OpaqueId,
)
from app.browser_skill.site_rules import (
    ReadRule,
    RegisteredExploration,
    RegisteredQueryReadRule,
    RegisteredSitePlan,
    RegisteredTraversal,
    SiteStepRule,
    StepPrimitiveBinding,
    canonical_origin,
)
from app.ports.capability_registry import CapabilitySpec
from app.ports.human_gate import VersionBinding
from app.version_binding import capability_version_bindings


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def publication_digest(domain: str, payload: dict[str, object]) -> str:
    """Cover all fields, excluding only this object's own top-level digest."""
    body = {key: value for key, value in payload.items() if key != "digest"}
    # v1 records predate the optional rollback provenance. Preserve their
    # immutable digests while covering every non-null provenance value.
    if domain == "browser_publication.durable.v1":
        if body.get("rollback_source_digest") is None:
            body.pop("rollback_source_digest", None)
        nested_site = body.get("site")
        if isinstance(nested_site, dict):
            site = dict(nested_site)
            if site.get("traversal") is None:
                site.pop("traversal", None)
            elif isinstance(site.get("traversal"), dict):
                traversal = dict(site["traversal"])
                if traversal.get("advance_mode") == "action":
                    traversal.pop("advance_mode", None)
                site["traversal"] = traversal
            if site.get("exploration") is None:
                site.pop("exploration", None)
            if not site.get("primitive_rules"):
                site.pop("primitive_rules", None)
            if not site.get("step_primitives"):
                site.pop("step_primitives", None)
            body["site"] = site
    if domain == "browser_site.publication.v1" and body.get("traversal") is None:
        body.pop("traversal", None)
    elif domain == "browser_site.publication.v1":
        traversal_value = body.get("traversal")
        if isinstance(traversal_value, dict):
            traversal = dict(traversal_value)
            if traversal.get("advance_mode") == "action":
                traversal.pop("advance_mode", None)
            body["traversal"] = traversal
    if domain == "browser_site.publication.v1" and body.get("exploration") is None:
        body.pop("exploration", None)
    if domain == "browser_site.publication.v1" and not body.get("primitive_rules"):
        body.pop("primitive_rules", None)
    if domain == "browser_site.publication.v1" and not body.get("step_primitives"):
        body.pop("step_primitives", None)
    return hashlib.sha256((domain + "\n" + canonical_json(body)).encode("utf-8")).hexdigest()


def _integrity(model: BaseModel, domain: str, digest: str) -> None:
    if publication_digest(domain, model.model_dump(mode="json")) != digest:
        raise ValueError("browser_publication_digest_mismatch")


class BrowserSiteDefinition(Contract):
    schema_version: Literal["browser_site.publication.v1"] = "browser_site.publication.v1"
    site_id: OpaqueId
    version: OpaqueId
    digest: Digest
    source: DecisionSource
    navigation_origins: Annotated[tuple[str, ...], Field(min_length=1, max_length=32)]
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
    def integrity(self) -> Self:
        canonical_origin(self.source.origin)
        if (len(set(self.navigation_origins)) != len(self.navigation_origins)
                or self.source.origin not in self.navigation_origins):
            raise ValueError("browser_publication_source_mismatch")
        for origin in self.navigation_origins:
            canonical_origin(origin)
        if len({rule.step.step_id for rule in self.steps}) != len(self.steps):
            raise ValueError("browser_publication_duplicate_step")
        if self.primitive_rules:
            by_id = {rule.step.step_id: rule for rule in self.primitive_rules}
            mapping = {item.step_id: item.primitive_id for item in self.step_primitives}
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
                raise ValueError("browser_publication_primitive_catalog_invalid")
        elif self.step_primitives:
            raise ValueError("browser_publication_primitive_catalog_invalid")
        if any(
            rule.step.effect not in {"read_only", "may_write"}
            or rule.effect.actual_effect != rule.step.effect
            for rule in self.steps
        ):
            raise ValueError("browser_publication_effect_denied")
        if self.traversal is not None and (
            self.traversal.object_type != self.read_rule.object_type
            or not any(
                rule.step.step_id == self.traversal.advance_step_id
                and rule.observation.region_id == self.traversal.region_id
                and rule.step.operation in (
                    {"read"} if self.traversal.advance_mode == "scroll"
                    else {"click", "navigate"}
                )
                and rule.step.effect == "read_only"
                and rule.effect.actual_effect == "read_only"
                and (self.traversal.advance_mode != "scroll"
                     or rule.step.locator.kind != "business_key")
                for rule in self.steps
            )
        ):
            raise ValueError("browser_publication_traversal_invalid")
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
                raise ValueError("browser_publication_exploration_invalid")
        _integrity(self, self.schema_version, self.digest)
        return self


class BrowserVerifierDefinition(Contract):
    schema_version: Literal["browser_verifier.publication.v1"] = "browser_verifier.publication.v1"
    verifier_id: OpaqueId
    version: OpaqueId
    digest: Digest
    contract: Literal["independent_confirmed_key_v1", "independent_query_detail_v1"] = (
        "independent_confirmed_key_v1"
    )
    read_rule: ReadRule
    require_complete_coverage: Literal[True] = True
    require_exact_owner: Literal[True] = True
    require_unique_business_key: Literal[True] = True

    @model_validator(mode="after")
    def integrity(self) -> Self:
        if (self.contract == "independent_query_detail_v1") != isinstance(
            self.read_rule, RegisteredQueryReadRule,
        ):
            raise ValueError("browser_publication_verifier_mode_mismatch")
        _integrity(self, self.schema_version, self.digest)
        return self


class BrowserOutputDefinition(Contract):
    schema_version: Literal["browser_output.publication.v1"] = "browser_output.publication.v1"
    digest: Digest
    # Canonical JSON strings keep nested JSON schemas deeply immutable.
    output_schema_json: Annotated[str, Field(min_length=2, max_length=65_536, repr=False)]
    field_ids: Annotated[tuple[OpaqueId, ...], Field(min_length=1, max_length=32)]
    maximum_bytes: Annotated[int, Field(ge=1, le=65_536)] = 16_384
    verified_only: Literal[True] = True

    @model_validator(mode="after")
    def integrity(self) -> Self:
        schema = json.loads(self.output_schema_json)
        if not isinstance(schema, dict) or canonical_json(schema) != self.output_schema_json:
            raise ValueError("browser_publication_output_schema_invalid")
        if len(set(self.field_ids)) != len(self.field_ids):
            raise ValueError("browser_publication_output_fields_invalid")
        _integrity(self, self.schema_version, self.digest)
        return self


class BrowserPublicationManifest(Contract):
    schema_version: Literal["browser_publication.durable.v1"] = "browser_publication.durable.v1"
    digest: Digest
    skill: BrowserSkill
    site: BrowserSiteDefinition
    verifier: BrowserVerifierDefinition
    output: BrowserOutputDefinition
    capability_snapshot_json: Annotated[str, Field(min_length=2, max_length=262_144, repr=False)]
    capability_bindings: Annotated[tuple[VersionBinding, ...], Field(min_length=1, max_length=2)]
    source_kind: Literal["registered_synthetic_fixture", "registered_site"] = (
        "registered_synthetic_fixture"
    )
    effect: Literal["read_only", "controlled_write"] = "read_only"
    execution_mode: Literal["browser_async_v1"] = "browser_async_v1"
    rollback_source_digest: Digest | None = None

    @property
    def capability(self) -> CapabilitySpec:
        """A fresh copy, used only for comparison with the existing registry."""
        return CapabilitySpec.model_validate_json(self.capability_snapshot_json)

    @property
    def capability_digest(self) -> str:
        return self.capability_bindings[0].digest

    def site_plan(self) -> RegisteredSitePlan:
        values = self.site.model_dump(exclude={"schema_version", "version", "digest"})
        return RegisteredSitePlan(
            **values, site_digest=self.site.digest, skill_id=self.skill.skill_id,
            skill_version=self.skill.version, skill_digest=self.skill.digest,
            verifier_id=self.verifier.verifier_id, verifier_digest=self.verifier.digest,
        )

    @model_validator(mode="after")
    def integrity(self) -> Self:
        capability = self.capability
        if canonical_json(capability.model_dump(mode="json")) != self.capability_snapshot_json:
            raise ValueError("browser_publication_capability_snapshot_invalid")
        registered_target = (
            self.source_kind == "registered_site"
            and capability.target_system in {"oa", "u8", "hikvision_ivms"}
        )
        synthetic_target = (
            self.source_kind == "registered_synthetic_fixture"
            and capability.target_system == "oa"
        )
        if self.rollback_source_digest is not None and (
            self.source_kind != "registered_site"
            or self.rollback_source_digest == self.digest
        ):
            raise ValueError("browser_publication_rollback_source_invalid")
        has_write = any(rule.step.effect == "may_write" for rule in self.site.steps)
        if ((self.effect == "controlled_write") != has_write
                or (has_write and self.source_kind != "registered_site")):
            raise ValueError("browser_publication_effect_denied")
        if (capability.status != "active"
                or capability.type != ("action" if has_write else "query")
                or not (registered_target or synthetic_target)
                or capability.execution_identity != "user_delegated"
                or capability.binding_required is not True):
            raise ValueError("browser_publication_capability_denied")
        if self.capability_bindings != capability_version_bindings(capability):
            raise ValueError("browser_publication_capability_binding_mismatch")
        if canonical_json(capability.output_schema) != self.output.output_schema_json:
            raise ValueError("browser_publication_output_schema_mismatch")
        if (self.site.read_rule != self.verifier.read_rule
                or self.output.field_ids != tuple(f.field_id for f in self.site.read_rule.fields)):
            raise ValueError("browser_publication_verifier_output_mismatch")
        if isinstance(self.site.read_rule, RegisteredQueryReadRule) and (
            self.site.read_rule.output_schema_json != self.output.output_schema_json
            or self.site.read_rule.maximum_result_bytes != self.output.maximum_bytes
        ):
            raise ValueError("browser_publication_query_schema_mismatch")
        _integrity(self.skill, "browser_skill.v1", self.skill.digest)
        self.site_plan().validate_skill(self.skill)
        _integrity(self, self.schema_version, self.digest)
        return self


def rollback_version_manifest(
    source: BrowserPublicationManifest, *, skill_version: str,
    site_version: str,
) -> BrowserPublicationManifest:
    """Copy audited old content into a new immutable version with provenance.

    This is a candidate only. The PG store must compare the frozen source and
    current head under lock, then require a fresh replay and HumanGate review.
    """
    source = BrowserPublicationManifest.model_validate_json(source.model_dump_json())
    if (source.source_kind != "registered_site"
            or skill_version == source.skill.version
            or site_version == source.site.version):
        raise ValueError("browser_publication_rollback_version_invalid")
    site = source.site.model_dump(mode="json")
    site["version"] = site_version
    site["digest"] = publication_digest("browser_site.publication.v1", site)
    skill = source.skill.model_dump(mode="json")
    skill.update(version=skill_version, site_digest=site["digest"])
    skill["digest"] = publication_digest("browser_skill.v1", skill)
    candidate = source.model_dump(mode="json")
    candidate.update(
        site=site, skill=skill, rollback_source_digest=source.digest,
    )
    candidate["digest"] = publication_digest(
        "browser_publication.durable.v1", candidate,
    )
    # JSON-mode dumps contain arrays; use the strict JSON validation boundary.
    return BrowserPublicationManifest.model_validate_json(canonical_json(candidate))


class BrowserPublicationRecord(Contract):
    manifest: BrowserPublicationManifest
    state: Literal["prepared", "active", "inactive"]
    activation_revision: Annotated[int, Field(ge=0, le=9_007_199_254_740_991)]

    @model_validator(mode="after")
    def revision_matches_state(self) -> Self:
        expected = {"prepared": 0, "active": 1, "inactive": 2}[self.state]
        if self.activation_revision != expected:
            raise ValueError("browser_publication_revision_invalid")
        return self
