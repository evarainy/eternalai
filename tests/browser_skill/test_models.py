from __future__ import annotations

import asyncio
import json
import pickle
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import replace

import pytest
from pydantic import ValidationError

from app.browser_skill.models import (
    ActionCommand,
    BrowserFailure,
    BrowserOwner,
    BrowserSessionRef,
    BrowserSkill,
    ConfirmedBusinessKey,
    Coverage,
    DecisionRequest,
    DecisionResult,
    DispatchPermit,
    DispatchReceipt,
    ExecutionContext,
    LocatorHint,
    ModelManifest,
    ObservationRequest,
    ParameterRef,
    ReadEvidence,
    ReadFieldEvidence,
    ReadSpec,
    SealedParameter,
    SkillStep,
    VerificationResult,
    VisibleCandidate,
)
from app.browser_skill.scoping import scope_snapshot
from tests.browser_skill.factories import DIGEST, binding, policy, projection, request, source


@pytest.mark.parametrize("field", ["value", "password", "script", "selector", "provider_payload"])
def test_visible_contract_rejects_raw_value_and_vendor_fields(field: str) -> None:
    data = projection().candidates[0].model_dump()
    data[field] = "forbidden synthetic marker"
    with pytest.raises(ValidationError, match="Extra inputs"):
        VisibleCandidate.model_validate(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("operation", "click"),
        ("url", "https://fixture.invalid"),
        ("owner", "synthetic-owner"),
        ("task_kind", "execute_script"),
    ],
)
def test_decision_input_cannot_own_action_or_authority(field: str, value: str) -> None:
    data = request().model_dump()
    data[field] = value
    with pytest.raises(ValidationError):
        DecisionRequest.model_validate(data)


def test_unique_candidates_and_same_scope_required() -> None:
    req = request()
    with pytest.raises(ValidationError, match="duplicate_candidate"):
        DecisionRequest(
            request_id=req.request_id,
            scope=req.scope,
            criteria=req.criteria,
            candidates=(req.candidates[0], req.candidates[0]),
        )
    with pytest.raises(ValidationError, match="candidate_scope_mismatch"):
        DecisionRequest(
            request_id=req.request_id,
            scope=req.scope.model_copy(update={"page_epoch": 2}),
            criteria=req.criteria,
            candidates=req.candidates,
        )


@pytest.mark.parametrize(
    "change",
    [
        {"page_epoch": 2},
        {"region_digest": "b" * 64},
        {"region_id": "other"},
    ],
)
def test_selected_result_rejects_relevant_staleness(change: dict[str, object]) -> None:
    req = request()
    result = DecisionResult(
        request_id=req.request_id,
        scope=req.scope,
        status="selected",
        selected=req.candidates[0].ref,
    )
    result.validate_for(req, req.scope)
    with pytest.raises(ValueError, match="browser_target_stale"):
        result.validate_for(req, req.scope.model_copy(update=change))


def test_result_cannot_select_new_id_or_candidate_generation() -> None:
    req = request()
    for update in ({"target_id": "invented"}, {"candidate_epoch": 2}):
        result = DecisionResult(
            request_id=req.request_id,
            scope=req.scope,
            status="selected",
            selected=req.candidates[0].ref.model_copy(update=update),
        )
        with pytest.raises(ValueError, match="decision_unknown_target"):
            result.validate_for(req, req.scope)
    with pytest.raises(ValidationError, match="selection_inconsistent"):
        DecisionResult(request_id=req.request_id, scope=req.scope, status="selected")
    with pytest.raises(ValidationError, match="requires_status_or_error"):
        DecisionResult(
            request_id=req.request_id, scope=req.scope, status="unsupported", error="unavailable"
        )


def test_projection_accepts_recursive_frame_and_safe_labels() -> None:
    result = scope_snapshot(projection(), binding(), policy())
    assert result.scope.frame_path[1].frame_id == "nested"
    assert tuple(c.name for c in result.candidates) == ("Open", "Close")


