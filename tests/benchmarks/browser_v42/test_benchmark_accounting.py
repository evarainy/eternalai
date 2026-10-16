"""Accounting regression tests using synthetic digest-only evidence."""

from __future__ import annotations

from dataclasses import replace

import pytest

from benchmarks.browser_v42.models import (
    AttemptEvidence,
    BrowserEvidence,
    BrowserProof,
    BrowserReceipt,
    CaseResult,
    CaseSpec,
    DecisionInput,
    DecisionProof,
    DecisionReceipt,
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

HEX = "a" * 64
OTHER_HEX = "b" * 64


def _browser(suite: SuiteSpec, *, source: str = "real_browser") -> BrowserEvidence:
    return BrowserEvidence(source, "cloud", suite.fixture_digest, HEX, HEX, HEX)  # type: ignore[arg-type]


def _attempt(
    request: DecisionInput,
    *,
    source: str = "real_model",
    outcome: str = "selected",
    invocation_id: str = "call.1",
    retry: bool = False,
) -> AttemptEvidence:
    return AttemptEvidence(
        source,
        invocation_id,
        HEX,
        request.request_digest,
        None if outcome == "error" else HEX,
        outcome,
        source == "real_model",
        "pinned.v1"
        if source == "real_model"
        else "deterministic.v1"
        if source == "deterministic"
        else None,
        retry,
        12 if source == "real_model" else None,
    )  # type: ignore[arg-type]


def _result(
    case: CaseSpec,
    suite: SuiteSpec,
    *,
    success: bool = True,
    source: str | None = None,
    first_correct: bool | None = None,
) -> CaseResult:
    browser = replace(
        _browser(suite), source_evidence_digest=_digest(["synthetic.browser", case.case_id])
    )
    request = DecisionInput(
        browser.observation_digest, browser.candidate_digest, suite.fixture_digest
    )
    source = source or ("real_model" if case.dataset.startswith("model_") else "deterministic")
    outcome = "abstained" if case.dataset == "model_negative" else "selected"
    attempt = _attempt(
        request, source=source, outcome=outcome, invocation_id=f"call.{case.case_id}"
    )
    oracle = OracleEvidence(first_correct, success, frozenset(), HEX)
    # Hypothetical verified records exercise arithmetic only. Real runtime
    # proofs must be collected from registered adapter transport journals.
    kind = "real_model" if source == "real_model" else "deterministic"
    proof = (
        DecisionProof(
            "synthetic.registered",
            kind,
            HEX,
            attempt.invocation_id,
            attempt.request_digest,
            OTHER_HEX,
            attempt.response_digest,
            attempt.deployment_pin or "",
            OTHER_HEX,
            "serve.v1",
            "bf16",
            "http_json",
            3,
            96,
            1000,
            "browser_choice.v1",
            "/select",
            HEX,
            OTHER_HEX,
        )
        if source in {"real_model", "deterministic"}
        else None
    )
    return CaseResult(
        case.case_id,
        "PASS" if success else "FAIL",
        browser,
        (attempt,),
        oracle,
        None if success else "ORACLE_REJECTED",
        browser_proof=BrowserProof(
            "synthetic.browser",
            browser.source_evidence_digest,
            "cloud",
            "cdp",
            suite.fixture_digest,
            HEX,
            HEX,
            case.case_id,
            case.case_parameters_digest,
        ),
        decision_proofs=(proof,),
    )


def _complete_results(suite: SuiteSpec) -> list[CaseResult]:
    """Exact business 95%, positive 98%, critical negative 100% boundary."""
    results = []
    for case in suite.cases:
        if case.dataset == "calibration":
            continue
        index = int(case.case_id.rsplit(".", 1)[-1])
        business_success = not (
            case.dataset == "business"
            and index == 20
            or case.dataset == "internal_login"
            and index == 20
        )
        initial_correct = not (
            case.dataset == "model_positive"
            and case.flow in {"E01_query_todos_owner_inbox", "E02_query_todos_crosspage_key"}
            and index == 20
        )
        results.append(
            _result(case, suite, success=business_success, first_correct=initial_correct)
        )
    return results


def test_frozen_scenario_schema_and_disjoint_denominators() -> None:
    suite = load_suite()
    assert len(suite.cases) == 279
    assert sum(c.dataset == "business" for c in suite.cases) == 100
    assert sum(c.dataset == "smoke" for c in suite.cases) == 9
    assert sum(c.dataset == "model_positive" for c in suite.cases) == 100
    assert sum(c.dataset == "model_negative" and c.critical for c in suite.cases) == 40
    assert len({c.parameter_ref for c in suite.cases}) == 279
    assert {c.locale for c in suite.cases if c.dataset.startswith("model_")} == {"zh-CN"}
    assert {c.skill for c in suite.cases if c.dataset == "business"} == {
        "query_todos",
        "check_messages",
        "open_todo",
        "search_contact",
    }
    assert all(c.flow is None for c in suite.cases if c.skill == "login_assist")


def test_strict_scenario_rejects_duplicate_and_tampered_evidence() -> None:
    suite = load_suite()
    from dataclasses import asdict

    raw = asdict(suite)
    raw["cases"] = [asdict(c) for c in suite.cases]
    raw["cases"].append(raw["cases"][0].copy())
    with pytest.raises(ValueError, match="duplicate case_id"):
        SuiteSpec.from_mapping(raw)
    raw["cases"].pop()
    raw["cases"][0]["parameter_digest"] = OTHER_HEX
    with pytest.raises(ValueError, match="parameter digest"):
        SuiteSpec.from_mapping(raw)
    raw["cases"][0]["parameter_digest"] = suite.cases[0].parameter_digest
    raw["old_model"] = "latest"
    with pytest.raises(ValueError, match="schema keys"):
        SuiteSpec.from_mapping(raw)


def test_runner_separates_oracle_and_keeps_first_attempt_after_retry() -> None:
    suite = load_suite()
    case = next(c for c in suite.cases if c.dataset == "model_positive")
    seen: list[DecisionInput] = []

    def decision(request: DecisionInput, number: int) -> AttemptEvidence:
        seen.append(request)
        assert "case_id" not in request.__dict__
        assert "expected" not in request.__dict__
        assert "critical" not in request.__dict__
        return _attempt(
            request,
            outcome="abstained" if number == 1 else "selected",
            invocation_id=f"call.{number}",
            retry=number == 1,
        )

    result = run_case(
        case,
        suite,
        browser=lambda _: _browser(suite),
        decision=decision,
        oracle=lambda *_: OracleEvidence(False, True, frozenset(), HEX),
        required_backend="cloud",
        required_checkpoint="pinned.v1",
        max_attempts=2,
    )
    assert result.status == "PASS"
    assert len(seen) == 2
    assert [attempt.outcome for attempt in result.attempts] == ["abstained", "selected"]
    report = summarize_results([result], suite)
    assert report.model_positive.success == 0
    assert report.model_positive.completed == 0
    assert report.model_positive.excluded_source == 1
    assert report.source_attempts == {"unverified_decision": 2}


def test_missing_environment_and_genuine_executed_failure_remain_distinct() -> None:
    suite = load_suite()
    case = suite.cases[0]
    waiting = run_case(case, suite, browser=None, decision=None, oracle=None)
    failed = run_case(
        case,
        suite,
        browser=lambda _: _browser(suite),
        decision=lambda *_: (_ for _ in ()).throw(RuntimeError("wire secret")),
        oracle=lambda *_: OracleEvidence(None, True, frozenset(), HEX),
    )
    assert waiting.status == "WAITING_ENV"
    assert failed.status == "FAIL"
    assert failed.failure_code == "DECISION_ERROR"
    assert "wire secret" not in repr(failed)
    partial = summarize_results([failed], suite)
    assert partial.business_by_flow[case.flow].gate == "WAITING_ENV"
    assert partial.business_by_flow[case.flow].excluded_source == 1
    assert partial.browser_sources == {"unverified_browser": 1}
    assert partial.business_by_flow[case.flow].waiting_env == 19


def test_threshold_boundaries_and_late_business_failure() -> None:
    suite = load_suite()
    results = _complete_results(suite)
    report = summarize_results(results, suite)
    assert report.gate == "PASS"
    assert report.wbs_acceptance == "NOT_EVALUATED"
    assert report.business_by_skill["query_todos"].success == 38
    assert report.model_positive.success == 98
    assert report.model_positive_by_flow["E01_query_todos_owner_inbox"].gate == "INFORMATIONAL"
    assert report.model_negative.success == 40
    assert report.actual_target_decisions_positive == 100
    assert report.actual_target_decisions_negative == 40
    assert report.serving.verified_calls == 140
    assert report.internal_login.success == 19
    assert report.calibration.gate == "WAITING_ENV"

    index = next(
        i
        for i, c in enumerate(suite.cases)
        if c.dataset == "business"
        and c.flow == "E01_query_todos_owner_inbox"
        and c.case_id.endswith(".019")
    )
    results[index] = _result(suite.cases[index], suite, success=False)
    assert (
        summarize_results(results, suite).business_by_flow["E01_query_todos_owner_inbox"].gate
        == "FAIL"
    )

    results = _complete_results(suite)
    positive = next(
        c
        for c in suite.cases
        if c.dataset == "model_positive" and c.flow == "E03_check_messages_conversation_unread"
    )
    target = next(i for i, r in enumerate(results) if r.case_id == positive.case_id)
    original = results[target]
    assert original.oracle is not None
    results[target] = replace(
        original,
        status="FAIL",
        oracle=replace(original.oracle, business_success=False),
        failure_code="LATE_VERIFY_FAILURE",
    )
    late = summarize_results(results, suite)
    assert late.model_positive.success == 98
    assert results[target].status == "FAIL"

    results = _complete_results(suite)
    critical = next(c for c in suite.cases if c.dataset == "model_negative" and c.critical)
    target = next(i for i, r in enumerate(results) if r.case_id == critical.case_id)
    results[target] = _result(critical, suite, first_correct=False)
    assert summarize_results(results, suite).model_negative.gate == "FAIL"


def test_fake_deterministic_and_fault_sources_cannot_fill_model_denominator() -> None:
    suite = load_suite()
    results = _complete_results(suite)
    case = next(c for c in suite.cases if c.dataset == "model_positive")
    target = next(i for i, r in enumerate(results) if r.case_id == case.case_id)
    for source in ("fake", "mock", "bypassed", "fault_injection", "deterministic"):
        polluted = results.copy()
        polluted[target] = _result(case, suite, source=source, first_correct=True)
        report = summarize_results(polluted, suite)
        assert report.model_positive.completed == 99
        assert report.model_positive.excluded_source == 1
        assert report.gate == "WAITING_ENV"
        assert report.source_attempts[source] >= 1


def test_duplicate_results_and_invalid_invocation_evidence_fail_closed() -> None:
    suite = load_suite()
    case = suite.cases[0]
    result = _result(case, suite)
    with pytest.raises(ValueError, match="duplicate case result"):
        summarize_results([result, result], suite)

    other = _result(suite.cases[1], suite)
    reused = replace(
        other,
        attempts=(replace(other.attempts[0], invocation_id=result.attempts[0].invocation_id),),
        decision_proofs=(
            replace(other.decision_proofs[0], invocation_id=result.attempts[0].invocation_id),
        ),
    )
    with pytest.raises(ValueError, match="invocation reused"):
        summarize_results([result, reused], suite)

    failed = run_case(
        case,
        suite,
        browser=lambda _: _browser(suite),
        decision=lambda request, _: replace(_attempt(request), request_digest=OTHER_HEX),
        oracle=lambda *_: OracleEvidence(True, True, frozenset(), HEX),
    )
    assert failed.status == "FAIL"
    assert failed.oracle is None
    assert failed.failure_code == "EVIDENCE_OR_ORACLE_ERROR"


def test_observed_hazard_fails_without_claiming_untested_paths_are_clear() -> None:
    suite = load_suite()
    case = suite.cases[0]
    original = _result(case, suite)
    assert original.oracle is not None
    hazard = replace(
        original,
        status="FAIL",
        failure_code="ORACLE_REJECTED",
        oracle=replace(
            original.oracle, business_success=False, hazards=frozenset({"wrong_object"})
        ),
    )
    report = summarize_results([hazard], suite)
    assert report.gate == "FAIL"
    assert report.observed_hazards == {"wrong_object": 1}
    assert report.oracle_observed_cases == 1
    assert report.business_by_flow[case.flow].waiting_env == 19


def test_self_claimed_real_source_and_forged_model_id_are_unverified() -> None:
    suite = load_suite()
    case = next(c for c in suite.cases if c.dataset == "model_positive")
    result = run_case(
        case,
        suite,
        browser=lambda _: _browser(suite),
        decision=lambda request, _: _attempt(request, source="real_model"),
        oracle=lambda *_: OracleEvidence(True, True, frozenset(), HEX),
    )
    assert result.status == "PASS"
    assert result.browser_proof is None
    assert result.decision_proofs == (None,)
    report = summarize_results([result], suite)
    assert report.model_positive.completed == 0
    assert report.actual_target_decisions_positive == 0
    assert report.source_attempts == {"unverified_decision": 1}
    assert report.browser_sources == {"unverified_browser": 1}


def test_registered_collector_checks_transport_correlation_and_unknown_source() -> None:
    suite = load_suite()
    case = next(c for c in suite.cases if c.dataset == "model_positive")
    browser = _browser(suite)
    request = DecisionInput(
        browser.observation_digest, browser.candidate_digest, suite.fixture_digest
    )
    attempt = _attempt(request)
    browser_receipt = BrowserReceipt(
        "registered.browser",
        "cloud",
        "cdp",
        suite.fixture_digest,
        HEX,
        HEX,
        HEX,
        case.case_id,
        case.case_parameters_digest,
    )
    decision_receipt = DecisionReceipt(
        "registered.decision",
        attempt.invocation_id,
        1,
        suite.fixture_digest,
        request.request_digest,
        OTHER_HEX,
        attempt.response_digest,
        "pinned.v1",
        OTHER_HEX,
        "serve.v1",
        "bf16",
        "http_json",
        1,
        96,
        1000,
        HEX,
        "browser_choice.v1",
        "/select",
    )
    registered_browser = RegisteredBrowser(
        "registered.browser",
        "cloud",
        "cdp",
        suite.fixture_digest,
    )
    registered_decision = RegisteredDecision(
        "registered.decision",
        "real_model",
        "pinned.v1",
        OTHER_HEX,
        "serve.v1",
        "bf16",
        "http_json",
        3,
        96,
        1000,
    )
    # Digest-only hypothetical registration tests the matching predicate here;
    # test_source_binding freezes bytes from both actual codecs independently.
    frozen_wire = ExpectedDecisionWire(
        case.case_id,
        case.case_parameters_digest,
        request.request_digest,
        1,
        1,
        registered_decision.contract_digest,
        "browser_choice.v1",
        OTHER_HEX,
        HEX,
    )
    registered_decision = replace(registered_decision, expected_wires=(frozen_wire,))

    def collector(receipt: DecisionReceipt) -> RegistryTransportCollector:
        return RegistryTransportCollector(
            {registered_browser.source_id: registered_browser},
            {registered_decision.source_id: registered_decision},
            lambda _: browser_receipt,
            lambda _: receipt,
        )

    trusted = collector(decision_receipt)
    assert trusted.verify_browser(case, suite, browser) is not None
    assert trusted.verify_decision(case, request, 1, attempt) is not None
    for bad in (
        replace(decision_receipt, source_id="unknown.source"),
        replace(decision_receipt, request_digest=OTHER_HEX),
        replace(decision_receipt, wire_request_digest="invalid"),
        replace(decision_receipt, wire_request_digest=HEX),
        replace(decision_receipt, response_deployment="old.checkpoint"),
        replace(decision_receipt, attempt_number=2),
        replace(decision_receipt, reserved_calls=0),
        replace(decision_receipt, max_tokens=95),
    ):
        assert collector(bad).verify_decision(case, request, 1, attempt) is None
    assert (
        RegistryTransportCollector(
            {},
            {registered_decision.source_id: registered_decision},
            lambda _: browser_receipt,
            lambda _: decision_receipt,
        ).verify_browser(case, suite, browser)
        is None
    )
    forged = _attempt(request, source="mock")
    assert trusted.verify_decision(case, request, 1, forged) is None


def test_verified_model_timeout_stays_in_initial_denominator() -> None:
    suite = load_suite()
    case = next(c for c in suite.cases if c.dataset == "model_positive")
    baseline = _result(case, suite, first_correct=True)
    first = baseline.attempts[0]
    timeout = replace(first, outcome="error", response_digest=None)
    proof = baseline.decision_proofs[0]
    assert proof is not None
    failed = replace(
        baseline,
        status="FAIL",
        failure_code="MODEL_TIMEOUT",
        attempts=(timeout,),
        decision_proofs=(replace(proof, response_digest=None),),
        oracle=OracleEvidence(False, False, frozenset(), HEX),
    )
    report = summarize_results([failed], suite)
    assert report.model_positive.completed == 1
    assert report.model_positive.failed == 1
    assert report.actual_target_decisions_positive == 1
    assert report.serving.service_errors == 1
