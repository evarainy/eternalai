"""Pure deterministic preservation check for the read-only OA overview."""

import json
from typing import Any

from app.ports.evaluation import (
    BusinessVerification,
    CheckResult,
    EvaluationScope,
    OverviewEvaluationInput,
    RuleId,
    VerificationChecks,
    VerificationReason,
    VerificationResult,
)

OVERVIEW_VERSION = "1.1.0"
EXPECTED_STEPS = (
    ("pending", "oa.list_pending_workflows"),
    ("messages", "oa.list_system_messages"),
)


def required_postcondition_rule(capability_id: str | None) -> RuleId | None:
    return "oa_read_overview_v1" if capability_id == "oa.read_overview" else None


def canonical_object(value: object) -> str:
    if type(value) is not dict:
        raise ValueError("Evaluation snapshot must be a JSON object")
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def load_object(snapshot: str) -> dict[str, Any]:
    value = json.loads(snapshot)
    if type(value) is not dict or canonical_object(value) != snapshot:
        raise ValueError("Evaluation snapshot is not a canonical JSON object")
    return value


def verification(
    result: VerificationResult,
    reason: VerificationReason,
    *,
    structure: CheckResult = "not_checked",
    source: CheckResult = "not_checked",
    pending: CheckResult = "not_checked",
    messages: CheckResult = "not_checked",
) -> BusinessVerification:
    return BusinessVerification(
        "oa_read_overview_v1",
        result,
        structure,
        reason,
        VerificationChecks(source, pending, messages),
    )


def unevaluated(capability_id: str | None) -> BusinessVerification:
    rule = required_postcondition_rule(capability_id)
    return BusinessVerification(
        rule,
        "not_evaluated",
        "not_checked",
        "execution_not_completed" if rule else "rule_not_configured",
        VerificationChecks("not_checked", "not_checked", "not_checked"),
    )


class OverviewPostconditionEvaluator:
    def evaluate(
        self,
        expected_scope: EvaluationScope,
        expected_capability_id: str,
        expected_version: str,
        evaluation_input: OverviewEvaluationInput | None,
    ) -> BusinessVerification:
        if expected_version != OVERVIEW_VERSION:
            return verification("error", "unsupported_version", source="failed")
        evidence = evaluation_input
        if evidence is None or any(
            not any(item.step_id == step for item in evidence.observations)
            for step, _ in EXPECTED_STEPS
        ):
            return verification("error", "evidence_missing", source="failed")
        if (
            evidence.rule_id != required_postcondition_rule(expected_capability_id)
            or evidence.scope != expected_scope
            or evidence.workflow_id != expected_capability_id
            or evidence.workflow_version != expected_version
            or any(
                item.scope != expected_scope
                or item.workflow_id != expected_capability_id
                or item.workflow_version != expected_version
                for item in evidence.observations
            )
        ):
            return verification("error", "evidence_scope_mismatch", source="failed")
        if evidence.request_json != "{}":
            return verification("error", "request_contract_invalid", source="failed")
        if tuple((item.step_id, item.capability_id) for item in evidence.observations) != (
            EXPECTED_STEPS
        ):
            return verification("error", "evidence_step_mismatch", source="failed")
        if evidence.structure_result == "failed":
            return verification("failed", "structure_invalid", structure="failed", source="passed")
        output = load_object(evidence.output_json)
        pending, messages = evidence.observations
        # Parse both sources even when the producer's structure signal claims success.
        load_object(pending.payload_json)
        load_object(messages.payload_json)
        if canonical_object(output["pending"]) != pending.payload_json:
            return verification(
                "failed", "pending_mismatch", structure="passed", source="passed", pending="failed"
            )
        if canonical_object(output["messages"]) != messages.payload_json:
            return verification(
                "failed",
                "messages_mismatch",
                structure="passed",
                source="passed",
                pending="passed",
                messages="failed",
            )
        return verification(
            "passed",
            "postconditions_satisfied",
            structure="passed",
            source="passed",
            pending="passed",
            messages="passed",
        )
