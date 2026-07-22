"""Bounded, process-local summaries for one Runtime instance."""

from __future__ import annotations

import re
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Literal

MAX_ENTRIES_PER_CONTEXT = 5
MAX_CONTEXTS = 32

_CREDENTIAL_VALUE_PATTERNS = (
    re.compile(r"bearer\s+\S+", re.IGNORECASE),
    re.compile(
        r"(?:authorization|session(?:[\s_-]?id)?|access[\s_-]?token|"
        r"refresh[\s_-]?token|set[\s_-]?cookie|cookie|password|passwd|"
        r"api[\s_-]?key|secret|client[\s_-]?secret|private[\s_-]?key)"
        r"\s*[:=]\s*\S+",
        re.IGNORECASE,
    ),
)


@dataclass(frozen=True, slots=True)
class SessionMemoryKey:
    """Exact isolation key; tenant remains explicit even before API wiring."""

    tenant_id: str
    session_id: str
    ai_user_id: str


@dataclass(frozen=True, slots=True)
class SessionMemorySummary:
    """Allowlisted success context; raw messages and result data never enter it."""

    capability_id: str
    terminal_status: Literal["completed"] = "completed"


class SessionMemory:
    """Keep a small deterministic window of successful summaries per exact key."""

    def __init__(self) -> None:
        self._entries: OrderedDict[
            SessionMemoryKey, deque[SessionMemorySummary]
        ] = OrderedDict()

    def remember_completed(
        self,
        key: SessionMemoryKey,
        *,
        capability_id: str,
    ) -> bool:
        """Store one safe completed summary, returning whether it was retained."""
        normalized_capability_id = capability_id.strip()
        if not normalized_capability_id or _contains_credential_value(
            normalized_capability_id
        ):
            return False

        summaries = self._entries.get(key)
        if summaries is None:
            if len(self._entries) == MAX_CONTEXTS:
                self._entries.popitem(last=False)
            summaries = deque(maxlen=MAX_ENTRIES_PER_CONTEXT)
            self._entries[key] = summaries
        else:
            self._entries.move_to_end(key)

        summaries.append(
            SessionMemorySummary(capability_id=normalized_capability_id)
        )
        return True

    def recall(self, key: SessionMemoryKey) -> tuple[SessionMemorySummary, ...]:
        """Return only summaries belonging to the exact isolation key."""
        summaries = self._entries.get(key)
        return tuple(summaries) if summaries is not None else ()

    @property
    def context_count(self) -> int:
        return len(self._entries)

    @property
    def entry_count(self) -> int:
        return sum(len(summaries) for summaries in self._entries.values())


def _contains_credential_value(value: str) -> bool:
    return any(pattern.search(value) for pattern in _CREDENTIAL_VALUE_PATTERNS)


__all__ = (
    "MAX_CONTEXTS",
    "MAX_ENTRIES_PER_CONTEXT",
    "SessionMemory",
    "SessionMemoryKey",
    "SessionMemorySummary",
)
