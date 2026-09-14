"""Serial daily synchronization, persistent retry timing and local diagnostics."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import AsyncContextManager

from app.organization_directory_access import (
    DEFAULT_MAX_AGE_S,
    DirectoryAccessError,
    check_freshness,
)
from app.ports.organization_directory import (
    OrganizationDirectoryError,
    OrganizationDirectorySnapshot,
    OrganizationDirectorySourcePort,
)
from app.ports.organization_directory_sync import (
    DirectoryErrorCode,
    DirectorySourceError,
    OrganizationDirectorySyncLease,
    OrganizationDirectorySyncPort,
    OrganizationDirectorySyncStatus,
)

SYNC_INTERVAL_S = 86400
SYNC_RETRY_S = 3600
SYNC_TICK_S = 60
SOURCE_DEADLINE_S = 900
_LOGGER = logging.getLogger(__name__)
SourceOpener = Callable[[], AsyncContextManager[OrganizationDirectorySourcePort]]
SnapshotReader = Callable[
    [OrganizationDirectorySourcePort, datetime], Awaitable[OrganizationDirectorySnapshot]
]


class OrganizationDirectorySyncService:
    def __init__(
        self,
        *,
        store: OrganizationDirectorySyncPort,
        reader: SnapshotReader,
        source_opener: SourceOpener | None = None,
        max_age_s: int = DEFAULT_MAX_AGE_S,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._reader = reader
        self._source_opener = source_opener
        self._max_age_s = max_age_s
        self._monotonic = monotonic
        self._freshness: str | None = None

    async def diagnostic(self) -> bool:
        state = await self._store.read_status()
        try:
            check_freshness(
                state.snapshot_version,
                state.source_fetched_at,
                state.last_success_at,
                state.observed_at,
                self._max_age_s,
            )
        except DirectoryAccessError:
            return False
        if state.last_attempt_status == "succeeded":
            return True
        if state.last_attempt_status == "running" and state.last_attempt_started_at is not None:
            age = (state.observed_at - state.last_attempt_started_at).total_seconds()
            return 0 <= age <= SOURCE_DEADLINE_S
        return False

    def _observe(self, state: OrganizationDirectorySyncStatus) -> None:
        kind = "fresh"
        try:
            check_freshness(
                state.snapshot_version,
                state.source_fetched_at,
                state.last_success_at,
                state.observed_at,
                self._max_age_s,
            )
        except DirectoryAccessError as exc:
            kind = exc.code
        if kind == self._freshness:
            return
        previous = self._freshness
        self._freshness = kind
        if kind == "organization_directory_missing":
            _LOGGER.warning("organization_directory_missing")
        elif kind in {"fresh", "organization_directory_stale"}:
            if kind == "fresh" and previous is None:
                return
            event = (
                "organization_directory_recovered"
                if kind == "fresh"
                else "organization_directory_became_stale"
            )
            age = (
                0.0
                if state.source_fetched_at is None
                else (state.observed_at - state.source_fetched_at).total_seconds()
            )
            _LOGGER.info("%s version=%d age_seconds=%.3f", event, state.snapshot_version, age)

    async def run_due(self, stop: asyncio.Event | None = None) -> bool:
        stop = stop if stop is not None else asyncio.Event()
        started = self._monotonic()
        try:
            async with self._store.try_acquire() as lease:
                if lease is None or stop.is_set():
                    return False
                state = await lease.read_status()
                self._observe(state)
                if state.last_attempt_status == "running":
                    await lease.mark_failed("sync_interrupted")
                    self._failed("sync_interrupted", started)
                    return False
                if state.last_attempt_status == "failed":
                    due = state.last_attempt_finished_at
                    interval = SYNC_RETRY_S
                elif state.last_attempt_status == "succeeded":
                    due = state.last_success_at
                    interval = SYNC_INTERVAL_S
                else:
                    due = None
                    interval = 0
                if due is not None and state.observed_at < due + timedelta(seconds=interval):
                    return False
                fetched_at = await lease.start_attempt()
                await self._attempt(lease, fetched_at, stop, started)
                return True
        except asyncio.CancelledError:
            raise
        except Exception:
            self._failed("storage_unavailable", started)
            return False

    async def _attempt(
        self,
        lease: OrganizationDirectorySyncLease,
        fetched_at: datetime,
        stop: asyncio.Event,
        started: float,
    ) -> None:
        publishing = False
        try:
            if self._source_opener is None:
                raise DirectorySourceError("source_unconfigured")
            async with self._source_opener() as source:
                try:
                    async with asyncio.timeout(SOURCE_DEADLINE_S):
                        snapshot = await self._reader(source, fetched_at)
                except TimeoutError:
                    raise DirectorySourceError("source_timeout") from None
                except OrganizationDirectoryError:
                    raise DirectorySourceError("source_unavailable") from None
                if not snapshot.is_complete:
                    raise DirectorySourceError("snapshot_incomplete")
                if stop.is_set():
                    raise DirectorySourceError("sync_interrupted")
                publishing = True
                await lease.replace_snapshot(snapshot)
            state = await lease.read_status()
            self._observe(state)
            _LOGGER.info(
                "organization_directory_sync_succeeded version=%d department_count=%d "
                "membership_count=%d nameless_count=%d duration_ms=%d",
                state.snapshot_version,
                len(snapshot.departments),
                len(snapshot.memberships),
                sum(member.display_name is None for member in snapshot.memberships),
                int((self._monotonic() - started) * 1000),
            )
        except asyncio.CancelledError:
            if not publishing:
                await lease.mark_failed("sync_interrupted")
                self._failed("sync_interrupted", started)
            raise
        except Exception as exc:
            code: DirectoryErrorCode = (
                exc.code
                if isinstance(exc, DirectorySourceError)
                else "storage_unavailable"
                if publishing
                else "source_unavailable"
            )
            # Known pre-publication validation failures remain safe to persist.
            if not publishing or code in {"snapshot_incomplete", "snapshot_invalid"}:
                await lease.mark_failed(code)
            self._failed(code, started)

    def _failed(self, code: DirectoryErrorCode, started: float) -> None:
        _LOGGER.warning(
            "organization_directory_sync_failed code=%s duration_ms=%d",
            code,
            int((self._monotonic() - started) * 1000),
        )


class OrganizationDirectoryScheduler:
    def __init__(
        self,
        service: OrganizationDirectorySyncService,
        *,
        waiter: Callable[[asyncio.Event, float], Awaitable[None]] | None = None,
    ) -> None:
        self._service = service
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._waiter = waiter or self._wait

    async def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            await self._service.run_due(self._stop)
            if not self._stop.is_set():
                await self._waiter(self._stop, SYNC_TICK_S)

    @staticmethod
    async def _wait(stop: asyncio.Event, seconds: float) -> None:
        try:
            await asyncio.wait_for(stop.wait(), seconds)
        except TimeoutError:
            pass
