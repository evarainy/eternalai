"""ARQ worker functions for P0-SPIKE-004 spike evaluation.

spike-only: This file exists only to test Redis + ARQ task enqueue, execution,
failure recording, and timeout behavior. It must not be imported by app/ modules.
"""

import asyncio


async def task_success(ctx: dict, payload: str) -> dict:
    """AC-1: Minimal task that succeeds and records input/output."""
    return {"status": "ok", "input": payload, "output": f"processed: {payload}"}


async def task_failure(ctx: dict) -> dict:
    """AC-2: Task that raises an exception to verify error recording."""
    raise RuntimeError("simulated spike failure for AC-2")


async def task_timeout(ctx: dict) -> None:
    """AC-3: Task that sleeps past job_timeout to verify timeout handling."""
    await asyncio.sleep(60)


class WorkerSettings:
    """ARQ worker configuration for spike testing."""

    functions = [task_success, task_failure, task_timeout]
    job_timeout = 5
    max_jobs = 2
