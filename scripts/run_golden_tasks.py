"""Run Golden Task judgments and emit a machine-readable JSON summary."""

from __future__ import annotations

import argparse
import importlib
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
    args = parser.parse_args(argv)

    try:
        evaluate_all_golden_tasks, build_summary = _load_runner()
        summary = build_summary(evaluate_all_golden_tasks())
    except Exception as exc:
        print(f"Golden Task runner infrastructure failure: {exc}", file=sys.stderr)
        return 2

    json.dump(summary, sys.stdout, ensure_ascii=False, sort_keys=True)
    sys.stdout.write("\n")
    if args.gate:
        decision = evaluate_gate(summary)
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
    Callable[[], list[Any]],
    Callable[[Sequence[Any]], dict[str, Any]],
]:
    module = importlib.import_module("tests.golden_tasks.test_golden_tasks")
    return (
        cast(Callable[[], list[Any]], getattr(module, "evaluate_all_golden_tasks")),
        cast(Callable[[Sequence[Any]], dict[str, Any]], getattr(module, "build_summary")),
    )


if __name__ == "__main__":
    raise SystemExit(main())
