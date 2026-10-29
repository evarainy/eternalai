"""One registered three-field FILL_STOP capability. No runtime IO at import."""

from __future__ import annotations

import hashlib
from typing import Any, TypeVar

from pydantic import BaseModel

from app.browser_skill.models import (
    BrowserSkill,
    DecisionBudget,
    DecisionSource,
    LocatorHint,
    ModelManifest,
    ObservationPolicy,
    ObservationRequest,
    ParameterRef,
    SkillStep,
)
from app.browser_skill.publication_contracts import (
    BrowserOutputDefinition,
    BrowserPublicationManifest,
    BrowserSiteDefinition,
    BrowserVerifierDefinition,
    canonical_json,
    publication_digest,
)
from app.browser_skill.site_rules import (
    EffectFact,
    QueryField,
    RegisteredQueryReadRule,
    SiteStepRule,
    canonical_origin,
)
from app.ports.capability_registry import CapabilitySpec
from app.version_binding import capability_version_bindings

CAPABILITY_ID = "browser.form.fill_stop"
SKILL_ID = "d_form_fill_stop"
MODE = "d_form_fill_stop_v1"
FIELDS = ("category", "position", "description")
OUTPUT_FIELDS = (
    "mode",
    "execution_status",
    "field_checks",
    "actions_completed",
    "submit_actions",
    "model_requests",
    "seconds",
)
T = TypeVar("T", bound=BaseModel)


def _schema_digest(schema: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(schema).encode("utf-8")).hexdigest()


def _signed(cls: type[T], domain: str, **values: Any) -> T:
    # JSON roundtrip is the strict contract's existing tuple/array boundary.
    provisional = cls.model_construct(digest="0" * 64, **values)
    body = provisional.model_dump(mode="json")
    body["digest"] = publication_digest(domain, body)
    return cls.model_validate_json(canonical_json(body))


def registration_digest() -> str:
    """Stable D registration identity; deliberately independent of source file bytes."""
    registration = {"source_id": SKILL_ID, "mode": MODE, "fields": list(FIELDS)}
    return hashlib.sha256(canonical_json(registration).encode()).hexdigest()


def build_fill_stop_manifest(*, origin: str) -> BrowserPublicationManifest:
    """The approved origin is supplied locally; no personal directory or values."""
    canonical_origin(origin)
    digest = registration_digest()
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": list(FIELDS),
        "properties": {
            name: {
                "type": "string",
                "minLength": 1,
                "maxLength": 100 if name == "category" else 500,
            }
            for name in FIELDS
        },
    }
    output_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": list(OUTPUT_FIELDS),
        "properties": {
            "mode": {"type": "string", "enum": ["FILL_STOP"]},
            "execution_status": {"type": "string", "enum": ["filled_stopped"]},
            "field_checks": {
                "type": "object",
                "additionalProperties": False,
                "required": list(FIELDS),
                "properties": {name: {"type": "boolean", "enum": [True]} for name in FIELDS},
            },
            "actions_completed": {"type": "integer", "minimum": 3, "maximum": 4},
            "submit_actions": {"type": "integer", "enum": [0]},
            "model_requests": {"type": "integer", "enum": [0]},
            "seconds": {"type": "number", "minimum": 0, "maximum": 1800},
        },
    }
    capability = CapabilitySpec(
        capability_id=CAPABILITY_ID,
        name="填写表单后停止",
        type="action",
        intent_tags=["browser.form.fill_stop"],
        input_schema=input_schema,
        output_schema=output_schema,
        input_schema_digest=_schema_digest(input_schema),
        output_schema_digest=_schema_digest(output_schema),
        risk_level="high",
        owner="eternalai-platform",
        version="1.0.0",
        status="active",
        target_system="oa",
        short_description="仅在已打开且获准的表单填写类别、故障位置和故障描述，随后停止；不保存或提交。",
        execution_identity="user_delegated",
        binding_required=True,
        policy_digest=None,
    )
    policy = _signed(
        ObservationPolicy,
        "browser_observation_policy.v1",
        policy_id="d_form_fields",
        allowed_names=("报修类别", "故障位置", "故障描述"),
        allowed_roles=("textbox", "combobox", "option"),
        maximum_candidates=8,
    )
    steps = (
        SkillStep(
            step_id="category",
            operation="select_option",
            effect="may_write",
            locator=LocatorHint(kind="role_name", value="报修类别"),
            option_ref=ParameterRef(name="category"),
        ),
        SkillStep(
            step_id="position",
            operation="fill",
            effect="may_write",
            locator=LocatorHint(kind="role_name", value="故障位置"),
            value_ref=ParameterRef(name="position"),
        ),
        SkillStep(
            step_id="description",
            operation="fill",
            effect="may_write",
            locator=LocatorHint(kind="role_name", value="故障描述"),
            value_ref=ParameterRef(name="description"),
        ),
    )
    read = RegisteredQueryReadRule(
        object_type="bound_form",
        key_ref=ParameterRef(name="bound_form"),
        fields=tuple(QueryField(field_id=name) for name in OUTPUT_FIELDS),
        output_schema_json=canonical_json(output_schema),
        maximum_result_bytes=2048,
    )
    verifier = _signed(
        BrowserVerifierDefinition,
        "browser_verifier.publication.v1",
        verifier_id="d_form_fields",
        version="v1",
        read_rule=read,
        contract="d_form_fields_v1",
    )
    site = _signed(
        BrowserSiteDefinition,
        "browser_site.publication.v1",
        site_id=SKILL_ID,
        version="v30",
        source=DecisionSource(source_id=SKILL_ID, origin=origin, fixture_digest=digest),
        navigation_origins=(origin,),
        policy=policy,
        read_rule=read,
        steps=tuple(
            SiteStepRule(
                step=step,
                observation=ObservationRequest(region_id="bound_form"),
                target_criteria=(step.locator.value,),
                option_criteria=("authorized_category",)
                if step.operation == "select_option"
                else (),
                effect=EffectFact(
                    operation=step.operation,
                    actual_effect="may_write",
                    proof_ref="d_form_finite_fields_v1",
                    evidence_digest=digest,
                ),
            )
            for step in steps
        ),
        # A model manifest is required by the older frozen site schema;
        # this deterministic execution path never constructs a Decision client.
        decision_manifest=ModelManifest(
            request_model="deterministic_local",
            deployment_model="d_form_zero_llm_v1",
            manifest_digest=digest,
        ),
        decision_budget=DecisionBudget(max_candidates=8),
    )
    skill = _signed(
        BrowserSkill,
        "browser_skill.v1",
        skill_id=SKILL_ID,
        version="v30",
        site_id=site.site_id,
        site_digest=site.digest,
        verifier_id=verifier.verifier_id,
        verifier_digest=verifier.digest,
        parameters=("bound_form", *FIELDS),
        steps=steps,
    )
    output = _signed(
        BrowserOutputDefinition,
        "browser_output.publication.v1",
        field_ids=OUTPUT_FIELDS,
        output_schema_json=canonical_json(output_schema),
        maximum_bytes=2048,
    )
    return _signed(
        BrowserPublicationManifest,
        "browser_publication.durable.v1",
        skill=skill,
        site=site,
        verifier=verifier,
        output=output,
        capability_snapshot_json=canonical_json(capability.model_dump(mode="json")),
        capability_bindings=capability_version_bindings(capability),
        source_kind="registered_site",
        effect="controlled_write",
        execution_mode=MODE,
    )
