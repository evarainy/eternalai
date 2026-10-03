"""Bounded durable READ_ONLY worker. Provider/model IO never runs in a DB transaction."""

from __future__ import annotations

import asyncio

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


class _Checkpoint:
    def __init__(self, store: BrowserRunStorePort, run: RunSnapshot, ttl: int) -> None:
        self.store, self.run, self.ttl = store, run, ttl

    async def refresh(self, *, allow_cancel: bool = False) -> RunSnapshot:
        old = self.run
        current = await self.store.get(old.owner, old.task_id, old.run_id)
        if (
            current.worker_id != old.worker_id or current.worker_epoch != old.worker_epoch
            or current.admission != old.admission
            or current.status not in {"running", "waiting_user"}
        ):
            raise BrowserReadExecutionError("stale")
        # get alone is not a worker fence. renew atomically verifies the freshly
        # read revision/deadline/epoch plus current authority under the Run lock.
        self.run = await self.store.renew(current, ttl_seconds=self.ttl)
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
    ) -> None:
        checked_run_id(worker_id)
        if type(enabled) is not bool or type(ttl_seconds) is not int or not 5 <= ttl_seconds <= 300:
            raise ValueError("browser_worker_configuration_invalid")
        self._store, self._execution, self._profiles = store, execution, profiles
        self._worker_id, self._enabled, self._ttl = worker_id, enabled, ttl_seconds

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
        try:
            await checkpoint.refresh(allow_cancel=True)
            if checkpoint.run.verification != "verified":
                if (
                    checkpoint.run.phase in {"running", "verifying", "waiting_user"}
                    or checkpoint.run.effect != "not_sent"
                ):
                    # A crash can lose the in-memory send receipt. An old
                    # not_sent column is not proof that started work was never
                    # sent; never enter the factory or replay its Skill.
                    if checkpoint.run.phase is None:
                        raise BrowserReadExecutionError("stale")
                    checkpoint.run = await self._store.advance(
                        checkpoint.run, phase=checkpoint.run.phase, effect="unknown",
                    )
                    if checkpoint.run.cancel_requested:
                        return await self._cancel(checkpoint)
                    checkpoint.run = await self._store.finalize(
                        checkpoint.run, status="failed", error_code="browser_effect_unknown",
                        dispatch_failure_code="effect_unknown",
                    )
                    return checkpoint.run
                if checkpoint.run.cancel_requested:
                    return await self._cancel(checkpoint)
                if checkpoint.run.phase == "queued":
                    checkpoint.run = await self._store.advance(
                        checkpoint.run, phase="acquiring", effect=checkpoint.run.effect,
                    )
                try:
                    outcome = await self._execution.execute(checkpoint.run, checkpoint)
                except BrowserReadExecutionError as error:
                    await checkpoint.refresh(allow_cancel=True)
                    if checkpoint.run.cancel_requested:
                        return await self._cancel(checkpoint)
                    checkpoint.run = await self._store.finalize(
                        checkpoint.run, status="failed",
                        error_code="browser_effect_unknown" if checkpoint.run.effect == "unknown"
                        else "browser_read_execution_failed",
                        dispatch_failure_code=error.code,
                    )
                    return checkpoint.run
                await checkpoint.refresh(allow_cancel=True)
                effect = "unknown" if checkpoint.run.effect == "unknown" else outcome.effect
                if checkpoint.run.cancel_requested:
                    if checkpoint.run.phase is None:
                        raise BrowserReadExecutionError("stale")
                    checkpoint.run = await self._store.advance(
                        checkpoint.run, phase=checkpoint.run.phase, effect=effect,
                    )
                if checkpoint.run.cancel_requested and outcome.verification != "verified":
                    return await self._cancel(checkpoint)
                if not checkpoint.run.cancel_requested:
                    checkpoint.run = await self._store.advance(
                        checkpoint.run, phase="verifying", effect=effect,
                    )
                if outcome.verification == "verified":
                    assert outcome.result is not None
                    assert outcome.result_digest is not None and outcome.evidence_digest is not None
                    checkpoint.run = await self._store.persist_verified(
                        checkpoint.run, result=outcome.result, result_digest=outcome.result_digest,
                        evidence_digest=outcome.evidence_digest,
                    )
                    self._execution.verified_persisted(checkpoint.run)
                else:
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
            await self._settle_capture(checkpoint)
            checkpoint.run = await self._store.finalize(checkpoint.run, status="completed")
            return checkpoint.run
        finally:
            # Cleanup is separately authorized, can outlive auth expiry and
            # cannot overwrite a verified business outcome or replay the query.
            # No cleanup assertion is inferred from a disconnect or exception.
            try:
                if checkpoint.run.status in {"completed", "failed", "cancelled"}:
                    cleanup = await self._execution.cleanup(checkpoint.run)
                    if cleanup != "pending":
                        await self._store.record_cleanup(checkpoint.run, cleanup=cleanup)
            except Exception:
                pass  # Existing pending/quarantined state remains the truthful obligation.

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
