"""Independent, synthetic transport receipts bind cases and actual codec bytes."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from typing import Literal

import pytest

from app.infra.browser.decision_adapters import LocalChoiceCodec, TypeSafeCodec
from benchmarks.browser_v42.models import (
    AttemptEvidence,
    BrowserEvidence,
    BrowserReceipt,
    CaseResult,
    CaseSpec,
    DecisionInput,
    DecisionReceipt,
    DecisionWireCodec,
    ExpectedDecisionWire,
    OracleEvidence,
    RegisteredBrowser,
    RegisteredDecision,
    SuiteSpec,
    _digest,
    load_suite,
)
from benchmarks.browser_v42.report import summarize_results
from benchmarks.browser_v42.runner import RegistryTransportCollector, run_case
from tests.browser_skill.factories import context, request

HEX = "a" * 64


@dataclass(frozen=True)
class _Sample:
    suite: SuiteSpec
    case: CaseSpec
    browser: BrowserEvidence
    request: DecisionInput
    attempt: AttemptEvidence
    browser_receipt: BrowserReceipt
    decision_receipt: DecisionReceipt
    browser_registration: RegisteredBrowser
    decision_registration: RegisteredDecision
    wire: bytes = field(repr=False)

    def collector(self) -> RegistryTransportCollector:
        return RegistryTransportCollector(
            {self.browser_registration.source_id: self.browser_registration},
            {self.decision_registration.source_id: self.decision_registration},
            lambda _: self.browser_receipt,
            lambda _: self.decision_receipt,
        )

    def execute(self) -> CaseResult:
        return run_case(
            self.case,
            self.suite,
            browser=lambda _: self.browser,
            decision=lambda *_: self.attempt,
            oracle=lambda *_: OracleEvidence(True, True, frozenset(), HEX),
            collector=self.collector(),
        )


def _sample(index: int = 0, codec_kind: Literal["typesafe", "local"] = "local") -> _Sample:
    suite = load_suite()
    case = [c for c in suite.cases if c.dataset == "model_positive"][index]
    codec = TypeSafeCodec() if codec_kind == "typesafe" else LocalChoiceCodec()
    neutral = request()
    call_context = context()
    wire = codec.encode(neutral, call_context)
    observation = _digest(neutral.scope.model_dump(mode="json"))
    candidates = _digest([c.model_dump(mode="json") for c in neutral.candidates])
    browser_digest = _digest(["browser.receipt", case.case_id])
    decision_digest = _digest(["decision.receipt", case.case_id, codec_kind])
    browser = BrowserEvidence(
        "real_browser", "cloud", suite.fixture_digest, observation, candidates, browser_digest
    )
    decision_input = DecisionInput(observation, candidates, suite.fixture_digest)
    attempt = AttemptEvidence(
        "real_model",
        f"call.{case.case_id}.{codec_kind}",
        decision_digest,
        decision_input.request_digest,
        HEX,
        "selected",
        True,
        call_context.manifest.deployment_model,
        duration_ms=12,
    )
    browser_registration = RegisteredBrowser(
        "registered.browser", "cloud", "cdp", suite.fixture_digest
    )
    decision_registration = RegisteredDecision(
        f"registered.{codec_kind}",
        "real_model",
        call_context.manifest.deployment_model,
        call_context.manifest.manifest_digest,
        "serve.v1",
        "bf16",
        "http_json",
        3,
        96,
        1000,
    )
    codec_name: DecisionWireCodec = (
        "typesafe.systemone.v1" if codec_kind == "typesafe" else "browser_choice.v1"
    )
    wire_digest = hashlib.sha256(wire).hexdigest()
    # Freeze before creating the transport receipt, from actual codec bytes and
    # all independently selected request/manifest/budget material. No receipt
    # or model response can supply this registration's expectation.
    expected = ExpectedDecisionWire(
        case.case_id,
        case.case_parameters_digest,
        decision_input.request_digest,
        1,
        1,
        decision_registration.contract_digest,
        codec_name,
        wire_digest,
        _digest(
            {
                "purpose": "synthetic.codec_pre_dispatch.v1",
                "codec": codec_name,
                "path": codec.path,
                "input_digest": _digest(neutral.model_dump(mode="json")),
                "manifest_digest": _digest(call_context.manifest.model_dump(mode="json")),
                "codec_budget_digest": _digest(call_context.budget.model_dump(mode="json")),
                "registration_digest": decision_registration.contract_digest,
                "wire_digest": wire_digest,
            }
        ),
    )
    decision_registration = replace(decision_registration, expected_wires=(expected,))
    browser_receipt = BrowserReceipt(
        browser_registration.source_id,
        "cloud",
        "cdp",
        suite.fixture_digest,
        observation,
        candidates,
        browser_digest,
        case.case_id,
        case.case_parameters_digest,
    )
    decision_receipt = DecisionReceipt(
        decision_registration.source_id,
        attempt.invocation_id,
        1,
        suite.fixture_digest,
        decision_input.request_digest,
        wire_digest,
        HEX,
        decision_registration.checkpoint,
        decision_registration.manifest_digest,
        decision_registration.serving_version,
        decision_registration.dtype,
        decision_registration.transport,
        1,
        decision_registration.max_tokens,
        decision_registration.timeout_ms,
        decision_digest,
        codec_name,
        codec.path,
    )
    return _Sample(
        suite,
        case,
        browser,
        decision_input,
        attempt,
        browser_receipt,
        decision_receipt,
        browser_registration,
        decision_registration,
        wire,
    )


def test_browser_receipt_reused_for_other_frozen_case_is_unverified() -> None:
    sample = _sample()
    other = _sample(1).case
    assert other.parameter_digest != sample.case.parameter_digest
    assert sample.collector().verify_browser(other, sample.suite, sample.browser) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"parameter_ref": "changed.reference"},
        {"parameter_digest": "b" * 64},
        {"dataset": "business"},
        {"flow": "E04_open_todo_confirmed_key"},
        {"skill": "open_todo"},
        {"locale": "changed.locale"},
        {"expected": "abstain"},
        {"critical": True},
    ],
)
def test_changed_case_parameters_cannot_reuse_browser_receipt(changes: dict[str, object]) -> None:
    sample = _sample()
    changed = replace(sample.case, **changes)
    assert changed.case_parameters_digest != sample.case.case_parameters_digest
    assert sample.collector().verify_browser(changed, sample.suite, sample.browser) is None
    changed_suite = replace(
        sample.suite,
        cases=tuple(changed if c == sample.case else c for c in sample.suite.cases),
    )
    assert changed in changed_suite.cases
    assert sample.collector().verify_browser(changed, changed_suite, sample.browser) is None


def test_report_rejects_browser_receipt_reuse_in_one_arm() -> None:
    first, second = _sample().execute(), _sample(1).execute()
    assert first.browser is not None and first.browser_proof is not None
    assert second.browser is not None and second.browser_proof is not None
    reused = replace(
        second,
        browser=replace(
            second.browser, source_evidence_digest=first.browser.source_evidence_digest
        ),
        browser_proof=replace(
            second.browser_proof, receipt_digest=first.browser_proof.receipt_digest
        ),
    )
    with pytest.raises(ValueError, match="browser receipt reused"):
        summarize_results((first, reused), load_suite())


@pytest.mark.parametrize("codec_kind", ["typesafe", "local"])
def test_correct_logical_request_with_changed_wire_is_unverified(
    codec_kind: Literal["typesafe", "local"],
) -> None:
    sample = _sample(codec_kind=codec_kind)
    codec = TypeSafeCodec() if codec_kind == "typesafe" else LocalChoiceCodec()
    changed = request().model_copy(update={"criteria": ("changed synthetic constraint",)})
    bad_wire = codec.encode(changed, context())
    assert bad_wire != sample.wire
    altered = replace(
        sample,
        decision_receipt=replace(
            sample.decision_receipt, wire_request_digest=hashlib.sha256(bad_wire).hexdigest()
        ),
    )
    assert altered.decision_receipt.request_digest == sample.request.request_digest
    assert (
        altered.collector().verify_decision(sample.case, sample.request, 1, sample.attempt) is None
    )


@pytest.mark.parametrize("codec_kind", ["typesafe", "local"])
def test_independent_frozen_samples_fill_only_their_actual_denominator(
    codec_kind: Literal["typesafe", "local"],
) -> None:
    samples = (_sample(codec_kind=codec_kind), _sample(1, codec_kind))
    report = summarize_results((sample.execute() for sample in samples), samples[0].suite)
    assert report.model_positive.completed == 2
    assert report.model_positive.success == 2
    assert report.model_positive.excluded_source == 0
    assert report.actual_target_decisions_positive == 2
    assert report.browser_sources == {"real_browser": 2}
    assert report.source_attempts == {"real_model": 2}
    assert report.gate == "WAITING_ENV"


def test_same_arm_repeated_sample_and_forged_parameter_proof_fail_closed() -> None:
    sample = _sample()
    result = sample.execute()
    with pytest.raises(ValueError, match="duplicate case result"):
        summarize_results((result, result), sample.suite)
    assert result.browser_proof is not None
    forged = replace(
        result,
        browser_proof=replace(result.browser_proof, case_parameters_digest="b" * 64),
    )
    with pytest.raises(ValueError, match="frozen case parameters"):
        summarize_results((forged,), sample.suite)
    with pytest.raises(ValueError, match="browser proof"):
        replace(result, case_id=_sample(1).case.case_id)


@pytest.mark.parametrize("codec_kind", ["typesafe", "local"])
def test_absent_or_ambiguous_pre_dispatch_wire_registration_is_unverified(
    codec_kind: Literal["typesafe", "local"],
) -> None:
    sample = _sample(codec_kind=codec_kind)
    expected = sample.decision_registration.expected_wires[0]
    for wires in ((), (expected, expected)):
        unverified = replace(
            sample,
            decision_registration=replace(sample.decision_registration, expected_wires=wires),
        ).execute()
        assert unverified.status == "PASS"
        assert unverified.decision_proofs == (None,)
        report = summarize_results((unverified,), sample.suite)
        assert report.model_positive.completed == 0
        assert report.model_positive.excluded_source == 1
        assert report.actual_target_decisions_positive == 0
        assert report.serving.verified_calls == 0
        assert report.source_attempts == {"unverified_decision": 1}
        assert report.gate == "WAITING_ENV"


@pytest.mark.parametrize(
    "changes",
    [
        {"manifest_digest": "b" * 64},
        {"serving_version": "serve.v2"},
        {"dtype": "fp32"},
        {"max_calls": 4},
        {"max_tokens": 97},
        {"timeout_ms": 1001},
    ],
)
def test_expected_wire_binds_the_full_frozen_serving_contract(changes: dict[str, object]) -> None:
    sample = _sample()
    registration = replace(sample.decision_registration, **changes)
    assert registration.contract_digest != sample.decision_registration.contract_digest
    receipt_changes = {name: value for name, value in changes.items() if name != "max_calls"}
    changed = replace(
        sample,
        decision_registration=registration,
        decision_receipt=replace(sample.decision_receipt, **receipt_changes),
    )
    assert (
        changed.collector().verify_decision(sample.case, sample.request, 1, sample.attempt) is None
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"codec": "typesafe.systemone.v1"},
        {"path": "/v1/systemone"},
        {"codec": None},
        {"path": None},
        {"reserved_calls": 2},
    ],
)
def test_receipt_codec_path_and_exact_reservation_must_match_freeze(
    changes: dict[str, object],
) -> None:
    sample = _sample()
    altered = replace(sample, decision_receipt=replace(sample.decision_receipt, **changes))
    assert (
        altered.collector().verify_decision(sample.case, sample.request, 1, sample.attempt) is None
    )


def test_logical_digest_cannot_be_used_as_actual_wire_digest() -> None:
    sample = _sample()
    assert sample.request.request_digest != hashlib.sha256(sample.wire).hexdigest()
    altered = replace(
        sample,
        decision_receipt=replace(
            sample.decision_receipt, wire_request_digest=sample.request.request_digest
        ),
    )
    assert (
        altered.collector().verify_decision(sample.case, sample.request, 1, sample.attempt) is None
    )


@pytest.mark.parametrize("codec_kind", ["typesafe", "local"])
def test_verified_proof_retains_independent_pre_dispatch_provenance(
    codec_kind: Literal["typesafe", "local"],
) -> None:
    sample = _sample(codec_kind=codec_kind)
    proof = sample.collector().verify_decision(sample.case, sample.request, 1, sample.attempt)
    expected = sample.decision_registration.expected_wires[0]
    assert proof is not None
    assert proof.wire_request_digest == hashlib.sha256(sample.wire).hexdigest()
    assert proof.wire_registration_digest == sample.decision_registration.contract_digest
    assert proof.wire_provenance_digest == expected.provenance_digest
    assert proof.codec == expected.codec
    assert proof.path == expected.path
    assert "wire" not in sample.request.__dict__
    assert "options" not in sample.request.__dict__


@pytest.mark.parametrize("field_name", ["fixture_digest", "observation_digest", "candidate_digest"])
def test_case_binding_retains_registered_browser_observation_constraints(field_name: str) -> None:
    sample = _sample()
    tampered = replace(sample.browser_receipt, **{field_name: "b" * 64})
    changed = replace(sample, browser_receipt=tampered)
    assert changed.collector().verify_browser(sample.case, sample.suite, sample.browser) is None
