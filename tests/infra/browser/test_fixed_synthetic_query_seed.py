"""Fixed source loading and bounded offline adapter/Executor wiring checks."""

import asyncio
import hashlib
import html
import json
import re
from dataclasses import replace
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic import ValidationError

from app.browser_skill.executor import BrowserExecutor
from app.browser_skill.models import (
    BrowserOwner,
    BrowserSessionRef,
    ConfirmedBusinessKey,
    ModelManifest,
    ParameterRef,
    ReadSpec,
    SealedParameter,
    TargetRef,
    VisibleCandidate,
)
from app.browser_skill.publication_contracts import BrowserPublicationManifest
from app.browser_skill.site_rules import RegisteredQueryReadRule
from app.browser_skill.verifier import IndependentVerifier, failure
from app.infra.adapters.oa.capabilities import expected_oa_capabilities
from app.infra.browser.composition import BrowserVerticalDependencies
from app.infra.browser.fixed_synthetic_seed import (
    SYNTHETIC_DETAIL_CAPABILITY_ID,
    SYNTHETIC_ORIGIN,
    SYNTHETIC_TENANT,
    SYNTHETIC_USER,
    SyntheticDetailArguments,
    build_fixed_synthetic_query_source,
    build_fixed_synthetic_source,
    install_fixed_synthetic_seed,
)
from app.infra.browser.playwright_web_adapter import PlaywrightWebAdapter, RegisteredExecution
from app.ports.browser_publication_store import BrowserPublicationError
from app.ports.llm_provider import LLMProviderPort
from app.ports.structured_output import StructuredOutputPort
from tests.infra.browser.test_playwright_web_adapter import Node, World


def _model() -> ModelManifest:
    return ModelManifest(
        request_model="fixture_model_v1", deployment_model="deploymentfixturev1",
        manifest_digest=hashlib.sha256(b"synthetic-query-loader-only-v1").hexdigest(),
    )


def _fixture_text(markup: str, element_id: str) -> str:
    found = re.search(fr'<span id="{element_id}">([^<]*)</span>', markup)
    assert found is not None
    return html.unescape(found.group(1))


def _query_world() -> tuple[World, TargetRef, TargetRef]:
    """The fake renderer consumes actual code-owned HTML, not expected output."""
    source = build_fixed_synthetic_query_source(_model())
    markup = source.html.decode("utf-8")
    actual = {
        name: _fixture_text(markup, name)
        for name in ("fixture-object-type", "fixture-key", "fixture-tenant",
                     "fixture-user", "fixture-collection")
    }
    assert actual["fixture-tenant"] == SYNTHETIC_TENANT
    assert actual["fixture-user"] == SYNTHETIC_USER
    w = World("read")
    owner = BrowserOwner(
        tenant_id=actual["fixture-tenant"], user_id=actual["fixture-user"],
        session_id=w.binding.owner.session_id,
    )
    w.binding = w.binding.model_copy(update={"owner": owner})
    w.session = BrowserSessionRef(session_ref=w.session.session_ref, binding=w.binding)
    w.live = SimpleNamespace(session=w.session, context=w.live.context)
    w.frame.url = source.manifest.site.source.origin
    w.skill = source.manifest.skill
    w.plan = source.manifest.site_plan()
    w.site = source.site
    w.rules = source.rules
    scope = w.view.scope.model_copy(update={"region_id": source.region.region_id})
    decoy_ref = TargetRef(target_id="alternative_row", candidate_epoch=1, scope=scope)
    real_ref = TargetRef(target_id="messages_row", candidate_epoch=2, scope=scope)
    w.view = w.view.model_copy(update={
        "policy_id": w.plan.policy.policy_id,
        "policy_digest": w.plan.policy.digest,
        "binding": w.binding,
        "scope": scope,
        "candidates": (
            VisibleCandidate(ref=decoy_ref, role="row", name="Other synthetic row",
                             value_state="not_applicable", visible=True, enabled=True),
            VisibleCandidate(ref=real_ref, role="row", name="Synthetic messages",
                             value_state="not_applicable", visible=True, enabled=True),
        ),
    })
    selector = source.rules.steps[0].selector
    w.row = Node(selector=source.rules.read.row_selector, also_matches=(selector,), values={
        "#fixture-object-type": actual["fixture-object-type"],
        "#fixture-key": actual["fixture-key"],
        "#fixture-tenant": actual["fixture-tenant"],
        "#fixture-user": actual["fixture-user"],
        "#fixture-collection": actual["fixture-collection"],
    })
    decoy = Node(selector=selector)
    w.region = Node(selector=source.region.region_selector, children=[decoy, w.row])
    decoy.parent = w.region
    w.row.parent = w.region
    w.controls = {decoy_ref.target_id: decoy, real_ref.target_id: w.row}
    w.control = w.row
    w._inputs = {"business_key": actual["fixture-key"]}
    w.confirmed = ReadSpec(
        binding=w.binding, verifier_id=w.skill.verifier_id,
        verifier_digest=w.skill.verifier_digest, fields=("collection",),
        business_key=ConfirmedBusinessKey(
            confirmation_ref="fixture_confirmation",
            object_type="system_message_collection",
            key_digest=hashlib.sha256(actual["fixture-key"].encode()).hexdigest(),
            value_ref=ParameterRef(name="business_key"),
        ),
        mode="independent_query_detail_v1",
    )

    async def resolve(ref, purpose, binding, digest, step_id):
        if (ref != w.confirmed.business_key.value_ref or purpose != "business_key"
                or binding != w.binding or digest != w.skill.digest
                or step_id != w.skill.verifier_id):
            raise failure("denied")
        return SealedParameter(
            w._inputs["business_key"], ref=ref, purpose=purpose, binding=binding,
            skill_digest=digest, step_id=step_id,
        )

    w.context = replace(
        w.context, skill=w.skill, expected_binding=w.binding,
        source=w.plan.source, navigation_origins=w.plan.navigation_origins,
        resolve_parameter=resolve,
    )
    w.adapter = PlaywrightWebAdapter(
        registry=w, observer=w.observer, site=w.site, rules=(w.rules,),
        executions=(RegisteredExecution(
            w.session, w.context, w.confirmed.business_key,
            project_output=source.projector,
        ),),
    )
    w.decision.override = real_ref
    return w, decoy_ref, real_ref


