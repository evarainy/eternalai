"""Read-only AgentScope tools over the existing orchestration and Gateway seams.

Each business tool call runs the existing task core: version manifest, Gateway
execution, required postconditions, approved projection and terminal status.
In the M1 runtime the first executed call of a turn runs on the turn task and
later calls get their own task. Identity comes only from the verified request
session; the model supplies business arguments only.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from hashlib import sha256
from hmac import compare_digest
from time import monotonic
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from agentscope.message import TextBlock, ToolResultState
from agentscope.state import AgentState
from agentscope.tool import ToolChunk

from app.evaluator import TerminalBusinessStatus, TerminalEvaluator
from app.evaluator.overview import OverviewPostconditionEvaluator, evaluate_required_postconditions
from app.evaluator.trace import terminal_evaluation_attributes
from app.infra.llm.agentscope_chat import (
    TurnLimits,
    current_turn_id,
    halt_tool_use,
    reserve_tool_execution,
    tool_halt_reason,
    turn_seconds_remaining,
)
from app.infra.orchestration.agentscope_runtime.ledger import (
    BindingCard,
    CallSnapshot,
    ClosingTask,
    ExecutionMode,
    LedgerExecution,
    LedgerFailure,
    TurnLedger,
    current_tool_call_id,
)
from app.infra.sdui.response_envelope_builder import ResponseEnvelopeBuilder
from app.knowledge import BasicKnowledge
from app.knowledge.capability_selection import (
    CandidateBinding,
    capability_fingerprint,
)
from app.ports.agent_orchestration import AgentOrchestrationPort, AgentResponseContext
from app.ports.auth import AuthenticatedSessionContext, authenticated_session
from app.ports.capability_gateway import ExecutionResult, ExecutionStatus
from app.ports.capability_registry import CapabilityRegistryPort, CapabilitySpec
from app.ports.error_codes import ErrorCode
from app.ports.evaluation import BusinessVerification, EvaluationScope
from app.ports.human_gate import (
    HumanGateConflictError,
    HumanGatePort,
    VersionBindingMismatchError,
    build_task_version_binding_manifest,
)
from app.ports.policy_guard import CapabilityCandidatePolicyPort
from app.ports.request_context import RequestChannel, RequestOrgContext
from app.ports.response_envelope import ResponseEnvelope
from app.ports.response_projection_contract import (
    ApprovedResponseProjectionError,
    ProjectionContractSnapshot,
)
from app.ports.task_store import (
    SessionStorePort,
    TaskEventRecord,
    TaskRecord,
    TaskStatus,
    TaskStorePort,
)
from app.ports.trace import TracePort

if TYPE_CHECKING:
    from agentscope.model import ChatModelBase

MAX_TOOL_RESULT_BYTES = 8192
_READ_FAILED_TEXT = "读取失败。"
_CLOSE_TIMEOUT_SECONDS = 5.0
_PENDING_CLOSES: set[asyncio.Future[None]] = set()
_HALTED_TEXT = "本轮已停止工具调用，请先向用户澄清。"

# Server-log convention shared with uvicorn.error.mcp_stages; code-owned values only.
_TOOL_LOGGER = logging.getLogger("uvicorn.error.agentscope_read_tools")


@dataclass(frozen=True, slots=True)
class AgentScopeRuntimeDependencies:
    """Production instances the M1 runtime and its executor reuse."""

    capability_registry: CapabilityRegistryPort
    candidate_policy: CapabilityCandidatePolicyPort
    orchestration: AgentOrchestrationPort
    task_store: TaskStorePort
    session_store: SessionStorePort
    trace_port: TracePort
    human_gate: HumanGatePort
    response_builder: ResponseEnvelopeBuilder
    knowledge: BasicKnowledge
    terminal_evaluator: TerminalEvaluator
    overview_evaluator: OverviewPostconditionEvaluator
    model_factory: Callable[[], ChatModelBase]
    model_name: str
    session_idle_ttl_seconds: int


@dataclass(frozen=True, slots=True)
class ReadToolScope:
    tenant_id: str
    ai_user_id: str
    session_id: str
    channel: RequestChannel
    # Verified login-ticket fingerprint; compared, never logged.
    fingerprint: bytes = field(repr=False)

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
            fingerprint=session.fingerprint,
        )


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


_NOT_EXPOSED_TEXT = "该工具未在本次调用中提供，请先激活对应工具组。"
_CALL_UNKNOWN_TEXT = "当前工具调用无法定位，请重新提问。"
_SESSION_INVALID_TEXT = "当前登录会话无效。"


@dataclass(frozen=True, slots=True)
class TaskClose:
    """Terminal writes of one task: status, terminal event, evaluation, finalize."""

    task_id: str
    trace_id: str
    capability_id: str | None
    execution: ExecutionResult
    raw_execution: ExecutionResult | None = None
    verification: BusinessVerification | None = None
    # Only the turn task records response_envelope_created, once, first.
    envelope_created: bool = False


def close_once(
    ledger: TurnLedger,
    deps: AgentScopeRuntimeDependencies,
    scope: ReadToolScope,
    close: TaskClose,
) -> ClosingTask:
    """Start the single bounded close of a task, or return the one already started.

    The deadline lives inside the close itself, so it holds after a waiter is
    cancelled; the module-level set keeps the close referenced until it ends.
    """

    existing = ledger.closes.get(close.task_id)
    if existing is not None:
        return existing
    progress = _CloseProgress(
        close.task_id, close.trace_id, close.capability_id or "none", monotonic(),
        allowed=frozenset(ledger.business_tools.values()),
    )
    future = asyncio.ensure_future(_bounded_m1_close(deps, scope, close, progress))
    _PENDING_CLOSES.add(future)
    future.add_done_callback(partial(_finish_close, progress))
    closing = ClosingTask(close.task_id, future)
    ledger.closes[close.task_id] = closing
    return closing


async def await_close(closing: ClosingTask, remaining: float | None) -> bool:
    """Wait for a close within min(5 s, remaining turn time); never cancels it."""

    wait = _CLOSE_TIMEOUT_SECONDS if remaining is None else min(_CLOSE_TIMEOUT_SECONDS, remaining)
    try:
        await asyncio.wait_for(asyncio.shield(closing.future), wait)
    except asyncio.CancelledError:
        if not closing.future.done():
            _log_close_wait(closing.task_id, "turn_cancelled")
        raise
    except Exception:
        # Timed out or the close itself failed; _finish_close logs how it ends.
        if not closing.future.done():
            _log_close_wait(closing.task_id, "turn_budget")
        return False
    return True


async def _bounded_m1_close(
    deps: AgentScopeRuntimeDependencies,
    scope: ReadToolScope,
    close: TaskClose,
    progress: _CloseProgress,
) -> None:
    async with asyncio.timeout(_CLOSE_TIMEOUT_SECONDS):
        await _write_close(deps, scope, close, progress)


async def _write_close(
    deps: AgentScopeRuntimeDependencies,
    scope: ReadToolScope,
    close: TaskClose,
    progress: _CloseProgress,
) -> None:
    # Same order as the legacy Runtime terminal: envelope event, task status,
    # terminal event, evaluation, finalize.
    execution = close.execution
    ids = (close.trace_id, close.task_id, scope.session_id)
    if close.envelope_created:
        await deps.trace_port.record_step(
            *ids, tenant_id=scope.tenant_id, ai_user_id=scope.ai_user_id,
            event_type="response_envelope_created", status="ok",
        )
    status = _task_status(execution.status)
    await deps.task_store.update_status(close.task_id, status, execution.error_code)
    progress.phase = "trace_step"
    if execution.status != "waiting_user":
        completed = execution.status == "completed"
        await deps.trace_port.record_step(
            *ids, tenant_id=scope.tenant_id, ai_user_id=scope.ai_user_id,
            event_type="task_completed" if completed else "task_failed",
            status="ok" if completed else "failed",
            capability_id=close.capability_id, error_code=execution.error_code,
        )
        progress.phase = "evaluation"
        evaluation_status, attributes = terminal_evaluation_attributes(
            deps.terminal_evaluator,
            business_status=cast(TerminalBusinessStatus, execution.status),
            error_code=execution.error_code,
            capability_id=close.capability_id,
            raw_execution=close.raw_execution,
            business_verification=close.verification,
        )
        await deps.trace_port.record_step(
            *ids, tenant_id=scope.tenant_id, ai_user_id=scope.ai_user_id,
            event_type="evaluation_recorded", status=evaluation_status,
            capability_id=close.capability_id, error_code=execution.error_code,
            attributes=attributes,
        )
    progress.phase = "trace_finalize"
    await deps.trace_port.finalize_task_trace(
        *ids, tenant_id=scope.tenant_id, ai_user_id=scope.ai_user_id,
        status=(
            "ok" if status in {"completed", "waiting_user"}
            else "blocked" if status == "no_capability_found" else "failed"
        ),
        capability_id=close.capability_id, error_code=execution.error_code,
    )


@dataclass(frozen=True, slots=True)
class _TaskOutcome:
    execution: ExecutionResult
    raw_execution: ExecutionResult
    verification: BusinessVerification | None
    envelope: ResponseEnvelope
    text: str


class ToolExecutor:
    """Run one business tool of the M1 catalog on the turn task or its own task.

    The first call that passes every pre-check claims the turn task (attach);
    the runtime closes that task. Every later call creates, runs and closes
    its own task (own). Results go to the turn ledger; the runtime summarizes.
    """

    def __init__(
        self,
        scope: ReadToolScope,
        deps: AgentScopeRuntimeDependencies,
        ledger: TurnLedger,
        binding: CandidateBinding,
        name: str,
        limits: TurnLimits,
    ) -> None:
        self._scope = scope
        self._deps = deps
        self._ledger = ledger
        self._binding = binding
        self._name = name
        self._limits = limits
        self._allowed = frozenset(ledger.business_tools.values())

    async def run(self, _agent_state: AgentState, **arguments: Any) -> ToolChunk:
        ledger = self._ledger
        if tool_halt_reason(_agent_state) is not None:
            # A consequence of an earlier failure or terminal; not recorded again.
            self._log("halted", "tool_use_halted", 0.0)
            return _chunk(_HALTED_TEXT, error_code="tool_use_halted")
        call_id = current_tool_call_id.get()
        snapshot = ledger.snapshot_for(call_id)
        if call_id is None or snapshot is None:
            return self._reject(call_id or "none", "internal_error", _CALL_UNKNOWN_TEXT)
        if self._name not in snapshot.capability_fingerprints:
            # Activated and called in the same response: the framework allows
            # it, but the model never saw this tool. A discovery error.
            self._log("not_exposed", "tool_not_exposed", 0.0)
            return _chunk(_NOT_EXPOSED_TEXT, error_code="tool_not_exposed")
        if not reserve_tool_execution(_agent_state, self._limits):
            return self._reject(call_id, "tool_execution_budget_exceeded", _HALTED_TEXT)
        if not self._session_matches():
            return self._reject(call_id, "internal_error", _SESSION_INVALID_TEXT)
        # Claimed synchronously, with no await since the check.
        if not ledger.turn_task_claimed:
            ledger.turn_task_claimed = True
            return await self._attach(_agent_state, call_id, snapshot, arguments)
        return await self._own(_agent_state, call_id, snapshot, arguments)

    def _reject(self, call_id: str, error_code: str, text: str) -> ToolChunk:
        self._ledger.record_pre_exec(call_id, self._name, error_code, "execution_rejected")
        self._log("rejected", error_code, 0.0)
        return _chunk(text, error_code=error_code)

    def _session_matches(self) -> bool:
        session = authenticated_session.get()
        return (
            session is not None
            and session.principal.ai_user_id == self._scope.ai_user_id
            and session.principal.org_ctx.tenant_id == self._scope.tenant_id
            and session.expires_at > datetime.now(UTC)
            and bool(self._scope.fingerprint)
            and compare_digest(session.fingerprint, self._scope.fingerprint)
        )

    async def _attach(
        self,
        state: AgentState,
        call_id: str,
        snapshot: CallSnapshot,
        arguments: dict[str, Any],
    ) -> ToolChunk:
        scope, deps, ledger = self._scope, self._deps, self._ledger
        binding = self._binding
        started = monotonic()
        attributes: dict[str, Any] = {
            "result": "valid",
            "match": "capability",
            "intent_fingerprint": sha256(binding.capability_id.encode("utf-8")).hexdigest(),
            **ledger.catalog_attributes,
        }
        if binding.target_system is not None:
            attributes["target_system"] = binding.target_system
        attributes["capability_type"] = binding.capability_type
        # The SDK would hand str(exc) to the model; the runtime closes the
        # turn task on every exit, including cancellation.
        try:
            await deps.trace_port.record_step(
                ledger.trace_id, ledger.task_id, scope.session_id,
                tenant_id=scope.tenant_id, ai_user_id=scope.ai_user_id,
                event_type="intent_parsed", status="ok", attributes=attributes,
            )
            outcome = await self._run_task(
                ledger.task_id, ledger.trace_id, ledger.response_id, snapshot, arguments
            )
        except Exception:
            ledger.failures.append(
                LedgerFailure("execution", call_id, self._name, "internal_error")
            )
            self._log("failed", "internal_error", started, task_id=ledger.task_id)
            return _chunk(_READ_FAILED_TEXT, error_code="internal_error", task_id=ledger.task_id)
        return self._record(state, call_id, "attach", ledger.task_id, outcome, started)

    async def _own(
        self,
        state: AgentState,
        call_id: str,
        snapshot: CallSnapshot,
        arguments: dict[str, Any],
    ) -> ToolChunk:
        # Every await from task creation to terminal close sits inside this
        # boundary. The task id exists before the create call, so every exit
        # closes it: a create that never committed fails safely in the close.
        scope, deps, ledger = self._scope, self._deps, self._ledger
        capability_id = self._binding.capability_id
        task_id, trace_id = str(uuid4()), str(uuid4())
        started = monotonic()
        outcome: _TaskOutcome | None = None
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
                attributes={"turn_task_id": ledger.task_id, "turn_id": current_turn_id()},
            )
            outcome = await self._run_task(task_id, trace_id, str(uuid4()), snapshot, arguments)
        except asyncio.CancelledError:
            execution = outcome.execution if outcome else _failed("internal_error", trace_id)
            closing = close_once(
                ledger, deps, scope, TaskClose(task_id, trace_id, capability_id, execution)
            )
            await await_close(closing, turn_seconds_remaining())
            self._log("cancelled", "internal_error", started, task_id=task_id, trace_id=trace_id)
            raise
        except Exception:
            outcome = None
        if outcome is None:
            outcome = self._failed_outcome(task_id, trace_id, str(uuid4()), "internal_error",
                                           _READ_FAILED_TEXT)
        closing = close_once(
            ledger, deps, scope,
            TaskClose(
                task_id, trace_id, capability_id, outcome.execution,
                raw_execution=outcome.raw_execution, verification=outcome.verification,
            ),
        )
        if not await await_close(closing, turn_seconds_remaining()):
            # A failed terminal write is reported as a failure, never as success.
            outcome = self._failed_outcome(
                task_id, trace_id, outcome.envelope.response_id, "internal_error",
                _READ_FAILED_TEXT,
            )
        return self._record(state, call_id, "own", task_id, outcome, started, trace_id=trace_id)

    def _record(
        self,
        state: AgentState,
        call_id: str,
        mode: ExecutionMode,
        task_id: str,
        outcome: _TaskOutcome,
        started: float,
        *,
        trace_id: str = "none",
    ) -> ToolChunk:
        ledger, execution = self._ledger, outcome.execution
        index = len(ledger.executions)
        completed = execution.status == "completed"
        ledger.executions.append(
            LedgerExecution(
                task_id=task_id,
                mode=mode,
                capability_id=self._binding.capability_id,
                execution=execution,
                envelope=outcome.envelope,
                raw_execution=outcome.raw_execution,
                verification=outcome.verification,
                projected=outcome.envelope.data if completed else None,
            )
        )
        self._log(execution.status, execution.error_code or "none", started,
                  task_id=task_id, trace_id=trace_id)
        if completed:
            return _chunk(outcome.text, error_code=None, task_id=task_id)
        code = execution.error_code or execution.status
        ledger.failures.append(LedgerFailure("execution", call_id, self._name, code, index))
        if execution.status == "binding_required" and ledger.terminal is None:
            # The binding card ends the turn: no further model reasoning.
            ledger.terminal = BindingCard(index)
            halt_tool_use(state, "turn_terminal")
        return _chunk(outcome.text, error_code=code, task_id=task_id)

    def _failed_outcome(
        self,
        task_id: str,
        trace_id: str,
        response_id: str,
        error_code: ErrorCode,
        text: str,
    ) -> _TaskOutcome:
        execution = _failed(error_code, trace_id)
        envelope = self._deps.orchestration.build_response(
            context=self._context(response_id, task_id, trace_id),
            execution=execution,
            projection=None,
        )
        return _TaskOutcome(execution, execution, None, envelope, text)

    def _context(self, response_id: str, task_id: str, trace_id: str) -> AgentResponseContext:
        return AgentResponseContext(
            response_id=response_id, task_id=task_id, session_id=self._scope.session_id,
            trace_id=trace_id, capability_id=self._binding.capability_id,
        )

    async def _run_task(
        self,
        task_id: str,
        trace_id: str,
        response_id: str,
        snapshot: CallSnapshot,
        arguments: dict[str, Any],
    ) -> _TaskOutcome:
        # Neither mode records response_envelope_created or closes here.
        scope, deps = self._scope, self._deps
        capability_id = self._binding.capability_id
        selection = await deps.orchestration.select_capability(
            capability_id=capability_id, target_system=None, capability_type=None
        )
        if (
            selection is None
            or selection.rule != "exact_id"
            or selection.capability.type != "query"
            or not _matches_binding(selection.capability, self._binding)
            or capability_fingerprint(selection.capability)
            != snapshot.capability_fingerprints[self._name]
        ):
            return self._failed_outcome(
                task_id, trace_id, response_id, "capability_candidate_stale",
                "能力定义已变化，请重新提问。",
            )
        capability = selection.capability.model_copy(deep=True)
        projection = ProjectionContractSnapshot.from_capability(capability)
        try:
            resolved = await deps.orchestration.resolve_task_version_bindings(
                capability=capability, intent_version_binding=snapshot.binding
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
            return self._failed_outcome(
                task_id, trace_id, response_id, "internal_error", "版本绑定失败。"
            )
        await deps.task_store.append_event(
            task_id,
            TaskEventRecord(
                event_id=str(uuid4()),
                task_id=task_id,
                event_type="capability_selected",
                timestamp=datetime.now(UTC),
                payload={"capability_id": capability_id, "selection_rule": "exact_id"},
            ),
        )
        await deps.trace_port.record_step(
            trace_id, task_id, scope.session_id,
            tenant_id=scope.tenant_id, ai_user_id=scope.ai_user_id,
            event_type="capability_selected", status="ok", capability_id=capability_id,
            attributes={
                "intent_fingerprint": sha256(capability_id.encode("utf-8")).hexdigest(),
                "selection_rule": "exact_id",
            },
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
        raw_execution = execution
        execution, verification = evaluate_required_postconditions(
            execution,
            scope=EvaluationScope(
                task_id, trace_id, scope.session_id, scope.tenant_id, scope.ai_user_id
            ),
            capability_id=capability_id,
            version=capability.version,
            evaluator=deps.overview_evaluator,
        )
        context = self._context(response_id, task_id, trace_id)
        try:
            envelope = deps.orchestration.build_response(
                context=context, execution=execution, projection=projection,
                business_verification=verification if verification.rule_id else None,
            )
        except ApprovedResponseProjectionError:
            execution = _failed("adapter_payload_invalid", trace_id)
            envelope = deps.orchestration.build_response(
                context=context, execution=execution, projection=projection
            )
        # The model sees the builder-sanitized message, never fallback text.
        text = _model_text(
            execution, envelope.message, envelope.data, capability_id, allowed=self._allowed
        )
        return _TaskOutcome(execution, raw_execution, verification, envelope, text)

    def _log(
        self,
        result: str,
        error_code: str,
        started: float,
        *,
        task_id: str = "none",
        trace_id: str = "none",
    ) -> None:
        _log(result, self._binding.capability_id, error_code, started,
             task_id=task_id, trace_id=trace_id, allowed=self._allowed)


def _log_close_wait(task_id: str, reason: str) -> None:
    _TOOL_LOGGER.info(
        "agentscope_read_tool_close result=waiting reason=%s turn=%s task=%s",
        reason, current_turn_id(), task_id,
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


def _model_text(
    execution: ExecutionResult,
    message: str,
    data: dict[str, Any] | None,
    capability_id: str = "other",
    *,
    allowed: Collection[str],
) -> str:
    # Only the approved projection reaches the model, never raw provider data.
    payload = {"status": execution.status, "message": message, "data": data}
    text = json.dumps(payload, ensure_ascii=False, default=str)
    raw = text.encode("utf-8")
    truncated = len(raw) > MAX_TOOL_RESULT_BYTES
    final_text = text
    if truncated:
        cut = raw[:MAX_TOOL_RESULT_BYTES].decode("utf-8", errors="ignore")
        final_text = cut + "\n[结果已截断，未展示部分不得推断]"
    # Symbolic sizes only: no content reaches the log.
    counts = [len(v) for v in data.values() if isinstance(v, list)] if data else []
    _TOOL_LOGGER.info(
        "agentscope_read_tool model_text capability=%s items=%s bytes_before=%s "
        "bytes_after=%s truncated=%s turn=%s",
        capability_id if capability_id in allowed else "other",
        max(counts) if counts else "na",
        len(raw),
        len(final_text.encode("utf-8")),
        truncated,
        current_turn_id(),
    )
    return final_text


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
    allowed: Collection[str],
) -> None:
    # Code-owned values only: approved capability id, status, error code,
    # timing and the turn/task/trace ids that tie this line to its records.
    _TOOL_LOGGER.info(
        "agentscope_read_tool result=%s capability=%s error=%s elapsed_ms=%s "
        "turn=%s task=%s trace=%s",
        result,
        capability_id if capability_id in allowed else "other",
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
    # Capability ids that may appear in close log lines.
    allowed: frozenset[str]
    # Write in progress: task_status -> trace_step [-> evaluation]
    # -> trace_finalize -> done.
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
        progress.capability_id if progress.capability_id in progress.allowed else "other",
        current_turn_id(),
        progress.task_id,
        progress.trace_id,
    )


__all__ = (
    "AgentScopeRuntimeDependencies",
    "ReadToolScope",
    "TaskClose",
    "ToolExecutor",
    "await_close",
    "close_once",
)