@pytest.mark.parametrize(
    "change",
    [
        {"name": "Unapproved label"},
        {"context": ("Unapproved text",)},
        {"visible": False},
        {"role": "textbox"},
    ],
)
def test_projection_filters_before_exposure(change: dict[str, object]) -> None:
    item = projection()
    candidate = item.candidates[0].model_copy(update=change)
    with pytest.raises(ValueError, match="projection_disallowed"):
        scope_snapshot(item.model_copy(update={"candidates": (candidate,)}), binding(), policy())


def test_projection_rejects_owner_revision_and_nested_remount() -> None:
    item = projection()
    for field in ("binding_revision", "authorization_revision", "lease_epoch"):
        with pytest.raises(ValueError, match="binding_stale"):
            scope_snapshot(item, binding().model_copy(update={field: 99}), policy())
    other = binding().model_copy(
        update={"owner": binding().owner.model_copy(update={"user_id": "other"})}
    )
    with pytest.raises(ValueError, match="binding_stale"):
        scope_snapshot(item, other, policy())
    path = item.scope.frame_path
    remount = item.scope.model_copy(
        update={"frame_path": (path[0], path[1].model_copy(update={"frame_epoch": 9}))}
    )
    with pytest.raises(ValueError, match="frame_stale"):
        scope_snapshot(item.model_copy(update={"scope": remount}), binding(), policy())


def test_coverage_cannot_turn_partial_into_no_data() -> None:
    with pytest.raises(ValidationError, match="empty_requires_complete"):
        Coverage(state="partial", reason="virtualized", trusted_empty=True)
    item = projection()
    with pytest.raises(ValueError, match="empty_evidence_conflict"):
        scope_snapshot(
            item.model_copy(
                update={
                    "coverage": Coverage(
                        state="complete",
                        reason="complete",
                        trusted_empty=True,
                    )
                }
            ),
            binding(),
            policy(),
        )


def test_skill_binds_values_and_read_spec_uses_confirmed_business_key() -> None:
    step = SkillStep(
        step_id="fill",
        operation="fill",
        locator=LocatorHint(kind="test_id", value="query"),
        effect="read_only",
        value_ref=ParameterRef(name="query"),
    )
    fields = dict(
        skill_id="query_todos",
        version="v1",
        digest=DIGEST,
        site_id="mock_oa",
        site_digest=DIGEST,
        verifier_id="by_key",
        verifier_digest=DIGEST,
        steps=(step,),
    )
    with pytest.raises(ValidationError, match="unbound_parameter"):
        BrowserSkill(**fields)
    skill = BrowserSkill(**fields, parameters=("query",))
    command = ActionCommand(
        skill_digest=skill.digest, step=step, binding=binding(), target=request().candidates[0].ref
    )
    assert command.step.value_ref.name == "query"
    spec = ReadSpec(
        binding=binding(),
        business_key=ConfirmedBusinessKey(
            confirmation_ref="confirmed",
            object_type="todo",
            key_digest=DIGEST,
            value_ref=ParameterRef(name="confirmed_key"),
        ),
        verifier_id="by_key",
        verifier_digest=DIGEST,
        fields=("status",),
    )
    assert spec.business_key.value_ref.name == "confirmed_key"
    with pytest.raises(ValidationError, match="Extra inputs"):
        ReadSpec.model_validate({**spec.model_dump(), "selected_row": "target_a"})


def test_latest_deployment_is_not_a_pin() -> None:
    with pytest.raises(ValidationError, match="deployment_must_be_pinned"):
        ModelManifest(
            request_model="alias", deployment_model="model-latest", manifest_digest=DIGEST
        )


