"""Periodic ledger reconcile: stalled execution re-dispatch and cancel convergence.

One queue row is executed at most once. Recovery never retries a queue row; it defers
a new one and points the ledger at it (``dispatch_count`` <= 3 including the first
dispatch). Each candidate is handled in its own transaction under the ledger row lock
(``FOR UPDATE SKIP LOCKED``), re-read before any conditional update.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infra.jobs.queue_sql import (
    GENERAL_QUEUE,
    RUN_BACKGROUND_JOB_TASK,
    QueueJobState,
    cancel_job,
    defer_job,
    read_job_state,
)

_LOGGER = logging.getLogger("uvicorn.error.background_jobs")
BATCH_SIZE = 100
MAX_DISPATCHES = 3
CLAIM_GRACE = timedelta(minutes=2)
HEARTBEAT_STALE = timedelta(seconds=60)


async def reconcile_once(session_factory: async_sessionmaker[AsyncSession]) -> int:
    """Reconcile one batch of non-terminal ledger rows; returns the number changed."""

    async with session_factory() as session:
        candidates = (
            await session.execute(
                text(
                    "SELECT job_id FROM public.background_jobs "
                    "WHERE status IN ('accepted','running','cancel_requested') "
                    "ORDER BY updated_at LIMIT :limit"
                ),
                {"limit": BATCH_SIZE},
            )
        ).scalars().all()
    changed = 0
    for job_id in candidates:
        async with session_factory() as session, session.begin():
            if await _reconcile_row(session, job_id):
                changed += 1
    return changed


async def _reconcile_row(session: AsyncSession, job_id: UUID) -> bool:
    row = (
        await session.execute(
            text(
                "SELECT status, queue_job_id, heartbeat_at, dispatch_count, now() AS db_now "
                "FROM public.background_jobs WHERE job_id = :job_id "
                "AND status IN ('accepted','running','cancel_requested') "
                "FOR UPDATE SKIP LOCKED"
            ),
            {"job_id": job_id},
        )
    ).mappings().one_or_none()
    if row is None:
        return False
    status = str(row["status"])
    queue_job_id = int(row["queue_job_id"])
    now: datetime = row["db_now"]
    heartbeat_at: datetime | None = row["heartbeat_at"]
    queue = await read_job_state(session, queue_job_id)
    heartbeat_stale = heartbeat_at is None or now - heartbeat_at >= HEARTBEAT_STALE

    if status == "cancel_requested":
        if not heartbeat_stale and queue is not None and not queue.finished:
            return False
        await session.execute(
            text(
                "UPDATE public.background_jobs SET status = 'cancelled', "
                "result_code = 'cancelled_after_interrupt', finished_at = now(), "
                "updated_at = now() WHERE job_id = :job_id"
            ),
            {"job_id": job_id},
        )
        _LOGGER.info("background_job_reconciled job=%s from=%s to=cancelled", job_id, status)
        return True
    if status == "accepted" and not _accepted_needs_recovery(queue, now):
        return False
    if status == "running" and not heartbeat_stale:
        return False
    return await _redispatch(session, job_id, status, queue_job_id, queue, row["dispatch_count"])


def _accepted_needs_recovery(queue: QueueJobState | None, now: datetime) -> bool:
    if queue is None or queue.finished:
        return True
    if queue.status == "todo":
        return False  # queued, however long
    # doing: fetched but not yet claimed; recover once the claim grace has elapsed.
    return queue.started_at is None or now - queue.started_at >= CLAIM_GRACE


async def _redispatch(
    session: AsyncSession,
    job_id: UUID,
    status: str,
    queue_job_id: int,
    queue: QueueJobState | None,
    dispatch_count: int,
) -> bool:
    if dispatch_count >= MAX_DISPATCHES:
        await session.execute(
            text(
                "UPDATE public.background_jobs SET status = 'failed', "
                "result_code = 'worker_interrupted', finished_at = now(), "
                "updated_at = now() WHERE job_id = :job_id"
            ),
            {"job_id": job_id},
        )
        _LOGGER.info("background_job_reconciled job=%s from=%s to=failed", job_id, status)
        return True
    if queue is not None and queue.status == "todo":
        await cancel_job(session, queue_job_id, abort=False)
    # An old ``doing`` row is left alone: no ledger row references it any more, so a
    # revived execution fails its claim or heartbeat identity check.
    new_queue_job_id = await defer_job(
        session,
        queue_name=GENERAL_QUEUE,
        task_name=RUN_BACKGROUND_JOB_TASK,
        args={"job_id": str(job_id)},
    )
    await session.execute(
        text(
            "UPDATE public.background_jobs SET status = 'accepted', "
            "queue_job_id = :queue_job_id, claim_token = NULL, heartbeat_at = NULL, "
            "dispatch_count = dispatch_count + 1, updated_at = now() "
            "WHERE job_id = :job_id"
        ),
        {"job_id": job_id, "queue_job_id": new_queue_job_id},
    )
    _LOGGER.info(
        "background_job_reconciled job=%s from=%s to=accepted queue_job=%s dispatch=%s",
        job_id,
        status,
        new_queue_job_id,
        dispatch_count + 1,
    )
    return True


__all__ = ("BATCH_SIZE", "CLAIM_GRACE", "HEARTBEAT_STALE", "MAX_DISPATCHES", "reconcile_once")
