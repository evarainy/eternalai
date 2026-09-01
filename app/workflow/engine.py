"""Strictly linear Workflow execution through the frozen Gateway boundary."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from hmac import compare_digest
from typing import Any, Mapping
from uuid import uuid4

from app.ports.capability_gateway import (
    CapabilityGatewayPort,
    ErrorCode,
    RequestOrgContext,
)
from app.ports.capability_registry import CapabilityRegistryPort, CapabilitySpec
from app.ports.human_gate import (
    HumanGatePort,
    VersionBinding,
    VersionBindingMismatchError,
)
from app.ports.task_store import TaskEventRecord, TaskStorePort
from app.ports.trace import TracePort
from app.version_binding import (
    capability_version_bindings,
    merge_version_bindings,
    workflow_confirmation_action_digest,
    workflow_version_binding,
)
from app.workflow.models import (
    WorkflowDefinition,
    WorkflowInputRef,
    WorkflowRunResult,
    WorkflowRunStatus,
    WorkflowStep,
)

RETRYABLE_ERROR_CODES: frozenset[ErrorCode] = frozenset({"adapter_timeout"})
MAX_STEP_RETRIES = 1


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


@dataclass(frozen=True)
class _WorkflowVersionBindingsSnapshot:
    """Bindings and the exact top-level binding derived from one definition snapshot."""

    bindings: tuple[VersionBinding, ...]
    workflow_binding: VersionBinding


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
        human_gate_port: HumanGatePort | None = None,
    ) -> None:
        self._definitions = definitions
        self._capability_registry = capability_registry
        self._gateway = gateway
        self._task_store = task_store
        self._trace_port = trace_port
        self._human_gate_port = human_gate_port
        self._checkpoints: dict[str, _WorkflowCheckpoint] = {}

    def configure_human_gate_port(self, human_gate_port: HumanGatePort) -> None:
        """Attach the shared Task-binding store before the first execution."""

        if self._checkpoints:
            raise RuntimeError("HumanGatePort cannot change after Workflow execution")
        if self._human_gate_port is not None and self._human_gate_port is not human_gate_port:
            raise RuntimeError("WorkflowEngine already has a different HumanGatePort")
        self._human_gate_port = human_gate_port

    async def version_bindings(
        self,
        *,
        workflow_capability: CapabilitySpec,
    ) -> _WorkflowVersionBindingsSnapshot:
        """Resolve the complete immutable tuple before a new Task can execute."""

        definition = self._snapshot_definition(
            workflow_capability.capability_id,
            workflow_capability.version,
        )
        workflow_binding = workflow_version_binding(workflow_capability, definition)
        bindings = await self._definition_version_bindings(
            definition,
            workflow_capability=workflow_capability,
            workflow_binding=workflow_binding,
        )
        return _WorkflowVersionBindingsSnapshot(
            bindings=bindings,
            workflow_binding=workflow_binding,
        )

    async def resume_version_bindings(
        self,
        *,
        task_id: str,
    ) -> tuple[VersionBinding, ...]:
        """Resolve what the saved checkpoint would execute, not the latest definition."""

        checkpoint = self._checkpoints.get(task_id)
        if checkpoint is None:
            raise ValueError("no waiting Workflow checkpoint exists for task")
        return await self._definition_version_bindings(checkpoint.definition)

    def discard_checkpoint(self, task_id: str) -> None:
        self._checkpoints.pop(task_id, None)

    def pending_confirmation_action_digest(self, task_id: str) -> str:
        """Recompute the exact resolved action saved in the waiting checkpoint."""

        checkpoint = self._checkpoints.get(task_id)
        if checkpoint is None:
            raise VersionBindingMismatchError("No waiting Workflow action is available")
        step = checkpoint.definition.steps[checkpoint.waiting_step_index]
        if step.confirmed_capability_id is None:
            raise VersionBindingMismatchError("Waiting Workflow action has no confirmed capability")
        arguments = deepcopy(dict(step.static_arguments))
        for argument_name, value_ref in step.input_mapping.items():
            arguments[argument_name] = self._resolve_value(
                value_ref,
                checkpoint.initial_input,
                checkpoint.step_outputs,
            )
        return workflow_confirmation_action_digest(
            workflow_id=checkpoint.definition.workflow_id,
            workflow_version=checkpoint.definition.version,
            step_id=step.step_id,
            step_index=checkpoint.waiting_step_index,
            waiting_capability_id=step.capability_id,
            confirmed_capability_id=step.confirmed_capability_id,
            arguments=arguments,
        )

    async def execute(
        self,
        *,
        workflow_id: str,
        expected_version: str,
        workflow_capability: CapabilitySpec,
        task_id: str,
        session_id: str,
        ai_user_id: str,
        initial_input: Mapping[str, Any],
        request_context: RequestOrgContext,
    ) -> WorkflowRunResult:
        definition = self._snapshot_definition(workflow_id, expected_version)
        self._validate_workflow_capability(workflow_capability, definition)
        if self._human_gate_port is None:
            await self._validate_steps(definition)
        else:
            bindings = await self._definition_version_bindings(
                definition,
                workflow_capability=workflow_capability,
            )
            await self._human_gate_port.assert_task_bindings(task_id, bindings)
        await self._append_state(
            task_id,
            "workflow_started",
            _workflow_state(definition),
        )

        try:
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
        except (ValueError, VersionBindingMismatchError) as exc:
            if not isinstance(exc, VersionBindingMismatchError) and self._human_gate_port is None:
                raise
            await self._append_state(
                task_id,
                "workflow_failed",
                _workflow_state(
                    definition,
                    "failed",
                    error_code="internal_error",
                ),
            )
            if isinstance(exc, VersionBindingMismatchError):
                raise
            raise VersionBindingMismatchError("Bound Workflow capability is unavailable") from exc

    async def resume(
        self,
        *,
        task_id: str,
        confirmed: bool,
        expected_action_digest: str | None = None,
    ) -> WorkflowRunResult:
        if confirmed is not True:
            raise ValueError("explicit Workflow confirmation is required")
        checkpoint = self._checkpoints.get(task_id)
        if checkpoint is None:
            raise ValueError("no waiting Workflow checkpoint exists for task")

        try:
            if expected_action_digest is not None and not compare_digest(
                expected_action_digest,
                self.pending_confirmation_action_digest(task_id),
            ):
                raise VersionBindingMismatchError("Pending Workflow action changed after preview")
            if self._human_gate_port is not None:
                bindings = await self._definition_version_bindings(checkpoint.definition)
                await self._human_gate_port.assert_task_bindings(task_id, bindings)
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
        except (ValueError, VersionBindingMismatchError) as exc:
            if (
                not isinstance(exc, VersionBindingMismatchError)
                and self._human_gate_port is None
                and expected_action_digest is None
            ):
                raise
            self._checkpoints.pop(task_id, None)
            await self._append_state(
                task_id,
                "workflow_failed",
                _workflow_state(
                    checkpoint.definition,
                    "failed",
                    error_code="internal_error",
                ),
            )
            if isinstance(exc, VersionBindingMismatchError):
                raise
            raise VersionBindingMismatchError(
                "confirmed Workflow capability or action binding is unavailable"
            ) from exc
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
                    tenant_id=request_context.tenant_id,
                    ai_user_id=ai_user_id,
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

            attempt = 1
            max_attempts = MAX_STEP_RETRIES + 1
            while True:
                step_status = "running" if attempt == 1 else "retrying"
                if self._human_gate_port is not None:
                    current_capability = await self._capability_registry.get(capability_id)
                    if current_capability is None or current_capability.type == "workflow":
                        raise VersionBindingMismatchError(
                            "Bound Workflow capability is unavailable"
                        )
                    await self._human_gate_port.assert_task_bindings(
                        task_id,
                        capability_version_bindings(current_capability),
                    )
                await self._trace_port.record_step(
                    request_context.request_id,
                    task_id,
                    session_id,
                    tenant_id=request_context.tenant_id,
                    ai_user_id=ai_user_id,
                    event_type="capability_selected",
                    status="ok",
                    capability_id=capability_id,
                    attributes=_step_state(
                        definition,
                        step,
                        index,
                        step_status,
                        attempt=attempt,
                        max_attempts=max_attempts,
                    ),
                )
                execution = await self._gateway.execute_capability(
                    task_id,
                    session_id,
                    ai_user_id,
                    capability_id,
                    arguments,
                    request_context,
                )
                retryable_failure = (
                    execution.status in {"failed", "timeout"}
                    and execution.error_code in RETRYABLE_ERROR_CODES
                )
                if not retryable_failure or attempt > MAX_STEP_RETRIES:
                    break
                attempt += 1

            if execution.status == "waiting_user":
                if index == confirmed_step_index:
                    raise RuntimeError("confirmed Workflow capability requested confirmation again")
                if step.confirmed_capability_id is None:
                    raise ValueError("waiting Workflow step must declare a confirmed capability")
                await self._append_state(
                    task_id,
                    "workflow_step_finished",
                    _step_state(
                        definition,
                        step,
                        index,
                        "waiting_confirm",
                        attempt=attempt,
                        max_attempts=max_attempts,
                        error_code=execution.error_code,
                    ),
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
                    error_code=execution.error_code,
                )
            if execution.status == "denied":
                await self._append_state(
                    task_id,
                    "workflow_step_finished",
                    _step_state(
                        definition,
                        step,
                        index,
                        "denied",
                        attempt=attempt,
                        max_attempts=max_attempts,
                        error_code=execution.error_code,
                    ),
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
                    error_code=execution.error_code,
                )
            if execution.status != "completed":
                workflow_status: WorkflowRunStatus = (
                    "timeout" if execution.status == "timeout" else "failed"
                )
                await self._append_state(
                    task_id,
                    "workflow_step_finished",
                    _step_state(
                        definition,
                        step,
                        index,
                        workflow_status,
                        attempt=attempt,
                        max_attempts=max_attempts,
                        error_code=execution.error_code,
                    ),
                )
                await self._append_state(
                    task_id,
                    "workflow_failed",
                    _workflow_state(
                        definition,
                        workflow_status,
                        error_code=execution.error_code,
                    ),
                )
                return WorkflowRunResult(
                    workflow_id=definition.workflow_id,
                    workflow_version=definition.version,
                    trace_id=request_context.request_id,
                    status=workflow_status,
                    output={},
                    step_outputs=step_outputs,
                    error_code=execution.error_code,
                )

            final_output = deepcopy(execution.data or {})
            step_outputs[step.step_id] = final_output
            terminal_state = _step_state(
                definition,
                step,
                index,
                "completed",
                attempt=attempt,
                max_attempts=max_attempts,
            )
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

    async def _definition_version_bindings(
        self,
        definition: WorkflowDefinition,
        *,
        workflow_capability: CapabilitySpec | None = None,
        workflow_binding: VersionBinding | None = None,
    ) -> tuple[VersionBinding, ...]:
        try:
            await self._validate_steps(definition)
        except ValueError as exc:
            raise VersionBindingMismatchError("Bound Workflow capability is unavailable") from exc
        if workflow_capability is None:
            workflow_capability = await self._capability_registry.get(definition.workflow_id)
        if workflow_capability is None:
            raise VersionBindingMismatchError("Registered Workflow binding is unavailable")
        self._validate_workflow_capability(workflow_capability, definition)
        if workflow_binding is None:
            workflow_binding = workflow_version_binding(workflow_capability, definition)
        groups: list[tuple[VersionBinding, ...]] = [(workflow_binding,)]
        capability_ids: set[str] = set()
        for step in definition.steps:
            capability_ids.add(step.capability_id)
            if step.confirmed_capability_id is not None:
                capability_ids.add(step.confirmed_capability_id)
        for capability_id in sorted(capability_ids):
            capability = await self._capability_registry.get(capability_id)
            if capability is None or capability.type == "workflow":
                raise VersionBindingMismatchError("Bound Workflow capability is unavailable")
            groups.append(capability_version_bindings(capability))
        return merge_version_bindings(*groups)

    @staticmethod
    def _validate_workflow_capability(
        capability: CapabilitySpec,
        definition: WorkflowDefinition,
    ) -> None:
        if (
            capability.status != "active"
            or capability.type != "workflow"
            or capability.capability_id != definition.workflow_id
            or capability.version != definition.version
        ):
            raise VersionBindingMismatchError("Registered Workflow binding is unavailable")

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
    *,
    error_code: ErrorCode | None = None,
) -> dict[str, Any]:
    state: dict[str, Any] = {
        "workflow_id": definition.workflow_id,
        "workflow_version": definition.version,
    }
    if status is not None:
        state["workflow_status"] = status
    if error_code is not None:
        state["error_code"] = error_code
    return state


def _step_state(
    definition: WorkflowDefinition,
    step: WorkflowStep,
    index: int,
    status: str,
    *,
    attempt: int | None = None,
    max_attempts: int | None = None,
    error_code: ErrorCode | None = None,
) -> dict[str, Any]:
    state: dict[str, Any] = {
        "workflow_id": definition.workflow_id,
        "workflow_version": definition.version,
        "step_id": step.step_id,
        "step_index": index,
        "step_status": status,
    }
    if attempt is not None:
        state["attempt"] = attempt
        state["retry_number"] = attempt - 1
    if max_attempts is not None:
        state["max_attempts"] = max_attempts
    if error_code is not None:
        state["error_code"] = error_code
    return state


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