def _execution_skill() -> BrowserSkill:
    return BrowserSkill(
        skill_id="query_todos",
        version="v1",
        digest=DIGEST,
        site_id="mock_oa",
        site_digest=DIGEST,
        verifier_id="by_key",
        verifier_digest=DIGEST,
        parameters=("query",),
        steps=(
            SkillStep(
                step_id="fill",
                operation="fill",
                locator=LocatorHint(kind="test_id", value="query"),
                effect="read_only",
                value_ref=ParameterRef(name="query"),
            ),
        ),
    )


def _execution_context() -> ExecutionContext:
    async def current(session):
        return binding()

    async def authorize(session, skill, command, current_binding):
        if current_binding != binding() or skill != _execution_skill():
            raise ValueError("denied")

    async def resolve(ref, purpose, current_binding, skill_digest, step_id):
        raise ValueError("no_input_registered")

    @asynccontextmanager
    async def barrier(session, command, current_binding):
        def begin():
            if current_binding != binding():
                raise ValueError("stale")

        yield DispatchPermit("execution", command, begin)

    return ExecutionContext(
        execution_id="execution",
        skill=_execution_skill(),
        expected_binding=binding(),
        source=source(),
        deadline_monotonic=time.monotonic() + 10,
        cancellation=asyncio.Event(),
        navigation_origins=("https://fixture.invalid",),
        current_binding=current,
        authorize=authorize,
        resolve_parameter=resolve,
        dispatch_barrier=barrier,
    )


def test_observation_request_bootstraps_without_supplied_facts() -> None:
    initial = ObservationRequest(region_id="inbox")
    assert initial.expected_scope is None
    assert (
        ObservationRequest(region_id="inbox", expected_scope=request().scope).expected_scope
        == request().scope
    )
    with pytest.raises(ValidationError, match="observation_region_mismatch"):
        ObservationRequest(region_id="other", expected_scope=request().scope)
    with pytest.raises(ValidationError, match="Extra inputs"):
        ObservationRequest.model_validate({"region_id": "inbox", "selector": "body"})


@pytest.mark.parametrize(
    "field,allowed_field",
    [
        ("row_label", "allowed_row_labels"),
        ("column_label", "allowed_column_labels"),
    ],
)
def test_row_and_column_labels_require_their_own_allowlist(field, allowed_field) -> None:
    item = projection()
    safe_label = "Pending" if field == "row_label" else "Status"
    candidate = item.candidates[0].model_copy(update={field: safe_label})
    item = item.model_copy(update={"candidates": (candidate,)})
    with pytest.raises(ValueError, match="projection_disallowed"):
        scope_snapshot(item, binding(), policy())
    checked = scope_snapshot(
        item, binding(), policy().model_copy(update={allowed_field: (safe_label,)})
    )
    assert getattr(checked.candidates[0], field) == safe_label
    with pytest.raises(ValueError, match="projection_disallowed"):
        scope_snapshot(
            item, binding(), policy().model_copy(update={"allowed_context": (safe_label,)})
        )


def test_select_option_requires_fixed_declared_parameter() -> None:
    step_args = dict(
        step_id="choose",
        operation="select_option",
        locator=LocatorHint(kind="test_id", value="department"),
        effect="read_only",
    )
    with pytest.raises(ValidationError, match="skill_option_reference_mismatch"):
        SkillStep(**step_args)
    step = SkillStep(**step_args, option_ref=ParameterRef(name="department"))
    skill = _execution_skill()
    with pytest.raises(ValidationError, match="unbound_parameter"):
        BrowserSkill(
            **{
                **{name: getattr(skill, name) for name in type(skill).model_fields},
                "steps": (step,),
            }
        )
    approved = BrowserSkill(
        **{
            **{name: getattr(skill, name) for name in type(skill).model_fields},
            "steps": (step,),
            "parameters": ("department",),
        }
    )
    assert approved.steps[0].option_ref.name == "department"
    with pytest.raises(ValidationError, match="skill_option_reference_mismatch"):
        SkillStep(**{**step_args, "operation": "click"}, option_ref=ParameterRef(name="department"))


