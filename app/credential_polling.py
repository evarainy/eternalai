"""Configured unattended credential polling orchestration."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Literal
from zoneinfo import ZoneInfo

from app.ports.auth import (
    CredentialWriteStamp,
    OASessionCredential,
    Principal,
    StaleCredentialWrite,
)
from app.ports.credential_binding import (
    BackgroundCredentialAcquirerPort,
    BackgroundWorkObjectSyncError,
    BackgroundWorkObjectSyncPort,
    CredentialAcquisitionError,
    CredentialPollCandidate,
    CredentialPollingStorePort,
    CredentialRecoveryState,
    CredentialRecoveryTrigger,
    OAKeepaliveStatus,
    OAKeepaliveView,
    OASessionHeartbeatResult,
    OASessionProbeResult,
    RecoveryOutcome,
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
        tenant_id: str,
        acquirer: BackgroundCredentialAcquirerPort,
        work_objects: BackgroundWorkObjectSyncPort,
        policy: CredentialPollingPolicy,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not tenant_id.strip():
            raise ValueError("source_profile_configuration_invalid")
        self._tenant_id = tenant_id
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
            for candidate in await self._binding_store.list_poll_candidates(
                tenant_id=self._tenant_id
            )
            if candidate.tenant_id == self._tenant_id and self._is_due(candidate, now)
        ][: self._policy.global_concurrency]
        await asyncio.gather(*(self._run_candidate(candidate) for candidate in candidates))
        return len(candidates)

    async def _run_candidate(self, candidate: CredentialPollCandidate) -> None:
        try:
            await self._run_candidate_once(candidate)
        except StaleCredentialWrite:
            logging.getLogger(__name__).info("credential_write_stale")

    async def _run_candidate_once(self, candidate: CredentialPollCandidate) -> None:
        async with self._binding_store.poll_lock(
            candidate.ai_user_id, candidate.target_system, tenant_id=candidate.tenant_id
        ) as acquired:
            if not acquired:
                return
            refreshed = await self._binding_store.refresh_poll_candidate(
                candidate.ai_user_id, candidate.target_system, tenant_id=candidate.tenant_id
            )
            now = self._validated_now()
            if refreshed is None or not self._is_due(refreshed, now):
                return
            try:
                stamp = await self._binding_store.claim_write(refreshed.snapshot)
            except StaleCredentialWrite:
                return
            refreshed = refreshed.model_copy(update={"write_stamp": stamp})
            try:
                result = await self._acquirer.acquire(refreshed)
                if result.write_stamp is None:
                    raise CredentialAcquisitionError("local_failure")
                stamp = result.write_stamp
                principal = result.principal
                if (principal.org_ctx.tenant_id, principal.ai_user_id) != (
                    candidate.tenant_id,
                    candidate.ai_user_id,
                ):
                    raise CredentialAcquisitionError("identity_mismatch")
                await self._work_objects.sync_for_background(principal)
            except StaleCredentialWrite:
                return
            except CredentialAcquisitionError as error:
                if error.code in {"credentials_rejected", "identity_mismatch"}:
                    await self._binding_store.mark_terminal_authentication_failure(
                        candidate.ai_user_id,
                        candidate.target_system,
                        "invalid",
                        tenant_id=candidate.tenant_id,
                        expected_write=stamp,
                    )
                elif error.code == "captcha_required":
                    await self._binding_store.mark_terminal_authentication_failure(
                        candidate.ai_user_id,
                        candidate.target_system,
                        "captcha_required",
                        tenant_id=candidate.tenant_id,
                        expected_write=stamp,
                    )
                elif error.code in _COUNTED_NON_AUTHENTICATION_FAILURES:
                    await self._binding_store.mark_non_authentication_failure(
                        candidate.ai_user_id,
                        candidate.target_system,
                        tenant_id=candidate.tenant_id,
                        expected_write=stamp,
                    )
                else:
                    await self._binding_store.mark_non_counted_failure(
                        candidate.ai_user_id,
                        candidate.target_system,
                        tenant_id=candidate.tenant_id,
                        expected_write=stamp,
                    )
            except BackgroundWorkObjectSyncError as error:
                if error.authentication_denied:
                    await self._binding_store.mark_terminal_authentication_failure(
                        candidate.ai_user_id,
                        candidate.target_system,
                        "invalid",
                        tenant_id=candidate.tenant_id,
                        expected_write=stamp,
                    )
                elif error.failure_code in _COUNTED_NON_AUTHENTICATION_FAILURES:
                    await self._binding_store.mark_non_authentication_failure(
                        candidate.ai_user_id,
                        candidate.target_system,
                        tenant_id=candidate.tenant_id,
                        expected_write=stamp,
                    )
                else:
                    await self._binding_store.mark_non_counted_failure(
                        candidate.ai_user_id,
                        candidate.target_system,
                        tenant_id=candidate.tenant_id,
                        expected_write=stamp,
                    )
            except Exception:
                await self._binding_store.mark_non_counted_failure(
                    candidate.ai_user_id,
                    candidate.target_system,
                    tenant_id=candidate.tenant_id,
                    expected_write=stamp,
                )
            else:
                await self._binding_store.mark_poll_succeeded(
                    candidate.ai_user_id,
                    candidate.target_system,
                    tenant_id=candidate.tenant_id,
                    expected_write=stamp,
                )

    def _is_due(self, candidate: CredentialPollCandidate, now: datetime) -> bool:
        return _poll_is_due(self._policy, candidate, now)

    def _inside_work_hours(self, now: datetime) -> bool:
        local_hour = now.astimezone(ZoneInfo(self._policy.timezone_name)).hour
        return self._policy.work_start_hour <= local_hour < self._policy.work_end_hour

    def _validated_now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise TypeError("credential polling clock must be timezone-aware")
        return now


def _poll_is_due(
    policy: CredentialPollingPolicy, candidate: CredentialPollCandidate, now: datetime
) -> bool:
    multiplier = 2 ** min(candidate.poll_failure_count, 20)
    delay_seconds = min(
        policy.maximum_backoff_seconds,
        policy.interval_seconds * multiplier,
    )
    return candidate.updated_at + timedelta(seconds=delay_seconds) <= now


_SESSION_LOGGER = logging.getLogger("uvicorn.error.oa_session")
_RECOVERY_POLL_SECONDS = 0.5


@dataclass(frozen=True, slots=True)
class CredentialRecoveryPolicy:
    cooldown_seconds: int
    wait_seconds: int
    kick_window_seconds: int

    def __post_init__(self) -> None:
        if min(self.cooldown_seconds, self.wait_seconds, self.kick_window_seconds) <= 0:
            raise ValueError("credential recovery limits must be positive")


def _judge_recovery(
    observed: CredentialRecoveryState,
    current: CredentialRecoveryState | None,
    now: datetime,
) -> RecoveryOutcome | Literal["unchanged"]:
    """One decision table for in-lock reads, waits and fenced writes."""

    if (
        current is None
        or not current.has_password
        or current.binding_state == "unbound"
        or current.snapshot.binding_id != observed.snapshot.binding_id
    ):
        return "binding_unavailable"
    if current.revoked or current.poll_status == "invalid":
        return "rejected"
    if current.poll_status == "captcha_required":
        return "captcha_required"
    if current.binding_state == "active":
        return "browser_bound"
    if current.binding_state != "unverified":
        return "binding_unavailable"
    if current.session_write_marker != observed.session_write_marker:
        if (
            current.poll_status == "active"
            and current.session_expires_at is not None
            and current.session_expires_at > now
        ):
            return "refreshed_by_other"
        if current.poll_status == "retrying":
            return "backoff"
        return "binding_unavailable"
    return "unchanged"


class CredentialSessionRecovery:
    """On-demand relogin with the user's stored password binding, at most once per call.

    Shares the poll lock, write fencing, failure accounting and backoff with
    ``CredentialPollingService`` but never runs the background Work Object sync.
    """

    def __init__(
        self,
        *,
        binding_store: CredentialPollingStorePort,
        tenant_id: str,
        acquirer: BackgroundCredentialAcquirerPort,
        policy: CredentialPollingPolicy,
        recovery_policy: CredentialRecoveryPolicy,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic_clock: Callable[[], float] = monotonic,
    ) -> None:
        if not tenant_id.strip():
            raise ValueError("source_profile_configuration_invalid")
        self._tenant_id = tenant_id
        self._binding_store = binding_store
        self._acquirer = acquirer
        self._policy = policy
        self._recovery_policy = recovery_policy
        self._clock = clock
        self._monotonic = monotonic_clock
        # Process-local only: real login attempts, own recoveries and kick suspicion.
        self._login_attempted_at: dict[tuple[str, str], float] = {}
        self._recovered_at: dict[tuple[str, str], float] = {}
        self._kick_counts: dict[tuple[str, str], int] = {}
        self._suspended: set[tuple[str, str]] = set()

    async def recover_session(
        self,
        *,
        tenant_id: str,
        ai_user_id: str,
        observed: CredentialRecoveryState,
    ) -> RecoveryOutcome:
        if tenant_id != self._tenant_id or observed.snapshot.ai_user_id != ai_user_id:
            return "binding_unavailable"
        key = (tenant_id, ai_user_id)
        if key in self._suspended or self._suspected_kick(key):
            return "suspended"
        attempted = self._login_attempted_at.get(key)
        if (
            attempted is not None
            and self._monotonic() - attempted < self._recovery_policy.cooldown_seconds
        ):
            verdict = await self._reread(observed)
            return "backoff" if verdict == "unchanged" else verdict
        try:
            async with self._binding_store.poll_lock(
                ai_user_id, "oa", tenant_id=tenant_id
            ) as acquired:
                if acquired:
                    return await self._recover_locked(key, observed)
            return await self._wait_for_other_writer(observed)
        except StaleCredentialWrite:
            # A fence, busy writer or lost coordinator proves nothing about a new Session.
            verdict = await self._reread(observed)
            return "unavailable" if verdict == "unchanged" else verdict

    def _suspected_kick(self, key: tuple[str, str]) -> bool:
        """Count verified failures of a Session this process wrote within the window."""

        recovered = self._recovered_at.get(key)
        if recovered is None:
            return False
        since_recovery = self._monotonic() - recovered
        if since_recovery > self._recovery_policy.kick_window_seconds:
            self._kick_counts.pop(key, None)
            return False
        count = self._kick_counts.get(key, 0) + 1
        self._kick_counts[key] = count
        if count < 2:
            return False
        self._suspended.add(key)
        _SESSION_LOGGER.warning(
            "oa_recovery_suspended reason=suspected_session_kick count=%s since_recovery_s=%s",
            count,
            round(since_recovery),
        )
        return True

    async def _read(self, observed: CredentialRecoveryState) -> CredentialRecoveryState | None:
        return await self._binding_store.recovery_state(
            observed.snapshot.ai_user_id, tenant_id=observed.snapshot.tenant_id
        )

    async def _reread(
        self, observed: CredentialRecoveryState
    ) -> RecoveryOutcome | Literal["unchanged"]:
        return _judge_recovery(observed, await self._read(observed), self._validated_now())

    async def _wait_for_other_writer(self, observed: CredentialRecoveryState) -> RecoveryOutcome:
        deadline = self._monotonic() + self._recovery_policy.wait_seconds
        while self._monotonic() < deadline:
            await asyncio.sleep(_RECOVERY_POLL_SECONDS)
            verdict = await self._reread(observed)
            if verdict != "unchanged":
                return verdict
        return "unavailable"

    async def _recover_locked(
        self, key: tuple[str, str], observed: CredentialRecoveryState
    ) -> RecoveryOutcome:
        tenant_id, ai_user_id = key
        current = await self._read(observed)
        verdict = _judge_recovery(observed, current, self._validated_now())
        if verdict != "unchanged" or current is None:
            return "unavailable" if verdict == "unchanged" else verdict
        candidate = await self._binding_store.refresh_poll_candidate(
            ai_user_id, "oa", tenant_id=tenant_id
        )
        if candidate is None:
            return "backoff"
        if current.poll_status == "retrying" and not _poll_is_due(
            self._policy, candidate, self._validated_now()
        ):
            # Failure backoff is never bypassed by an on-demand request.
            return "backoff"
        stamp = await self._binding_store.claim_write(candidate.snapshot)
        candidate = candidate.model_copy(update={"write_stamp": stamp})
        self._login_attempted_at[key] = self._monotonic()
        succeeded = False
        try:
            result = await self._acquirer.acquire(candidate)
            if result.write_stamp is None:
                raise CredentialAcquisitionError("local_failure")
            stamp = result.write_stamp
            principal = result.principal
            if (principal.org_ctx.tenant_id, principal.ai_user_id) != key:
                raise CredentialAcquisitionError("identity_mismatch")
        except StaleCredentialWrite:
            raise
        except CredentialAcquisitionError as error:
            await self._mark_failure(key, error.code, stamp)
        except Exception:
            await self._mark_failure(key, "local_failure", stamp)
        else:
            await self._binding_store.mark_poll_succeeded(
                ai_user_id, "oa", tenant_id=tenant_id, expected_write=stamp
            )
            succeeded = True
        verdict = await self._reread(observed)
        if verdict == "refreshed_by_other" and succeeded:
            # The kick count survives recoveries; only a failure outside the window resets it.
            self._recovered_at[key] = self._monotonic()
            return "recovered"
        return "unavailable" if verdict == "unchanged" else verdict

    async def _mark_failure(
        self, key: tuple[str, str], code: str, stamp: CredentialWriteStamp
    ) -> None:
        # Same accounting as the background poll, so retries share one backoff.
        tenant_id, ai_user_id = key
        if code in {"credentials_rejected", "identity_mismatch"}:
            await self._binding_store.mark_terminal_authentication_failure(
                ai_user_id, "oa", "invalid", tenant_id=tenant_id, expected_write=stamp
            )
        elif code == "captcha_required":
            await self._binding_store.mark_terminal_authentication_failure(
                ai_user_id, "oa", "captcha_required", tenant_id=tenant_id, expected_write=stamp
            )
        elif code in _COUNTED_NON_AUTHENTICATION_FAILURES:
            await self._binding_store.mark_non_authentication_failure(
                ai_user_id, "oa", tenant_id=tenant_id, expected_write=stamp
            )
        else:
            await self._binding_store.mark_non_counted_failure(
                ai_user_id, "oa", tenant_id=tenant_id, expected_write=stamp
            )

    def _validated_now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise TypeError("credential recovery clock must be timezone-aware")
        return now


class OASessionKeeper:
    """User-activity OA heartbeat and verified-expiry recovery for one tenant."""

    def __init__(
        self,
        *,
        binding_store: CredentialPollingStorePort,
        tenant_id: str,
        load_session: Callable[[str], Awaitable[OASessionCredential | None]],
        heartbeat: Callable[[OASessionCredential], Awaitable[OASessionHeartbeatResult]],
        recovery: CredentialSessionRecovery | None,
        credential_ttl_seconds: int,
        heartbeat_interval_seconds: int,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic_clock: Callable[[], float] = monotonic,
    ) -> None:
        if not tenant_id.strip():
            raise ValueError("source_profile_configuration_invalid")
        if credential_ttl_seconds <= 0 or heartbeat_interval_seconds <= 5:
            raise ValueError("OA session keepalive limits are invalid")
        self._binding_store = binding_store
        self._tenant_id = tenant_id
        self._load_session = load_session
        self._heartbeat = heartbeat
        # None stops every automatic relogin (rollback / suspected kick), heartbeat stays.
        self._recovery = recovery
        self._credential_ttl = timedelta(seconds=credential_ttl_seconds)
        self._throttle_seconds = heartbeat_interval_seconds - 5
        self._clock = clock
        self._monotonic = monotonic_clock
        self._keepalive_cache: dict[tuple[str, str], tuple[float, OAKeepaliveView]] = {}

    async def observe(
        self, *, tenant_id: str, ai_user_id: str
    ) -> CredentialRecoveryState | None:
        if tenant_id != self._tenant_id:
            return None
        return await self._binding_store.recovery_state(ai_user_id, tenant_id=tenant_id)

    async def probe(
        self,
        *,
        tenant_id: str,
        ai_user_id: str,
        observed: CredentialRecoveryState,
        credential: OASessionCredential | None,
    ) -> OASessionProbeResult:
        if tenant_id != self._tenant_id or observed.snapshot.ai_user_id != ai_user_id:
            return "unavailable"
        session = credential
        try:
            if session is None:
                # A locally expired row is still probed: only OA can say it is dead.
                session = await self._load_session(ai_user_id)
            if session is None:
                return "unavailable"
            result = await self._heartbeat(session)
        except Exception as error:
            _SESSION_LOGGER.info(
                "oa_heartbeat_unavailable exception_type=%s", type(error).__name__
            )
            return "unavailable"
        finally:
            session = None
        if result == "alive":
            # Only the local deadline moves; poll timing and write fencing are untouched.
            extended = await self._binding_store.extend_session_expiry(
                observed.snapshot, self._clock() + self._credential_ttl
            )
            _SESSION_LOGGER.info("oa_session_expiry_extended applied=%s", extended)
        return result

    async def recover(
        self,
        *,
        tenant_id: str,
        ai_user_id: str,
        observed: CredentialRecoveryState,
        trigger: CredentialRecoveryTrigger,
    ) -> RecoveryOutcome:
        started = self._monotonic()
        outcome: RecoveryOutcome = (
            "suspended"
            if self._recovery is None
            else await self._recovery.recover_session(
                tenant_id=tenant_id, ai_user_id=ai_user_id, observed=observed
            )
        )
        _SESSION_LOGGER.info(
            "oa_session_recovery trigger=%s outcome=%s waited_ms=%s",
            trigger,
            outcome,
            round((self._monotonic() - started) * 1000),
        )
        return outcome

    async def keepalive(self, principal: Principal) -> OAKeepaliveView:
        key = (principal.org_ctx.tenant_id, principal.ai_user_id)
        cached = self._keepalive_cache.get(key)
        if cached is not None and self._monotonic() - cached[0] < self._throttle_seconds:
            return cached[1]
        view = OAKeepaliveView(oa=await self._keepalive_once(*key))
        self._keepalive_cache[key] = (self._monotonic(), view)
        _SESSION_LOGGER.info("oa_keepalive result=%s", view.oa)
        return view

    async def _keepalive_once(self, tenant_id: str, ai_user_id: str) -> OAKeepaliveStatus:
        observed = await self.observe(tenant_id=tenant_id, ai_user_id=ai_user_id)
        if observed is None or observed.session_write_marker is None:
            return "binding_unavailable"
        if observed.revoked:
            return "rejected"
        probe = await self.probe(
            tenant_id=tenant_id, ai_user_id=ai_user_id, observed=observed, credential=None
        )
        if probe != "dead":
            return probe
        return await self.recover(
            tenant_id=tenant_id, ai_user_id=ai_user_id, observed=observed, trigger="keepalive"
        )


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
    "CredentialRecoveryPolicy",
    "CredentialSessionRecovery",
    "OASessionKeeper",
)
