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
        start_resources: Callable[[], Awaitable[None]] | None = None,
        stop_resources: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._run_ready, self._cleanup_ready = run_ready, cleanup_ready
        self._start_resources, self._stop_resources = start_resources, stop_resources
        self._resources_started = False
        self._task: asyncio.Task[None] | None = None
        self._cleanup_task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._failed_lanes: set[str] = set()
        self.last_pass_failed = False

    async def start(self) -> None:
        if self._task is not None or self._cleanup_task is not None:
            return
        if self._start_resources is not None:
            self._resources_started = True
            try:
                await self._start_resources()
            except BaseException:
                await self._close_resources()
                raise
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
        if tasks:
            self._stop.set()
            for task in tasks:
                task.cancel()
            _done, pending = await asyncio.wait(tasks, timeout=90)
            # Keep the transport alive for an unsettled task; a later stop may
            # retry the bounded drain without pretending that cleanup finished.
            if pending:
                self._failed_lanes.add("shutdown")
                self.last_pass_failed = True
                raise RuntimeError("browser_worker_shutdown_pending")
            for task in tasks:
                if not task.cancelled() and task.exception() is not None:
                    self.last_pass_failed = True
            self._task = self._cleanup_task = None
        await self._close_resources()

    async def _close_resources(self) -> None:
        if self._resources_started:
            if self._stop_resources is not None:
                await self._stop_resources()
            self._resources_started = False

    async def _run(self) -> None:
        await self._poll(self._run_ready, "business")

    async def _poll(self, callback: Callable[[], Awaitable[object]], lane: str) -> None:
        while not self._stop.is_set():
            completed = False
            try:
                result = await callback()
                completed = lane == "business" and isinstance(result, tuple) and bool(result)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Only the fixed lane classification is retained, never identities,
                # protected values or exception text.
                self._failed_lanes.add(lane)
            else:
                self._failed_lanes.discard(lane)
            self.last_pass_failed = bool(self._failed_lanes)
            # A completed business Run may have left another durable Run ready.
            # Yield to cleanup/cancellation before scanning again; an empty or
            # failed pass still uses the bounded polling delay below.
            if completed:
                await asyncio.sleep(0)
                continue
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=2)
            except TimeoutError:
                continue