def test_sealed_parameter_rejects_wrong_purpose_step_or_current_binding() -> None:
    private = secrets.token_urlsafe(24)
    metadata = dict(
        ref=ParameterRef(name="query"),
        purpose="fill_value",
        binding=binding(),
        skill_digest=DIGEST,
        step_id="fill",
    )
    sealed = SealedParameter(private, **metadata)
    assert sealed.consume(lambda value: secrets.compare_digest(value, private), **metadata)
    for change in (
        {"purpose": "navigation_url"},
        {"step_id": "other"},
        {"binding": binding().model_copy(update={"binding_revision": 9})},
        {"skill_digest": "b" * 64},
    ):
        with pytest.raises(ValueError, match="sealed_parameter_authority_mismatch"):
            sealed.consume(lambda value: None, **{**metadata, **change})
    safe_representation = private not in repr(sealed) and private not in str(sealed)
    assert safe_representation
    assert not hasattr(sealed, "__dict__") and not hasattr(sealed, "model_dump")
    with pytest.raises(TypeError):
        json.dumps(sealed)
    with pytest.raises(TypeError, match="serialization_forbidden"):
        pickle.dumps(sealed)


def test_option_parameter_is_a_bounded_approved_set() -> None:
    metadata = dict(
        ref=ParameterRef(name="department"),
        purpose="option_values",
        binding=binding(),
        skill_digest=DIGEST,
        step_id="choose",
    )
    for values in ((), "unbounded string", ("same", "same"), tuple(str(n) for n in range(256))):
        with pytest.raises(ValueError, match="sealed_option_values_invalid"):
            SealedParameter(values, **metadata)
    sealed = SealedParameter(("finance", "engineering"), **metadata)
    assert sealed.consume(lambda value: len(value), **metadata) == 2


def test_dispatch_permit_is_single_use_and_receipt_is_not_business_success() -> None:
    ctx = _execution_context()
    command = ActionCommand(
        skill_digest=DIGEST,
        step=ctx.skill.steps[0],
        binding=binding(),
        target=request().candidates[0].ref,
    )
    sends = []
    permit = DispatchPermit("execution", command, lambda: sends.append("send"))
    assert permit.durability == "memory_only" and permit.send_started is False
    permit.begin_send()
    with pytest.raises(ValueError, match="already_started"):
        permit.begin_send()
    assert sends == ["send"] and permit.send_started is True
    receipt = DispatchReceipt(
        skill_digest=DIGEST, step_id="fill", state="acknowledged", evidence_digest=DIGEST
    )
    receipt.validate_for(command)
    assert not hasattr(receipt, "verified")
    with pytest.raises(ValidationError, match="acknowledged_requires_evidence"):
        DispatchReceipt(skill_digest=DIGEST, step_id="fill", state="acknowledged")
    with pytest.raises(ValidationError, match="verification_evidence_required"):
        VerificationResult(status="verified")


@pytest.mark.parametrize("code", ["overloaded", "invalid_request", "resource_not_found"])
def test_pre_dispatch_failures_keep_distinct_codes(code) -> None:
    failure = BrowserFailure(
        code=code, phase="dispatch", dispatch_state="not_sent", cleanup_required=False
    )
    receipt = DispatchReceipt(
        skill_digest=DIGEST, step_id="fill", state="not_sent", failure=failure
    )
    assert receipt.failure.code == code and receipt.state == "not_sent"
    with pytest.raises(ValidationError, match="dispatch_failure_state_mismatch"):
        DispatchReceipt(skill_digest=DIGEST, step_id="fill", state="possibly_sent", failure=failure)


