"""PostgreSQL background job ledger (``public.background_jobs``).

The ledger row is the only serialization point: acceptance, claim, heartbeat, final
write, cancellation and reconcile all lock or conditionally update that row first.
Queue rows are read without locks; the official cancel function may then lock the
queue row, so the lock order is always ledger row -> queue row.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infra.jobs.queue_sql import (
    GENERAL_QUEUE,
    QUEUE_SCHEMA,
    RUN_BACKGROUND_JOB_TASK,
    cancel_job,
    defer_job,
    read_job_state,
)
from app.ports.background_jobs import (
    MAX_ACTIVE_JOBS_PER_USER,
    TERMINAL_STATUSES,
    BackgroundJobAcceptance,
    BackgroundJobAcceptRequest,
    BackgroundJobCancellation,
    BackgroundJobError,
    BackgroundJobOwner,
    BackgroundJobRunOwner,
    BackgroundJobView,
)

_LOGGER = logging.getLogger("uvicorn.error.background_jobs")
_VIEW_COLUMNS = (
    "job_id, job_kind, status, result_code, result_summary::text AS result_summary, "
    "created_at, started_at, cancel_requested_at, finished_at, updated_at"
)
_OWNER_FILTER = "tenant_id = :tenant_id AND requested_by_ai_user_id = :ai_user_id"
_LIVE_EXECUTION = (
    "job_id = :job_id AND claim_token = :claim_token "
    "AND status IN ('running','cancel_requested')"
)
WORKER_HEARTBEAT_WINDOW_SECONDS = 60


def view_from_row(row: RowMapping) -> BackgroundJobView:
    summary = row["result_summary"]
    return BackgroundJobView(
        job_id=row["job_id"],
        kind=row["job_kind"],
        status=row["status"],
        result_code=row["result_code"],
        result_summary=None if summary is None else json.loads(summary),
        created_at=row["created_at"],
        started_at=row["started_at"],
        cancel_requested_at=row["cancel_requested_at"],
        finished_at=row["finished_at"],
        updated_at=row["updated_at"],
    )


def _owner_params(owner: BackgroundJobOwner) -> dict[str, str]:
    return {"tenant_id": owner.tenant_id, "ai_user_id": owner.ai_user_id}


@dataclass(frozen=True)
class JobClaim:
    job_id: UUID
    kind: str
    params: dict[str, Any]
    claim_token: UUID
    attempt_count: int
    owner: BackgroundJobRunOwner


class PostgreSQLBackgroundJobLedger:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    # ---- API side -------------------------------------------------------------

    async def accept(self, request: BackgroundJobAcceptRequest) -> BackgroundJobAcceptance:
        async with self._sessions() as session:
            try:
                acceptance, queue_job_id = await self._accept(session, request)
            except BackgroundJobError:
                raise
            except Exception:
                # Raised before COMMIT: closing the session rolls everything back.
                _LOGGER.warning("background_job_accept_failed kind=%s", request.kind)
                raise BackgroundJobError("background_job_accept_failed") from None
            if acceptance.deduplicated:
                return acceptance
            try:
                await session.commit()
            except Exception:
                _LOGGER.error(
                    "background_job_accept_outcome_unknown job=%s", acceptance.job.job_id
                )
                raise BackgroundJobError("background_job_accept_outcome_unknown") from None
        _LOGGER.info(
            "background_job_accepted kind=%s job=%s queue_job=%s",
            request.kind,
            acceptance.job.job_id,
            queue_job_id,
        )
        return acceptance

    async def _accept(
        self, session: AsyncSession, request: BackgroundJobAcceptRequest
    ) -> tuple[BackgroundJobAcceptance, int | None]:
        owner = _owner_params(request.owner)
        await session.execute(text(f"SET LOCAL search_path TO {QUEUE_SCHEMA}, public"))
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": f"bg-owner:{request.owner.tenant_id}:{request.owner.ai_user_id}"},
        )
        existing = (
            await session.execute(
                text(
                    f"SELECT {_VIEW_COLUMNS}, request_fingerprint FROM public.background_jobs "
                    f"WHERE {_OWNER_FILTER} AND idempotency_key = :idempotency_key"
                ),
                {**owner, "idempotency_key": request.idempotency_key},
            )
        ).mappings().one_or_none()
        if existing is not None:
            if bytes(existing["request_fingerprint"]) != request.request_fingerprint:
                raise BackgroundJobError("background_job_idempotency_conflict")
            return BackgroundJobAcceptance(job=view_from_row(existing), deduplicated=True), None
        active = (
            await session.execute(
                text(
                    "SELECT count(*) FROM public.background_jobs WHERE "
                    f"{_OWNER_FILTER} AND status IN ('accepted','running','cancel_requested')"
                ),
                owner,
            )
        ).scalar_one()
        if active >= MAX_ACTIVE_JOBS_PER_USER:
            raise BackgroundJobError("background_job_limit_reached")
        job_id = uuid4()
        queue_job_id = await defer_job(
            session,
            queue_name=GENERAL_QUEUE,
            task_name=RUN_BACKGROUND_JOB_TASK,
            args={"job_id": str(job_id)},
        )
        row = (
            await session.execute(
                text(
                    "INSERT INTO public.background_jobs (job_id, tenant_id, "
                    "requested_by_ai_user_id, session_id, job_kind, external_effect, params, "
                    "idempotency_key, request_fingerprint, status, queue_job_id, "
                    "created_at, updated_at) VALUES (:job_id, :tenant_id, :ai_user_id, "
                    ":session_id, :job_kind, :external_effect, CAST(:params AS jsonb), "
                    ":idempotency_key, :request_fingerprint, 'accepted', :queue_job_id, "
                    f"now(), now()) RETURNING {_VIEW_COLUMNS}"
                ),
                {
                    **owner,
                    "job_id": job_id,
                    "session_id": request.session_id,
                    "job_kind": request.kind,
                    "external_effect": request.external_effect,
                    "params": json.dumps(
                        dict(request.params), sort_keys=True, separators=(",", ":")
                    ),
                    "idempotency_key": request.idempotency_key,
                    "request_fingerprint": request.request_fingerprint,
                    "queue_job_id": queue_job_id,
                },
            )
        ).mappings().one()
        return BackgroundJobAcceptance(job=view_from_row(row), deduplicated=False), queue_job_id

    async def get(self, owner: BackgroundJobOwner, job_id: UUID) -> BackgroundJobView | None:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    text(
                        f"SELECT {_VIEW_COLUMNS} FROM public.background_jobs "
                        f"WHERE job_id = :job_id AND {_OWNER_FILTER}"
                    ),
                    {**_owner_params(owner), "job_id": job_id},
                )
            ).mappings().one_or_none()
        return None if row is None else view_from_row(row)

    async def list(self, owner: BackgroundJobOwner, limit: int) -> list[BackgroundJobView]:
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    text(
                        f"SELECT {_VIEW_COLUMNS} FROM public.background_jobs "
                        f"WHERE {_OWNER_FILTER} ORDER BY created_at DESC, job_id LIMIT :limit"
                    ),
                    {**_owner_params(owner), "limit": limit},
                )
            ).mappings().all()
        return [view_from_row(row) for row in rows]

    async def cancel(
        self, owner: BackgroundJobOwner, job_id: UUID
    ) -> BackgroundJobCancellation | None:
        async with self._sessions() as session, session.begin():
            row = (
                await session.execute(
                    text(
                        f"SELECT {_VIEW_COLUMNS}, queue_job_id FROM public.background_jobs "
                        f"WHERE job_id = :job_id AND {_OWNER_FILTER} FOR UPDATE"
                    ),
                    {**_owner_params(owner), "job_id": job_id},
                )
            ).mappings().one_or_none()
            if row is None:
                return None
            status = row["status"]
            if status in TERMINAL_STATUSES or status == "cancel_requested":
                return BackgroundJobCancellation(job=view_from_row(row), pending=False)
            queue_job_id = int(row["queue_job_id"])
            queue = await read_job_state(session, queue_job_id)
            if queue is not None and not queue.finished:
                changed = await cancel_job(session, queue_job_id, abort=True)
                if changed and status == "running" and queue.status == "doing":
                    updated = await _set_status(
                        session, job_id, "cancel_requested", result_code=None
                    )
                    return BackgroundJobCancellation(job=updated, pending=True)
                if changed:
                    updated = await _set_status(
                        session, job_id, "cancelled", result_code="cancelled"
                    )
                    return BackgroundJobCancellation(job=updated, pending=False)
            # Queue row already finished, missing, or finished concurrently.
            updated = await _set_status(
                session, job_id, "cancelled", result_code="cancelled_after_interrupt"
            )
            return BackgroundJobCancellation(job=updated, pending=False)

    async def worker_alive(self) -> bool:
        async with self._sessions() as session:
            alive = (
                await session.execute(
                    text(
                        "SELECT COALESCE(max(last_heartbeat) > now() - "
                        f"make_interval(secs => {WORKER_HEARTBEAT_WINDOW_SECONDS}), false) "
                        f"FROM {QUEUE_SCHEMA}.procrastinate_workers"
                    )
                )
            ).scalar_one()
        return bool(alive)

    # ---- worker side ----------------------------------------------------------

    async def claim(self, job_id: UUID, queue_job_id: int) -> JobClaim | None:
        async with self._sessions() as session, session.begin():
            row = (
                await session.execute(
                    text(
                        "SELECT status, queue_job_id, job_kind, params::text AS params, "
                        "tenant_id, requested_by_ai_user_id, session_id "
                        "FROM public.background_jobs WHERE job_id = :job_id FOR UPDATE"
                    ),
                    {"job_id": job_id},
                )
            ).mappings().one_or_none()
            if (
                row is None
                or row["status"] != "accepted"
                or int(row["queue_job_id"]) != queue_job_id
            ):
                return None
            token = uuid4()
            attempt = (
                await session.execute(
                    text(
                        "UPDATE public.background_jobs SET status = 'running', "
                        "claim_token = :claim_token, heartbeat_at = now(), "
                        "attempt_count = attempt_count + 1, "
                        "started_at = COALESCE(started_at, now()), updated_at = now() "
                        "WHERE job_id = :job_id RETURNING attempt_count"
                    ),
                    {"job_id": job_id, "claim_token": token},
                )
            ).scalar_one()
            return JobClaim(
                job_id=job_id,
                kind=str(row["job_kind"]),
                params=json.loads(row["params"]),
                claim_token=token,
                attempt_count=int(attempt),
                owner=BackgroundJobRunOwner(
                    tenant_id=str(row["tenant_id"]),
                    ai_user_id=str(row["requested_by_ai_user_id"]),
                    session_id=None if row["session_id"] is None else str(row["session_id"]),
                ),
            )

    async def heartbeat(self, job_id: UUID, claim_token: UUID) -> str | None:
        async with self._sessions() as session, session.begin():
            status = (
                await session.execute(
                    text(
                        "UPDATE public.background_jobs SET heartbeat_at = now() "
                        f"WHERE {_LIVE_EXECUTION} RETURNING status"
                    ),
                    {"job_id": job_id, "claim_token": claim_token},
                )
            ).scalar_one_or_none()
        return None if status is None else str(status)

    async def finalize(
        self,
        job_id: UUID,
        claim_token: UUID,
        *,
        status: str,
        result_code: str,
        result_summary: dict[str, Any] | None,
    ) -> bool:
        async with self._sessions() as session, session.begin():
            written = (
                await session.execute(
                    text(
                        "UPDATE public.background_jobs SET status = :status, "
                        "result_code = :result_code, "
                        "result_summary = CAST(:result_summary AS jsonb), "
                        "finished_at = now(), updated_at = now() "
                        f"WHERE {_LIVE_EXECUTION} RETURNING job_id"
                    ),
                    {
                        "job_id": job_id,
                        "claim_token": claim_token,
                        "status": status,
                        "result_code": result_code,
                        "result_summary": None
                        if result_summary is None
                        else json.dumps(result_summary, sort_keys=True, separators=(",", ":")),
                    },
                )
            ).scalar_one_or_none()
        return written is not None


async def _set_status(
    session: AsyncSession, job_id: UUID, status: str, *, result_code: str | None
) -> BackgroundJobView:
    finished = "now()" if status in TERMINAL_STATUSES else "NULL"
    row = (
        await session.execute(
            text(
                "UPDATE public.background_jobs SET status = :status, "
                "result_code = :result_code, "
                "cancel_requested_at = COALESCE(cancel_requested_at, now()), "
                f"finished_at = {finished}, updated_at = now() "
                f"WHERE job_id = :job_id RETURNING {_VIEW_COLUMNS}"
            ),
            {"job_id": job_id, "status": status, "result_code": result_code},
        )
    ).mappings().one()
    _LOGGER.info("background_job_cancel job=%s status=%s", job_id, status)
    return view_from_row(row)


__all__ = (
    "JobClaim",
    "PostgreSQLBackgroundJobLedger",
    "WORKER_HEARTBEAT_WINDOW_SECONDS",
    "view_from_row",
)
