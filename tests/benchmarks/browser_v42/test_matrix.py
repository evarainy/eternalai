"""Four-cell fixture and model-pin accounting without real providers."""

from __future__ import annotations

from dataclasses import replace

import pytest

from benchmarks.browser_v42.matrix import (
    ArmReport,
    ArmSpec,
    MatrixSpec,
    compare_arms,
    evaluate_arm,
    validate_matrix_inputs,
)
from benchmarks.browser_v42.models import (
    AttemptEvidence,
    BrowserEvidence,
    BrowserProof,
    CaseResult,
    DecisionInput,
    DecisionProof,
    OracleEvidence,
    SuiteSpec,
    load_suite,
)
from benchmarks.browser_v42.report import summarize_results
from benchmarks.browser_v42.runner import run_case

HEX = "a" * 64
OTHER_HEX = "b" * 64


def _arm_rows(suite: SuiteSpec, *, configured: bool) -> list[dict[str, object]]:
    rows = []
    for browser in ("cloud", "enterprise"):
        for decision in ("jev", "local"):
            rows.append(
                {
                    "browser_backend": browser,
                    "decision_backend": decision,
                    "fixture_seed": suite.fixture_seed,
                    "fixture_digest": suite.fixture_digest,
                    "checkpoint": f"fixed.{decision}.v1" if configured else None,
                    "serving_version": "serve.v1" if configured else None,
                    "dtype": "bf16" if configured else None,
                    "budgets": {"max_calls": 3, "max_tokens": 96, "timeout_ms": 1000}
                    if configured
                    else None,
                    "transport": {"browser": "cdp", "decision": "http_json"}
                    if configured
                    else None,
                }
            )
    return rows


def _spec(suite: SuiteSpec, *, configured: bool) -> MatrixSpec:
    return MatrixSpec.from_mapping(
        {
            "fixture_seed": suite.fixture_seed,
            "fixture_digest": suite.fixture_digest,
            "arms": _arm_rows(suite, configured=configured),
        },
        suite,
    )


def _single_result(suite: SuiteSpec, arm: ArmSpec, candidate_digest: str = HEX) -> CaseResult:
    case = suite.cases[0]
    assert arm.checkpoint is not None
    assert arm.serving_version is not None
    assert arm.dtype is not None
    assert arm.transport is not None
    browser = BrowserEvidence(
        "real_browser", arm.browser_backend, suite.fixture_digest, HEX, candidate_digest, HEX
    )
    request = DecisionInput(HEX, candidate_digest, suite.fixture_digest)
    invocation_id = f"call.{arm.browser_backend}.{arm.decision_backend}"
    attempt = AttemptEvidence(
        "real_model",
        invocation_id,
        HEX,
        request.request_digest,
        HEX,
        "selected",
        True,
        arm.checkpoint,
        duration_ms=12,
    )
    return CaseResult(
        case.case_id,
        "PASS",
        browser,
        (attempt,),
        OracleEvidence(True, True, frozenset(), HEX),
        browser_proof=BrowserProof(
            "synthetic.browser",
            HEX,
            arm.browser_backend,
            arm.transport.browser,
            suite.fixture_digest,
            HEX,
            candidate_digest,
        ),
        decision_proofs=(
            DecisionProof(
                "synthetic.decision",
                "real_model",
                HEX,
                invocation_id,
                request.request_digest,
                OTHER_HEX,
                HEX,
                arm.checkpoint,
                OTHER_HEX,
                arm.serving_version,
                arm.dtype,
                arm.transport.decision,
                3,
                96,
                1000,
            ),
        ),
    )


def test_matrix_requires_exact_four_cells_and_identical_frozen_fixtures() -> None:
    suite = load_suite()
    rows = _arm_rows(suite, configured=True)
    spec = _spec(suite, configured=True)
    assert len(spec.arms) == 4
    assert {a.key for a in spec.arms} == {
        ("cloud", "jev"),
        ("cloud", "local"),
        ("enterprise", "jev"),
        ("enterprise", "local"),
    }
    with pytest.raises(ValueError, match="four distinct"):
        MatrixSpec.from_mapping(
            {
                "fixture_seed": suite.fixture_seed,
                "fixture_digest": suite.fixture_digest,
                "arms": rows[:3],
            },
            suite,
        )
    rows[1] = rows[0].copy()
    with pytest.raises(ValueError, match="four distinct"):
        MatrixSpec.from_mapping(
            {
                "fixture_seed": suite.fixture_seed,
                "fixture_digest": suite.fixture_digest,
                "arms": rows,
            },
            suite,
        )
    changed = replace(spec, fixture_digest=OTHER_HEX)
    with pytest.raises(ValueError, match="fixture identity"):
        validate_matrix_inputs(changed, suite)