def test_execution_context_is_local_and_origins_are_exact() -> None:
    ctx = _execution_context()
    assert "fixture.invalid" not in repr(ctx) and "current_binding" not in repr(ctx)
    assert not hasattr(ctx, "__dict__") and not hasattr(ctx, "model_dump")
    with pytest.raises(TypeError, match="serialization_forbidden"):
        pickle.dumps(ctx)
    with pytest.raises(TypeError, match="serialization_forbidden"):
        ctx.__getstate__()
    with pytest.raises(TypeError):
        json.dumps(ctx)
    for origin in (
        "https://fixture.invalid/path",
        "https://fixture.invalid?query",
        "https://fixture.invalid:443",
        "https://FIXTURE.invalid",
        "https://*.invalid",
        "https://fixture.invalid\\other",
    ):
        with pytest.raises(ValueError, match="execution_navigation_origins_invalid"):
            replace(ctx, navigation_origins=(origin,))
    with pytest.raises(ValueError, match="execution_deadline_invalid"):
        replace(ctx, deadline_monotonic=float("nan"))
    assert (
        asyncio.run(
            ctx.current_binding(BrowserSessionRef(session_ref="reserved", binding=binding()))
        )
        == binding()
    )


def test_independent_read_evidence_is_bound_to_confirmed_key_owner_and_fields() -> None:
    key = ConfirmedBusinessKey(
        confirmation_ref="confirmed",
        object_type="todo",
        key_digest=DIGEST,
        value_ref=ParameterRef(name="business_key"),
    )
    spec = ReadSpec(
        binding=binding(),
        business_key=key,
        verifier_id="by_key",
        verifier_digest=DIGEST,
        fields=("status",),
    )
    evidence = ReadEvidence(
        binding=binding(),
        business_key=key,
        match_count=1,
        key_match=True,
        owner_match=True,
        coverage=Coverage(state="complete", reason="complete"),
        fields=(ReadFieldEvidence(field_id="status", status="matched"),),
        evidence_digest=DIGEST,
    )
    evidence.validate_for(spec, binding())
    assert evidence.fields[0].status == "matched"
    with pytest.raises(ValueError, match="binding_stale"):
        evidence.validate_for(spec, binding().model_copy(update={"lease_epoch": 9}))
    with pytest.raises(ValueError, match="read_key_mismatch"):
        evidence.model_copy(
            update={"business_key": key.model_copy(update={"key_digest": "b" * 64})}
        ).validate_for(spec, binding())
    with pytest.raises(ValueError, match="read_fields_mismatch"):
        evidence.model_copy(
            update={"fields": (ReadFieldEvidence(field_id="other", status="matched"),)}
        ).validate_for(spec, binding())
    base = {name: getattr(evidence, name) for name in type(evidence).model_fields}
    with pytest.raises(ValidationError, match="empty_read_cannot_match"):
        ReadEvidence(**{**base, "match_count": 0})
    with pytest.raises(ValidationError):
        ReadEvidence(**{**base, "match_count": -1})
    with pytest.raises(ValidationError, match="duplicate_read_evidence_field"):
        ReadEvidence(**{**base, "fields": evidence.fields * 2})


def test_real_server_bound_session_shape_is_preserved_and_not_serialized() -> None:
    from app.infra.auth.crypto import PrincipalSessionBinder
    from app.ports.auth import Principal, PrincipalOrgContext

    principal = Principal(
        ai_user_id="synthetic_user",
        display_name="Synthetic",
        roles=(),
        org_ctx=PrincipalOrgContext(tenant_id="synthetic_tenant"),
    )
    binder = PrincipalSessionBinder(binding_key=secrets.token_bytes(32))
    private_bound_identity = binder.bind(principal, secrets.token_urlsafe(24))
    owner = BrowserOwner(
        tenant_id="synthetic_tenant", user_id="synthetic_user", session_id=private_bound_identity
    )
    preserved = owner.session_id == private_bound_identity
    excluded = (
        private_bound_identity not in owner.model_dump_json()
        and private_bound_identity not in repr(owner)
    )
    verifies = binder.bind(principal, owner.session_id) == private_bound_identity
    assert preserved and excluded and verifies
