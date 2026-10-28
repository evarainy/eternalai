"""In-process AgentScope conversation state, one entry per verified login session.

Keys combine tenant, user, bound session id and an HMAC of the verified login
ticket fingerprint under a per-process random key, so the raw fingerprint
(the revocation key) is never kept. One turn at a time per key: the busy flag
is set synchronously and cleared only after every close, interrupt and
abandon of the turn ended, and after any rollback that waited for them.
State is lost on restart (single worker, process memory).
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import secrets
from collections import OrderedDict
from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from time import monotonic
from typing import TYPE_CHECKING, Any

from agentscope.message import Msg
from agentscope.state import AgentState

from app.infra.llm.agentscope_chat import current_turn_id, start_interrupt
from app.ports.runtime import RuntimeSessionBusyError

if TYPE_CHECKING:
    from agentscope.agent import Agent
    from agentscope.model import ChatModelBase

MAX_SESSIONS = 500
CONTEXT_TOKEN_LIMIT = 16000
_KEY_DOMAIN = b"eternalai-m1-agentstate-v1\0"
_SESSION_LOGGER = logging.getLogger("uvicorn.error.agentscope_runtime")

SessionKey = tuple[str, str, str, str]


@dataclass(slots=True)
class _Entry:
    state: AgentState
    busy: bool
    last_used: float
    # Login-instance deadline (survives renewal); None means the instance has none.
    hard_expiry: datetime | None


@dataclass(frozen=True, slots=True)
class TurnSnapshot:
    """Context and activated groups at turn start, for rollback."""

    context: list[Msg]
    activated_groups: list[str]


class SessionCoordinator:
    def __init__(
        self,
        *,
        idle_ttl_seconds: float,
        max_sessions: int = MAX_SESSIONS,
        clock: Callable[[], float] = monotonic,
        utc_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if idle_ttl_seconds <= 0 or max_sessions <= 0:
            raise ValueError("session limits must be positive")
        self._secret = secrets.token_bytes(32)
        self._idle_ttl = idle_ttl_seconds
        self._max = max_sessions
        self._clock = clock
        self._utc_clock = utc_clock
        self._entries: OrderedDict[SessionKey, _Entry] = OrderedDict()

    def key_for(
        self, *, tenant_id: str, ai_user_id: str, fingerprint: bytes, session_id: str
    ) -> SessionKey:
        """Dict key only; never logged, traced or returned."""

        digest = hmac.new(self._secret, _KEY_DOMAIN + fingerprint, sha256).hexdigest()
        return (tenant_id, ai_user_id, digest, session_id)

    def acquire(self, key: SessionKey, *, expires_at: datetime | None) -> AgentState:
        """Mark the session busy and return its state; raise if already busy.

        No await between the check and the set, so two turns cannot both pass.
        """

        self._sweep()
        entry = self._entries.get(key)
        if entry is not None and entry.busy:
            raise RuntimeSessionBusyError("session_busy")
        if entry is None:
            if len(self._entries) >= self._max and not self._evict_one():
                raise RuntimeSessionBusyError("session_slots_busy")
            entry = _Entry(AgentState(), busy=False, last_used=self._clock(),
                           hard_expiry=expires_at)
            self._entries[key] = entry
        entry.busy = True
        entry.last_used = self._clock()
        entry.hard_expiry = expires_at
        self._entries.move_to_end(key)
        return entry.state

    def release(self, key: SessionKey) -> None:
        entry = self._entries.get(key)
        if entry is not None:
            entry.busy = False
            entry.last_used = self._clock()

    def release_after(
        self,
        key: SessionKey,
        futures: Collection[asyncio.Future[Any]],
        *,
        before_release: Callable[[], None] | None = None,
    ) -> None:
        """Release once every given future has actually ended (count-down, no gather).

        ``before_release`` runs synchronously after the last future ends and
        before the session can take its next turn; futures that failed count
        as ended.
        """

        def _finish() -> None:
            try:
                if before_release is not None:
                    before_release()
            finally:
                self.release(key)

        pending = [future for future in futures if not future.done()]
        if not pending:
            _finish()
            return
        remaining = len(pending)

        def _done(_: asyncio.Future[Any]) -> None:
            nonlocal remaining
            remaining -= 1
            if remaining == 0:
                _finish()

        for future in pending:
            future.add_done_callback(_done)

    def _sweep(self) -> None:
        now, utc_now = self._clock(), self._utc_clock()
        expired = [
            key
            for key, entry in self._entries.items()
            if not entry.busy
            and (
                now - entry.last_used > self._idle_ttl
                or (entry.hard_expiry is not None and entry.hard_expiry <= utc_now)
            )
        ]
        for key in expired:
            del self._entries[key]

    def _evict_one(self) -> bool:
        for key, entry in self._entries.items():
            if not entry.busy:
                del self._entries[key]
                return True
        return False


def start_parked_interrupt(agent: Agent, state: AgentState) -> asyncio.Future[bool] | None:
    """Interrupt a reply left parked on an external call, or None if none is parked.

    Called under the busy flag before ``prepare_turn_state``; the caller
    registers the future before awaiting it so release waits for it.
    """

    if not state.has_awaiting_tool_calls(agent.name):
        return None
    _SESSION_LOGGER.info("agentscope_parked_closed turn=%s", current_turn_id())
    return start_interrupt(agent, state.reply_id)


async def prepare_turn_state(
    state: AgentState, *, read_groups: Collection[str], model: ChatModelBase
) -> TurnSnapshot:
    """Per-turn maintenance under the busy flag with no call in flight."""

    tool_context = state.tool_context
    kept = [name for name in tool_context.activated_groups if name in read_groups]
    dropped = len(tool_context.activated_groups) - len(kept)
    tool_context.activated_groups[:] = kept
    if dropped:
        _SESSION_LOGGER.info(
            "agentscope_runtime_state activated_dropped=%s turn=%s", dropped, current_turn_id()
        )
    trimmed = 0
    while state.context and await model.count_tokens(state.context, None) > CONTEXT_TOKEN_LIMIT:
        _drop_first_turn(state)
        trimmed += 1
    # The SDK keeps a call and its result in one assistant message, so whole
    # turns never split them; a dangling call can only come from a broken turn.
    if _has_open_calls(state):
        _drop_last_turn(state)
        _SESSION_LOGGER.info(
            "agentscope_runtime_state dropped_unfinished_turn=1 turn=%s", current_turn_id()
        )
    if trimmed:
        _SESSION_LOGGER.info(
            "agentscope_runtime_state trimmed_turns=%s turn=%s", trimmed, current_turn_id()
        )
    return TurnSnapshot(
        context=[message.model_copy(deep=True) for message in state.context],
        activated_groups=list(tool_context.activated_groups),
    )


def rollback_turn_state(state: AgentState, snapshot: TurnSnapshot) -> None:
    state.context[:] = [message.model_copy(deep=True) for message in snapshot.context]
    state.tool_context.activated_groups[:] = list(snapshot.activated_groups)


def _drop_first_turn(state: AgentState) -> None:
    # A turn runs from one user message to the next one.
    context = state.context
    end = next(
        (index for index, message in enumerate(context) if index > 0 and message.role == "user"),
        len(context),
    )
    del context[:end]


def _drop_last_turn(state: AgentState) -> None:
    context = state.context
    start = max(
        (index for index, message in enumerate(context) if message.role == "user"), default=0
    )
    del context[start:]


def _has_open_calls(state: AgentState) -> bool:
    # Same test as AgentState.get_unfinished_tool_calls, without its
    # current-reply filter: any call in the last message lacking a result.
    if not state.context:
        return False
    last = state.context[-1]
    if last.role != "assistant":
        return False
    result_ids = {block.id for block in last.get_content_blocks("tool_result")}
    return any(block.id not in result_ids for block in last.get_content_blocks("tool_call"))


__all__ = (
    "CONTEXT_TOKEN_LIMIT",
    "MAX_SESSIONS",
    "SessionCoordinator",
    "SessionKey",
    "TurnSnapshot",
    "prepare_turn_state",
    "rollback_turn_state",
    "start_parked_interrupt",
)
