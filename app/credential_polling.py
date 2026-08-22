"""Configured unattended credential polling orchestration."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.ports.credential_binding import (
    BackgroundCredentialAcquirerPort,
    BackgroundWorkObjectSyncError,
    BackgroundWorkObjectSyncPort,
    CredentialAcquisitionError,
    CredentialPollCandidate,
    CredentialPollingStorePort,
)
from app.ports.job_queue import JobQueuePort

_COUNTED_NON_AUTHENTICATION_FAILURES = frozenset(
    {"network_unreachable", "timeout", "upstream_5xx", "invalid_response"}
)
CREDENTIAL_POLLING_TASK_TYPE = "credential.poll_due"


@dataclass(frozen=True, slots=True)
class CredentialPollingPolicy:
    interval_seconds: int
    maximum_backoff_seconds: int
    work_start_hour: int
    work_end_hour: int
    timezone_name: str
    global_concurrency: int
    scheduler_tick_seconds: int

    def __post_init__(self) -> None:
        if self.interval_seconds < 600:
            raise ValueError("credential polling interval must be at least 600 seconds")
        if self.maximum_backoff_seconds < self.interval_seconds:
            raise ValueError("maximum backoff must not be shorter than the interval")
        if not 0 <= self.work_start_hour < self.work_end_hour <= 24:
            raise ValueError("credential polling work hours are invalid")
        if self.global_concurrency <= 0 or self.scheduler_tick_seconds <= 0:
            raise ValueError("credential polling limits must be positive")
        ZoneInfo(self.timezone_name)


class CredentialPollingService:
    def __init__(
        self,
        *,
        binding_store: CredentialPollingStorePort,
        acquirer: BackgroundCredentialAcquirerPort,
        work_objects: BackgroundWorkObjectSyncPort,
        policy: CredentialPollingPolicy,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._binding_store = binding_store
        self._acquirer = acquirer
        self._work_objects = work_objects
        self._policy = policy
        self._clock = clock

    async def run_due(self) -> int:
        now = self._validated_now()
        if not self._inside_work_hours(now):
            return 0
        candidates = [
            candidate
            for candidate in await self._binding_store.list_poll_candidates()
            if self._is_due(candidate, now)
        ][: self._policy.global_concurrency]
        await asyncio.gather(*(self._run_candidate(candidate) for candidate in candidates))
        return len(candidates)

    async def _run_candidate(self, candidate: CredentialPollCandidate) -> None:
        async with self._binding_store.poll_lock(
            candidate.ai_user_id,
            candidate.target_system,
        ) as acquired:
            if not acquired:
                return
            refreshed = await self._binding_store.refresh_poll_candidate(
                candidate.ai_user_id,
                candidate.target_system,
            )
            now = self._validated_now()
            if refreshed is None or not self._is_due(refreshed, now):
                return
            try:
                principal = await self._acquirer.acquire(refreshed)
                await self._work_objects.sync_for_background(principal)
            except CredentialAcquisitionError as error:
                if error.code in {"credentials_rejected", "identity_mismatch"}:
                    await self._binding_store.mark_terminal_authentication_failure(
                        candidate.ai_user_id,
                        candidate.target_system,
                        "invalid",
                    )
                elif error.code == "captcha_required":
                    await self._binding_store.mark_terminal_authentication_failure(
                        candidate.ai_user_id,
                        candidate.target_system,
                        "captcha_required",
                    )
                elif error.code in _COUNTED_NON_AUTHENTICATION_FAILURES:
                    await self._binding_store.mark_non_authentication_failure(
                        candidate.ai_user_id,
                        candidate.target_system,
                    )
                else:
                    await self._binding_store.mark_non_counted_failure(
                        candidate.ai_user_id,
                        candidate.target_system,
                    )
            except BackgroundWorkObjectSyncError as error:
                if error.authentication_denied:
                    await self._binding_store.mark_terminal_authentication_failure(
                        candidate.ai_user_id,
                        candidate.target_system,
                        "invalid",
                    )
                elif error.failure_code in _COUNTED_NON_AUTHENTICATION_FAILURES:
                    await self._binding_store.mark_non_authentication_failure(
                        candidate.ai_user_id,
                        candidate.target_system,
                    )
                else:
                    await self._binding_store.mark_non_counted_failure(
                        candidate.ai_user_id,
                        candidate.target_system,
                    )
            except Exception:
                await self._binding_store.mark_non_counted_failure(
                    candidate.ai_user_id,
                    candidate.target_system,
                )
            else:
                await self._binding_store.mark_poll_succeeded(
                    candidate.ai_user_id,
                    candidate.target_system,
                )

    def _is_due(self, candidate: CredentialPollCandidate, now: datetime) -> bool:
        multiplier = 2 ** min(candidate.poll_failure_count, 20)
        delay_seconds = min(
            self._policy.maximum_backoff_seconds,
            self._policy.interval_seconds * multiplier,
        )
        return candidate.updated_at + timedelta(seconds=delay_seconds) <= now

    def _inside_work_hours(self, now: datetime) -> bool:
        local_hour = now.astimezone(ZoneInfo(self._policy.timezone_name)).hour
        return self._policy.work_start_hour <= local_hour < self._policy.work_end_hour

    def _validated_now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise TypeError("credential polling clock must be timezone-aware")
        return now


class CredentialPollingScheduler:
    """Clock trigger whose polling work is always carried by JobQueuePort."""

    def __init__(
        self,
        *,
        job_queue: JobQueuePort,
        tick_seconds: int,
    ) -> None:
        self._job_queue = job_queue
        self._tick_seconds = tick_seconds
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        if self._task is None:
            self._stopping.clear()
            self._task = asyncio.create_task(self._run(), name="credential-polling")

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        self._stopping.set()
        await task
        self._task = None

    async def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                job_id = await self._job_queue.enqueue(
                    CREDENTIAL_POLLING_TASK_TYPE,
                    {},
                )
                if await self._job_queue.get_status(job_id) != "complete":
                    raise RuntimeError("credential polling job failed")
            except Exception:
                logging.getLogger(__name__).warning(
                    "credential_polling_tick_failed"
                )
            try:
                await asyncio.wait_for(
                    self._stopping.wait(),
                    timeout=self._tick_seconds,
                )
            except TimeoutError:
                pass


__all__ = (
    "CREDENTIAL_POLLING_TASK_TYPE",
    "CredentialPollingPolicy",
    "CredentialPollingScheduler",
    "CredentialPollingService",
)