def test_missing_arm_configuration_waits_without_invocation_or_fallback() -> None:
    suite = load_suite()
    spec = _spec(suite, configured=False)

    def forbidden(*_: object) -> None:
        raise AssertionError("unconfigured arm must not invoke any provider")

    reports = tuple(
        evaluate_arm(arm, suite, browser=forbidden, decision=forbidden, oracle=forbidden)
        for arm in spec.arms
    )
    matrix = compare_arms(reports, suite)
    assert matrix.gate == "WAITING_ENV"
    assert matrix.wbs_acceptance == "NOT_EVALUATED"
    assert len(matrix.arms) == 4
    assert all(report.report.model_positive.completed == 0 for report in matrix.arms.values())
    assert all(
        report.report.business_by_flow["E01_query_todos_owner_inbox"].waiting_env == 20
        for report in matrix.arms.values()
    )


def test_old_model_variable_and_partial_pin_are_rejected() -> None:
    suite = load_suite()
    row = _arm_rows(suite, configured=True)[0]
    row["model"] = "latest"
    with pytest.raises(ValueError, match="schema keys"):
        ArmSpec.from_mapping(row)
    row.pop("model")
    row["checkpoint"] = None
    with pytest.raises(ValueError, match="partial arm configuration"):
        ArmSpec.from_mapping(row)


def test_actual_decision_pin_mismatch_is_failure_after_dispatch() -> None:
    suite = load_suite()
    arm = _spec(suite, configured=True).arms[0]
    case = suite.cases[0]

    def browser(_: object) -> BrowserEvidence:
        return BrowserEvidence(
            "real_browser", arm.browser_backend, suite.fixture_digest, HEX, HEX, HEX
        )

    def old_decision(request: DecisionInput, _: int) -> AttemptEvidence:
        return AttemptEvidence(
            "real_model",
            "call.1",
            HEX,
            request.request_digest,
            HEX,
            "selected",
            True,
            "old.checkpoint",
            duration_ms=12,
        )

    result = run_case(
        case,
        suite,
        browser=browser,
        decision=old_decision,
        oracle=lambda *_: OracleEvidence(True, True, frozenset(), HEX),
        required_backend=arm.browser_backend,
        required_checkpoint=arm.checkpoint,
    )
    assert result.status == "FAIL"
    assert result.attempts[0].deployment_pin == "old.checkpoint"
    assert result.failure_code == "EVIDENCE_OR_ORACLE_ERROR"


def test_comparison_rejects_candidate_digest_drift_and_preserves_each_cell() -> None:
    suite = load_suite()
    spec = _spec(suite, configured=True)
    arm_reports = []
    for arm in spec.arms:
        result = _single_result(suite, arm)
        arm_reports.append(ArmReport(arm, (result,), summarize_results((result,), suite)))
    matrix = compare_arms(tuple(arm_reports), suite)
    assert matrix.gate == "WAITING_ENV"
    assert all(
        row.report.business_by_flow["E01_query_todos_owner_inbox"].completed == 1
        for row in matrix.arms.values()
    )

    drift = _single_result(suite, spec.arms[-1], OTHER_HEX)
    arm_reports[-1] = ArmReport(spec.arms[-1], (drift,), summarize_results((drift,), suite))
    with pytest.raises(ValueError, match="candidate digest"):
        compare_arms(tuple(arm_reports), suite)

    old = _single_result(suite, spec.arms[-1])
    old_attempt = replace(old.attempts[0], deployment_pin="old.checkpoint")
    old_proof = replace(old.decision_proofs[0], checkpoint="old.checkpoint")
    old = replace(old, attempts=(old_attempt,), decision_proofs=(old_proof,))
    arm_reports[-1] = ArmReport(spec.arms[-1], (old,), summarize_results((old,), suite))
    with pytest.raises(ValueError, match="deployment differs"):
        compare_arms(tuple(arm_reports), suite)


def test_comparison_recomputes_reports_and_rejects_missing_or_duplicate_cells() -> None:
    suite = load_suite()
    spec = _spec(suite, configured=True)
    reports = tuple(ArmReport(arm, (), summarize_results((), suite)) for arm in spec.arms)
    with pytest.raises(ValueError, match="four distinct"):
        compare_arms(reports[:3] + (reports[0],), suite)
    one = _single_result(suite, spec.arms[0])
    falsified = ArmReport(spec.arms[0], (one,), summarize_results((), suite))
    with pytest.raises(ValueError, match="raw accounting"):
        compare_arms((falsified, *reports[1:]), suite)
