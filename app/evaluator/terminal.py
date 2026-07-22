"""Deterministic rules for evaluating terminal business outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

from app.ports.capability_gateway import ErrorCode

TerminalBusinessStatus: TypeAlias = Literal[
    "completed",
    "failed",
    "denied",
    "binding_required",
    "timeout",
    "no_capability_found",
]
EvaluationResult: TypeAlias = Literal["passed", "failed", "error"]
EvaluationReason: TypeAlias = Literal[
    "business_completed",
    "business_not_completed",
    "evaluator_error",
]


@dataclass(frozen=True)
class EvaluationConclusion:
    """A trace-safe conclusion independent from the business terminal state."""

    business_status: TerminalBusinessStatus
    business_error_code: ErrorCode | None
    evaluation_result: EvaluationResult
    reason: EvaluationReason
    rule_id: Literal["terminal_status_v1"] = "terminal_status_v1"

    def trace_attributes(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "business_status": self.business_status,
            "business_error_code": self.business_error_code,
            "evaluation_result": self.evaluation_result,
            "reason": self.reason,
        }


class TerminalEvaluator:
    """Evaluate terminal status with deterministic, non-controlling rules."""

    def evaluate(
        self,
        business_status: TerminalBusinessStatus,
        error_code: ErrorCode | None,
    ) -> EvaluationConclusion:
        if business_status == "completed":
            return EvaluationConclusion(
                business_status=business_status,
                business_error_code=error_code,
                evaluation_result="passed",
                reason="business_completed",
            )
        return EvaluationConclusion(
            business_status=business_status,
            business_error_code=error_code,
            evaluation_result="failed",
            reason="business_not_completed",
        )