def test_query_seed_binds_admitted_key_without_expected_output() -> None:
    source = build_fixed_synthetic_query_source(_model())
    manifest = BrowserPublicationManifest.model_validate_json(source.manifest.model_dump_json())
    rule = manifest.site.read_rule
    assert isinstance(rule, RegisteredQueryReadRule)
    assert rule.key_ref.name == "business_key"
    assert manifest.skill.parameters == ("business_key",)
    assert tuple(field.model_dump() for field in rule.fields) == ({"field_id": "collection"},)
    assert rule.output_schema_json == manifest.output.output_schema_json
    assert rule.maximum_result_bytes == manifest.output.maximum_bytes
    assert source.rules.read.object_type is not None
    assert source.rules.read.object_type.selector == "#fixture-object-type"
    assert b'id="fixture-object-type">system_message_collection<' in source.html
    assert source.manifest.site.source.fixture_digest == hashlib.sha256(source.html).hexdigest()
    assert source.manifest.site.policy.allowed_names == (
        "Synthetic messages", "Other synthetic row",
    )
    assert source.manifest.site.policy.maximum_candidates == 2
    assert source.rules.steps[0].selector == '[data-testid="fixture_row"]'
    assert source.rules.read.row_selector == "#fixture-row"
    assert source.manifest.site.steps[0].target_criteria == (
        "Select the row named Synthetic messages",
    )
    rows = re.findall(
        r'<div id="([^"]+)" role="row" aria-label="([^"]+)" '
        r'data-testid="fixture_row">', source.html.decode("utf-8"),
    )
    assert rows == [
        ("fixture-row", "Synthetic messages"),
        ("fixture-alternative", "Other synthetic row"),
    ]
    assert source.html.count(b'id="fixture-key"') == 1
    legacy = build_fixed_synthetic_source(_model())
    assert b'fixture-alternative' not in legacy.html
    assert legacy.manifest.site.policy.maximum_candidates == 1
    assert legacy.rules.steps[0].selector == "#fixture-row"
    with pytest.raises(BrowserPublicationError, match="fixture_expected_values_forbidden"):
        _ = source.fixture_reference_values


def test_query_two_candidates_use_actual_executor_decision_and_live_target() -> None:
    async def run() -> None:
        w, decoy_ref, real_ref = _query_world()
        verifier = IndependentVerifier(w.adapter, w.site, w.read_spec)
        executor = BrowserExecutor(
            w.adapter, w.decision, verifier, w.site, w.read_spec, w.decision_context,
        )
        result = await executor.run(w.session, w.context)
        assert result.sequence == "completed" and result.failure is None
        assert result.model_calls == 1 and len(w.decision.calls) == 1
        request = w.decision.calls[0]
        assert request.criteria == w.plan.steps[0].target_criteria
        assert tuple(c.ref for c in request.candidates) == (decoy_ref, real_ref)
        assert result.decisions[0].selected == real_ref
        assert result.receipts[0].state == "acknowledged"
        assert result.verification is not None and result.verification.status == "verified"
        assert w.dom_sends == [] and w.observer.calls >= 2
        fields = w.adapter._consume_verified_result(w.session, w.context, result.verification, dict)
        assert w.adapter._executions[w.session.session_ref].project_output is not None
        projected = w.adapter._executions[w.session.session_ref].project_output(fields)
        assert projected == json.loads(w.row.values["#fixture-collection"])

    asyncio.run(run())


