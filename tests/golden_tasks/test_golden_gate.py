"""Threshold gate tests for the Golden Task runner."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from scripts import run_golden_tasks


def _item(gt_id: str, category: str, status: str) -> dict[str, Any]:
    return {
        "golden_task_id": gt_id,
        "category": category,
        "status": status,
        "reasons": [] if status == "passed" else [f"{gt_id} {status}"],
    }


def _summary(
    results: Sequence[dict[str, Any]],
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary = {
        "total": len(results),
        "passed": sum(1 for item in results if item["status"] == "passed"),
        "failed": sum(1 for item in results if item["status"] == "failed"),
        "skipped": sum(1 for item in results if item["status"] == "skipped"),
        "not_applicable": sum(
            1 for item in results if item["status"] == "not_applicable"
        ),
        "positive_passed": sum(
            1
            for item in results
            if item["category"] == "positive" and item["status"] == "passed"
        ),
        "negative_passed": sum(
            1
            for item in results
            if item["category"] == "negative" and item["status"] == "passed"
        ),
        "positive_total": sum(1 for item in results if item["category"] == "positive"),
        "negative_total": sum(1 for item in results if item["category"] == "negative"),
        "positive_not_applicable": sum(
            1
            for item in results
            if item["category"] == "positive" and item["status"] == "not_applicable"
        ),
        "negative_not_applicable": sum(
            1
            for item in results
            if item["category"] == "negative" and item["status"] == "not_applicable"
        ),
        "results": list(results),
    }
    if overrides is not None:
        summary.update(overrides)
    return summary


def _passing_summary() -> dict[str, Any]:
    return _summary(
        [
            _item("GT-P-001", "positive", "passed"),
            _item("GT-P-002", "positive", "passed"),
            _item("GT-N-001", "negative", "passed"),
        ]
    )


def test_failed_count_fails_gate_with_r1_reason() -> None:
    summary = _summary(
        [
            _item("GT-P-001", "positive", "passed"),
            _item("GT-P-002", "positive", "failed"),
            _item("GT-N-001", "negative", "passed"),
        ]
    )

    decision = run_golden_tasks.evaluate_gate(summary)

    assert decision.passed is False
    assert any("R1" in reason and "failed" in reason for reason in decision.reasons)


def test_positive_rate_below_80_percent_fails_and_exact_80_passes() -> None:
    below_threshold = _summary(
        [
            *[_item(f"GT-P-{index}", "positive", "passed") for index in range(1, 8)],
            *[_item(f"GT-P-{index}", "positive", "skipped") for index in range(8, 11)],
            _item("GT-N-001", "negative", "passed"),
        ]
    )
    exact_threshold = _summary(
        [
            *[_item(f"GT-P-{index}", "positive", "passed") for index in range(1, 9)],
            *[_item(f"GT-P-{index}", "positive", "skipped") for index in range(9, 11)],
            _item("GT-N-001", "negative", "passed"),
        ]
    )

    below_decision = run_golden_tasks.evaluate_gate(below_threshold)
    exact_decision = run_golden_tasks.evaluate_gate(exact_threshold)

    assert below_decision.passed is False
    assert any("R4" in reason for reason in below_decision.reasons)
    assert exact_decision.passed is False
    assert any("R2" in reason for reason in exact_decision.reasons)
    assert not any("R4" in reason for reason in exact_decision.reasons)


def test_negative_effective_denominator_must_all_pass() -> None:
    summary = _summary(
        [
            _item("GT-P-001", "positive", "passed"),
            _item("GT-N-001", "negative", "passed"),
            _item("GT-N-002", "negative", "skipped"),
        ]
    )

    decision = run_golden_tasks.evaluate_gate(summary)

    assert decision.passed is False
    assert any("R5" in reason for reason in decision.reasons)


def test_not_applicable_is_excluded_from_denominator_without_failing_gate() -> None:
    summary = _summary(
        [
            *[_item(f"GT-P-{index}", "positive", "passed") for index in range(1, 5)],
            _item("GT-P-005", "positive", "passed"),
            _item("GT-P-006", "positive", "not_applicable"),
            _item("GT-N-001", "negative", "passed"),
            _item("GT-N-002", "negative", "not_applicable"),
        ],
        overrides={"positive_passed": 4},
    )

    decision = run_golden_tasks.evaluate_gate(summary)

    assert decision.passed is True
    assert not any("R4" in reason for reason in decision.reasons)
    assert not any("not_applicable" in reason for reason in decision.reasons)
    assert summary["positive_passed"] * 5 == (
        summary["positive_total"] - summary["positive_not_applicable"]
    ) * 4
    assert summary["positive_not_applicable"] == 1
    assert summary["negative_not_applicable"] == 1


def test_unexempt_skipped_fails_and_exemption_only_waives_r2(
    monkeypatch: Any,
) -> None:
    summary = _summary(
        [
            _item("GT-P-001", "positive", "passed"),
            _item("GT-P-002", "positive", "passed"),
            _item("GT-P-003", "positive", "skipped"),
            _item("GT-N-001", "negative", "passed"),
        ]
    )

    unexempt_decision = run_golden_tasks.evaluate_gate(summary)
    monkeypatch.setattr(
        run_golden_tasks,
        "GATE_SKIP_EXEMPT_GT_IDS",
        frozenset({"GT-P-003"}),
    )
    exempt_decision = run_golden_tasks.evaluate_gate(summary)

    assert run_golden_tasks.GATE_SKIP_EXEMPT_GT_IDS == frozenset({"GT-P-003"})
    assert unexempt_decision.passed is False
    assert any("R2" in reason for reason in unexempt_decision.reasons)
    assert exempt_decision.passed is False
    assert not any("R2" in reason for reason in exempt_decision.reasons)
    assert any("R4" in reason for reason in exempt_decision.reasons)


def test_unknown_category_and_empty_effective_denominators_fail_closed() -> None:
    unknown_category = _summary(
        [
            _item("GT-P-001", "positive", "passed"),
            _item("GT-X-001", "boundary", "passed"),
            _item("GT-N-001", "negative", "passed"),
        ]
    )
    empty_positive = _summary(
        [
            _item("GT-P-001", "positive", "not_applicable"),
            _item("GT-N-001", "negative", "passed"),
        ]
    )

    unknown_decision = run_golden_tasks.evaluate_gate(unknown_category)
    empty_decision = run_golden_tasks.evaluate_gate(empty_positive)

    assert unknown_decision.passed is False
    assert any("R6" in reason for reason in unknown_decision.reasons)
    assert empty_decision.passed is False
    assert any("R3" in reason for reason in empty_decision.reasons)


def test_main_gate_returns_one_for_threshold_failure(
    monkeypatch: Any,
    capsys: Any,
) -> None:
    summary = _summary(
        [
            _item("GT-P-001", "positive", "failed"),
            _item("GT-N-001", "negative", "passed"),
        ]
    )

    def fake_load_runner() -> tuple[Any, Any]:
        return lambda: [], lambda _results: summary

    monkeypatch.setattr(run_golden_tasks, "_load_runner", fake_load_runner)

    exit_code = run_golden_tasks.main(["--gate"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert '"failed": 1' in captured.out
    assert "R1" in captured.err


def test_main_gate_returns_zero_for_passing_summary(
    monkeypatch: Any,
    capsys: Any,
) -> None:
    def fake_load_runner() -> tuple[Any, Any]:
        return lambda: [], lambda _results: _passing_summary()

    monkeypatch.setattr(run_golden_tasks, "_load_runner", fake_load_runner)

    exit_code = run_golden_tasks.main(["--gate"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert '"failed": 0' in captured.out
    assert captured.err == ""


def test_main_infrastructure_failure_returns_two(
    monkeypatch: Any,
    capsys: Any,
) -> None:
    def fake_load_runner() -> tuple[Any, Any]:
        raise RuntimeError("load failed")

    monkeypatch.setattr(run_golden_tasks, "_load_runner", fake_load_runner)

    exit_code = run_golden_tasks.main(["--gate"])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert captured.out == ""
    assert "infrastructure failure" in captured.err


def _agentscope_summary(
    mutate: Any = None,
) -> dict[str, Any]:
    from scripts.golden_task_evaluator import AGENTSCOPE_NOT_APPLICABLE, GT_IDS
    from scripts.golden_task_fixture_support import load_fixture

    results: list[dict[str, Any]] = []
    for gt_id in GT_IDS:
        category = str(load_fixture(gt_id)["category"])
        item = _item(
            gt_id,
            category,
            "not_applicable" if gt_id in AGENTSCOPE_NOT_APPLICABLE else "passed",
        )
        item["injection"] = "ran_passed" if gt_id <= "GT-005" else "none"
        results.append(item)
    if mutate is not None:
        mutate(results)
    return _summary(results)


def _by_id(results: list[dict[str, Any]], gt_id: str) -> dict[str, Any]:
    return next(item for item in results if item["golden_task_id"] == gt_id)


def test_agentscope_gate_passes_full_expected_summary() -> None:
    summary = _agentscope_summary()

    decision = run_golden_tasks.evaluate_agentscope_gate(summary)

    assert decision.reasons == []
    assert summary["total"] == 34
    assert summary["not_applicable"] == 8
    assert summary["positive_passed"] == 10
    assert summary["negative_passed"] == 16


def test_agentscope_gate_fails_when_scored_task_not_passed() -> None:
    def mutate(results: list[dict[str, Any]]) -> None:
        _by_id(results, "GT-020")["status"] = "failed"

    decision = run_golden_tasks.evaluate_agentscope_gate(_agentscope_summary(mutate))

    assert decision.passed is False
    assert any("A3 scored tasks not passed: GT-020" in r for r in decision.reasons)


def test_agentscope_gate_fails_when_not_applicable_set_differs() -> None:
    def mutate(results: list[dict[str, Any]]) -> None:
        _by_id(results, "GT-006")["status"] = "passed"

    decision = run_golden_tasks.evaluate_agentscope_gate(_agentscope_summary(mutate))

    assert decision.passed is False
    assert any("A2" in r and "GT-006" in r for r in decision.reasons)


def test_agentscope_gate_fails_on_scored_task_marked_not_applicable() -> None:
    def mutate(results: list[dict[str, Any]]) -> None:
        _by_id(results, "GT-008")["status"] = "not_applicable"

    decision = run_golden_tasks.evaluate_agentscope_gate(_agentscope_summary(mutate))

    assert decision.passed is False
    assert any("A2" in r and "GT-008" in r for r in decision.reasons)


def test_agentscope_gate_requires_injection_pairs_to_have_passed() -> None:
    def mutate(results: list[dict[str, Any]]) -> None:
        _by_id(results, "GT-003")["injection"] = "ran_failed"
        _by_id(results, "GT-005")["injection"] = "none"

    decision = run_golden_tasks.evaluate_agentscope_gate(_agentscope_summary(mutate))

    assert decision.passed is False
    assert any("A4" in r and "GT-003, GT-005" in r for r in decision.reasons)


def test_agentscope_gate_fails_on_missing_or_duplicated_ids() -> None:
    def mutate(results: list[dict[str, Any]]) -> None:
        results.pop()
        results.append(dict(results[0]))

    decision = run_golden_tasks.evaluate_agentscope_gate(_agentscope_summary(mutate))

    assert decision.passed is False
    assert any("A1 result ids differ" in r for r in decision.reasons)


def test_main_agentscope_gate_uses_agentscope_rules(
    monkeypatch: Any,
    capsys: Any,
) -> None:
    # Passes evaluate_gate (legacy thresholds) but not the agentscope contract.
    summary = _passing_summary()
    monkeypatch.setattr(
        run_golden_tasks,
        "_load_runner",
        lambda: (lambda **_selection: [], lambda _results: summary),
    )

    legacy_exit = run_golden_tasks.main(["--gate"])
    capsys.readouterr()
    agentscope_exit = run_golden_tasks.main(["--engine", "agentscope", "--gate"])
    captured = capsys.readouterr()

    assert legacy_exit == 0
    assert agentscope_exit == 1
    assert "A1" in captured.err


def test_gate_rejects_ids_selection(capsys: Any) -> None:
    try:
        run_golden_tasks.main(["--engine", "agentscope", "--gate", "--ids", "GT-001"])
    except SystemExit as exc:
        assert exc.code == 2
    else:  # pragma: no cover - argparse always exits
        raise AssertionError("expected SystemExit")
    assert "--ids" in capsys.readouterr().err
