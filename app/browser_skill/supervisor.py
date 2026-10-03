"""Application-owned polling of the durable browser queue, with bounded shutdown."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextvars import Context


class BrowserWorkerSupervisor:
    """Thin lifecycle adapter; durable claims and authority remain in the Run store.

    A fresh context prevents an HTTP authentication ContextVar from becoming
    background authority. Failed passes remain observable and are never success.
    """

    def __init__(self, run_ready: Callable[[], Awaitable[object]]) -> None:
        self._run_ready = run_ready
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self.last_pass_failed = False

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(
            self._run(), name="browser-readonly-worker", context=Context(),
        )

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        self._stop.set()
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=5)
        except (asyncio.CancelledError, TimeoutError):
            pass

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._run_ready()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Keep durable pending/cleanup obligations. No exception text
                # or protected input/result enters diagnostics.
                self.last_pass_failed = True
            else:
                self.last_pass_failed = False
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=2)
            except TimeoutError:
                continue
