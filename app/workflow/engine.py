"""Strictly linear Workflow execution through the frozen Gateway boundary."""

from __future__ import annotations

from copy import deepcopy
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
    WorkflowStep,
)


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

        step_outputs: dict[str, dict[str, Any]] = {}
        final_output: dict[str, Any] = {}
        for index, step in enumerate(definition.steps):
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

            await self._trace_port.record_step(
                request_context.request_id,
                task_id,
                session_id,
                event_type="capability_selected",
                status="ok",
                capability_id=step.capability_id,
                attributes=_step_state(definition, step, index, "running"),
            )
            execution = await self._gateway.execute_capability(
                task_id,
                session_id,
                ai_user_id,
                step.capability_id,
                arguments,
                request_context,
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
            _workflow_state(definition),
        )
        return WorkflowRunResult(
            workflow_id=definition.workflow_id,
            workflow_version=definition.version,
            trace_id=request_context.request_id,
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


def _workflow_state(definition: WorkflowDefinition) -> dict[str, Any]:
    return {
        "workflow_id": definition.workflow_id,
        "workflow_version": definition.version,
    }


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


__all__ = ("WorkflowEngine",)
