"""Run a caller-built read definition of ``DefinitionStep`` steps.

Used only by ``WorkflowEngine.execute_definition``. The caller owns the task,
its version-binding manifest and closing. Every leaf call is asserted against
that manifest, traced, sent through the Gateway with the caller's identity and
projected by ``project_output`` before any later step or item may use it.
Steps run in order; a ``for_each`` step runs serially once per list element.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from app.ports.capability_gateway import (
    CapabilityGatewayPort,
    ErrorCode,
    ExecutionResult,
    RequestOrgContext,
)
from app.ports.capability_registry import CapabilityRegistryPort, CapabilitySpec
from app.ports.human_gate import HumanGatePort
from app.ports.task_store import TaskEventRecord, TaskStorePort
from app.ports.trace import TracePort
from app.version_binding import capability_version_bindings
from app.workflow.models import (
    DefinitionAssertionOp,
    DefinitionItemOutcome,
    DefinitionRef,
    DefinitionRunResult,
    DefinitionRunStatus,
    DefinitionStep,
    DefinitionStepOutcome,
    WorkflowDefinition,
)

# A call is not started with less time left than this (seconds).
MIN_CALL_SECONDS = 10.0
MAX_ATTEMPTS = 2

ProjectOutput = Callable[[CapabilitySpec, ExecutionResult], dict[str, Any]]
_CallStatus = Literal["ok", "failed", "stop", "deadline"]


class DefinitionReferenceError(ValueError):
    """A reference does not resolve against the values at hand."""


@dataclass(frozen=True)
class DefinitionRunDeps:
    gateway: CapabilityGatewayPort
    registry: CapabilityRegistryPort
    trace_port: TracePort
    task_store: TaskStorePort
    human_gate: HumanGatePort | None


def resolve_ref(
    ref: DefinitionRef,
    initial_input: Mapping[str, Any],
    step_outputs: Mapping[str, Mapping[str, Any]],
    item: Any,
) -> Any:
    """Walk ``ref.path`` from its source; any missing segment is an error."""

    if ref.source == "workflow_input":
        value: Any = initial_input
    elif ref.source == "step_output":
        if ref.step_id is None or ref.step_id not in step_outputs:
            raise DefinitionReferenceError("step output unavailable")
        value = step_outputs[ref.step_id]
    else:
        value = item
    for segment in ref.path:
        if isinstance(segment, int) and isinstance(value, list):
            if not 0 <= segment < len(value):
                raise DefinitionReferenceError("index out of range")
            value = value[segment]
        elif isinstance(segment, str) and isinstance(value, Mapping):
            if segment not in value:
                raise DefinitionReferenceError("key missing")
            value = value[segment]
        else:
            raise DefinitionReferenceError("path does not match value")
    return value


async def run_definition(
    deps: DefinitionRunDeps,
    definition: WorkflowDefinition,
    *,
    task_id: str,
    session_id: str,
    ai_user_id: str,
    initial_input: Mapping[str, Any],
    request_context: RequestOrgContext,
    retryable_error_codes: frozenset[ErrorCode],
    stop_error_codes: frozenset[ErrorCode],
    deadline: float,
    project_output: ProjectOutput,
) -> DefinitionRunResult:
    steps = definition.steps
    if not steps or any(not isinstance(step, DefinitionStep) for step in steps):
        raise ValueError("definition run requires DefinitionStep steps")
    loop = asyncio.get_running_loop()
    outcomes: dict[str, DefinitionStepOutcome] = {}
    projected: dict[str, dict[str, Any]] = {}

    async def append(event_type: str, payload: dict[str, Any]) -> None:
        await deps.task_store.append_event(
            task_id,
            TaskEventRecord(
                event_id=str(uuid4()),
                task_id=task_id,
                event_type=event_type,
                timestamp=datetime.now(UTC),
                payload=payload,
            ),
        )

    def state(step: DefinitionStep, index: int, status: str, **extra: Any) -> dict[str, Any]:
        return {
            "workflow_id": definition.workflow_id,
            "workflow_version": definition.version,
            "step_id": step.step_id,
            "step_index": index,
            "step_status": status,
            **extra,
        }

    def finish(
        status: DefinitionRunStatus,
        error_code: ErrorCode | None = None,
        failed_assertion: tuple[str, DefinitionAssertionOp] | None = None,
    ) -> DefinitionRunResult:
        for later in steps:
            outcomes.setdefault(later.step_id, DefinitionStepOutcome(later.step_id, "not_executed"))
        return DefinitionRunResult(
            workflow_id=definition.workflow_id,
            workflow_version=definition.version,
            trace_id=request_context.request_id,
            status=status,
            steps=dict(outcomes),
            error_code=error_code,
            failed_assertion=failed_assertion,
        )

    async def call(
        step: DefinitionStep, index: int, item: Any
    ) -> tuple[_CallStatus, dict[str, Any] | None, ErrorCode | None, int]:
        """One leaf call with retries: (status, projected data, error, attempts)."""

        try:
            arguments = deepcopy(dict(step.static_arguments))
            for name, ref in step.arguments.items():
                arguments[name] = deepcopy(resolve_ref(ref, initial_input, projected, item))
        except DefinitionReferenceError:
            return "failed", None, "adapter_missing_required_field", 0
        capability = await deps.registry.get(step.capability_id)
        if capability is None or capability.status != "active" or capability.type != "query":
            return "stop", None, "capability_not_found", 0
        attempt = 0
        while True:
            remaining = deadline - loop.time()
            if remaining < MIN_CALL_SECONDS:
                return "deadline", None, None, attempt
            attempt += 1
            if deps.human_gate is not None:
                await deps.human_gate.assert_task_bindings(
                    task_id, capability_version_bindings(capability)
                )
            await deps.trace_port.record_step(
                request_context.request_id,
                task_id,
                session_id,
                tenant_id=request_context.tenant_id,
                ai_user_id=ai_user_id,
                event_type="capability_selected",
                status="ok",
                capability_id=step.capability_id,
                attributes=state(
                    step, index, "running" if attempt == 1 else "retrying", attempt=attempt
                ),
            )
            try:
                async with asyncio.timeout(remaining):
                    execution = await deps.gateway.execute_capability(
                        task_id,
                        session_id,
                        ai_user_id,
                        step.capability_id,
                        arguments,
                        request_context,
                    )
            except TimeoutError:
                return "deadline", None, "adapter_timeout", attempt
            code = execution.error_code
            if execution.status == "completed":
                try:
                    return "ok", project_output(capability, execution), None, attempt
                except Exception:
                    # Projection decides what may be used downstream; never raw data.
                    return "failed", None, "adapter_payload_invalid", attempt
            if execution.status == "timeout" and code is None:
                code = "adapter_timeout"
            if code in stop_error_codes or execution.status in {"denied", "binding_required"}:
                return "stop", None, code or "policy_denied", attempt
            if code in retryable_error_codes and attempt < MAX_ATTEMPTS:
                continue
            return "failed", None, code or "internal_error", attempt

    await append(
        "workflow_started",
        {"workflow_id": definition.workflow_id, "workflow_version": definition.version},
    )
    complete_failure: tuple[str, DefinitionAssertionOp] | None = None
    for index, raw_step in enumerate(steps):
        assert isinstance(raw_step, DefinitionStep)
        step = raw_step
        if step.for_each is None:
            status, data, code, attempts = await call(step, index, None)
            if status != "ok" or data is None:
                outcomes[step.step_id] = DefinitionStepOutcome(
                    step.step_id,
                    "not_executed" if status == "deadline" and code is None else "failed",
                    error_code=code,
                    attempts=attempts,
                )
                await append(
                    "workflow_step_finished", state(step, index, "failed", error_code=code)
                )
                return finish("deadline" if status == "deadline" else "failed", code)
            projected[step.step_id] = data
            outcomes[step.step_id] = DefinitionStepOutcome(
                step.step_id, "ok", data=data, attempts=attempts
            )
        else:
            try:
                items = resolve_ref(step.for_each.items, initial_input, projected, None)
            except DefinitionReferenceError:
                items = None
            if not isinstance(items, list):
                outcomes[step.step_id] = DefinitionStepOutcome(
                    step.step_id, "failed", error_code="adapter_missing_required_field"
                )
                return finish("failed", "adapter_missing_required_field")
            if len(items) > step.for_each.max_items:
                return finish("over_limit", failed_assertion=(step.step_id, "max_count"))
            item_outcomes: list[DefinitionItemOutcome] = []
            stopped: DefinitionRunStatus | None = None
            stop_code: ErrorCode | None = None
            for position, item in enumerate(items):
                if stopped is not None:
                    item_outcomes.append(DefinitionItemOutcome(position, "not_executed"))
                    continue
                status, data, code, attempts = await call(step, index, item)
                if status == "ok":
                    item_outcomes.append(
                        DefinitionItemOutcome(position, "ok", data=data, attempts=attempts)
                    )
                    continue
                if status == "stop":
                    stopped, stop_code = "failed", code
                elif status == "deadline":
                    stopped = "deadline"
                item_outcomes.append(
                    DefinitionItemOutcome(
                        position,
                        "not_executed" if status == "deadline" and code is None else "failed",
                        error_code=code,
                        attempts=attempts,
                    )
                )
            outcomes[step.step_id] = DefinitionStepOutcome(
                step.step_id, "ok", items=tuple(item_outcomes)
            )
            await append(
                "workflow_step_finished",
                state(
                    step,
                    index,
                    "completed",
                    item_count=len(item_outcomes),
                    item_ok=sum(1 for o in item_outcomes if o.status == "ok"),
                    item_failed=sum(1 for o in item_outcomes if o.status == "failed"),
                ),
            )
            if stopped is not None:
                return finish(stopped, stop_code)
            continue
        await append("workflow_step_finished", state(step, index, "completed"))
        for assertion in step.assertions:
            try:
                value = resolve_ref(assertion.ref, initial_input, projected, None)
            except DefinitionReferenceError:
                value = None
            if assertion.op == "non_empty":
                if not isinstance(value, list) or not value:
                    return finish("empty", failed_assertion=(step.step_id, "non_empty"))
            elif assertion.op == "max_count":
                if (
                    not isinstance(value, list)
                    or assertion.value is None
                    or len(value) > assertion.value
                ):
                    return finish("over_limit", failed_assertion=(step.step_id, "max_count"))
            elif value is not True and complete_failure is None:
                # complete: the step's own completeness flag; the run continues and
                # the caller reports the result as possibly incomplete.
                complete_failure = (step.step_id, "complete")
    await append(
        "workflow_completed",
        {
            "workflow_id": definition.workflow_id,
            "workflow_version": definition.version,
            "workflow_status": "completed",
        },
    )
    return finish("completed", failed_assertion=complete_failure)


__all__ = (
    "DefinitionReferenceError",
    "DefinitionRunDeps",
    "MIN_CALL_SECONDS",
    "ProjectOutput",
    "resolve_ref",
    "run_definition",
)
