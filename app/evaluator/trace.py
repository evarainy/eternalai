"""Shared terminal-status trace construction; no business evidence is invented."""

from typing import Any, Literal

from app.evaluator.overview import unevaluated
from app.evaluator.terminal import EvaluationConclusion, TerminalBusinessStatus, TerminalEvaluator
from app.ports.capability_gateway import ErrorCode, ExecutionResult
from app.ports.evaluation import BusinessVerification


def terminal_evaluation_attributes(
    evaluator: TerminalEvaluator,
    *,
    business_status: TerminalBusinessStatus,
    error_code: ErrorCode | None,
    capability_id: str | None,
    raw_execution: ExecutionResult | None = None,
    business_verification: BusinessVerification | None = None,
) -> tuple[Literal["ok", "failed"], dict[str, Any]]:
    try:
        conclusion = evaluator.evaluate(business_status, error_code)
    except Exception:
        conclusion = EvaluationConclusion(
            business_status=business_status,
            business_error_code=error_code,
            evaluation_result="error",
            reason="evaluator_error",
        )
    business = business_verification or unevaluated(capability_id)
    attributes = conclusion.trace_attributes()
    attributes.update(
        {
            "evaluation_scope": "terminal_status",
            "execution_status": raw_execution.status if raw_execution else business_status,
            "execution_error_code": raw_execution.error_code if raw_execution else error_code,
            "business_verification": {
                "rule_id": business.rule_id,
                "result": business.result,
                "structure_result": business.structure_result,
                "reason": business.reason,
                "checks": {
                    "source_binding": business.checks.source_binding,
                    "pending_preserved": business.checks.pending_preserved,
                    "messages_preserved": business.checks.messages_preserved,
                },
            },
        }
    )
    return (
        "ok"
        if conclusion.evaluation_result == "passed" and business.result not in {"failed", "error"}
        else "failed",
        attributes,
    )
