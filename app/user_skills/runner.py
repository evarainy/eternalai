"""The single execution path of a personal flow (chat, trial, run API, worker).

The runner is the only owner of the task lifecycle: admission, task and trace
creation, version-binding manifest, the definition run and the close. Entries
only hand over trusted context.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Any, Literal, Protocol
from uuid import uuid4

from jsonschema import Draft202012Validator, FormatChecker

from app.knowledge.capability_selection import capability_fingerprint
from app.ports.capability_gateway import ErrorCode, ExecutionResult
from app.ports.capability_registry import CapabilityRegistryPort, CapabilitySpec
from app.ports.human_gate import HumanGatePort, build_task_version_binding_manifest
from app.ports.policy_guard import CapabilityCandidatePolicyPort
from app.ports.request_context import RequestChannel, RequestOrgContext
from app.ports.response_projection_contract import canonical_schema_digest
from app.ports.task_store import TaskRecord, TaskStorePort
from app.ports.trace import TracePort
from app.user_skills.definition import UserSkillDefinition, compile_definition, params_schema
from app.user_skills.rendering import MAX_DISPLAY_ROWS, render_result
from app.version_binding import (
    capability_version_bindings,
    merge_version_bindings,
    workflow_version_binding,
)
from app.workflow.models import DefinitionRunResult, WorkflowDefinition

_LOGGER = logging.getLogger("uvicorn.error.user_skills")

RETRYABLE: frozenset[ErrorCode] = frozenset(
    {"adapter_timeout", "adapter_http_500", "adapter_error", "adapter_empty_response"}
)
STOP: frozenset[ErrorCode] = frozenset(
    {
        "identity_unbound",
        "identity_expired",
        "identity_revoked",
        "needs_binding_scope",
        "policy_denied",
        "upstream_permission_denied",
    }
)
BROWSER_PREFIX = "browser."

Entry = Literal["chat", "trial", "run", "worker"]
PauseReason = Literal["capability_changed", "capability_unavailable"]


class DefinitionEngine(Protocol):
    async def execute_definition(
        self,
        definition: WorkflowDefinition,
        *,
        workflow_capability: CapabilitySpec,
        task_id: str,
        session_id: str,
        ai_user_id: str,
        initial_input: Mapping[str, Any],
        request_context: RequestOrgContext,
        retryable_error_codes: frozenset[ErrorCode],
        stop_error_codes: frozenset[ErrorCode],
        deadline: float,
        project_output: Callable[[CapabilitySpec, ExecutionResult], dict[str, Any]],
    ) -> DefinitionRunResult: ...


class TaskCloser(Protocol):
    async def __call__(
        self,
        *,
        task_id: str,
        trace_id: str,
        session_id: str,
        tenant_id: str,
        ai_user_id: str,
        capability_id: str,
        completed: bool,
        error_code: ErrorCode | None,
    ) -> None: ...


@dataclass(frozen=True)
class RunTarget:
    """What to run: a saved version, or a trial snapshot (``skill_id`` None)."""

    workflow_id: str
    version_label: str
    name: str
    purpose: str
    definition: UserSkillDefinition
    skill_id: str | None = None
    version: int | None = None


@dataclass(frozen=True)
class RunContext:
    tenant_id: str
    ai_user_id: str
    session_id: str
    channel: RequestChannel
    entry: Entry
    deadline: float  # event-loop time
    roles: tuple[str, ...] = ()
    turn_task_id: str | None = None
    job_id: str | None = None


@dataclass(frozen=True)
class Admission:
    leaves: tuple[CapabilitySpec, ...] = ()
    error_code: ErrorCode | None = None
    pause: PauseReason | None = None


@dataclass(frozen=True)
class RunOutcome:
    """``result`` is the ``kind == "result"`` payload; ``pause`` asks the caller to pause."""

    run_id: str
    task_id: str | None
    status: Literal["completed", "partial", "failed"]
    result: dict[str, Any]
    error_code: ErrorCode | None = None
    pause: PauseReason | None = None
    run_result: DefinitionRunResult | None = None


class InputInvalid(ValueError):
    """Inputs do not satisfy the compiled parameter schema."""

    def __init__(self, missing: Sequence[str]) -> None:
        super().__init__("user skill input invalid")
        self.missing = tuple(missing)


def check_inputs(definition: UserSkillDefinition, inputs: Mapping[str, Any]) -> None:
    schema = params_schema(definition)
    errors = list(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(dict(inputs))
    )
    if not errors:
        return
    required = set(schema["required"])
    missing = sorted(name for name in required if inputs.get(name) in (None, ""))
    raise InputInvalid(missing)


class UserSkillRunner:
    def __init__(
        self,
        *,
        registry: CapabilityRegistryPort,
        policy: CapabilityCandidatePolicyPort,
        task_store: TaskStorePort,
        trace_port: TracePort,
        human_gate: HumanGatePort,
        engine: DefinitionEngine,
        project_output: Callable[[CapabilitySpec, ExecutionResult], dict[str, Any]],
        closer: TaskCloser,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._registry = registry
        self._policy = policy
        self._task_store = task_store
        self._trace = trace_port
        self._human_gate = human_gate
        self._engine = engine
        self._project = project_output
        self._close = closer
        self._clock = clock

    async def admit(self, definition: UserSkillDefinition, ctx: RunContext) -> Admission:
        """Read-only admission of every leaf, for this user, right now."""

        request_context = self._request_context(ctx)
        leaves: list[CapabilitySpec] = []
        for step in definition.steps:
            spec = await self._registry.get(step.capability_id)
            if (
                spec is None
                or spec.status != "active"
                or spec.type != "query"
                or spec.execution_identity != "user_delegated"
                or spec.capability_id.startswith(BROWSER_PREFIX)
            ):
                return Admission(error_code="capability_not_found", pause="capability_unavailable")
            if capability_fingerprint(spec) != step.capability_fingerprint:
                return Admission(
                    error_code="capability_candidate_stale", pause="capability_changed"
                )
            verdict = await self._policy.preview_capability(
                ai_user_id=ctx.ai_user_id,
                capability_id=spec.capability_id,
                request_context=request_context.model_copy(deep=True),
            )
            if verdict != "defer":
                return Admission(error_code="policy_denied")
            leaves.append(spec)
        return Admission(leaves=tuple(leaves))

    async def run(
        self,
        target: RunTarget,
        inputs: Mapping[str, Any],
        ctx: RunContext,
        *,
        max_rows: int | None = MAX_DISPLAY_ROWS,
    ) -> RunOutcome:
        run_id = str(uuid4())
        started = monotonic()
        check_inputs(target.definition, inputs)
        admission = await self.admit(target.definition, ctx)
        if admission.error_code is not None:
            self._log(ctx, target, "refused", admission.error_code, started)
            return RunOutcome(
                run_id,
                None,
                "failed",
                render_result(
                    target,
                    inputs,
                    None,
                    run_id=run_id,
                    error_code=admission.error_code,
                    queried_at=self._clock(),
                ),
                error_code=admission.error_code,
                pause=admission.pause,
            )
        task_id, trace_id = str(uuid4()), str(uuid4())
        workflow = self._workflow_spec(target, admission.leaves)
        definition = compile_definition(
            target.definition, workflow_id=target.workflow_id, version=target.version_label
        )
        await self._task_store.create_task(
            TaskRecord(
                task_id=task_id,
                session_id=ctx.session_id,
                ai_user_id=ctx.ai_user_id,
                tenant_id=ctx.tenant_id,
                status="running",
                trace_id=trace_id,
                capability_id=target.workflow_id,
            )
        )
        completed = False
        error_code: ErrorCode | None = "internal_error"
        run_result: DefinitionRunResult | None = None
        try:
            await self._trace.start_task_trace(
                trace_id,
                task_id,
                ctx.session_id,
                tenant_id=ctx.tenant_id,
                ai_user_id=ctx.ai_user_id,
            )
            attributes: dict[str, Any] = {"entry": ctx.entry}
            if ctx.turn_task_id is not None:
                attributes["turn_task_id"] = ctx.turn_task_id
            if ctx.job_id is not None:
                attributes["job_id"] = ctx.job_id
            await self._trace.record_step(
                trace_id,
                task_id,
                ctx.session_id,
                tenant_id=ctx.tenant_id,
                ai_user_id=ctx.ai_user_id,
                event_type="task_created",
                status="ok",
                attributes=attributes,
            )
            bindings = merge_version_bindings(
                (workflow_version_binding(workflow, definition),),
                *(capability_version_bindings(leaf) for leaf in admission.leaves),
            )
            await self._human_gate.bind_task(
                build_task_version_binding_manifest(
                    task_id=task_id, bindings=bindings, locked_at=self._clock()
                )
            )
            request_context = self._request_context(ctx, request_id=trace_id)
            run_result = await self._engine.execute_definition(
                definition,
                workflow_capability=workflow,
                task_id=task_id,
                session_id=ctx.session_id,
                ai_user_id=ctx.ai_user_id,
                initial_input=dict(inputs),
                request_context=request_context,
                retryable_error_codes=RETRYABLE,
                stop_error_codes=STOP,
                deadline=ctx.deadline,
                project_output=self._project,
            )
            result = render_result(
                target,
                inputs,
                run_result,
                run_id=run_id,
                error_code=run_result.error_code,
                queried_at=self._clock(),
                max_rows=max_rows,
            )
            error_code = result.get("error_code") or run_result.error_code
            completed = result["status"] != "failed"
        finally:
            # Every exit, cancellation included, closes the task before leaving.
            await asyncio.shield(
                self._close(
                    task_id=task_id,
                    trace_id=trace_id,
                    session_id=ctx.session_id,
                    tenant_id=ctx.tenant_id,
                    ai_user_id=ctx.ai_user_id,
                    capability_id=target.workflow_id,
                    completed=completed,
                    error_code=None if completed else (error_code or "internal_error"),
                )
            )
        assert run_result is not None
        status = result["status"]
        self._log(ctx, target, status, error_code, started, run_result)
        return RunOutcome(
            run_id, task_id, status, result, error_code=error_code, run_result=run_result
        )

    def _request_context(
        self, ctx: RunContext, *, request_id: str | None = None
    ) -> RequestOrgContext:
        return RequestOrgContext(
            request_id=request_id or str(uuid4()),
            channel=ctx.channel,
            tenant_id=ctx.tenant_id,
            roles=list(ctx.roles),
        )

    def _workflow_spec(self, target: RunTarget, leaves: Sequence[CapabilitySpec]) -> CapabilitySpec:
        """In-memory workflow descriptor for binding only; never registered."""

        input_schema = params_schema(target.definition)
        output_schema: dict[str, Any] = {"type": "object"}
        systems = {leaf.target_system for leaf in leaves}
        return CapabilitySpec(
            capability_id=target.workflow_id,
            name=target.name,
            type="workflow",
            input_schema=input_schema,
            output_schema=output_schema,
            input_schema_digest=canonical_schema_digest(input_schema),
            output_schema_digest=canonical_schema_digest(output_schema),
            risk_level="low",
            owner="user_skill",
            version=target.version_label,
            status="active",
            short_description=target.purpose,
            target_system=next(iter(systems)) if len(systems) == 1 else None,
            execution_identity="user_delegated",
            binding_required=any(leaf.binding_required for leaf in leaves),
        )

    def _log(
        self,
        ctx: RunContext,
        target: RunTarget,
        status: str,
        error_code: str | None,
        started: float,
        run_result: DefinitionRunResult | None = None,
    ) -> None:
        counts: dict[str, int] = {"ok": 0, "failed": 0, "not_executed": 0}
        if run_result is not None:
            for outcome in run_result.steps.values():
                for item in outcome.items or ():
                    counts[item.status] += 1
        # Value-free: ids, counts, status and codes only.
        _LOGGER.info(
            "user_skill.run entry=%s skill=%s version=%s steps=%d items_ok=%d "
            "items_failed=%d items_not_executed=%d status=%s error=%s elapsed_ms=%d",
            ctx.entry,
            target.skill_id or "trial",
            target.version_label,
            len(target.definition.steps),
            counts["ok"],
            counts["failed"],
            counts["not_executed"],
            status,
            error_code or "none",
            int((monotonic() - started) * 1000),
        )


__all__ = (
    "Admission",
    "InputInvalid",
    "RunContext",
    "RunOutcome",
    "RunTarget",
    "TaskCloser",
    "UserSkillRunner",
    "check_inputs",
)
