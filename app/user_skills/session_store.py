"""Process-memory state of personal flows per login session (lost on restart).

Holds this session's read execution record, proposals, trial snapshots, run
snapshots and the pending takeover slot. Values never leave process memory and
are never logged. Keys are (tenant, user, session) from the trusted scope.
"""

from __future__ import annotations

import asyncio
import json
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from time import monotonic
from typing import Any, Literal

MAX_RECORDS = 40
MAX_RECORD_BYTES = 256 * 1024
SNAPSHOT_TTL_SECONDS = 600.0
IDLE_TTL_SECONDS = 4 * 3600.0
MAX_SESSIONS = 500

SessionKey = tuple[str, str, str]


@dataclass(frozen=True)
class RecordEntry:
    seq: int
    turn_id: str
    capability_id: str
    capability_fingerprint: str
    arguments: dict[str, Any]
    data: dict[str, Any]


@dataclass
class TrialSnapshot:
    trial_id: str
    proposal_id: str
    created: float
    payload: dict[str, Any]
    savable: bool
    state: Literal["ready", "saving", "saved"] = "ready"
    saved_result: dict[str, Any] | None = None


@dataclass
class _Session:
    last_used: float
    records: deque[RecordEntry] = field(default_factory=lambda: deque(maxlen=MAX_RECORDS))
    next_seq: int = 1
    dropped: int = 0
    proposals: dict[str, tuple[float, dict[str, Any]]] = field(default_factory=dict)
    trials: dict[str, TrialSnapshot] = field(default_factory=dict)
    runs: dict[str, tuple[float, dict[str, Any]]] = field(default_factory=dict)
    takeover: dict[str, Any] | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class UserSkillSessionStore:
    def __init__(self, clock: Callable[[], float] = monotonic) -> None:
        self._clock = clock
        self._sessions: OrderedDict[SessionKey, _Session] = OrderedDict()

    def _get(self, key: SessionKey) -> _Session:
        now = self._clock()
        for stale in [k for k, s in self._sessions.items() if now - s.last_used > IDLE_TTL_SECONDS]:
            del self._sessions[stale]
        session = self._sessions.get(key)
        if session is None:
            session = _Session(last_used=now)
            self._sessions[key] = session
            while len(self._sessions) > MAX_SESSIONS:
                self._sessions.popitem(last=False)
        session.last_used = now
        self._sessions.move_to_end(key)
        return session

    def now(self) -> float:
        return self._clock()

    def lock(self, key: SessionKey) -> asyncio.Lock:
        return self._get(key).lock

    # -- execution record -------------------------------------------------
    def record(
        self,
        key: SessionKey,
        *,
        turn_id: str,
        capability_id: str,
        capability_fingerprint: str,
        arguments: dict[str, Any],
        data: dict[str, Any],
    ) -> bool:
        size = len(json.dumps([arguments, data], ensure_ascii=False, default=str).encode())
        session = self._get(key)
        if size > MAX_RECORD_BYTES:
            session.dropped += 1
            return False
        if len(session.records) == MAX_RECORDS:
            session.dropped += 1
        session.records.append(
            RecordEntry(
                session.next_seq, turn_id, capability_id, capability_fingerprint, arguments, data
            )
        )
        session.next_seq += 1
        return True

    def records(self, key: SessionKey) -> tuple[tuple[RecordEntry, ...], int]:
        session = self._get(key)
        return tuple(session.records), session.dropped

    def has_records(self, key: SessionKey) -> bool:
        session = self._sessions.get(key)
        return session is not None and bool(session.records)

    # -- snapshots ----------------------------------------------------------
    def _fresh(self, created: float) -> bool:
        return self._clock() - created <= SNAPSHOT_TTL_SECONDS

    def put_proposal(self, key: SessionKey, proposal_id: str, payload: dict[str, Any]) -> None:
        self._get(key).proposals[proposal_id] = (self._clock(), payload)

    def proposal(self, key: SessionKey, proposal_id: str) -> dict[str, Any] | None:
        entry = self._get(key).proposals.get(proposal_id)
        return entry[1] if entry is not None and self._fresh(entry[0]) else None

    def put_trial(self, key: SessionKey, trial: TrialSnapshot) -> None:
        self._get(key).trials[trial.trial_id] = trial

    def trial(self, key: SessionKey, trial_id: str) -> TrialSnapshot | None:
        trial = self._get(key).trials.get(trial_id)
        if trial is None:
            return None
        if trial.state != "saved" and not self._fresh(trial.created):
            return None
        return trial

    def put_run(self, key: SessionKey, run_id: str, snapshot: dict[str, Any]) -> None:
        runs = self._get(key).runs
        runs[run_id] = (self._clock(), snapshot)
        for stale in [rid for rid, (created, _) in runs.items() if not self._fresh(created)]:
            del runs[stale]

    def run(self, key: SessionKey, run_id: str) -> dict[str, Any] | None:
        entry = self._get(key).runs.get(run_id)
        return entry[1] if entry is not None and self._fresh(entry[0]) else None

    def set_takeover(self, key: SessionKey, context: dict[str, Any]) -> None:
        self._get(key).takeover = context

    def take_takeover(self, key: SessionKey) -> dict[str, Any] | None:
        session = self._sessions.get(key)
        if session is None:
            return None
        context, session.takeover = session.takeover, None
        return context


__all__ = ("RecordEntry", "SessionKey", "TrialSnapshot", "UserSkillSessionStore")
