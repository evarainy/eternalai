"""Conservative retention decision for immutable historical Profile rows.

The schema has no per-generation retired_at column. The locked head's updated_at
is advanced on retirement, so it is a safe later bound for that transition.
Orphan transition time is not recorded; orphan rows remain discovery-only.
This is eligibility only; it is never evidence of ciphertext destruction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True, slots=True)
class ProfileCleanupCandidatePage:
    """Eligible IDs and fixed-code deferred IDs from one bounded PG page."""

    generation_ids: tuple[str, ...]
    deferred: tuple[tuple[str, str], ...]
    next_cursor: str | None


def retention_ready(
    *, disposition: str, created_at: datetime, head_updated_at: datetime | None,
    now: datetime, retention_seconds: int | None,
) -> bool:
    if (disposition != "retired"
            or head_updated_at is None
            or type(retention_seconds) is not int
            or not 1 <= retention_seconds <= 365 * 24 * 3600
            or any(value.tzinfo is None or value.utcoffset() is None
                   for value in (created_at, head_updated_at, now))):
        return False
    cutoff = now - timedelta(seconds=retention_seconds)
    return created_at <= cutoff and head_updated_at <= cutoff
