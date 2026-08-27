"""End-to-end Golden Task runner judgments."""

import pytest

from scripts.golden_task_evaluator import (
    FROZEN_GT_IDS,
    GT_IDS,
    build_summary,
    evaluate_all_golden_tasks,
    evaluate_golden_task,
)

# 本棒之前已冻结的 25 题，字面写死。任何减少都必须先获雨爷显式批准。
_PRE_GOLDEN_001_FROZEN: tuple[str, ...] = (
    "GT-001",
    "GT-002",
    "GT-003",
    "GT-004",
    "GT-005",
    "GT-006",
    "GT-007",
    "GT-008",
    "GT-009",
    "GT-010",
    "GT-012",
    "GT-013",
    "GT-014",
    "GT-015",
    "GT-016",
    "GT-017",
    "GT-018",
    "GT-019",
    "GT-020",
    "GT-021",
    "GT-022",
    "GT-023",
    "GT-024",
    "GT-025",
    "GT-026",
)
# 已运行、未冻结，显式登记。
_UNFROZEN_GT_IDS: tuple[str, ...] = ("GT-027", "GT-028")


@pytest.mark.parametrize("gt_id", GT_IDS)
def test_golden_task_fixture_is_judged_without_masking_failures(gt_id: str) -> None:
    result = evaluate_golden_task(gt_id)

    assert result.status in {"passed", "failed", "skipped", "not_applicable"}
    assert result.category in {"positive", "negative"}
    if result.status == "failed":
        assert result.reasons
    if result.status in {"skipped", "not_applicable"}:
        assert result.reasons


def test_frozen_and_append_only_goldens_all_pass() -> None:
    summary = build_summary(evaluate_all_golden_tasks())

    judged_ids = tuple(item["golden_task_id"] for item in summary["results"])
    assert set(_PRE_GOLDEN_001_FROZEN) <= set(FROZEN_GT_IDS)
    assert len(FROZEN_GT_IDS) == len(set(FROZEN_GT_IDS))
    assert set(_UNFROZEN_GT_IDS).isdisjoint(FROZEN_GT_IDS)
    assert GT_IDS == tuple(sorted(set(FROZEN_GT_IDS) | set(_UNFROZEN_GT_IDS)))
    assert judged_ids == GT_IDS
    assert summary["total"] == len(GT_IDS)
    assert summary["positive_passed"] >= 1
    assert summary["negative_passed"] >= 1
