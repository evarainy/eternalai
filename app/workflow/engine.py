"""Strictly linear Workflow execution through the frozen Gateway boundary."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping
from uuid import uuid4

from app.ports.capability_gateway import CapabilityGatewayPort, RequestOrgContext
from app.ports.capability_registry import CapabilityRegistryPort
from app.ports.task_store import TaskEventRecord, TaskStorePort
from app.ports.trace import TracePort
from app.workflow.models import (
    WorkflowDefinition,
    WorkflowInputRef,
    WorkflowRunResult,
    WorkflowRunStatus,
    WorkflowStep,
)


@dataclass(frozen=True)
class _WorkflowCheckpoint:
    definition: WorkflowDefinition
    waiting_step_index: int
    task_id: str
    session_id: str
    ai_user_id: str
    initial_input: dict[str, Any]
    request_context: RequestOrgContext
    step_outputs: dict[str, dict[str, Any]]


class WorkflowEngine:
    """Execute a version-locked tuple of steps without graph semantics."""

    def __init__(
        self,
        *,
        definitions: Mapping[str, WorkflowDefinition],
        capability_registry: CapabilityRegistryPort,
        gateway: CapabilityGatewayPort,
        task_store: TaskStorePort,
        trace_port: TracePort,
    ) -> None:
        self._definitions = definitions
        self._capability_registry = capability_registry
        self._gateway = gateway
        self._task_store = task_store
        self._trace_port = trace_port
        self._checkpoints: dict[str, _WorkflowCheckpoint] = {}

    async def execute(
        self,
        *,
        workflow_id: str,
        expected_version: str,
        task_id: str,
        session_id: str,
        ai_user_id: str,
        initial_input: Mapping[str, Any],
        request_context: RequestOrgContext,
    ) -> WorkflowRunResult:
        definition = self._snapshot_definition(workflow_id, expected_version)
        await self._validate_steps(definition)
        await self._append_state(
            task_id,
            "workflow_started",
            _workflow_state(definition),
        )

        return await self._run_steps(
            definition=definition,
            start_index=0,
            confirmed_step_index=None,
            task_id=task_id,
            session_id=session_id,
            ai_user_id=ai_user_id,
            initial_input=initial_input,
            request_context=request_context,
            step_outputs={},
        )

    async def resume(
        self,
        *,
        task_id: str,
        confirmed: bool,
    ) -> WorkflowRunResult:
        if confirmed is not True:
            raise ValueError("explicit Workflow confirmation is required")
        checkpoint = self._checkpoints.get(task_id)
        if checkpoint is None:
            raise ValueError("no waiting Workflow checkpoint exists for task")

        waiting_step = checkpoint.definition.steps[checkpoint.waiting_step_index]
        await self._validate_confirmed_capability(waiting_step)
        await self._append_state(
            task_id,
            "workflow_resumed",
            _resume_state(
                checkpoint.definition,
                waiting_step,
                checkpoint.waiting_step_index,
            ),
        )
        result = await self._run_steps(
            definition=checkpoint.definition,
            start_index=checkpoint.waiting_step_index,
            confirmed_step_index=checkpoint.waiting_step_index,
            task_id=checkpoint.task_id,
            session_id=checkpoint.session_id,
            ai_user_id=checkpoint.ai_user_id,
            initial_input=checkpoint.initial_input,
            request_context=checkpoint.request_context,
            step_outputs=deepcopy(checkpoint.step_outputs),
        )
        if result.status != "waiting_confirm":
            self._checkpoints.pop(task_id, None)
        return result

    async def _run_steps(
        self,
        *,
        definition: WorkflowDefinition,
        start_index: int,
        confirmed_step_index: int | None,
        task_id: str,
        session_id: str,
        ai_user_id: str,
        initial_input: Mapping[str, Any],
        request_context: RequestOrgContext,
        step_outputs: dict[str, dict[str, Any]],
    ) -> WorkflowRunResult:

        final_output: dict[str, Any] = {}
        for index in range(start_index, len(definition.steps)):
            step = definition.steps[index]
            if step.when is not None and not self._condition_matches(
                step.when.value,
                step.when.equals,
                initial_input,
                step_outputs,
            ):
                await self._trace_port.record_step(
                    request_context.request_id,
                    task_id,
                    session_id,
                    event_type="capability_selected",
                    status="skipped",
                    capability_id=step.capability_id,
                    attributes=_step_state(definition, step, index, "skipped"),
                )
                await self._append_state(
                    task_id,
                    "workflow_step_finished",
                    _step_state(definition, step, index, "skipped"),
                )
                continue

            arguments = deepcopy(dict(step.static_arguments))
            for argument_name, value_ref in step.input_mapping.items():
                arguments[argument_name] = self._resolve_value(
                    value_ref,
                    initial_input,
                    step_outputs,
                )

            capability_id = step.capability_id
            if index == confirmed_step_index:
                if step.confirmed_capability_id is None:
                    raise ValueError("waiting Workflow step has no confirmed capability")
                capability_id = step.confirmed_capability_id

            await self._trace_port.record_step(
                request_context.request_id,
                task_id,
                session_id,
                event_type="capability_selected",
                status="ok",
                capability_id=capability_id,
                attributes=_step_state(definition, step, index, "running"),
            )
            execution = await self._gateway.execute_capability(
                task_id,
                session_id,
                ai_user_id,
                capability_id,
                arguments,
                request_context,
            )
            if execution.status == "waiting_user":
                if index == confirmed_step_index:
                    raise RuntimeError("confirmed Workflow capability requested confirmation again")
                if step.confirmed_capability_id is None:
                    raise ValueError("waiting Workflow step must declare a confirmed capability")
                await self._append_state(
                    task_id,
                    "workflow_step_finished",
                    _step_state(definition, step, index, "waiting_confirm"),
                )
                await self._append_state(
                    task_id,
                    "workflow_waiting_confirm",
                    _checkpoint_state(
                        definition,
                        step,
                        index,
                        initial_input,
                        step_outputs,
                    ),
                )
                self._checkpoints[task_id] = _WorkflowCheckpoint(
                    definition=deepcopy(definition),
                    waiting_step_index=index,
                    task_id=task_id,
                    session_id=session_id,
                    ai_user_id=ai_user_id,
                    initial_input=deepcopy(dict(initial_input)),
                    request_context=deepcopy(request_context),
                    step_outputs=deepcopy(step_outputs),
                )
                return WorkflowRunResult(
                    workflow_id=definition.workflow_id,
                    workflow_version=definition.version,
                    trace_id=request_context.request_id,
                    status="waiting_confirm",
                    output={},
                    step_outputs=step_outputs,
                )
            if execution.status == "denied":
                await self._append_state(
                    task_id,
                    "workflow_step_finished",
                    _step_state(definition, step, index, "denied"),
                )
                await self._append_state(
                    task_id,
                    "workflow_completed",
                    _workflow_state(definition, "denied"),
                )
                return WorkflowRunResult(
                    workflow_id=definition.workflow_id,
                    workflow_version=definition.version,
                    trace_id=request_context.request_id,
                    status="denied",
                    output={},
                    step_outputs=step_outputs,
                )
            if execution.status != "completed":
                raise RuntimeError("non-completed Workflow step handling is reserved for P1-B4-004")

            final_output = deepcopy(execution.data or {})
            step_outputs[step.step_id] = final_output
            terminal_state = _step_state(definition, step, index, "completed")
            await self._append_state(
                task_id,
                "workflow_step_finished",
                terminal_state,
            )

        await self._append_state(
            task_id,
            "workflow_completed",
            _workflow_state(definition, "completed"),
        )
        return WorkflowRunResult(
            workflow_id=definition.workflow_id,
            workflow_version=definition.version,
            trace_id=request_context.request_id,
            status="completed",
            output=final_output,
            step_outputs=step_outputs,
        )

    def _snapshot_definition(
        self,
        workflow_id: str,
        expected_version: str,
    ) -> WorkflowDefinition:
        source = self._definitions.get(workflow_id)
        if source is None or source.version != expected_version:
            raise ValueError("registered Workflow version is unavailable")
        return deepcopy(source)

    async def _validate_steps(self, definition: WorkflowDefinition) -> None:
        seen_step_ids: set[str] = set()
        for step in definition.steps:
            if not step.step_id or step.step_id in seen_step_ids:
                raise ValueError("Workflow step ids must be non-empty and unique")
            references = list(step.input_mapping.values())
            if step.when is not None:
                references.append(step.when.value)
            for value_ref in references:
                if value_ref.source == "step_output" and value_ref.step_id not in seen_step_ids:
                    raise ValueError(
                        "Workflow step_output references must target a strictly earlier step"
                    )
            seen_step_ids.add(step.step_id)
            capability = await self._capability_registry.get(step.capability_id)
            if (
                capability is None
                or capability.status != "active"
                or capability.risk_level != "low"
                or capability.type == "workflow"
            ):
                raise ValueError(
                    "each Workflow step must reference an active low-risk registered capability"
                )
            if step.confirmed_capability_id is not None:
                await self._validate_confirmed_capability(step)

    async def _validate_confirmed_capability(self, step: WorkflowStep) -> None:
        confirmed_capability_id = step.confirmed_capability_id
        if confirmed_capability_id is None or confirmed_capability_id == step.capability_id:
            raise ValueError(
                "confirmed Workflow capability must differ from the waiting capability"
            )
        confirmed_capability = await self._capability_registry.get(confirmed_capability_id)
        if (
            confirmed_capability is None
            or confirmed_capability.status != "active"
            or confirmed_capability.risk_level != "low"
            or confirmed_capability.type == "workflow"
        ):
            raise ValueError(
                "confirmed Workflow capability must be active, low-risk, "
                "registered, and non-workflow"
            )

    def _condition_matches(
        self,
        value_ref: WorkflowInputRef,
        expected: Any,
        initial_input: Mapping[str, Any],
        step_outputs: Mapping[str, Mapping[str, Any]],
    ) -> bool:
        return bool(
            self._resolve_value(
                value_ref,
                initial_input,
                step_outputs,
            )
            == expected
        )

    def _resolve_value(
        self,
        value_ref: WorkflowInputRef,
        initial_input: Mapping[str, Any],
        step_outputs: Mapping[str, Mapping[str, Any]],
    ) -> Any:
        if value_ref.source == "workflow_input":
            if value_ref.step_id is not None or value_ref.key not in initial_input:
                raise ValueError("invalid Workflow input mapping")
            return initial_input[value_ref.key]
        if value_ref.step_id is None:
            raise ValueError("step output mapping requires step_id")
        output = step_outputs.get(value_ref.step_id)
        if output is None or value_ref.key not in output:
            raise ValueError("Workflow step output is unavailable")
        return output[value_ref.key]

    async def _append_state(
        self,
        task_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        await self._task_store.append_event(
            task_id,
            TaskEventRecord(
                event_id=str(uuid4()),
                task_id=task_id,
                event_type=event_type,
                timestamp=datetime.now(UTC),
                payload=payload,
            ),
        )


def _workflow_state(
    definition: WorkflowDefinition,
    status: WorkflowRunStatus | None = None,
) -> dict[str, Any]:
    state: dict[str, Any] = {
        "workflow_id": definition.workflow_id,
        "workflow_version": definition.version,
    }
    if status is not None:
        state["workflow_status"] = status
    return state


def _step_state(
    definition: WorkflowDefinition,
    step: WorkflowStep,
    index: int,
    status: str,
) -> dict[str, Any]:
    return {
        "workflow_id": definition.workflow_id,
        "workflow_version": definition.version,
        "step_id": step.step_id,
        "step_index": index,
        "step_status": status,
    }


def _resume_state(
    definition: WorkflowDefinition,
    step: WorkflowStep,
    index: int,
) -> dict[str, Any]:
    return {
        "workflow_id": definition.workflow_id,
        "workflow_version": definition.version,
        "waiting_step_id": step.step_id,
        "waiting_step_index": index,
        "confirmed_capability_id": step.confirmed_capability_id,
    }


def _checkpoint_state(
    definition: WorkflowDefinition,
    waiting_step: WorkflowStep,
    waiting_step_index: int,
    initial_input: Mapping[str, Any],
    step_outputs: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    recovery_input_keys: set[str] = set()
    step_output_keys: dict[str, set[str]] = {}
    for step in definition.steps[waiting_step_index:]:
        references = list(step.input_mapping.values())
        if step.when is not None:
            references.append(step.when.value)
        for value_ref in references:
            if value_ref.source == "workflow_input":
                if value_ref.key in initial_input:
                    recovery_input_keys.add(value_ref.key)
                continue
            if value_ref.step_id is None or value_ref.step_id not in step_outputs:
                continue
            if value_ref.key in step_outputs[value_ref.step_id]:
                step_output_keys.setdefault(value_ref.step_id, set()).add(value_ref.key)

    return {
        "workflow_id": definition.workflow_id,
        "workflow_version": definition.version,
        "waiting_step_id": waiting_step.step_id,
        "waiting_step_index": waiting_step_index,
        "confirmed_capability_id": waiting_step.confirmed_capability_id,
        "completed_step_ids": list(step_outputs),
        "step_output_keys": {step_id: sorted(keys) for step_id, keys in step_output_keys.items()},
        "recovery_input_keys": sorted(recovery_input_keys),
    }


__all__ = ("WorkflowEngine",)
