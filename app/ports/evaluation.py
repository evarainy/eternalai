"""Immutable, in-process evidence and closed verification summaries."""

from dataclasses import dataclass, fields
from typing import Literal, TypeAlias, get_args

RuleId: TypeAlias = Literal["oa_read_overview_v1"]
CheckResult: TypeAlias = Literal["passed", "failed", "not_checked"]
VerificationResult: TypeAlias = Literal["passed", "failed", "error", "not_evaluated"]
VerificationReason: TypeAlias = Literal[
    "postconditions_satisfied",
    "structure_invalid",
    "pending_mismatch",
    "messages_mismatch",
    "output_snapshot_mismatch",
    "evidence_missing",
    "evidence_scope_mismatch",
    "evidence_step_mismatch",
    "unsupported_version",
    "request_contract_invalid",
    "evaluator_error",
    "rule_not_configured",
    "execution_not_completed",
]


def _strings(*values: object) -> None:
    if any(type(value) is not str or not value.strip() for value in values):
        raise ValueError("Evaluation identifiers and snapshots must be nonempty strings")


def _closed(value: object, choices: tuple[str, ...]) -> None:
    if type(value) is not str or value not in choices:
        raise ValueError("Invalid evaluation enum")


@dataclass(frozen=True, slots=True, repr=False)
class EvaluationScope:
    task_id: str
    trace_id: str
    session_id: str
    tenant_id: str
    ai_user_id: str

    def __post_init__(self) -> None:
        _strings(*(getattr(self, item.name) for item in fields(self)))


@dataclass(frozen=True, slots=True, repr=False)
class StepObservation:
    scope: EvaluationScope
    workflow_id: str
    workflow_version: str
    step_id: str
    capability_id: str
    attempt: int
    payload_json: str

    def __post_init__(self) -> None:
        if type(self.scope) is not EvaluationScope:
            raise ValueError("Invalid evaluation scope")
        _strings(
            self.workflow_id,
            self.workflow_version,
            self.step_id,
            self.capability_id,
            self.payload_json,
        )
        if type(self.attempt) is not int or self.attempt < 1:
            raise ValueError("Observation attempt must be a positive integer")


@dataclass(frozen=True, slots=True, repr=False)
class OverviewEvaluationInput:
    scope: EvaluationScope
    workflow_id: str
    workflow_version: str
    rule_id: RuleId
    request_json: str
    observations: tuple[StepObservation, ...]
    output_json: str
    structure_result: Literal["passed", "failed"]

    def __post_init__(self) -> None:
        if type(self.scope) is not EvaluationScope:
            raise ValueError("Invalid evaluation scope")
        _strings(self.workflow_id, self.workflow_version, self.request_json, self.output_json)
        _closed(self.rule_id, get_args(RuleId))
        _closed(self.structure_result, ("passed", "failed"))
        if type(self.observations) is not tuple or any(
            type(item) is not StepObservation for item in self.observations
        ):
            raise ValueError("Observations must be an immutable typed tuple")


@dataclass(frozen=True, slots=True)
class VerificationChecks:
    source_binding: CheckResult
    pending_preserved: CheckResult
    messages_preserved: CheckResult

    def __post_init__(self) -> None:
        for item in fields(self):
            _closed(getattr(self, item.name), get_args(CheckResult))


@dataclass(frozen=True, slots=True)
class BusinessVerification:
    rule_id: RuleId | None
    result: VerificationResult
    structure_result: CheckResult
    reason: VerificationReason
    checks: VerificationChecks

    def __post_init__(self) -> None:
        if self.rule_id is not None:
            _closed(self.rule_id, get_args(RuleId))
        _closed(self.result, get_args(VerificationResult))
        _closed(self.structure_result, get_args(CheckResult))
        _closed(self.reason, get_args(VerificationReason))
        if type(self.checks) is not VerificationChecks:
            raise ValueError("Invalid verification checks")
