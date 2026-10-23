"""Read-only AgentScope tools over the existing orchestration and Gateway seams.

Each model tool call becomes one existing-style task: version manifest, Gateway
execution, approved projection and terminal status. Identity comes only from
the verified request session; the model supplies business arguments only.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from hashlib import sha256
from time import monotonic
from typing import Any
from uuid import uuid4

from agentscope.message import TextBlock, ToolResultState
from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.state import AgentState
from agentscope.tool import FunctionTool, ToolChunk, Toolkit

from app.infra.adapters.business_mcp.fixed_query_contracts import SERVICE_CONFIG_ID
from app.infra.llm.agentscope_chat import (
    current_turn_id,
    reserve_tool_execution,
    tool_halt_reason,
    turn_seconds_remaining,
)
from app.knowledge.capability_selection import (
    CandidateBinding,
    CapabilityCandidateSet,
    capability_fingerprint,
    is_safe_capability_id,
    select_capability_candidates,
)
from app.ports.agent_orchestration import AgentOrchestrationPort, AgentResponseContext
from app.ports.auth import AuthenticatedSessionContext, authenticated_session
from app.ports.capability_gateway import ExecutionResult, ExecutionStatus
from app.ports.capability_registry import CapabilityRegistryPort, CapabilitySpec
from app.ports.error_codes import ErrorCode
from app.ports.human_gate import (
    HumanGateConflictError,
    HumanGatePort,
    VersionBinding,
    VersionBindingMismatchError,
    build_task_version_binding_manifest,
)
from app.ports.policy_guard import CapabilityCandidatePolicyPort
from app.ports.request_context import RequestChannel, RequestOrgContext
from app.ports.response_projection_contract import (
    ApprovedResponseProjectionError,
    ProjectionContractSnapshot,
)
from app.ports.task_store import TaskRecord, TaskStatus, TaskStorePort
from app.ports.trace import TracePort
from app.version_binding import prompt_version_binding

# Reads approved for the M0 count: the two reads actually connected for the
# user (2026-10-22). OA login and profile are identity sources, not tools; the
# other OA and business MCP reads are never registered.
M0_READ_CAPABILITY_IDS = frozenset(
    {
        "oa.list_pending_workflows",
        f"business.{SERVICE_CONFIG_ID}.person_find",
    }
)
AGENT_PROMPT_RESOURCE_ID = "runtime.agentscope_agent"
AGENT_PROMPT_VERSION = "m0-1"
MAX_TOOL_RESULT_BYTES = 8192
_TOOL_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")
_READ_FAILED_TEXT = "读取失败。"
_CLOSE_TIMEOUT_SECONDS = 5.0
_PENDING_CLOSES: set[asyncio.Future[None]] = set()
_HALTED_TEXT = "本轮已停止工具调用，请先向用户澄清。"
_SCOPE_TEXT = "需要用户补充范围（账套、资源范围或设备域），请先向用户确认，不要猜测。"
_ALLOW = PermissionDecision(behavior=PermissionBehavior.ALLOW, message="approved read")

# Server-log convention shared with uvicorn.error.mcp_stages; code-owned values only.
_TOOL_LOGGER = logging.getLogger("uvicorn.error.agentscope_read_tools")


@dataclass(frozen=True, slots=True)
class AgentScopeReadDependencies:
    """The production instances the read bridge reuses; built by composition."""

    capability_registry: CapabilityRegistryPort
    candidate_policy: CapabilityCandidatePolicyPort
    orchestration: AgentOrchestrationPort
    task_store: TaskStorePort
    trace_port: TracePort
    human_gate: HumanGatePort


@dataclass(frozen=True, slots=True)
class ReadToolScope:
    tenant_id: str
    ai_user_id: str
    session_id: str
    channel: RequestChannel

    @classmethod
    def from_session(
        cls,
        session: AuthenticatedSessionContext,
        *,
        bound_session_id: str,
        channel: RequestChannel = "web",
    ) -> ReadToolScope:
        """Derive the scope from a verified session and a binder-bound session id."""

        return cls(
            tenant_id=session.principal.org_ctx.tenant_id,
            ai_user_id=session.principal.ai_user_id,
            session_id=bound_session_id,
            channel=channel,
        )


@dataclass(frozen=True, slots=True)
class ReadToolExecution:
    """Value-free record of one tool execution for counting and M1 mapping."""

    task_id: str
    capability_id: str
    status: ExecutionStatus
    error_code: str | None
    # Approved projection of a completed read, in memory only for in-process
    # judging; never logged or returned.
    projected: dict[str, Any] | None = field(default=None, repr=False, compare=False)


@dataclass(slots=True)
class ReadToolkit:
    toolkit: Toolkit
    selection: CapabilityCandidateSet
    exposed_schema_digests: dict[str, str]
    executions: list[ReadToolExecution] = field(default_factory=list)
    # Tool name -> schema exactly as exposed to the model and the SDK.
    exposed_schemas: dict[str, dict[str, Any]] = field(default_factory=dict)


async def build_read_toolkit(
    scope: ReadToolScope,
    deps: AgentScopeReadDependencies,
    message: str,
    *,
    model: str,
    system_prompt: str,
    allowed_capability_ids: frozenset[str] = M0_READ_CAPABILITY_IDS,
) -> ReadToolkit:
    """Register this request's visible, approved query capabilities as tools."""

    visible = await _visible_reads(scope, deps, allowed_capability_ids)
    selection = select_capability_candidates(message, visible)
    specs = {spec.capability_id: spec for spec in visible}
    toolkit = Toolkit()
    result = ReadToolkit(toolkit=toolkit, selection=selection, exposed_schema_digests={})
    if selection.outcome != "ready":
        return result
    tools: list[tuple[CandidateBinding, CapabilitySpec, str, dict[str, Any]]] = []
    for binding in selection.bindings:
        spec = specs[binding.capability_id]
        name = binding.capability_id.replace(".", "__")
        if _TOOL_NAME.fullmatch(name) is None:
            continue
        exposed = _exposed_schema(spec.input_schema)
        result.exposed_schema_digests[name] = _schema_digest(exposed)
        result.exposed_schemas[name] = deepcopy(exposed)
        tools.append((binding, spec, name, exposed))
    intent_binding = prompt_version_binding(
        resource_id=AGENT_PROMPT_RESOURCE_ID,
        version=AGENT_PROMPT_VERSION,
        model=model,
        prompts=(system_prompt,),
        response_schema={"tools": dict(sorted(result.exposed_schema_digests.items()))},
    )
    for binding, spec, name, exposed in tools:
        executor = _ReadExecutor(scope, deps, binding, intent_binding, result)
        await toolkit.add_tool(
            FunctionTool(
                executor.run,
                name=name,
                description=_description(spec),
                input_schema=exposed,
                is_concurrency_safe=False,
                is_read_only=True,
                is_state_injected=True,
                permission=_ALLOW,
            )
        )
    return result


