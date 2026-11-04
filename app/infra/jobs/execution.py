"""Execution wrapper for ``run_background_job`` (FOUNDATION_PLAN v1.6 §1.3).

Task topology: the wrapper coroutine W (Procrastinate's job task) claims the ledger
row, then starts the handler task H and the heartbeat task B. W never awaits H
directly (``asyncio.wait`` neither cancels H nor raises its outcome), so cancelling W
never cancels H implicitly. B only cancels an unfinished H and never W. The outcome
is decided solely by how H actually finished, and exactly one final-write task F is
created; every cancellation channel only waits for that same F.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from uuid import UUID

from procrastinate.job_context import AbortReason, JobContext

from app.background_jobs import JobExecution, JobKindSpec, JobOutcome, parse_job_params
from app.infra.jobs.ledger import JobClaim, PostgreSQLBackgroundJobLedger
from app.ports.background_jobs import BackgroundJobError
from app.ports.file_artifacts import JobOutputOwner, JobOutputPublisher

_LOGGER = logging.getLogger("uvicorn.error.background_jobs")
HEARTBEAT_INTERVAL_SECONDS = 15.0
_USER = "user"
_LOST = "lost"


class _CancelCause:
    """Write-once cancellation cause shared by B and W."""

    def __init__(self) -> None:
        self.value: str | None = None

    def set(self, value: str) -> None:
        if self.value is None:
            self.value = value


@dataclass(frozen=True)
class _Final:
    status: str
    result_code: str
    result_summary: dict[str, object] | None


class BackgroundJobRunner:
    def __init__(
        self,
        ledger: PostgreSQLBackgroundJobLedger,
        kinds: dict[str, JobKindSpec],
        execution: JobExecution,
        job_outputs: Callable[[JobOutputOwner], JobOutputPublisher] | None = None,
    ) -> None:
        self._ledger = ledger
        self._kinds = kinds
        self._execution = execution
        self._job_outputs = job_outputs

    async def run(self, context: JobContext, job_id: str) -> None:
        queue_job_id = context.job.id
        assert queue_job_id is not None
        ledger_job_id = UUID(job_id)
        claim = await self._ledger.claim(ledger_job_id, queue_job_id)
        if claim is None:
            _LOGGER.info(
                "background_job_stale_execution job=%s queue_job=%s", job_id, queue_job_id
            )
            return
        _LOGGER.info(
            "background_job_started job=%s attempt=%s queue_job=%s",
            job_id,
            claim.attempt_count,
            queue_job_id,
        )
        cause = _CancelCause()
        handler = asyncio.create_task(self._handle(claim))
        heartbeat = asyncio.create_task(self._heartbeat(claim, handler, cause))
        pending_cancel = False

        while not handler.done():
            try:
                await asyncio.wait({handler})
            except asyncio.CancelledError:
                pending_cancel = True
                if not handler.done() and cause.value is None:
                    reason = context.abort_reason()
                    cause.set(_USER if reason is AbortReason.USER_REQUEST else _LOST)
                    handler.cancel()
        while not heartbeat.done():
            try:
                await asyncio.wait({heartbeat})
            except asyncio.CancelledError:
                pending_cancel = True

        final = self._outcome(job_id, handler, cause)
        if final is None:
            _LOGGER.info("background_job_finished job=%s status=lost", job_id)
            raise asyncio.CancelledError
        write = asyncio.create_task(
            self._ledger.finalize(
                ledger_job_id,
                claim.claim_token,
                status=final.status,
                result_code=final.result_code,
                result_summary=final.result_summary,
            )
        )
        while not write.done():
            try:
                await asyncio.wait({write})
            except asyncio.CancelledError:
                pending_cancel = True
        error = write.exception()
        if error is not None:
            _LOGGER.error("background_job_final_write_failed job=%s", job_id)
            raise error
        if write.result():
            _LOGGER.info(
                "background_job_finished job=%s status=%s result_code=%s",
                job_id,
                final.status,
                final.result_code,
            )
        else:
            _LOGGER.info("background_job_stale_execution job=%s stage=final_write", job_id)
        if pending_cancel or final.status == "cancelled":
            raise asyncio.CancelledError

    async def _handle(self, claim: JobClaim) -> JobOutcome:
        spec = self._kinds.get(claim.kind)
        if spec is None:
            raise BackgroundJobError("background_job_kind_invalid")
        params = parse_job_params(spec, claim.params)
        execution = self._execution
        if self._job_outputs is not None:
            execution = replace(
                execution,
                outputs=self._job_outputs(
                    JobOutputOwner(job_id=claim.job_id, claim_token=claim.claim_token)
                ),
            )
        return await spec.handler(params, execution)

    async def _heartbeat(
        self, claim: JobClaim, handler: asyncio.Task[JobOutcome], cause: _CancelCause
    ) -> None:
        while not handler.done():
            await asyncio.wait({handler}, timeout=HEARTBEAT_INTERVAL_SECONDS)
            if handler.done():
                return
            try:
                status = await self._ledger.heartbeat(claim.job_id, claim.claim_token)
            except Exception:
                _LOGGER.warning("background_job_heartbeat_failed job=%s", claim.job_id)
                continue
            if handler.done():
                return  # a late heartbeat result never changes the outcome
            if status is None:
                cause.set(_LOST)
            elif status == "cancel_requested":
                cause.set(_USER)
            else:
                continue
            handler.cancel()
            return

    @staticmethod
    def _outcome(
        job_id: str, handler: asyncio.Task[JobOutcome], cause: _CancelCause
    ) -> _Final | None:
        if handler.cancelled():
            if cause.value == _USER:
                return _Final("cancelled", "cancelled", None)
            return None  # lost: the ledger is left to reconcile
        error = handler.exception()
        if error is None:
            outcome = handler.result()
            return _Final("succeeded", outcome.result_code, outcome.result_summary)
        code = error.code if isinstance(error, BackgroundJobError) else "handler_failed"
        _LOGGER.warning("background_job_handler_failed job=%s code=%s", job_id, code)
        return _Final("failed", code, None)


__all__ = ("BackgroundJobRunner", "HEARTBEAT_INTERVAL_SECONDS")
