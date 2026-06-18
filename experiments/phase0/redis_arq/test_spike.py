"""P0-SPIKE-004: Redis + ARQ baseline spike test.

Uses ARQ's native Worker burst mode to process jobs and job.result_info()
to verify results. No manual queue draining.

Runs against a local Redis instance (expected on 127.0.0.1:6379).
Exits 0 if all three acceptance criteria pass, 1 otherwise.

spike-only: This script is not part of the production test suite.
"""

import asyncio
import sys
import traceback

from arq import create_pool
from arq.connections import RedisSettings
from arq.jobs import JobStatus
from arq.worker import create_worker

from worker import task_success, task_failure, task_timeout, WorkerSettings

REDIS_SETTINGS = RedisSettings(host="127.0.0.1", port=6379)
RESULTS = {"ac1": None, "ac2": None, "ac3": None}


async def flush_redis(pool) -> None:
    """Clear all keys to avoid WRONGTYPE collisions from prior runs."""
    await pool.flushdb()


async def run_burst() -> None:
    """Run the ARQ worker once in burst mode to process all queued jobs."""
    worker = create_worker(
        WorkerSettings,
        redis_settings=REDIS_SETTINGS,
        burst=True,
        max_burst_jobs=10,
    )
    await worker.async_run()


async def test_ac1(pool) -> bool:
    """AC-1: Minimal task can be enqueued and executed."""
    print("\n--- AC-1: Task enqueue and execution ---")
    try:
        job = await pool.enqueue_job("task_success", "hello-spike")
        if job is None:
            print("FAIL: enqueue_job returned None")
            return False

        await run_burst()

        info = await job.result_info()
        if info is None:
            print("FAIL: no result info available")
            return False

        if not info.success:
            print(f"FAIL: job did not succeed, result={info.result}")
            return False

        result = info.result
        expected_output = "processed: hello-spike"
        if result.get("output") != expected_output:
            print(f"FAIL: expected output '{expected_output}', got '{result.get('output')}'")
            return False

        print(f"PASS: task executed successfully, result={result}")
        return True
    except Exception:
        traceback.print_exc()
        return False


async def test_ac2(pool) -> bool:
    """AC-2: Failed task records exception."""
    print("\n--- AC-2: Failure recording ---")
    try:
        job = await pool.enqueue_job("task_failure")
        if job is None:
            print("FAIL: enqueue_job returned None")
            return False

        await run_burst()

        info = await job.result_info()
        if info is None:
            print("FAIL: no result info available")
            return False

        if info.success:
            print(f"FAIL: expected failure, but job succeeded with result={info.result}")
            return False

        exc = info.result
        if not isinstance(exc, RuntimeError):
            print(f"FAIL: expected RuntimeError, got {type(exc).__name__}: {exc}")
            return False
        if "simulated spike failure for AC-2" not in str(exc):
            print(f"FAIL: expected message 'simulated spike failure for AC-2', got '{exc}'")
            return False
        print(f"PASS: exception recorded: {type(exc).__name__}: {exc}")
        return True
    except Exception:
        traceback.print_exc()
        return False


async def test_ac3(pool) -> bool:
    """AC-3: Timeout task produces timeout error."""
    print("\n--- AC-3: Timeout handling ---")
    try:
        job = await pool.enqueue_job("task_timeout")
        if job is None:
            print("FAIL: enqueue_job returned None")
            return False

        await run_burst()

        info = await job.result_info()
        if info is None:
            print("FAIL: no result info available")
            return False

        if info.success:
            print(f"FAIL: expected timeout failure, but job succeeded with result={info.result}")
            return False

        exc = info.result
        is_timeout = isinstance(exc, asyncio.TimeoutError) or "timeout" in str(type(exc).__name__).lower()
        if not is_timeout:
            print(f"FAIL: expected timeout error, got {type(exc).__name__}: {exc}")
            return False

        print(f"PASS: timeout detected, exception={type(exc).__name__}: {exc}")
        return True
    except Exception:
        traceback.print_exc()
        return False


async def main() -> int:
    print("=== P0-SPIKE-004: Redis + ARQ Baseline Spike ===\n")

    try:
        pool = await create_pool(REDIS_SETTINGS)
        await pool.ping()
        print("Redis connection: OK")
    except Exception as exc:
        print(f"Redis connection FAILED: {exc}")
        print("BLOCKED: needs_test_environment — cannot reach Redis on 127.0.0.1:6379")
        return 1

    await flush_redis(pool)
    print("Redis flushed: OK")

    RESULTS["ac1"] = await test_ac1(pool)
    RESULTS["ac2"] = await test_ac2(pool)
    RESULTS["ac3"] = await test_ac3(pool)

    await pool.aclose()

    print("\n=== Results ===")
    for ac, passed in RESULTS.items():
        print(f"  {ac.upper()}: {'PASS' if passed else 'FAIL'}")

    all_pass = all(RESULTS.values())
    print(f"\nOverall: {'ALL PASSED' if all_pass else 'SOME FAILED'}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
