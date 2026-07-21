"""Minimal in-process models for a strictly linear Workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

WorkflowInputSource = Literal["workflow_input", "step_output"]


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
    static_arguments: Mapping[str, Any] = field(default_factory=dict)
    input_mapping: Mapping[str, WorkflowInputRef] = field(default_factory=dict)
    when: WorkflowCondition | None = None


@dataclass(frozen=True)
class WorkflowDefinition:
    workflow_id: str
    version: str
    steps: tuple[WorkflowStep, ...]


@dataclass(frozen=True)
class WorkflowRunResult:
    workflow_id: str
    workflow_version: str
    trace_id: str
    output: dict[str, Any]
    step_outputs: dict[str, dict[str, Any]]


__all__ = (
    "WorkflowCondition",
    "WorkflowDefinition",
    "WorkflowInputRef",
    "WorkflowRunResult",
    "WorkflowStep",
)