async def _visible_reads(
    scope: ReadToolScope,
    deps: AgentScopeReadDependencies,
    allowed: frozenset[str],
) -> list[CapabilitySpec]:
    # Same visibility rule as the current Runtime: active, safe id, policy
    # preview "defer"; then only approved query capabilities.
    snapshot = await deps.capability_registry.list(status="active")
    request_context = RequestOrgContext(
        request_id=str(uuid4()), tenant_id=scope.tenant_id, channel=scope.channel
    )
    visible: list[CapabilitySpec] = []
    for item in sorted(snapshot, key=lambda capability: capability.capability_id):
        if (
            item.status != "active"
            or item.type != "query"
            or item.capability_id not in allowed
            or not is_safe_capability_id(item.capability_id)
        ):
            continue
        visibility = await deps.candidate_policy.preview_capability(
            ai_user_id=scope.ai_user_id,
            capability_id=item.capability_id,
            request_context=request_context.model_copy(deep=True),
        )
        if visibility == "defer":
            visible.append(item.model_copy(deep=True))
    return visible


def _exposed_schema(input_schema: dict[str, Any]) -> dict[str, Any]:
    # The SDK validates arguments before the tool runs. Gateway checks
    # `required` only after identity (binding-derived stage, GT-012), so the
    # model-facing schema drops it; every other keyword is kept as registered.
    exposed = deepcopy(input_schema)
    exposed.pop("required", None)
    return exposed


