"""Run Golden Task judgments and emit a machine-readable JSON summary."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Modifying this set requires explicit authorization from a future task and
# human approval.
GATE_SKIP_EXEMPT_GT_IDS: frozenset[str] = frozenset()
# Scored tasks whose injection companion must have run and passed in the
# agentscope replay (the only scored tasks with injection enabled).
AGENTSCOPE_INJECTION_GT_IDS: tuple[str, ...] = (
    "GT-001",
    "GT-002",
    "GT-003",
    "GT-004",
    "GT-005",
)
AGENTSCOPE_EXPECTED_TOTAL = 34
AGENTSCOPE_SCORED_POSITIVE = 10
AGENTSCOPE_SCORED_NEGATIVE = 16


@dataclass(frozen=True)
class GateDecision:
    passed: bool
    reasons: list[str]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--summary",
        action="store_true",
        help="emit the Golden Task summary JSON",
    )
    parser.add_argument(
        "--gate",
        action="store_true",
        help="emit the summary JSON and fail when Golden Task thresholds fail",
    )
    parser.add_argument(
        "--engine",
        choices=("legacy", "agentscope"),
        default="legacy",
        help="runtime under test; agentscope replay is gated only locally, not in CI",
    )
    parser.add_argument(
        "--ids",
        help="comma-separated Golden Task ids to run instead of all of them",
    )
    args = parser.parse_args(argv)
    if args.gate and args.ids:
        parser.error("--gate judges the full run only; omit --ids")

    # Arguments are passed only when given, so the default call is unchanged.
    selection: dict[str, Any] = {}
    if args.engine != "legacy":
        selection["engine"] = args.engine
    if args.ids:
        selection["gt_ids"] = [item.strip() for item in args.ids.split(",") if item.strip()]

    try:
        evaluate_all_golden_tasks, build_summary = _load_runner()
        summary = build_summary(evaluate_all_golden_tasks(**selection))
    except Exception as exc:
        print(f"Golden Task runner infrastructure failure: {exc}", file=sys.stderr)
        return 2

    json.dump(summary, sys.stdout, ensure_ascii=False, sort_keys=True)
    sys.stdout.write("\n")
    if args.gate:
        decision = (
            evaluate_agentscope_gate(summary)
            if args.engine == "agentscope"
            else evaluate_gate(summary)
        )
        if not decision.passed:
            for reason in decision.reasons:
                print(f"Golden Task gate failed: {reason}", file=sys.stderr)
            return 1
    return 0


def evaluate_gate(summary: dict[str, Any]) -> GateDecision:
    results = list(cast(Sequence[dict[str, Any]], summary.get("results", [])))
    reasons: list[str] = []

    failed = _int_summary_value(summary, "failed")
    if failed > 0:
        reasons.append(f"R1 failed > 0: failed={failed}")

    skipped_unexempt = [
        str(item.get("golden_task_id", "<unknown>"))
        for item in results
        if item.get("status") == "skipped"
        and str(item.get("golden_task_id", "<unknown>"))
        not in GATE_SKIP_EXEMPT_GT_IDS
    ]
    if skipped_unexempt:
        reasons.append(
            "R2 unexempt skipped Golden Tasks: "
            f"{', '.join(sorted(skipped_unexempt))}"
        )

    positive_total = _summary_count(
        summary,
        "positive_total",
        results,
        category="positive",
    )
    negative_total = _summary_count(
        summary,
        "negative_total",
        results,
        category="negative",
    )
    positive_not_applicable = _summary_count(
        summary,
        "positive_not_applicable",
        results,
        category="positive",
        status="not_applicable",
    )
    negative_not_applicable = _summary_count(
        summary,
        "negative_not_applicable",
        results,
        category="negative",
        status="not_applicable",
    )
    positive_effective = positive_total - positive_not_applicable
    negative_effective = negative_total - negative_not_applicable
    if positive_effective == 0:
        reasons.append("R3 positive effective denominator is 0")
    if negative_effective == 0:
        reasons.append("R3 negative effective denominator is 0")

    positive_passed = _summary_count(
        summary,
        "positive_passed",
        results,
        category="positive",
        status="passed",
    )
    negative_passed = _summary_count(
        summary,
        "negative_passed",
        results,
        category="negative",
        status="passed",
    )
    if positive_effective > 0 and positive_passed * 5 < positive_effective * 4:
        reasons.append(
            "R4 positive pass rate below 80%: "
            f"positive_passed={positive_passed}, "
            f"positive_effective={positive_effective}"
        )
    if negative_effective > 0 and negative_passed != negative_effective:
        reasons.append(
            "R5 negative pass rate below 100%: "
            f"negative_passed={negative_passed}, "
            f"negative_effective={negative_effective}"
        )

    unknown_categories = sorted(
        {
            str(item.get("category", "<missing>"))
            for item in results
            if item.get("category") not in {"positive", "negative"}
        }
    )
    if unknown_categories:
        reasons.append(
            "R6 unknown Golden Task categories: "
            f"{', '.join(unknown_categories)}"
        )

    return GateDecision(passed=not reasons, reasons=reasons)


def evaluate_agentscope_gate(summary: dict[str, Any]) -> GateDecision:
    """Judge the full agentscope replay (M2_DESIGN §6.2); legacy rules still apply."""

    try:
        gt_ids, not_applicable = _load_agentscope_expectations()
    except Exception as exc:
        return GateDecision(passed=False, reasons=[f"A0 expectations unavailable: {exc}"])
    results = list(cast(Sequence[dict[str, Any]], summary.get("results", [])))
    reasons = list(evaluate_gate(summary).reasons)

    ids = [str(item.get("golden_task_id", "<unknown>")) for item in results]
    if len(ids) != len(set(ids)) or set(ids) != set(gt_ids):
        reasons.append(
            "A1 result ids differ from GT_IDS: "
            f"missing={sorted(set(gt_ids) - set(ids))}, "
            f"unexpected={sorted(set(ids) - set(gt_ids))}, "
            f"duplicated={sorted({i for i in ids if ids.count(i) > 1})}"
        )
    if len(results) != AGENTSCOPE_EXPECTED_TOTAL:
        reasons.append(
            f"A1 total != {AGENTSCOPE_EXPECTED_TOTAL}: total={len(results)}"
        )

    actual_na = {
        str(item.get("golden_task_id"))
        for item in results
        if item.get("status") == "not_applicable"
    }
    if actual_na != set(not_applicable):
        reasons.append(
            "A2 not_applicable set differs: "
            f"missing={sorted(set(not_applicable) - actual_na)}, "
            f"unexpected={sorted(actual_na - set(not_applicable))}"
        )

    scored = [item for item in results if str(item.get("golden_task_id")) not in not_applicable]
    not_passed = sorted(
        str(item.get("golden_task_id")) for item in scored if item.get("status") != "passed"
    )
    if not_passed:
        reasons.append(f"A3 scored tasks not passed: {', '.join(not_passed)}")
    for category, expected in (
        ("positive", AGENTSCOPE_SCORED_POSITIVE),
        ("negative", AGENTSCOPE_SCORED_NEGATIVE),
    ):
        passed = sum(
            1
            for item in scored
            if item.get("category") == category and item.get("status") == "passed"
        )
        if passed != expected:
            reasons.append(f"A3 scored {category} passed {passed}/{expected}")

    by_id = {str(item.get("golden_task_id")): item for item in results}
    not_ran = [
        gt_id
        for gt_id in AGENTSCOPE_INJECTION_GT_IDS
        if by_id.get(gt_id, {}).get("injection") != "ran_passed"
    ]
    if not_ran:
        reasons.append(f"A4 injection not ran_passed: {', '.join(not_ran)}")
    return GateDecision(passed=not reasons, reasons=reasons)


def _load_agentscope_expectations() -> tuple[tuple[str, ...], dict[str, str]]:
    from scripts.golden_task_evaluator import AGENTSCOPE_NOT_APPLICABLE, GT_IDS

    return GT_IDS, AGENTSCOPE_NOT_APPLICABLE


def _int_summary_value(summary: dict[str, Any], key: str) -> int:
    value = summary.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value


def _summary_count(
    summary: dict[str, Any],
    key: str,
    results: Sequence[dict[str, Any]],
    *,
    category: str,
    status: str | None = None,
) -> int:
    value = summary.get(key)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return _count_results(results, category=category, status=status)


def _count_results(
    results: Sequence[dict[str, Any]],
    *,
    category: str,
    status: str | None = None,
) -> int:
    return sum(
        1
        for item in results
        if item.get("category") == category
        and (status is None or item.get("status") == status)
    )


def _load_runner() -> tuple[
    Callable[..., list[Any]],
    Callable[[Sequence[Any]], dict[str, Any]],
]:
    from scripts.golden_task_evaluator import build_summary, evaluate_all_golden_tasks

    return evaluate_all_golden_tasks, build_summary


if __name__ == "__main__":
    raise SystemExit(main())