@pytest.mark.parametrize("fault,code", [
    ("decoy", "stale"), ("unknown", "invalid_response"), ("stale_ref", "stale"),
    ("key", "stale"), ("tenant", "stale"), ("user", "stale"),
    ("object_type", "stale"),
])
def test_query_two_candidate_wrong_target_or_actual_record_fails(
    fault: str, code: str,
) -> None:
    async def run() -> None:
        w, decoy_ref, real_ref = _query_world()
        if fault == "decoy":
            w.decision.override = decoy_ref
        elif fault == "unknown":
            w.decision.override = TargetRef(
                target_id="unknown", candidate_epoch=1, scope=real_ref.scope,
            )
        elif fault == "stale_ref":
            def change() -> None:
                real = w.view.candidates[1]
                changed = real.model_copy(update={
                    "ref": real.ref.model_copy(update={"candidate_epoch": 3}),
                })
                w.view = w.view.model_copy(update={
                    "candidates": (w.view.candidates[0], changed),
                })
            w.decision.hook = change
        elif fault == "key":
            w.row.values["#fixture-key"] = "wrong_business_key"
        elif fault == "tenant":
            w.row.values["#fixture-tenant"] = "other_tenant"
        elif fault == "user":
            w.row.values["#fixture-user"] = "other_user"
        else:
            w.row.values["#fixture-object-type"] = "other_type"
        verifier = IndependentVerifier(w.adapter, w.site, w.read_spec)
        result = await BrowserExecutor(
            w.adapter, w.decision, verifier, w.site, w.read_spec, w.decision_context,
        ).run(w.session, w.context)
        assert result.sequence == "stopped" and result.failure is not None
        assert result.failure.code == code
        assert result.model_calls == 1 and len(w.decision.calls) == 1
        assert result.receipts == () and result.verification is None
        assert w.adapter._private_reads == {}

    asyncio.run(run())


def test_selected_real_row_still_needs_independent_json_validation() -> None:
    async def run() -> None:
        w, _, real_ref = _query_world()
        w.row.values["#fixture-collection"] = "{}"
        verifier = IndependentVerifier(w.adapter, w.site, w.read_spec)
        result = await BrowserExecutor(
            w.adapter, w.decision, verifier, w.site, w.read_spec, w.decision_context,
        ).run(w.session, w.context)
        assert result.decisions[0].selected == real_ref
        assert result.receipts[0].state == "acknowledged"
        assert result.verification is not None and result.verification.status == "mismatch"
        assert w.adapter._private_reads == {}

    asyncio.run(run())


def test_query_capability_is_separate_and_does_not_widen_canonical_oa() -> None:
    canonical = expected_oa_capabilities()[1]
    source = build_fixed_synthetic_query_source(_model())
    detail = source.manifest.capability
    assert detail.capability_id == SYNTHETIC_DETAIL_CAPABILITY_ID
    assert detail.capability_id != canonical.capability_id
    assert canonical.input_schema["properties"] == {}
    assert detail.input_schema["required"] == ["business_key"]
    assert detail.input_schema["additionalProperties"] is False
    assert detail.output_schema == canonical.output_schema
    assert source.manifest.site.navigation_origins == (SYNTHETIC_ORIGIN,)


@pytest.mark.parametrize("arguments", [
    {}, {"business_key": ""}, {"business_key": "x" * 97}, {"business_key": 1},
    {"business_key": "key", "fixture_collection": "untrusted"},
    {"business_key": "https://example.invalid"},
])
def test_query_key_is_required_bounded_and_closed(arguments: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        SyntheticDetailArguments.model_validate(arguments)


def test_query_accepts_a_legal_nonfixture_key_without_selecting_a_result() -> None:
    parsed = SyntheticDetailArguments(business_key="different_business_key")
    assert parsed.business_key == "different_business_key"
    assert parsed.model_dump() == {"business_key": "different_business_key"}


def test_projector_returns_actual_dynamic_collection_not_fixture_reference() -> None:
    source = build_fixed_synthetic_query_source(_model())
    observed = {
        "messages": [{
            "message_id": "different_message", "title": "Actual dynamic title",
            "content": "Actual dynamic content", "source_name": "Synthetic source",
            "occurred_at": "2026-10-03T01:02:03Z", "business_state": "read",
            "link": None, "mobile_link": None,
        }],
        "returned_count": 1, "is_complete": True,
    }
    result = source.projector({"collection": json.dumps(observed)})
    assert result == observed
    assert "Actual dynamic title" in json.dumps(result)


@pytest.mark.parametrize("query,code", [
    (False, "browser_fixed_seed_expected_input_contract_unapproved"),
    (True, "browser_fixed_seed_source_placement_unapproved"),
])
def test_local_installation_stays_off_without_approved_source_placement(
    query: bool, code: str,
) -> None:
    source = (build_fixed_synthetic_query_source(_model()) if query
              else build_fixed_synthetic_source(_model()))
    absent_dependencies = cast(BrowserVerticalDependencies, None)
    absent_provider = cast(LLMProviderPort, None)
    absent_parser = cast(StructuredOutputPort, None)
    assert install_fixed_synthetic_seed(
        absent_dependencies, source=source, llm_provider=absent_provider,
        structured_output=absent_parser, intent_model="fixture_model_v1",
    ) is None
    with pytest.raises(BrowserPublicationError, match=code):
        install_fixed_synthetic_seed(
            absent_dependencies, source=source, llm_provider=absent_provider,
            structured_output=absent_parser, intent_model="fixture_model_v1", enabled=True,
        )