def _description(spec: CapabilitySpec) -> str:
    required = spec.input_schema.get("required")
    text = f"{spec.name}：{spec.short_description}"
    if isinstance(required, list) and required:
        text += "。必填参数：" + "、".join(str(item) for item in required)
        text += "；缺少时先向用户询问，不要猜测。"
    return text


def _schema_digest(schema: dict[str, Any]) -> str:
    canonical = json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()


def _optional_str_argument(arguments: dict[str, Any], key: str) -> str | None:
    value = arguments.get(key)
    return None if value is None else str(value)


def _task_status(status: ExecutionStatus) -> TaskStatus:
    # Same mapping as the current Runtime's task status projection.
    if status == "completed":
        return "completed"
    if status == "no_capability_found":
        return "no_capability_found"
    if status == "waiting_user":
        return "waiting_user"
    return "failed"


class _ReadExecutor:
    def __init__(
        self,
        scope: ReadToolScope,
        deps: AgentScopeReadDependencies,
        binding: CandidateBinding,
        intent_binding: VersionBinding,
        toolkit: ReadToolkit,
    ) -> None:
        self._scope = scope
        self._deps = deps
        self._binding = binding
        self._intent_binding = intent_binding
        self._toolkit = toolkit

    async def run(self, _agent_state: AgentState, **arguments: Any) -> ToolChunk:
        capability_id = self._binding.capability_id
        if tool_halt_reason(_agent_state) is not None:
            _log("halted", capability_id, "none", 0.0)
            return _chunk(_HALTED_TEXT, error_code="tool_use_halted")
        if not reserve_tool_execution(_agent_state):
            _log("budget", capability_id, "none", 0.0)
            return _chunk(_HALTED_TEXT, error_code="tool_execution_budget_exceeded")
        if not self._session_matches():
            _log("identity", capability_id, "internal_error", 0.0)
            return _chunk("当前登录会话无效。", error_code="internal_error")
        return await self._execute(capability_id, arguments)

    def _session_matches(self) -> bool:
        session = authenticated_session.get()
        return (
            session is not None
            and session.principal.ai_user_id == self._scope.ai_user_id
            and session.principal.org_ctx.tenant_id == self._scope.tenant_id
            and session.expires_at > datetime.now(UTC)
        )

    async def _execute(self, capability_id: str, arguments: dict[str, Any]) -> ToolChunk:
        # Every await from task creation to terminal close sits inside this
        # boundary: the SDK would hand str(exc) to the model, and a created
        # task must never be left running. The task id exists before the
        # create call, so every exit path closes it: a create that never
        # committed makes the close fail safely in update_status.
        scope, deps = self._scope, self._deps
        task_id, trace_id = str(uuid4()), str(uuid4())
        started = monotonic()
        execution: ExecutionResult | None = None
        text = _READ_FAILED_TEXT
        projected: dict[str, Any] | None = None
        try:
            await deps.task_store.create_task(
                TaskRecord(
                    task_id=task_id,
                    session_id=scope.session_id,
                    ai_user_id=scope.ai_user_id,
                    tenant_id=scope.tenant_id,
                    status="running",
                    trace_id=trace_id,
                    capability_id=capability_id,
                )
            )
            await deps.trace_port.start_task_trace(
                trace_id, task_id, scope.session_id,
                tenant_id=scope.tenant_id, ai_user_id=scope.ai_user_id,
            )
            await deps.trace_port.record_step(
                trace_id, task_id, scope.session_id,
                tenant_id=scope.tenant_id, ai_user_id=scope.ai_user_id,
                event_type="task_created", status="ok",
            )
            execution, text, projected = await self._run_task(
                task_id, trace_id, capability_id, arguments
            )
        except asyncio.CancelledError:
            await self._close_safely(
                task_id, trace_id, capability_id,
                execution or _failed("internal_error", trace_id),
            )
            _log("cancelled", capability_id, "internal_error", started,
                 task_id=task_id, trace_id=trace_id)
            raise
        except Exception:
            execution, text = _failed("internal_error", trace_id), _READ_FAILED_TEXT
            projected = None
        if execution is None:
            execution = _failed("internal_error", trace_id)
        if not await self._close_safely(task_id, trace_id, capability_id, execution):
            # A failed terminal write is reported as a failure, never as success.
            execution, text = _failed("internal_error", trace_id), _READ_FAILED_TEXT
            projected = None
        self._toolkit.executions.append(
            ReadToolExecution(
                task_id, capability_id, execution.status, execution.error_code,
                projected if execution.status == "completed" else None,
            )
        )
        _log(execution.status, capability_id, execution.error_code or "none", started,
             task_id=task_id, trace_id=trace_id)
        if execution.status == "completed":
            return _chunk(text, error_code=None, task_id=task_id)
        return _chunk(text, error_code=execution.error_code or execution.status, task_id=task_id)

    async def _close_safely(
        self,
        task_id: str,
        trace_id: str,
        capability_id: str,
        execution: ExecutionResult,
    ) -> bool:
        # The deadline lives inside the shielded close itself, so it still
        # holds after the waiter is cancelled; a module-level set keeps the
        # task referenced until it finishes. Cancellation still propagates.
        progress = _CloseProgress(task_id, trace_id, capability_id, monotonic())
        closing = asyncio.ensure_future(self._bounded_close(progress, execution))
        _PENDING_CLOSES.add(closing)
        closing.add_done_callback(partial(_finish_close, progress))
        # The wait also fits the turn's absolute budget: once it is spent the
        # caller stops waiting (and a pending cancellation propagates) while
        # the close keeps its own deadline.
        remaining = turn_seconds_remaining()
        wait = _CLOSE_TIMEOUT_SECONDS if remaining is None else min(
            _CLOSE_TIMEOUT_SECONDS, remaining
        )
        try:
            await asyncio.wait_for(asyncio.shield(closing), wait)
        except asyncio.CancelledError:
            if not closing.done():
                _log_close(progress, "waiting", "turn_cancelled", "none")
            raise
        except Exception:
            if not closing.done():
                # The turn stopped waiting; the close goes on in the background
                # and _finish_close logs how it ends.
                _log_close(progress, "waiting", "turn_budget", "none")
            return False
        return True

    async def _bounded_close(self, progress: _CloseProgress, execution: ExecutionResult) -> None:
        async with asyncio.timeout(_CLOSE_TIMEOUT_SECONDS):
            await self._close_task(
                progress.task_id, progress.trace_id, progress.capability_id, execution,
                progress=progress,
            )

    async def _run_task(
        self,
        task_id: str,
        trace_id: str,
        capability_id: str,
        arguments: dict[str, Any],
    ) -> tuple[ExecutionResult, str, dict[str, Any] | None]:
        scope, deps = self._scope, self._deps
        selection = await deps.orchestration.select_capability(
            capability_id=capability_id, target_system=None, capability_type=None
        )
        if (
            selection is None
            or selection.rule != "exact_id"
            or selection.capability.type != "query"
            or not _matches_binding(selection.capability, self._binding)
        ):
            stale = _failed("capability_candidate_stale", trace_id)
            return stale, "能力定义已变化，请重新提问。", None
        capability = selection.capability.model_copy(deep=True)
        projection = ProjectionContractSnapshot.from_capability(capability)
        try:
            resolved = await deps.orchestration.resolve_task_version_bindings(
                capability=capability, intent_version_binding=self._intent_binding
            )
            manifest = build_task_version_binding_manifest(
                task_id=task_id, bindings=resolved.bindings, locked_at=datetime.now(UTC)
            )
            matching = tuple(
                binding for binding in manifest.bindings
                if binding.resource_type == "tool" and binding.resource_id == capability_id
            )
            if not projection.matches(capability) or matching != (resolved.projection_binding,):
                raise VersionBindingMismatchError("projection binding differs from capability")
            await deps.human_gate.bind_task(manifest)
        except (HumanGateConflictError, VersionBindingMismatchError, ValueError):
            return _failed("internal_error", trace_id), "版本绑定失败。", None
        await deps.trace_port.record_step(
            trace_id, task_id, scope.session_id,
            tenant_id=scope.tenant_id, ai_user_id=scope.ai_user_id,
            event_type="capability_selected", status="ok", capability_id=capability_id,
            attributes={"selection_rule": "exact_id"},
        )
        request_context = RequestOrgContext(
            request_id=trace_id,
            channel=scope.channel,
            tenant_id=scope.tenant_id,
            account_set_id=_optional_str_argument(arguments, "account_set_id"),
            resource_scope=_optional_str_argument(arguments, "resource_scope"),
            device_domain_id=_optional_str_argument(arguments, "device_domain_id"),
        )
        try:
            execution = await deps.orchestration.execute_capability(
                task_id=task_id,
                session_id=scope.session_id,
                ai_user_id=scope.ai_user_id,
                capability=capability,
                arguments=arguments,
                request_context=request_context,
            )
        except VersionBindingMismatchError:
            execution = _failed("internal_error", trace_id)
        context = AgentResponseContext(
            response_id=str(uuid4()), task_id=task_id, session_id=scope.session_id,
            trace_id=trace_id, capability_id=capability_id,
        )
        try:
            envelope = deps.orchestration.build_response(
                context=context, execution=execution, projection=projection
            )
        except ApprovedResponseProjectionError:
            execution = _failed("adapter_payload_invalid", trace_id)
            envelope = deps.orchestration.build_response(
                context=context, execution=execution, projection=projection
            )
        await deps.trace_port.record_step(
            trace_id, task_id, scope.session_id,
            tenant_id=scope.tenant_id, ai_user_id=scope.ai_user_id,
            event_type="response_envelope_created", status="ok",
        )
        if execution.error_code == "needs_binding_scope":
            # The envelope above carries the existing scope clarification; the
            # model gets a fixed instruction to ask instead of guessing.
            return execution, _SCOPE_TEXT, None
        return (
            execution,
            _model_text(execution, envelope.fallback_text, envelope.data),
            envelope.data,
        )

    async def _close_task(
        self,
        task_id: str,
        trace_id: str,
        capability_id: str,
        execution: ExecutionResult,
        *,
        progress: _CloseProgress | None = None,
    ) -> None:
        scope, deps = self._scope, self._deps
        status = _task_status(execution.status)
        await deps.task_store.update_status(task_id, status, execution.error_code)
        if progress is not None:
            progress.phase = "trace_step"
        if execution.status != "waiting_user":
            completed = execution.status == "completed"
            await deps.trace_port.record_step(
                trace_id, task_id, scope.session_id,
                tenant_id=scope.tenant_id, ai_user_id=scope.ai_user_id,
                event_type="task_completed" if completed else "task_failed",
                status="ok" if completed else "failed",
                capability_id=capability_id, error_code=execution.error_code,
            )
        if progress is not None:
            progress.phase = "trace_finalize"
        await deps.trace_port.finalize_task_trace(
            trace_id, task_id, scope.session_id,
            tenant_id=scope.tenant_id, ai_user_id=scope.ai_user_id,
            status=(
                "ok" if status in {"completed", "waiting_user"}
                else "blocked" if status == "no_capability_found" else "failed"
            ),
            capability_id=capability_id, error_code=execution.error_code,
        )


