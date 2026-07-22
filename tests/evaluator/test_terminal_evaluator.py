from __future__ import annotations

from typing import cast

import pytest

from app.evaluator import TerminalBusinessStatus, TerminalEvaluator
from app.ports.capability_gateway import ErrorCode


@pytest.mark.parametrize(
    ("business_status", "error_code", "expected_result", "expected_reason"),
    [
        ("completed", None, "passed", "business_completed"),
        ("failed", "adapter_error", "failed", "business_not_completed"),
        ("denied", "policy_denied", "failed", "business_not_completed"),
        (
            "binding_required",
            "identity_unbound",
            "failed",
            "business_not_completed",
        ),
        ("timeout", "adapter_timeout", "failed", "business_not_completed"),
        (
            "no_capability_found",
            "capability_not_found",
            "failed",
            "business_not_completed",
        ),
    ],
)
def test_terminal_status_mapping_is_complete_and_deterministic(
    business_status: TerminalBusinessStatus,
    error_code: ErrorCode | None,
    expected_result: str,
    expected_reason: str,
) -> None:
    conclusion = TerminalEvaluator().evaluate(business_status, error_code)

    assert conclusion.trace_attributes() == {
        "rule_id": "terminal_status_v1",
        "business_status": business_status,
        "business_error_code": error_code,
        "evaluation_result": expected_result,
        "reason": expected_reason,
    }


def test_completed_status_is_not_rewritten_by_an_inconsistent_error_code() -> None:
    conclusion = TerminalEvaluator().evaluate(
        "completed",
        cast(ErrorCode, "internal_error"),
    )

    assert conclusion.evaluation_result == "passed"
    assert conclusion.business_error_code == "internal_error"
