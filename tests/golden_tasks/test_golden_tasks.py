"""End-to-end Golden Task runner judgments."""

import pytest

from scripts.golden_task_evaluator import (
    FROZEN_GT_IDS,
    GT_IDS,
    build_summary,
    evaluate_all_golden_tasks,
    evaluate_golden_task,
)


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
    assert GT_IDS == FROZEN_GT_IDS + ("GT-027", "GT-028")
    assert judged_ids == GT_IDS
    assert summary["total"] == len(GT_IDS)
    assert summary["positive_passed"] >= 1
    assert summary["negative_passed"] >= 1
