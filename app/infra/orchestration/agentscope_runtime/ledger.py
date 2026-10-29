"""Per-turn ledger shared by the M1 executor, toolkit middleware and runtime.

The ledger is the only place a turn's executions, failures and terminal
outcome are recorded; the runtime summarizes it once when the turn ends.
Values stay in process memory and are never logged.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from app.ports.capability_gateway import ExecutionResult
from app.ports.evaluation import BusinessVerification
from app.ports.human_gate import VersionBinding
from app.ports.response_envelope import ResponseEnvelope
from app.ports.runtime import StagedWaiting

if TYPE_CHECKING:
    from app.infra.orchestration.agentscope_read_tools import TaskClose

ExecutionMode = Literal["attach", "own"]
FailureKind = Literal["execution", "pre_exec"]
# Why a call was rejected before the executor core ran: the SDK refused its
# arguments (schema_invalid) or the executor refused it (execution_rejected).
PreExecReason = Literal["schema_invalid", "execution_rejected"]

# Id of the tool call being executed, set by the M1 middleware around the
# SDK's acting step; tools run sequentially in the same task.
current_tool_call_id: ContextVar[str | None] = ContextVar(
    "eternalai_m1_tool_call_id", default=None
)


@dataclass(frozen=True, slots=True)
class LedgerExecution:
    """One business execution whose core ran (attach or own task)."""

    task_id: str
    mode: ExecutionMode
    capability_id: str
    execution: ExecutionResult
    envelope: ResponseEnvelope
    raw_execution: ExecutionResult = field(repr=False, compare=False)
    verification: BusinessVerification | None = field(default=None, repr=False, compare=False)
    projected: dict[str, Any] | None = field(default=None, repr=False, compare=False)
    # sha256 of the canonical SDK-parsed arguments; argument values are not kept.
    args_digest: str = ""
    # Capability fingerprint in the exposure snapshot of the issuing call.
    exposed_fingerprint: str = ""


@dataclass(frozen=True, slots=True)
class LedgerFailure:
    kind: FailureKind
    call_id: str
    name: str
    # Actual symbolic code; may be outside ErrorCode (e.g. tool_input_rejected).
    error_code: str
    execution_index: int | None = None
    pre_exec_reason: PreExecReason | None = None


@dataclass(frozen=True, slots=True)
class BindingCard:
    """Gateway asked for a binding; the card of that execution ends the turn."""

    execution_index: int


@dataclass(frozen=True, slots=True)
class Proposal:
    """A write proposal waits for user confirmation; its card ends the turn."""

    execution_index: int


@dataclass(frozen=True, slots=True)
class BrowserSettled:
    """The browser coordinator decided the turn (card, admission or its failure).

    ``close`` is None when the store or the handoff already wrote the task
    state (collection card, admission); otherwise the turn closes it once.
    """

    envelope: ResponseEnvelope
    close: TaskClose | None = None


@dataclass(frozen=True, slots=True)
class Unsupported:
    """The model reported that no visible group can handle the request."""


@dataclass(frozen=True, slots=True)
class Clarify:
    """The model asked the user for missing information with ask_user."""

    question: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class CallSnapshot:
    """What one model call actually exposed, and the binding it ran under."""

    call_index: int
    # Exposed tool name -> function schema digest, in exposure order.
    tool_digests: Mapping[str, str]
    # Exposed business tool name -> capability fingerprint.
    capability_fingerprints: Mapping[str, str]
    binding: VersionBinding


@dataclass(slots=True)
class ClosingTask:
    task_id: str
    future: asyncio.Future[None]


@dataclass(slots=True)
class TurnLedger:
    response_id: str
    task_id: str
    trace_id: str
    turn_id: str
    # Business tool name -> capability id for this turn's catalog.
    business_tools: Mapping[str, str]
    # Value-free catalog statistics written with intent_parsed.
    catalog_attributes: Mapping[str, Any]
    executions: list[LedgerExecution] = field(default_factory=list)
    failures: list[LedgerFailure] = field(default_factory=list)
    terminal: BindingCard | Proposal | Unsupported | Clarify | BrowserSettled | None = None
    model_error: str | None = None
    calls: dict[int, CallSnapshot] = field(default_factory=dict)
    call_of: dict[str, int] = field(default_factory=dict)
    response_tools: dict[int, tuple[str, ...]] = field(default_factory=dict)
    closes: dict[str, ClosingTask] = field(default_factory=dict)
    unclosed: list[str] = field(default_factory=list)
    # Set synchronously by the executor when a call takes the turn task.
    turn_task_claimed: bool = False
    reset_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Write tool name -> capability id; a subset of business_tools.
    write_tools: Mapping[str, str] = field(default_factory=dict)
    # Browser tool name -> capability id; a subset of business_tools (M2 §3.3).
    browser_tools: Mapping[str, str] = field(default_factory=dict)
    # Waiting proposal staged with the coordinator but not yet published.
    staged: StagedWaiting | None = None
    # Bounded futures the session waits for before rollback and release.
    interrupt: asyncio.Future[bool] | None = None
    abandon: asyncio.Future[None] | None = None
    # Set once the proposal is published: the coordinator alone closes the
    # turn task from then on.
    handoff: bool = False

    def snapshot_for(self, call_id: str | None) -> CallSnapshot | None:
        """Exposure snapshot of the model call that issued ``call_id``."""

        if call_id is None:
            return None
        index = self.call_of.get(call_id)
        return None if index is None else self.calls.get(index)

    def record_pre_exec(
        self, call_id: str, name: str, error_code: str, reason: PreExecReason
    ) -> None:
        self.failures.append(
            LedgerFailure("pre_exec", call_id, name, error_code, pre_exec_reason=reason)
        )


__all__ = (
    "BindingCard",
    "BrowserSettled",
    "CallSnapshot",
    "Clarify",
    "ClosingTask",
    "ExecutionMode",
    "LedgerExecution",
    "LedgerFailure",
    "PreExecReason",
    "Proposal",
    "TurnLedger",
    "Unsupported",
    "current_tool_call_id",
)