def _finish_close(progress: _CloseProgress, task: asyncio.Future[None]) -> None:
    # Drop the strong reference and retrieve any failure, which asyncio's
    # default handler would otherwise log with its exception text; log only
    # the phase, a symbolic reason, the exception class and the real duration.
    _PENDING_CLOSES.discard(task)
    if task.cancelled():
        _log_close(progress, "failed", "cancelled", "none")
        return
    error = task.exception()
    if error is None:
        progress.phase = "done"
        _log_close(progress, "ok", "none", "none")
    elif isinstance(error, TimeoutError):
        _log_close(progress, "failed", "timeout", "TimeoutError")
    else:
        _log_close(progress, "failed", "storage_error", type(error).__name__)


def _matches_binding(capability: CapabilitySpec, binding: CandidateBinding) -> bool:
    return (
        capability.status == "active"
        and capability.capability_id == binding.capability_id
        and capability.version == binding.version
        and capability_fingerprint(capability) == binding.fingerprint
    )


def _failed(error_code: ErrorCode, trace_id: str) -> ExecutionResult:
    return ExecutionResult(status="failed", error_code=error_code, trace_id=trace_id)


def _model_text(execution: ExecutionResult, message: str, data: dict[str, Any] | None) -> str:
    # Only the approved projection reaches the model, never raw provider data.
    payload = {"status": execution.status, "message": message, "data": data}
    text = json.dumps(payload, ensure_ascii=False, default=str)
    raw = text.encode("utf-8")
    if len(raw) <= MAX_TOOL_RESULT_BYTES:
        return text
    cut = raw[:MAX_TOOL_RESULT_BYTES].decode("utf-8", errors="ignore")
    return cut + "\n[结果已截断，未展示部分不得推断]"


