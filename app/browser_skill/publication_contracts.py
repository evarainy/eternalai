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
    RegisteredReadRule,
    RegisteredSitePlan,
    SiteStepRule,
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
    read_rule: RegisteredReadRule
    decision_manifest: ModelManifest
    decision_budget: DecisionBudget

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
        if any(rule.step.effect != "read_only" or rule.effect.actual_effect != "read_only"
               for rule in self.steps):
            raise ValueError("browser_publication_effect_denied")
        _integrity(self, self.schema_version, self.digest)
        return self


class BrowserVerifierDefinition(Contract):
    schema_version: Literal["browser_verifier.publication.v1"] = "browser_verifier.publication.v1"
    verifier_id: OpaqueId
    version: OpaqueId
    digest: Digest
    contract: Literal["independent_confirmed_key_v1"] = "independent_confirmed_key_v1"
    read_rule: RegisteredReadRule
    require_complete_coverage: Literal[True] = True
    require_exact_owner: Literal[True] = True
    require_unique_business_key: Literal[True] = True

    @model_validator(mode="after")
    def integrity(self) -> Self:
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
    source_kind: Literal["registered_synthetic_fixture"] = "registered_synthetic_fixture"
    effect: Literal["read_only"] = "read_only"
    execution_mode: Literal["browser_async_v1"] = "browser_async_v1"

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
        if (capability.status != "active" or capability.type != "query"
                or capability.target_system != "oa"
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
        _integrity(self.skill, "browser_skill.v1", self.skill.digest)
        self.site_plan().validate_skill(self.skill)
        _integrity(self, self.schema_version, self.digest)
        return self


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
