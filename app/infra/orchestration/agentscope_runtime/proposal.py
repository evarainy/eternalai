"""Write proposals of one M1 turn.

Write tools are AgentScope external tools: the framework parks their call and
never runs it. ``ProposalCoordinator.drive`` checks each parked call
synchronously, answers discovery errors with an ERROR result so the model can
go on, and runs an accepted proposal on the turn task inside the caller's
bounded turn scope: the workflow runs behind the confirmation barrier, a
waiting result is staged with the shared confirmation coordinator and the
waiting marks are written. Publication, the handoff of the turn task and the
interrupt of the parked call belong to the runtime, right after the scope
ends. Confirmations never read or write AgentState. A call of the registered
browser tool passes the same checks plus an explicit key allowlist and is
handed to the browser coordinator, which settles the turn (M2 §3.3).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import jsonschema
from agentscope.event import ExternalExecutionResultEvent
from agentscope.message import (
    Msg,
    TextBlock,
    ToolCallBlock,
    ToolCallState,
    ToolResultBlock,
    ToolResultState,
)
from agentscope.state import AgentState
from agentscope.tool import ToolChunk

from app.infra.llm.agentscope_chat import (
    TurnLimits,
    current_turn_id,
    halt_tool_use,
    reserve_tool_execution,
)
from app.infra.orchestration.agentscope_read_tools import (
    AgentScopeRuntimeDependencies,
    ReadToolScope,
    ToolExecutor,
    session_matches,
    write_waiting_marks,
)
from app.infra.orchestration.agentscope_runtime.catalog import ToolCatalog
from app.infra.orchestration.agentscope_runtime.ledger import (
    CallSnapshot,
    LedgerExecution,
    LedgerFailure,
    PreExecReason,
    Proposal,
    TurnLedger,
)
from app.infra.orchestration.agentscope_runtime.toolkit import RESET_TOOLS
from app.knowledge.capability_selection import CandidateBinding
from app.ports.agent_orchestration import ConfirmationPreview
from app.ports.capability_gateway import ExecutionResult
from app.ports.capability_registry import CapabilitySpec
from app.ports.human_gate import TaskVersionBindingManifest
from app.ports.response_projection_contract import ProjectionContractSnapshot
from app.ports.runtime import (
    ConfirmationCoordinatorPort,
    ConfirmationOwner,
    StagedWaiting,
    WaitingProposal,
)

if TYPE_CHECKING:
    from agentscope.agent import Agent

    from app.infra.orchestration.agentscope_read_tools import _CallKey, _TaskOutcome
    from app.infra.orchestration.agentscope_runtime.browser_handoff import (
        BrowserHandoffCoordinator,
    )
    from app.infra.orchestration.agentscope_runtime.ledger import ExecutionMode

_DISCOVERY_TEXTS = {
    "tool_not_exposed": "该工具未在本次调用中提供，请先激活对应工具组。",
    "write_proposal_conflict": (
        "写入类工具必须单独调用，并且只能是本轮第一个业务操作；本次未执行，请直接用文字回复用户。"
    ),
}
_PROPOSAL_FAILED_TEXT = "提议失败。"
_PENDING_ABANDONS: set[asyncio.Future[None]] = set()
_PROPOSAL_LOGGER = logging.getLogger("uvicorn.error.agentscope_runtime")


@dataclass(frozen=True, slots=True)
class _Accepted:
    snapshot: CallSnapshot
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _Discovery:
    code: str


class WriteProposalExecutor(ToolExecutor):
    """Run one accepted write proposal on the turn task; stage a waiting result."""

    def __init__(
        self,
        scope: ReadToolScope,
        deps: AgentScopeRuntimeDependencies,
        ledger: TurnLedger,
        binding: CandidateBinding,
        name: str,
        limits: TurnLimits,
        *,
        owner: ConfirmationOwner,
    ) -> None:
        super().__init__(scope, deps, ledger, binding, name, limits)
        self._owner = owner

    async def _stage_card(
        self,
        task_id: str,
        trace_id: str,
        response_id: str,
        capability: CapabilitySpec,
        arguments: dict[str, Any],
        execution: ExecutionResult,
        manifest: TaskVersionBindingManifest,
        snapshot: CallSnapshot,
        projection: ProjectionContractSnapshot,
    ) -> tuple[str, ExecutionResult, ConfirmationPreview | None]:
        if execution.status != "waiting_user":
            return response_id, execution, None
        staged = await self._deps.confirmations.stage_waiting(
            WaitingProposal(
                owner=self._owner,
                task_id=task_id,
                trace_id=trace_id,
                response_id=response_id,
                capability=capability,
                arguments=dict(arguments),
                execution=execution,
                binding_manifest=manifest,
                intent_version_binding=snapshot.binding,
                projection=projection,
            )
        )
        if staged.execution.status == "waiting_user":
            # Registered at once, so every later exit abandons it until published.
            self._ledger.staged = staged
        return staged.response_id, staged.execution, staged.confirmation

    def _record(
        self,
        state: AgentState,
        call_id: str,
        call: _CallKey,
        mode: ExecutionMode,
        task_id: str,
        outcome: _TaskOutcome,
        started: float,
        *,
        trace_id: str = "none",
    ) -> ToolChunk:
        ledger, staged, execution = self._ledger, self._ledger.staged, outcome.execution
        if staged is None or execution.status != "waiting_user":
            return super()._record(
                state, call_id, call, mode, task_id, outcome, started, trace_id=trace_id
            )
        envelope = outcome.envelope
        if envelope.status != "waiting_user" or envelope.response_id != staged.response_id:
            # Never a card that differs from the staged item; the runtime abandons it.
            ledger.failures.append(
                LedgerFailure("execution", call_id, self._name, "internal_error")
            )
            self._log("failed", "internal_error", started, task_id=task_id)
            return _result_chunk(_PROPOSAL_FAILED_TEXT, "internal_error")
        ledger.executions.append(
            LedgerExecution(
                task_id=task_id,
                mode=mode,
                capability_id=self._binding.capability_id,
                execution=execution,
                envelope=envelope,
                raw_execution=outcome.raw_execution,
                verification=outcome.verification,
                args_digest=call.args_digest,
                exposed_fingerprint=call.fingerprint,
            )
        )
        ledger.terminal = Proposal(len(ledger.executions) - 1)
        halt_tool_use(state, "turn_terminal")
        self._log("waiting_user", execution.error_code or "none", started, task_id=task_id)
        return _result_chunk(outcome.text, None)


class ProposalCoordinator:
    """Checks and runs the parked write calls of one turn."""

    def __init__(
        self,
        *,
        scope: ReadToolScope,
        deps: AgentScopeRuntimeDependencies,
        ledger: TurnLedger,
        limits: TurnLimits,
        executors: Mapping[str, WriteProposalExecutor],
        schemas: Mapping[str, dict[str, Any]],
        browser: BrowserHandoffCoordinator | None = None,
    ) -> None:
        self._scope = scope
        self._deps = deps
        self._ledger = ledger
        self._limits = limits
        self._executors = executors
        self._schemas = schemas
        self._browser = browser

    @classmethod
    def from_catalog(
        cls,
        catalog: ToolCatalog,
        *,
        scope: ReadToolScope,
        deps: AgentScopeRuntimeDependencies,
        ledger: TurnLedger,
        limits: TurnLimits,
        owner: ConfirmationOwner,
        browser: BrowserHandoffCoordinator | None = None,
    ) -> ProposalCoordinator:
        writes = [tool for tool in catalog.tools if tool.kind == "write"]
        external = [*writes, *(tool for tool in catalog.tools if tool.kind == "browser")]
        return cls(
            scope=scope,
            deps=deps,
            ledger=ledger,
            limits=limits,
            executors={
                tool.name: WriteProposalExecutor(
                    scope, deps, ledger, tool.binding, tool.name, limits, owner=owner
                )
                for tool in writes
            },
            schemas={tool.name: tool.input_schema for tool in external},
            browser=browser,
        )

    async def drive(self, agent: Agent, final: Msg) -> Msg | None:
        """Handle parked calls until the reply ends or stays parked.

        Returns the reply's final message, or None when the reply is left
        parked (a staged proposal, a rejected call or a terminal turn); the
        runtime then interrupts it and never uses the placeholder text.
        """

        state, ledger = agent.state, self._ledger
        for _ in range(self._limits.max_tool_executions):
            awaiting = state.get_awaiting_tool_calls(agent.name)
            if not awaiting:
                return final
            call = awaiting[0]
            if len(awaiting) != 1 or call.state != ToolCallState.SUBMITTED:
                self._reject(call, "internal_error")
                return None
            verdict = self._check(state, call)
            if isinstance(verdict, _Discovery):
                _log_write_rejected(verdict.code, "discovery")
                final = await agent.reply(
                    _result_event(
                        state.reply_id, call,
                        _result_chunk(_DISCOVERY_TEXTS[verdict.code], verdict.code),
                    )
                )
                continue
            if verdict is None:
                return None
            # Claimed synchronously, with no await since the checks.
            ledger.turn_task_claimed = True
            if call.name in ledger.browser_tools:
                # The coordinator settles the turn; the parked call is interrupted.
                assert self._browser is not None
                await self._browser.run(verdict.arguments)
                return None
            chunk = await self._executors[call.name].run_claimed(
                state, call.id, verdict.snapshot, verdict.arguments
            )
            terminal = ledger.terminal
            if isinstance(terminal, Proposal):
                execution = ledger.executions[terminal.execution_index].execution
                await write_waiting_marks(
                    self._deps, self._scope, ledger.task_id, ledger.trace_id,
                    error_code=execution.error_code,
                )
                return None
            # A direct business result goes back to the model like a read result.
            final = await agent.reply(_result_event(state.reply_id, call, chunk))
        if state.has_awaiting_tool_calls(agent.name):
            self._reject(state.get_awaiting_tool_calls(agent.name)[0], "internal_error")
            return None
        return final

    def _check(self, state: AgentState, call: ToolCallBlock) -> _Accepted | _Discovery | None:
        """Synchronous checks a-g of a parked call; None means interrupt the reply."""

        ledger, name = self._ledger, call.name
        browser = name in ledger.browser_tools
        if not browser and name not in ledger.write_tools:
            self._reject(call, "internal_error")
            return None
        if browser and (self._browser is None or self._browser.tool_name != name):
            self._reject(call, "internal_error")
            return None
        snapshot = ledger.snapshot_for(call.id)
        if snapshot is None or name not in snapshot.capability_fingerprints:
            return _Discovery("tool_not_exposed")
        if ledger.terminal is not None:
            _log_write_rejected("turn_terminal", "terminal")
            return None
        if ledger.failures or ledger.turn_task_claimed:
            # A proposal must be the first business execution of the turn.
            return _Discovery("write_proposal_conflict")
        index = ledger.call_of.get(call.id)
        response = ledger.response_tools.get(index, ()) if index is not None else ()
        if [item for item in response if item != RESET_TOOLS] != [name]:
            return _Discovery("write_proposal_conflict")
        # Strict parse: the framework's repaired input is never accepted.
        invalid_stage = "invalid_json"
        try:
            arguments = json.loads(call.input)
            invalid_stage = "not_object"
            if not isinstance(arguments, dict):
                raise ValueError("arguments must be an object")
            jsonschema.validate(arguments, self._schemas[name])
            invalid_stage = "browser_keys_or_types"
            if browser and not self._browser.accepts(arguments):  # type: ignore[union-attr]
                raise ValueError("browser arguments outside the allowlist")
        except jsonschema.ValidationError as rejected:
            # Only code-owned labels: never log instance, message or schema paths.
            keyword = rejected.validator
            reason = keyword if keyword in {
                "type", "minLength", "maxLength", "required", "enum", "additionalProperties"
            } else "other"
            _log_write_rejected("schema_invalid", reason)
            self._reject(call, "tool_input_rejected", "schema_invalid")
            return None
        except ValueError:
            _log_write_rejected("schema_invalid", invalid_stage)
            self._reject(call, "tool_input_rejected", "schema_invalid")
            return None
        if not reserve_tool_execution(state, self._limits):
            self._reject(call, "tool_execution_budget_exceeded")
            return None
        if not session_matches(self._scope):
            self._reject(call, "internal_error")
            return None
        return _Accepted(snapshot, arguments)

    def _reject(
        self,
        call: ToolCallBlock,
        error_code: str,
        reason: PreExecReason = "execution_rejected",
    ) -> None:
        self._ledger.record_pre_exec(call.id, call.name, error_code, reason)
        _log_write_rejected(error_code, "pre_exec")


def start_abandon(
    confirmations: ConfirmationCoordinatorPort, staged: StagedWaiting
) -> asyncio.Future[None]:
    """Clean up an unpublished proposal in an independent task bounded by the coordinator.

    The session waits for the future before release; a failure or timeout is
    logged by class only and never raised to a waiter.
    """

    future = asyncio.ensure_future(confirmations.abandon_waiting(staged))
    _PENDING_ABANDONS.add(future)
    future.add_done_callback(_finish_abandon)
    return future


def _finish_abandon(future: asyncio.Future[None]) -> None:
    _PENDING_ABANDONS.discard(future)
    if future.cancelled():
        _log_abandon("failed", "cancelled", "none")
        return
    error = future.exception()
    if error is None:
        _log_abandon("ok", "none", "none")
    else:
        reason = "timeout" if isinstance(error, TimeoutError) else "error"
        _log_abandon("failed", reason, type(error).__name__)


def _result_chunk(text: str, error_code: str | None) -> ToolChunk:
    # Same shape as the read executor's results; metadata never reaches the model.
    return ToolChunk(
        content=[TextBlock(text=text)],
        state=ToolResultState.SUCCESS if error_code is None else ToolResultState.ERROR,
        metadata={} if error_code is None else {"error_code": error_code},
    )


def _result_event(
    reply_id: str, call: ToolCallBlock, chunk: ToolChunk
) -> ExternalExecutionResultEvent:
    return ExternalExecutionResultEvent(
        reply_id=reply_id,
        execution_results=[
            ToolResultBlock(
                id=call.id,
                name=call.name,
                output=[block for block in chunk.content if isinstance(block, TextBlock)],
                state=chunk.state,
                metadata=dict(chunk.metadata),
            )
        ],
    )


def _log_write_rejected(code: str, stage: str) -> None:
    # Code-owned values only; never arguments or tool names.
    _PROPOSAL_LOGGER.info(
        "agentscope_write_rejected code=%s stage=%s turn=%s", code, stage, current_turn_id()
    )


def _log_abandon(result: str, reason: str, error_class: str) -> None:
    _PROPOSAL_LOGGER.info(
        "agentscope_proposal_abandon result=%s reason=%s error_class=%s turn=%s",
        result, reason, error_class if error_class.isidentifier() else "other",
        current_turn_id(),
    )


__all__ = (
    "ProposalCoordinator",
    "WriteProposalExecutor",
    "start_abandon",
)