def _chunk(text: str, *, error_code: str | None, task_id: str | None = None) -> ToolChunk:
    # Metadata stays inside the agent (the formatter never sends it to the
    # model); the task id lets in-process judging pair a call with its read.
    metadata = {} if task_id is None else {"task_id": task_id}
    if error_code is None:
        return ToolChunk(
            content=[TextBlock(text=text)], state=ToolResultState.SUCCESS, metadata=metadata
        )
    return ToolChunk(
        content=[TextBlock(text=text)],
        state=ToolResultState.ERROR,
        metadata={**metadata, "error_code": error_code},
    )


def _log(
    result: str,
    capability_id: str,
    error_code: str,
    started: float,
    *,
    task_id: str = "none",
    trace_id: str = "none",
) -> None:
    # Code-owned values only: approved capability id, status, error code,
    # timing and the turn/task/trace ids that tie this line to its records.
    _TOOL_LOGGER.info(
        "agentscope_read_tool result=%s capability=%s error=%s elapsed_ms=%s "
        "turn=%s task=%s trace=%s",
        result,
        capability_id if capability_id in M0_READ_CAPABILITY_IDS else "other",
        error_code,
        round((monotonic() - started) * 1000) if started else 0,
        current_turn_id(),
        task_id,
        trace_id,
    )


@dataclass(slots=True)
class _CloseProgress:
    task_id: str
    trace_id: str
    capability_id: str
    started: float
    # Write in progress: task_status -> trace_step -> trace_finalize -> done.
    phase: str = "task_status"


def _log_close(progress: _CloseProgress, result: str, reason: str, error_class: str) -> None:
    # A failure in phase task_status means the task row keeps its running
    # status (known storage-fault limitation); later phases mean the task row
    # was already closed and only Trace writes are missing.
    _TOOL_LOGGER.info(
        "agentscope_read_tool_close result=%s phase=%s reason=%s error_class=%s "
        "elapsed_ms=%s capability=%s turn=%s task=%s trace=%s",
        result,
        progress.phase,
        reason,
        error_class,
        round((monotonic() - progress.started) * 1000),
        progress.capability_id if progress.capability_id in M0_READ_CAPABILITY_IDS else "other",
        current_turn_id(),
        progress.task_id,
        progress.trace_id,
    )


__all__ = (
    "M0_READ_CAPABILITY_IDS",
    "AgentScopeReadDependencies",
    "ReadToolExecution",
    "ReadToolScope",
    "ReadToolkit",
    "build_read_toolkit",
)
