"""Minimal in-process models for a strictly linear Workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, TypeAlias

from app.ports.capability_gateway import ErrorCode
from app.ports.evaluation import StepObservation

WorkflowInputSource = Literal["workflow_input", "step_output"]
WorkflowRunStatus: TypeAlias = Literal[
    "completed",
    "denied",
    "waiting_confirm",
    "failed",
    "timeout",
]


@dataclass(frozen=True)
class WorkflowInputRef:
    source: WorkflowInputSource
    key: str
    step_id: str | None = None


@dataclass(frozen=True)
class WorkflowCondition:
    value: WorkflowInputRef
    equals: Any


@dataclass(frozen=True)
class WorkflowStep:
    step_id: str
    capability_id: str
    confirmed_capability_id: str | None = None
    static_arguments: Mapping[str, Any] = field(default_factory=dict)
    input_mapping: Mapping[str, WorkflowInputRef] = field(default_factory=dict)
    when: WorkflowCondition | None = None


@dataclass(frozen=True)
class WorkflowDefinition:
    workflow_id: str
    version: str
    steps: tuple[WorkflowStep, ...]
    output_step_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class GovernedWorkflowPolicy:
    service_config_id: str
    outer_capability_id: str
    leaf_capability_id: str
    remote_tool: str
    version: str
    recovery_capability_id: str | None = None
    risk: Literal["high"] = "high"
    retry_limit: Literal[0] = 0
    confirmation_required: Literal[True] = True


@dataclass(frozen=True)
class GovernedWorkflowDefinition(WorkflowDefinition):
    policy: GovernedWorkflowPolicy | None = None


@dataclass(frozen=True)
class WorkflowRunResult:
    workflow_id: str
    workflow_version: str
    trace_id: str
    status: WorkflowRunStatus
    output: dict[str, Any]
    step_outputs: dict[str, dict[str, Any]]
    error_code: ErrorCode | None = None
    evaluation_observations: tuple[StepObservation, ...] = ()


class GovernedTerminalResult(WorkflowRunResult):
    """Typed in-process proof returned only after a durable governed terminal."""


class GovernedConfirmationFailureResult(GovernedTerminalResult):
    """Durable unsent failure; confirmation is invalid, original error is retained."""


class GovernedFinalizationError(RuntimeError):
    """Business terminal is durable; task/trace writes still require repair."""

    def __init__(self, result: WorkflowRunResult) -> None:
        super().__init__("governed terminal finalizer incomplete")
        self.result = result


# Definition-run entry (``WorkflowEngine.execute_definition``) only. These are
# separate types so existing definitions, and the digests bound to them, keep
# exactly their current shape.
DefinitionRefSource: TypeAlias = Literal["workflow_input", "step_output", "loop_item"]
DefinitionAssertionOp: TypeAlias = Literal["non_empty", "max_count", "complete"]
DefinitionCallStatus: TypeAlias = Literal["ok", "failed", "not_executed"]
DefinitionRunStatus: TypeAlias = Literal["completed", "failed", "empty", "over_limit", "deadline"]


@dataclass(frozen=True)
class DefinitionRef:
    source: DefinitionRefSource
    path: tuple[str | int, ...] = ()
    step_id: str | None = None


@dataclass(frozen=True)
class DefinitionForEach:
    """Run the step once per element of a list, serially, within ``max_items``."""

    items: DefinitionRef
    max_items: int


@dataclass(frozen=True)
class DefinitionAssertion:
    """Checked after the step completes, against its projected output."""

    op: DefinitionAssertionOp
    ref: DefinitionRef
    value: int | None = None


@dataclass(frozen=True)
class DefinitionStep(WorkflowStep):
    """A read step whose arguments are path references; never confirmable."""

    arguments: Mapping[str, DefinitionRef] = field(default_factory=dict)
    for_each: DefinitionForEach | None = None
    assertions: tuple[DefinitionAssertion, ...] = ()


@dataclass(frozen=True)
class DefinitionItemOutcome:
    index: int
    status: DefinitionCallStatus
    data: dict[str, Any] | None = None
    error_code: ErrorCode | None = None
    attempts: int = 0


@dataclass(frozen=True)
class DefinitionStepOutcome:
    step_id: str
    status: DefinitionCallStatus
    data: dict[str, Any] | None = None
    items: tuple[DefinitionItemOutcome, ...] | None = None
    error_code: ErrorCode | None = None
    attempts: int = 0


@dataclass(frozen=True)
class DefinitionRunResult:
    """Outcome of ``execute_definition``; business partial is derived by the caller."""

    workflow_id: str
    workflow_version: str
    trace_id: str
    status: DefinitionRunStatus
    steps: Mapping[str, DefinitionStepOutcome]
    error_code: ErrorCode | None = None
    failed_assertion: tuple[str, DefinitionAssertionOp] | None = None


__all__ = (
    "DefinitionAssertion",
    "DefinitionForEach",
    "DefinitionItemOutcome",
    "DefinitionRef",
    "DefinitionRunResult",
    "DefinitionStep",
    "DefinitionStepOutcome",
    "GovernedFinalizationError",
    "GovernedTerminalResult",
    "WorkflowCondition",
    "WorkflowDefinition",
    "WorkflowInputRef",
    "WorkflowRunResult",
    "WorkflowRunStatus",
    "WorkflowStep",
)
