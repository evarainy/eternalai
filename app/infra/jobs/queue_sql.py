"""Procrastinate queue operations inside the caller's SQLAlchemy transaction.

Controller ruling O1 (2026-11-03): the API runs on asyncpg + Proactor, where a psycopg
connection for ``defer_async`` is unavailable. Enqueue, cancel and status reads
therefore call the official Procrastinate SQL contract directly on the caller's
``AsyncSession``; the caller's single COMMIT makes ledger and queue rows atomic. The
module is driver independent (asyncpg and psycopg): only scalars are bound and
PostgreSQL builds the composite array.

Pinned contract — Procrastinate 3.10.0, schema.sql SHA256
c70ec4b400a60ad9592787653aae5ae77ae41bf801d56b5bd2712751a07f2009:

* ``procrastinate_defer_jobs_v1(procrastinate_job_to_defer_v1[]) RETURNS bigint[]``
  with the composite fields (queue_name, task_name, priority, lock, queueing_lock,
  args jsonb, scheduled_at); new rows start as ``todo`` and fire the official
  insert/notify triggers (delivered on COMMIT).
* ``procrastinate_cancel_job_v1(job_id, abort, delete_job) RETURNS bigint``: the id of
  the changed row, NULL when nothing changed (mapped to False, never "cancelled").
* ``procrastinate_jobs.status`` and ``procrastinate_events(job_id, type, at)``.

Upgrading Procrastinate requires re-checking these signatures and semantics against
the new schema.sql. No lock/queueing_lock is used for job acceptance, and no retry
function is ever called.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

QUEUE_SCHEMA = "eternalai_queue"
GENERAL_QUEUE = "general"
MAINTENANCE_QUEUE = "maintenance"
RUN_BACKGROUND_JOB_TASK = "run_background_job"

_QUEUE_SEARCH_PATH = text(f"SET LOCAL search_path TO {QUEUE_SCHEMA}, public")
_DEFER = text(
    f"SELECT unnest({QUEUE_SCHEMA}.procrastinate_defer_jobs_v1(ARRAY[ROW("
    "CAST(:queue_name AS varchar), CAST(:task_name AS varchar), 0, "
    "CAST(NULL AS text), CAST(NULL AS text), CAST(:args AS jsonb), "
    f"CAST(NULL AS timestamptz))::{QUEUE_SCHEMA}.procrastinate_job_to_defer_v1])) AS id"
)
_CANCEL = text(
    f"SELECT {QUEUE_SCHEMA}.procrastinate_cancel_job_v1("
    "CAST(:job_id AS bigint), CAST(:abort AS boolean), false) AS id"
)
_READ = text(
    "SELECT j.status::text AS status, (SELECT max(e.at) "
    f"FROM {QUEUE_SCHEMA}.procrastinate_events e "
    "WHERE e.job_id = j.id AND e.type = 'started') AS started_at "
    f"FROM {QUEUE_SCHEMA}.procrastinate_jobs j WHERE j.id = CAST(:job_id AS bigint)"
)


@dataclass(frozen=True)
class QueueJobState:
    status: str
    started_at: datetime | None

    @property
    def finished(self) -> bool:
        return self.status not in ("todo", "doing")


async def defer_job(
    session: AsyncSession, *, queue_name: str, task_name: str, args: Mapping[str, Any]
) -> int:
    """Insert one queue row in the current transaction and return its id."""

    # Official functions and triggers resolve unqualified names via search_path.
    await session.execute(_QUEUE_SEARCH_PATH)
    result = await session.execute(
        _DEFER,
        {
            "queue_name": queue_name,
            "task_name": task_name,
            "args": json.dumps(dict(args), sort_keys=True, separators=(",", ":")),
        },
    )
    return int(result.scalar_one())


async def cancel_job(session: AsyncSession, queue_job_id: int, *, abort: bool) -> bool:
    """Official cancel with ``delete_job=False``; False when the row was not changed."""

    await session.execute(_QUEUE_SEARCH_PATH)
    changed = (
        await session.execute(_CANCEL, {"job_id": queue_job_id, "abort": abort})
    ).scalar_one()
    if changed is None:
        return False
    if int(changed) != queue_job_id:
        raise RuntimeError("background_job_queue_cancel_mismatch")
    return True


async def read_job_state(session: AsyncSession, queue_job_id: int) -> QueueJobState | None:
    """Unlocked read of the queue row status and its latest ``started`` event."""

    row = (await session.execute(_READ, {"job_id": queue_job_id})).mappings().one_or_none()
    if row is None:
        return None
    return QueueJobState(status=str(row["status"]), started_at=row["started_at"])


__all__ = (
    "GENERAL_QUEUE",
    "MAINTENANCE_QUEUE",
    "QUEUE_SCHEMA",
    "QueueJobState",
    "RUN_BACKGROUND_JOB_TASK",
    "cancel_job",
    "defer_job",
    "read_job_state",
)
