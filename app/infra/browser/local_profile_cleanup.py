"""Conservative retention decision for immutable historical Profile metadata.

New transitions have a database-authored timestamp. A historical retired row
may instead use the head's later updated_at as a safe lower bound; historical
orphan rows without a transition timestamp remain deferred. Eligibility is
never evidence of ciphertext destruction.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from app.ports.browser_profile_store import (
    BrowserProfileContext,
    BrowserProfileError,
    BrowserProfileStorePort,
    ProfileCleanupCandidatePage,
)


def retention_ready(
    *, disposition: str, created_at: datetime, head_updated_at: datetime | None,
    now: datetime, retention_seconds: int | None,
    disposition_changed_at: datetime | None = None,
) -> bool:
    if (disposition not in {"retired", "orphan"}
            or type(retention_seconds) is not int
            or not 1 <= retention_seconds <= 365 * 24 * 3600
            or any(value.tzinfo is None or value.utcoffset() is None
                   for value in (created_at, now))):
        return False
    changed_at = disposition_changed_at
    if changed_at is None and disposition == "retired":
        changed_at = head_updated_at
    if changed_at is None or changed_at.tzinfo is None or changed_at.utcoffset() is None:
        return False
    cutoff = now - timedelta(seconds=retention_seconds)
    return created_at <= cutoff and changed_at <= cutoff


class ProfileCleanupScheduler:
    """Bounded cleanup using fresh host-supplied contexts, never invented owners."""

    def __init__(
        self, store: BrowserProfileStorePort,
        contexts: Callable[[], Awaitable[tuple[BrowserProfileContext, ...]]],
        *, interval_seconds: int = 60, batch_limit: int = 100,
    ) -> None:
        if (not callable(contexts)
                or type(interval_seconds) is not int
                or not 10 <= interval_seconds <= 3600
                or type(batch_limit) is not int or not 1 <= batch_limit <= 1000):
            raise ValueError("browser_profile_cleanup_schedule_invalid")
        self._store, self._contexts = store, contexts
        self._interval, self._limit = interval_seconds, batch_limit
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self.last_result: tuple[int, int, int] = (0, 0, 0)

    async def sweep(self) -> None:
        scanned = removed = failed = 0
        try:
            contexts = await self._contexts()
            if (type(contexts) is not tuple or len(contexts) > 16
                    or any(not isinstance(context, BrowserProfileContext)
                           for context in contexts)):
                raise BrowserProfileError("browser_profile_cleanup_context_invalid")
            for context in contexts:
                cursor: str | None = None
                for _ in range(4):
                    page = await self._store.discover_cleanup_candidates(
                        context, limit=self._limit, after_generation_id=cursor,
                    )
                    if (not isinstance(page, ProfileCleanupCandidatePage)
                            or len(page.generation_ids) + len(page.deferred) > self._limit
                            or (page.next_cursor is not None and (
                                type(page.next_cursor) is not str
                                or (cursor is not None and page.next_cursor <= cursor)
                            ))):
                        raise BrowserProfileError("browser_profile_cleanup_page_invalid")
                    scanned += len(page.generation_ids) + len(page.deferred)
                    for generation_id in page.generation_ids:
                        try:
                            await self._store.cleanup(context, generation_id)
                        except BrowserProfileError:
                            failed += 1
                        else:
                            removed += 1
                    cursor = page.next_cursor
                    if cursor is None:
                        break
            self.last_result = (scanned, removed, failed)
            if failed:
                raise BrowserProfileError("browser_profile_cleanup_incomplete")
        except Exception:
            self.last_result = (scanned, removed, max(1, failed))
            raise

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="browser-profile-cleanup")

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        self._stop.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.sweep()
            except asyncio.CancelledError:
                raise
            except Exception:
                # The payload and immutable receipt remain for a later attempt.
                pass
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except TimeoutError:
                continue
