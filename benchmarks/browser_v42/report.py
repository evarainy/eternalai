"""Independent denominators and conservative gates for browser v4.2."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from math import ceil
from typing import Literal

from benchmarks.browser_v42.models import FLOWS, CaseResult, CaseSpec, SuiteSpec

Gate = Literal["PASS", "FAIL", "WAITING_ENV"]


@dataclass(frozen=True)
class GroupMetric:
    planned: int
    completed: int
    success: int
    failed: int
    waiting_env: int
    excluded_source: int
    execution_errors: int
    gate: Gate | Literal["INFORMATIONAL"]


@dataclass(frozen=True)
class ServingMetric:
    verified_calls: int
    service_errors: int
    p50_ms: int | None
    p95_ms: int | None


@dataclass(frozen=True)
class SuiteReport:
    fixture_seed: str
    fixture_digest: str
    business_by_flow: dict[str, GroupMetric]
    business_by_skill: dict[str, GroupMetric]
    smoke_by_flow: dict[str, GroupMetric]
    internal_login: GroupMetric
    model_positive_by_flow: dict[str, GroupMetric]
    model_negative_by_flow: dict[str, GroupMetric]
    model_positive: GroupMetric
    model_negative: GroupMetric
    calibration: GroupMetric
    source_attempts: dict[str, int]
    browser_sources: dict[str, int]
    actual_target_decisions_positive: int
    actual_target_decisions_negative: int
    serving: ServingMetric
    observed_hazards: dict[str, int]
    oracle_observed_cases: int
    gate: Gate
    wbs_acceptance: Literal["NOT_EVALUATED"] = "NOT_EVALUATED"


def _source_eligible(case: CaseSpec, result: CaseResult) -> bool:
    if result.browser is None or result.browser_proof is None:
        return False
    if len(result.attempts) != len(result.decision_proofs):
        return False
    if case.dataset in {"model_positive", "model_negative"}:
        return bool(result.attempts) and all(
            proof is not None and proof.kind == "real_model" for proof in result.decision_proofs
        )
    return all(
        proof is not None and proof.kind in {"real_model", "deterministic"}
        for proof in result.decision_proofs
    )


def _correct(case: CaseSpec, result: CaseResult) -> bool:
    if case.dataset in {"model_positive", "model_negative"}:
        if not result.attempts or result.oracle is None:
            return False
        first = result.attempts[0]
        expected_outcome = "selected" if case.dataset == "model_positive" else "abstained"
        return (
            first.source == "real_model"
            and first.grounded
            and first.outcome == expected_outcome
            and result.oracle.initial_correct is True
        )
    return result.status == "PASS" and result.oracle is not None and result.oracle.business_success


def _metric(
    rows: Iterable[tuple[CaseSpec, CaseResult]],
    *,
    minimum: int,
    rate: float | None,
) -> GroupMetric:
    pairs = tuple(rows)
    completed = success = waiting = excluded = execution_errors = 0
    for case, result in pairs:
        if result.status == "WAITING_ENV":
            waiting += 1
        elif _source_eligible(case, result):
            completed += 1
            success += int(_correct(case, result))
        elif result.browser is not None:
            excluded += 1
        else:
            execution_errors += 1
    planned = len(pairs)
    failed = completed - success
    if rate is None:
        group_gate: Gate | Literal["INFORMATIONAL"] = (
            "WAITING_ENV"
            if planned < minimum or waiting or excluded or completed < planned
            else "INFORMATIONAL"
        )
    else:
        allowed_failures = planned - ceil(rate * planned)
        if execution_errors or failed > allowed_failures:
            group_gate = "FAIL"
        elif planned < minimum or waiting or excluded or completed < planned:
            group_gate = "WAITING_ENV"
        else:
            group_gate = "PASS"
    return GroupMetric(
        planned,
        completed,
        success,
        failed,
        waiting,
        excluded,
        execution_errors,
        group_gate,
    )


def summarize_results(results: Iterable[CaseResult], suite: SuiteSpec) -> SuiteReport:
    """Summarize a partial run without dropping planned cases or mixing sources."""
    by_id: dict[str, CaseResult] = {}
    known = {case.case_id for case in suite.cases}
    for result in results:
        if result.case_id not in known:
            raise ValueError("result is outside frozen suite")
        if result.case_id in by_id:
            raise ValueError("duplicate case result")
        by_id[result.case_id] = result
    seen_invocations: set[tuple[str, str]] = set()
    for result in by_id.values():
        for proof in result.decision_proofs:
            if proof is None:
                continue
            key = (proof.source_id, proof.invocation_id)
            if key in seen_invocations:
                raise ValueError("verified decision invocation reused across cases")
            seen_invocations.add(key)
    paired = tuple(
        (case, by_id.get(case.case_id, CaseResult(case.case_id, "WAITING_ENV")))
        for case in suite.cases
    )

    def group(
        dataset: str, flow: str | None = None, skill: str | None = None
    ) -> tuple[tuple[CaseSpec, CaseResult], ...]:
        return tuple(
            (case, result)
            for case, result in paired
            if case.dataset == dataset
            and (flow is None or case.flow == flow)
            and (skill is None or case.skill == skill)
        )

    business_by_flow = {
        flow: _metric(group("business", flow), minimum=20, rate=0.95) for flow in FLOWS
    }
    business_by_skill = {
        skill: _metric(
            group("business", skill=skill), minimum=40 if skill == "query_todos" else 20, rate=0.95
        )
        for skill in sorted(set(FLOWS.values()))
    }
    smoke_by_flow = {
        flow: _metric(group("smoke", flow), minimum=3, rate=1.0)
        for flow in FLOWS
        if flow.startswith(("E01", "E03", "E04"))
    }
    internal_login = _metric(group("internal_login"), minimum=1, rate=0.95)
    # Per-flow model rows are descriptive; the approved 98% gate is on the
    # independent >=100 positive set for each matrix arm.
    positive_by_flow = {
        flow: _metric(group("model_positive", flow), minimum=20, rate=None) for flow in FLOWS
    }
    negative_by_flow = {
        flow: _metric(group("model_negative", flow), minimum=8, rate=1.0) for flow in FLOWS
    }
    positive = _metric(group("model_positive"), minimum=100, rate=0.98)
    negative = _metric(group("model_negative"), minimum=40, rate=1.0)
    calibration = _metric(group("calibration"), minimum=1, rate=None)

    source_attempts: Counter[str] = Counter()
    browser_sources: Counter[str] = Counter()
    hazards: Counter[str] = Counter()
    oracle_observed_cases = 0
    target_decisions: Counter[str] = Counter()
    durations: list[int] = []
    service_errors = 0
    for case, result in paired:
        if result.browser is not None:
            browser_category = (
                "real_browser"
                if result.browser_proof is not None
                else result.browser.source
                if result.browser.source in {"fake_browser", "mock_browser"}
                else "unverified_browser"
            )
            browser_sources[browser_category] += 1
        for index, attempt in enumerate(result.attempts):
            proof = result.decision_proofs[index] if index < len(result.decision_proofs) else None
            decision_category = (
                proof.kind
                if proof is not None
                else attempt.source
                if attempt.source in {"fake", "mock", "bypassed", "fault_injection"}
                else "unverified_decision"
            )
            source_attempts[decision_category] += 1
            if (
                case.dataset in {"model_positive", "model_negative"}
                and decision_category == "real_model"
            ):
                if index == 0:
                    target_decisions[case.dataset] += 1
                if attempt.duration_ms is not None:
                    durations.append(attempt.duration_ms)
                service_errors += int(attempt.outcome == "error")
        if result.oracle is not None:
            oracle_observed_cases += 1
            hazards.update(result.oracle.hazards)

    durations.sort()
    serving = ServingMetric(
        len(durations),
        service_errors,
        durations[ceil(0.5 * len(durations)) - 1] if durations else None,
        durations[ceil(0.95 * len(durations)) - 1] if durations else None,
    )

    gates = [
        m.gate
        for m in (
            *business_by_flow.values(),
            *business_by_skill.values(),
            *smoke_by_flow.values(),
            internal_login,
            *negative_by_flow.values(),
            positive,
            negative,
        )
    ]
    if hazards or "FAIL" in gates:
        gate: Gate = "FAIL"
    elif "WAITING_ENV" in gates:
        gate = "WAITING_ENV"
    else:
        gate = "PASS"
    return SuiteReport(
        suite.fixture_seed,
        suite.fixture_digest,
        business_by_flow,
        business_by_skill,
        smoke_by_flow,
        internal_login,
        positive_by_flow,
        negative_by_flow,
        positive,
        negative,
        calibration,
        dict(source_attempts),
        dict(browser_sources),
        target_decisions["model_positive"],
        target_decisions["model_negative"],
        serving,
        dict(hazards),
        oracle_observed_cases,
        gate,
    )
