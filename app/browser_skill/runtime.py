"""Bounded durable READ_ONLY worker. Provider/model IO never runs in a DB transaction."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from app.browser_skill.models import BrowserOwner
from app.ports.browser_profile_store import BrowserProfileStorePort
from app.ports.browser_read_execution import (
    BrowserCaptureResolution,
    BrowserReadExecutionError,
    BrowserReadExecutionPort,
)
from app.ports.browser_run_store import (
    BrowserRunStoreError,
    BrowserRunStorePort,
    RunSnapshot,
    checked_run_id,
)


class _BrowserReadDeferred(BrowserReadExecutionError):
    """Internal pre-resource scheduling signal; no public protocol/status change."""

    def __init__(self) -> None:
        super().__init__("unavailable")


class _Checkpoint:
    def __init__(self, store: BrowserRunStorePort, run: RunSnapshot, ttl: int) -> None:
        self.store, self.run, self.ttl = store, run, ttl
        self.refresh_calls = 0

    async def refresh(self, *, allow_cancel: bool = False) -> RunSnapshot:
        old = self.run
        self.refresh_calls = min(10000, self.refresh_calls + 1)
        try:
            current = await self.store.refresh_worker(old, ttl_seconds=self.ttl)
        except BrowserRunStoreError as error:
            if error.code == "browser_run_checkpoint_stale":
                raise BrowserReadExecutionError("stale") from None
            raise
        if (
            current.worker_id != old.worker_id or current.worker_epoch != old.worker_epoch
            or current.admission != old.admission
            or current.status not in {"running", "waiting_user"}
        ):
            raise BrowserReadExecutionError("stale")
        # The store has read, fenced and renewed this exact worker in one closed
        # transaction. Keep the application identity and cancellation boundary.
        self.run = current
        if self.run.cancel_requested and not allow_cancel:
            raise BrowserReadExecutionError("cancelled")
        return self.run

    async def attach_lease(
        self, *, provider_key: str, provider_manifest_digest: bytes, lease_epoch: int,
    ) -> RunSnapshot:
        await self.refresh()
        self.run = await self.store.attach_lease(
            self.run, provider_key=provider_key,
            provider_manifest_digest=provider_manifest_digest, lease_epoch=lease_epoch,
        )
        return self.run

    async def start_execution(self) -> RunSnapshot:
        await self.refresh()
        if self.run.lease_epoch is None:
            raise BrowserReadExecutionError("denied")
        self.run = await self.store.advance(self.run, phase="running", effect=self.run.effect)
        return self.run


class BrowserReadWorker:
    """One claimed Run per call; default-off and no hidden polling/runtime re-entry."""

    def __init__(
        self, store: BrowserRunStorePort, execution: BrowserReadExecutionPort,
        profiles: BrowserProfileStorePort, *, worker_id: str, enabled: bool = False,
        ttl_seconds: int = 60,
        record_diagnostic: Callable[[RunSnapshot, dict[str, str | int]], Awaitable[None]] | None = None,
    ) -> None:
        checked_run_id(worker_id)
        if type(enabled) is not bool or type(ttl_seconds) is not int or not 5 <= ttl_seconds <= 300:
            raise ValueError("browser_worker_configuration_invalid")
        self._store, self._execution, self._profiles = store, execution, profiles
        self._worker_id, self._enabled, self._ttl = worker_id, enabled, ttl_seconds
        self._record_diagnostic = record_diagnostic

    async def run_next(self, owner: BrowserOwner) -> RunSnapshot | None:
        if not self._enabled:
            return None
        run = await self._store.claim_next(owner, worker_id=self._worker_id, ttl_seconds=self._ttl)
        if run is None:
            return None
        return await self._run_claimed(run)

    async def _run_claimed(self, run: RunSnapshot) -> RunSnapshot:
        """Internal continuation for a store-claimed Run; retain every existing fence."""
        if not self._enabled or run.worker_id != self._worker_id:
            raise BrowserReadExecutionError("denied")
        checkpoint = _Checkpoint(self._store, run, self._ttl)
        origin = stage_started = time.monotonic()
        stage: str | None = "checkpoint"
        diagnostic: dict[str, str | int] = {}
        deferred = interrupted = False

        def mark(next_stage: str | None, result: str = "ok") -> None:
            nonlocal stage, stage_started
            now = time.monotonic()
            if stage is not None:
                prefix = "worker_" + stage
                diagnostic.update({
                    prefix + "_start_ms": max(0, min(300000, int((stage_started - origin) * 1000))),
                    prefix + "_end_ms": max(0, min(300000, int((now - origin) * 1000))),
                    prefix + "_duration_ms": max(0, min(300000, int((now - stage_started) * 1000))),
                    prefix + "_parent": "worker_claimed", prefix + "_result": result,
                })
            stage, stage_started = next_stage, now

        try:
            await checkpoint.refresh(allow_cancel=True)
            if checkpoint.run.verification != "verified":
                if (
                    checkpoint.run.phase in {"running", "verifying", "waiting_user"}
                    or checkpoint.run.effect != "not_sent"
                ):
                    mark("recovery")
                    # A crash can lose the in-memory send receipt. An old
                    # not_sent column is not proof that started work was never
                    # sent; never enter the factory or replay its Skill.
                    if checkpoint.run.phase is None:
                        raise BrowserReadExecutionError("stale")
                    checkpoint.run = await self._store.advance(
                        checkpoint.run, phase=checkpoint.run.phase, effect="unknown",
                    )
                    if checkpoint.run.cancel_requested:
                        mark("cancel")
                        return await self._cancel(checkpoint)
                    mark("finalize")
                    checkpoint.run = await self._store.finalize(
                        checkpoint.run, status="failed", error_code="browser_effect_unknown",
                        dispatch_failure_code="effect_unknown",
                    )
                    return checkpoint.run
                if checkpoint.run.cancel_requested:
                    mark("cancel")
                    return await self._cancel(checkpoint)
                if checkpoint.run.phase == "queued":
                    checkpoint.run = await self._store.advance(
                        checkpoint.run, phase="acquiring", effect=checkpoint.run.effect,
                    )
                try:
                    mark("adapter_execution")
                    outcome = await self._execution.execute(checkpoint.run, checkpoint)
                except _BrowserReadDeferred:
                    mark("deferred", "deferred")
                    await checkpoint.refresh(allow_cancel=True)
                    if checkpoint.run.cancel_requested:
                        mark("cancel")
                        return await self._cancel(checkpoint)
                    if (checkpoint.run.phase != "acquiring"
                            or checkpoint.run.lease_epoch is not None
                            or checkpoint.run.provider_key is not None
                            or checkpoint.run.effect != "not_sent"
                            or checkpoint.run.verification == "verified"):
                        raise BrowserReadExecutionError("stale") from None
                    # Keep the active acquiring Run and its current worker TTL.
                    # A later expired claim may retry only pre-resource work.
                    deferred = True
                    return checkpoint.run
                except BrowserReadExecutionError as error:
                    mark("execution_failure", "failed")
                    await checkpoint.refresh(allow_cancel=True)
                    if checkpoint.run.cancel_requested:
                        mark("cancel")
                        return await self._cancel(checkpoint)
                    mark("finalize")
                    checkpoint.run = await self._store.finalize(
                        checkpoint.run, status="failed",
                        error_code="browser_effect_unknown" if checkpoint.run.effect == "unknown"
                        else "browser_read_execution_failed",
                        dispatch_failure_code=error.code,
                    )
                    return checkpoint.run
                mark(
                    "result_settlement", "verified" if outcome.verification == "verified" else "failed",
                )
                await checkpoint.refresh(allow_cancel=True)
                effect = "unknown" if checkpoint.run.effect == "unknown" else outcome.effect
                if checkpoint.run.cancel_requested:
                    if checkpoint.run.phase is None:
                        raise BrowserReadExecutionError("stale")
                    checkpoint.run = await self._store.advance(
                        checkpoint.run, phase=checkpoint.run.phase, effect=effect,
                    )
                if checkpoint.run.cancel_requested and outcome.verification != "verified":
                    mark("cancel")
                    return await self._cancel(checkpoint)
                if not checkpoint.run.cancel_requested:
                    checkpoint.run = await self._store.advance(
                        checkpoint.run, phase="verifying", effect=effect,
                    )
                if outcome.verification == "verified":
                    mark("persist_verified")
                    assert outcome.result is not None
                    assert outcome.result_digest is not None and outcome.evidence_digest is not None
                    checkpoint.run = await self._store.persist_verified(
                        checkpoint.run, result=outcome.result, result_digest=outcome.result_digest,
                        evidence_digest=outcome.evidence_digest,
                    )
                    self._execution.verified_persisted(checkpoint.run)
                else:
                    mark("finalize")
                    checkpoint.run = await self._store.finalize(
                        checkpoint.run, status="failed",
                        error_code="browser_effect_unknown" if effect == "unknown"
                        else "browser_verification_failed",
                        verification=outcome.verification,
                        dispatch_failure_code=outcome.dispatch_failure_code,
                    )
                    return checkpoint.run
            # This branch intentionally never opens an execution, runs a query,
            # or calls a verifier for an already durable verified result.
            mark("capture")
            await self._settle_capture(checkpoint)
            mark("finalize")
            checkpoint.run = await self._store.finalize(checkpoint.run, status="completed")
            return checkpoint.run
        except asyncio.CancelledError:
            interrupted = True
            raise
        finally:
            # Cleanup is separately authorized, can outlive auth expiry and
            # cannot overwrite a verified business outcome or replay the query.
            # No cleanup assertion is inferred from a disconnect or exception.
            worker_result = (
                "interrupted" if interrupted else "deferred" if deferred
                else checkpoint.run.status
                if checkpoint.run.status in {"completed", "failed", "cancelled"}
                else "error"
            )
            mark("cleanup", worker_result)
            cleanup_result = "skipped"
            try:
                if checkpoint.run.status in {"completed", "failed", "cancelled"}:
                    cleanup = await self._execution.cleanup(checkpoint.run)
                    if cleanup != "pending":
                        await self._store.record_cleanup(checkpoint.run, cleanup=cleanup)
                    cleanup_result = cleanup
            except Exception:
                cleanup_result = "failed"
                pass  # Existing pending/quarantined state remains the truthful obligation.
            mark(None, cleanup_result)
            claimed_ms = max(0, min(300000, int((time.monotonic() - origin) * 1000)))
            diagnostic.update({
                "browser_completion_scope": "worker_claimed",
                "browser_worker_outcome": worker_result,
                "browser_timing_clock": "monotonic_relative_ms",
                "browser_stage_offsets_origin": "worker_claimed",
                "worker_claimed_start_ms": 0,
                "worker_claimed_end_ms": claimed_ms,
                "worker_claimed_duration_ms": claimed_ms,
                "worker_claimed_result": worker_result,
                "worker_refresh_calls": checkpoint.refresh_calls,
            })
            if self._record_diagnostic is not None:
                try:
                    await asyncio.wait_for(
                        self._record_diagnostic(checkpoint.run, diagnostic), timeout=1.0,
                    )
                except Exception:
                    logging.getLogger(__name__).warning("browser_worker_diagnostic_trace_unavailable")

    async def _cancel(self, checkpoint: _Checkpoint) -> RunSnapshot:
        await checkpoint.refresh(allow_cancel=True)
        await self._execution.stop(checkpoint.run, checkpoint)
        await checkpoint.refresh(allow_cancel=True)
        # Store's independent authority checks actual termination/barrier proof.
        checkpoint.run = await self._store.acknowledge_cancel(checkpoint.run)
        checkpoint.run = await self._store.finalize(
            checkpoint.run, status="failed" if checkpoint.run.effect == "unknown" else "cancelled",
            error_code="browser_effect_unknown" if checkpoint.run.effect == "unknown"
            else "browser_cancelled",
            dispatch_failure_code="effect_unknown" if checkpoint.run.effect == "unknown" else None,
        )
        return checkpoint.run

    async def _settle_capture(self, checkpoint: _Checkpoint) -> None:
        await checkpoint.refresh(allow_cancel=True)
        if checkpoint.run.capture_status in {"promoted", "failed", "quarantined"}:
            return
        if checkpoint.run.capture_status == "not_requested":
            if checkpoint.run.cancel_requested:
                return
            checkpoint.run = await self._store.prepare_capture(checkpoint.run)
        try:
            await checkpoint.refresh()
            async with asyncio.timeout(15):
                resolution = await self._execution.lookup_capture(checkpoint.run, checkpoint)
            await checkpoint.refresh()
            if resolution.status == "absent" and checkpoint.run.capture_status == "prepared":
                # Commit the operation and send marker BEFORE provider IO. A crash
                # from this point can only recover by lookup, never send again.
                checkpoint.run = await self._store.transition_capture(checkpoint.run, status="sent")
                await checkpoint.refresh()
                async with asyncio.timeout(15):
                    resolution = await self._execution.send_capture(checkpoint.run, checkpoint)
                await checkpoint.refresh()
            if resolution.status == "found":
                await self._promote(checkpoint, resolution)
            else:
                checkpoint.run = await self._store.transition_capture(
                    checkpoint.run,
                    status="failed" if resolution.status == "failed" else "quarantined",
                )
        except BrowserRunStoreError:
            raise  # A failed fence must never be recovered by taking a newer worker's claim.
        except Exception:
            await checkpoint.refresh(allow_cancel=True)
            if checkpoint.run.capture_status not in {"promoted", "failed", "quarantined"}:
                checkpoint.run = await self._store.transition_capture(
                    checkpoint.run, status="quarantined",
                )

    async def _promote(self, checkpoint: _Checkpoint, found: BrowserCaptureResolution) -> None:
        context, generation = found.context, found.generation
        assert context is not None and generation is not None and found.evidence is not None
        assert found.expected_profile_revision is not None
        run = checkpoint.run
        fact = generation.fact
        if (
            context.run_id != run.run_id or context.task_id != run.task_id
            or context.worker_epoch != run.worker_epoch or context.claim.auth.owner != run.owner
            or context.claim.lease_epoch != run.lease_epoch
            or context.claim.binding.binding_id != run.admission.binding_id
            or context.claim.binding.target_system != run.admission.target_system
            or context.claim.binding.binding_revision != run.admission.binding_revision
            or context.claim.provider_key != run.provider_key
            or fact.capture_operation_id != run.capture_operation_id
            or fact.provider_key != run.provider_key
            or fact.lease_epoch != run.lease_epoch
            or fact.manifest_digest != run.provider_manifest_digest
            or fact.binding_revision != run.admission.binding_revision
        ):
            raise BrowserReadExecutionError("denied")
        if run.capture_status != "validated":
            await self._profiles.record_validated(
                context, generation, found.evidence,
                expected_profile_revision=found.expected_profile_revision,
            )
            # Profile operations atomically update Run revision/capture state.
            await checkpoint.refresh()
        await self._profiles.promote(
            context, fact.generation_id, expected_profile_revision=found.expected_profile_revision,
        )
        await checkpoint.refresh()
        if checkpoint.run.capture_status != "promoted":
            raise BrowserReadExecutionError("invalid_response")

    async def cleanup(self, run: RunSnapshot) -> RunSnapshot:
        """Independent reconciler entry; no worker claim or business execution."""
        cleanup = await self._execution.cleanup(run)
        if cleanup == "pending":
            return run
        return await self._store.record_cleanup(run, cleanup=cleanup)
