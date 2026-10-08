"""Application-owned browser business and cleanup tasks, with bounded shutdown."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextvars import Context


class BrowserWorkerSupervisor:
    """One business lane and an independent cleanup lane; DB claims remain authority."""

    def __init__(
        self, run_ready: Callable[[], Awaitable[object]], *,
        cleanup_ready: Callable[[], Awaitable[object]] | None = None,
    ) -> None:
        self._run_ready, self._cleanup_ready = run_ready, cleanup_ready
        self._task: asyncio.Task[None] | None = None
        self._cleanup_task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._failed_lanes: set[str] = set()
        self.last_pass_failed = False

    async def start(self) -> None:
        if self._task is not None or self._cleanup_task is not None:
            return
        self._stop.clear()
        self._failed_lanes.clear()
        self.last_pass_failed = False
        self._task = asyncio.create_task(
            self._run(), name="browser-readonly-worker", context=Context(),
        )
        if self._cleanup_ready is not None:
            self._cleanup_task = asyncio.create_task(
                self._poll(self._cleanup_ready, "cleanup"),
                name="browser-readonly-cleanup", context=Context(),
            )

    async def stop(self) -> None:
        tasks = tuple(
            task for task in (self._task, self._cleanup_task) if task is not None
        )
        if not tasks:
            return
        self._stop.set()
        for task in tasks:
            task.cancel()
        _done, pending = await asyncio.wait(tasks, timeout=5)
        # Keep ownership of a task that has not stopped; never report it settled.
        if pending:
            self._failed_lanes.add("shutdown")
            self.last_pass_failed = True
            raise RuntimeError("browser_worker_shutdown_pending")
        for task in tasks:
            if not task.cancelled() and task.exception() is not None:
                self.last_pass_failed = True
        self._task = self._cleanup_task = None

    async def _run(self) -> None:
        await self._poll(self._run_ready, "business")

    async def _poll(self, callback: Callable[[], Awaitable[object]], lane: str) -> None:
        while not self._stop.is_set():
            try:
                await callback()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Only the fixed lane classification is retained, never identities,
                # protected values or exception text.
                self._failed_lanes.add(lane)
            else:
                self._failed_lanes.discard(lane)
            self.last_pass_failed = bool(self._failed_lanes)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=2)
            except TimeoutError:
                continue
