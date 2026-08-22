"""Credential polling safety, retry, and stale-candidate tests."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import SecretStr

from app.api.v1.work_objects import WorkObjectService
from app.credential_polling import (
    CREDENTIAL_POLLING_TASK_TYPE,
    CredentialPollingPolicy,
    CredentialPollingScheduler,
    CredentialPollingService,
)
from app.infra.auth.background import OAPasswordCredentialAcquirer
from app.infra.observability.postgresql_trace import PostgreSQLTraceWriter
from app.infra.sdui.response_envelope_builder import ResponseEnvelopeBuilder
from app.ports.auth import LoginCredential, Principal, PrincipalOrgContext
from app.ports.capability_gateway import ExecutionResult
from app.ports.credential_binding import (
    BackgroundWorkObjectSyncError,
    CredentialAcquisitionError,
    CredentialAcquisitionFailureCode,
    CredentialBindingView,
    CredentialPollCandidate,
    CredentialTargetSystem,
    CredentialTerminalFailure,
    PasswordBindingCredential,
)
from app.ports.job_queue import JobStatus
from app.ports.request_context import RequestOrgContext
from app.ports.work_object import (
    OAPendingWorkSnapshot,
    WorkObjectHandlingMark,
    WorkObjectRecord,
)

NOW = datetime(2026, 8, 21, 2, 0, tzinfo=UTC)
CANDIDATE = CredentialPollCandidate(
    ai_user_id="usr_v1_synthetic",
    target_system="oa",
    poll_failure_count=0,
    updated_at=NOW - timedelta(minutes=11),
)
PRINCIPAL = Principal(
    ai_user_id=CANDIDATE.ai_user_id,
    display_name="Synthetic User",
    roles=(),
    org_ctx=PrincipalOrgContext(),
)


class FakeBindingStore:
    def __init__(self) -> None:
        self.listed_candidate: CredentialPollCandidate | None = CANDIDATE
        self.candidate: CredentialPollCandidate | None = CANDIDATE
        self.terminal: list[CredentialTerminalFailure] = []
        self.counted_failures = 0
        self.non_counted_failures = 0
        self.successes = 0
        self.refreshes = 0

    async def list_poll_candidates(self) -> list[CredentialPollCandidate]:
        return [self.listed_candidate] if self.listed_candidate is not None else []

    async def refresh_poll_candidate(
        self,
        ai_user_id: str,
        target_system: CredentialTargetSystem,
    ) -> CredentialPollCandidate | None:
        assert (ai_user_id, target_system) == (
            CANDIDATE.ai_user_id,
            CANDIDATE.target_system,
        )
        self.refreshes += 1
        return self.candidate

    @asynccontextmanager
    async def poll_lock(
        self,
        ai_user_id: str,
        target_system: CredentialTargetSystem,
    ) -> AsyncIterator[bool]:
        del ai_user_id, target_system
        yield True

    async def mark_poll_succeeded(
        self,
        ai_user_id: str,
        target_system: CredentialTargetSystem,
    ) -> None:
        del ai_user_id, target_system
        self.successes += 1
        self.candidate = None

    async def mark_non_authentication_failure(
        self,
        ai_user_id: str,
        target_system: CredentialTargetSystem,
    ) -> None:
        del ai_user_id, target_system
        self.counted_failures += 1

    async def mark_non_counted_failure(
        self,
        ai_user_id: str,
        target_system: CredentialTargetSystem,
    ) -> None:
        del ai_user_id, target_system
        self.non_counted_failures += 1

    async def mark_terminal_authentication_failure(
        self,
        ai_user_id: str,
        target_system: CredentialTargetSystem,
        failure: CredentialTerminalFailure,
    ) -> None:
        del ai_user_id, target_system
        self.terminal.append(failure)
        self.candidate = None

    async def bind_password(
        self,
        ai_user_id: str,
        target_system: CredentialTargetSystem,
        credential: PasswordBindingCredential,
    ) -> CredentialBindingView:
        raise AssertionError

    async def get_password_binding(
        self,
        ai_user_id: str,
        target_system: CredentialTargetSystem,
    ) -> CredentialBindingView:
        raise AssertionError

    async def unbind_password(
        self,
        ai_user_id: str,
        target_system: CredentialTargetSystem,
    ) -> CredentialBindingView:
        raise AssertionError

    async def load_password_for_poll(
        self,
        ai_user_id: str,
        target_system: CredentialTargetSystem,
    ) -> PasswordBindingCredential:
        raise AssertionError


class FakeAcquirer:
    def __init__(
        self,
        failure_code: CredentialAcquisitionFailureCode | None = None,
    ) -> None:
        self.failure_code = failure_code
        self.calls = 0

    async def acquire(self, candidate: CredentialPollCandidate) -> Principal:
        assert candidate.ai_user_id == PRINCIPAL.ai_user_id
        self.calls += 1
        if self.failure_code is not None:
            raise CredentialAcquisitionError(self.failure_code)
        return PRINCIPAL


class FakeWorkObjects:
    async def sync_for_background(self, principal: Principal) -> object:
        assert principal == PRINCIPAL
        return object()


class FailingWorkObjects:
    def __init__(self, failure: BackgroundWorkObjectSyncError) -> None:
        self.failure = failure

    async def sync_for_background(self, principal: Principal) -> object:
        assert principal == PRINCIPAL
        raise self.failure


class CanaryCaptchaSession:
    async def get_json(
        self,
        path: str,
        params: dict[str, str],
    ) -> dict[str, Any]:
        del path, params
        raise AssertionError("captcha preflight must use post_form")

    async def post_form(
        self,
        path: str,
        fields: dict[str, str],
    ) -> dict[str, Any]:
        assert path == "/api/hrm/login/getLoginForm"
        assert fields == {}
        return {"loginSetting": {"hasValidateCode": False}}

    def cookies(self) -> dict[str, str]:
        return {}


class CanaryAuthentication:
    def __init__(self, canary: str) -> None:
        self._canary = canary
        self.calls = 0

    async def authenticate(self, credential: LoginCredential) -> Principal:
        assert credential.loginid.get_secret_value() == "synthetic-login"
        assert credential.userpassword.get_secret_value() == self._canary
        self.calls += 1
        return PRINCIPAL


class CanaryBindingStore(FakeBindingStore):
    def __init__(self, canary: str) -> None:
        super().__init__()
        self._canary = canary

    async def load_password_for_poll(
        self,
        ai_user_id: str,
        target_system: CredentialTargetSystem,
    ) -> PasswordBindingCredential:
        assert (ai_user_id, target_system) == (
            CANDIDATE.ai_user_id,
            CANDIDATE.target_system,
        )
        return PasswordBindingCredential(
            login_id=SecretStr("synthetic-login"),
            password=SecretStr(self._canary),
        )


class CanaryGateway:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def execute_capability(
        self,
        task_id: str,
        session_id: str,
        ai_user_id: str,
        capability_id: str,
        arguments: dict[str, Any],
        request_context: RequestOrgContext,
    ) -> ExecutionResult:
        self.calls.append(
            {
                "task_id": task_id,
                "session_id": session_id,
                "ai_user_id": ai_user_id,
                "capability_id": capability_id,
                "arguments": arguments,
                "request_context": request_context.model_dump(mode="json"),
            }
        )
        return ExecutionResult(
            status="completed",
            trace_id=request_context.request_id,
            data={
                "workflows": [],
                "returned_count": 0,
                "authoritative_count": 0,
                "is_complete": True,
            },
        )


class CanaryWorkObjectStore:
    async def upsert_oa_pending_workflows(
        self,
        *,
        assignee_ai_user_id: str,
        assignee_display_name: str,
        snapshots: list[OAPendingWorkSnapshot],
        fetched_at: datetime,
    ) -> None:
        del assignee_ai_user_id, assignee_display_name, fetched_at
        assert snapshots == []

    async def list_for_assignee(
        self,
        assignee_ai_user_id: str,
        *,
        limit: int = 201,
    ) -> list[WorkObjectRecord]:
        del limit
        assert assignee_ai_user_id == PRINCIPAL.ai_user_id
        return []

    async def get_for_assignee(
        self,
        work_object_id: str,
        assignee_ai_user_id: str,
    ) -> WorkObjectRecord | None:
        del work_object_id, assignee_ai_user_id
        return None

    async def set_handling_mark_for_assignee(
        self,
        work_object_id: str,
        assignee_ai_user_id: str,
        mark: WorkObjectHandlingMark,
        *,
        marked_at: datetime,
    ) -> WorkObjectRecord | None:
        del work_object_id, assignee_ai_user_id, mark, marked_at
        return None


def _service(
    store: FakeBindingStore,
    acquirer: FakeAcquirer,
    *,
    clock: datetime = NOW,
) -> CredentialPollingService:
    return CredentialPollingService(
        binding_store=store,
        acquirer=acquirer,
        work_objects=FakeWorkObjects(),
        policy=CredentialPollingPolicy(
            interval_seconds=600,
            maximum_backoff_seconds=3600,
            work_start_hour=8,
            work_end_hour=18,
            timezone_name="Asia/Shanghai",
            global_concurrency=4,
            scheduler_tick_seconds=60,
        ),
        clock=lambda: clock,
    )


def _service_with_work_objects(
    store: FakeBindingStore,
    work_objects: FailingWorkObjects,
) -> CredentialPollingService:
    return CredentialPollingService(
        binding_store=store,
        acquirer=FakeAcquirer(),
        work_objects=work_objects,
        policy=CredentialPollingPolicy(
            interval_seconds=600,
            maximum_backoff_seconds=3600,
            work_start_hour=8,
            work_end_hour=18,
            timezone_name="Asia/Shanghai",
            global_concurrency=4,
            scheduler_tick_seconds=60,
        ),
        clock=lambda: NOW,
    )


def test_password_rejection_is_terminal_and_does_not_consume_failure_count() -> None:
    store = FakeBindingStore()
    acquirer = FakeAcquirer("credentials_rejected")

    assert asyncio.run(_service(store, acquirer).run_due()) == 1

    assert store.terminal == ["invalid"]
    assert store.counted_failures == 0
    assert store.non_counted_failures == 0
    assert acquirer.calls == 1


def test_captcha_is_terminal_and_does_not_consume_failure_count() -> None:
    store = FakeBindingStore()

    asyncio.run(_service(store, FakeAcquirer("captcha_required")).run_due())

    assert store.terminal == ["captcha_required"]
    assert store.counted_failures == 0


@pytest.mark.parametrize(
    "failure_code",
    ["network_unreachable", "timeout", "upstream_5xx", "invalid_response"],
)
def test_only_explicit_external_failure_classes_increment_counter(
    failure_code: CredentialAcquisitionFailureCode,
) -> None:
    store = FakeBindingStore()

    asyncio.run(_service(store, FakeAcquirer(failure_code)).run_due())

    assert store.counted_failures == 1
    assert store.non_counted_failures == 0
    assert store.terminal == []


@pytest.mark.parametrize("failure_code", ["local_failure", "unsupported_target"])
def test_local_or_unknown_failure_does_not_consume_external_counter(
    failure_code: CredentialAcquisitionFailureCode,
) -> None:
    store = FakeBindingStore()

    asyncio.run(_service(store, FakeAcquirer(failure_code)).run_due())

    assert store.counted_failures == 0
    assert store.non_counted_failures == 1


@pytest.mark.parametrize(
    ("failure", "expected_terminal", "expected_counted", "expected_non_counted"),
    [
        (BackgroundWorkObjectSyncError(authentication_denied=True), ["invalid"], 0, 0),
        (
            BackgroundWorkObjectSyncError(
                authentication_denied=False,
                failure_code="timeout",
            ),
            [],
            1,
            0,
        ),
        (BackgroundWorkObjectSyncError(authentication_denied=False), [], 0, 1),
    ],
)
def test_work_object_sync_counts_only_explicit_external_failures(
    failure: BackgroundWorkObjectSyncError,
    expected_terminal: list[CredentialTerminalFailure],
    expected_counted: int,
    expected_non_counted: int,
) -> None:
    store = FakeBindingStore()

    asyncio.run(
        _service_with_work_objects(store, FailingWorkObjects(failure)).run_due()
    )

    assert store.terminal == expected_terminal
    assert store.counted_failures == expected_counted
    assert store.non_counted_failures == expected_non_counted


def test_stale_candidate_is_revalidated_inside_lock_before_authentication() -> None:
    store = FakeBindingStore()
    store.candidate = None
    acquirer = FakeAcquirer()

    assert asyncio.run(_service(store, acquirer).run_due()) == 1

    assert store.refreshes == 1
    assert acquirer.calls == 0
    assert store.counted_failures == 0
    assert store.non_counted_failures == 0


def test_password_canary_stops_before_trace_and_response_envelope_boundary(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    canary = "CREDENTIAL-POLLING-BOUNDARY-CANARY"
    trace_method_names = (
        "record_event",
        "start_task_trace",
        "record_step",
        "record_policy_decision",
        "record_gateway_call",
        "finalize_task_trace",
    )
    trace_probes: list[AsyncMock] = []
    for method_name in trace_method_names:
        probe = AsyncMock(side_effect=AssertionError(f"trace:{canary}"))
        monkeypatch.setattr(PostgreSQLTraceWriter, method_name, probe)
        trace_probes.append(probe)
    envelope_probe = Mock(side_effect=AssertionError(f"envelope:{canary}"))
    monkeypatch.setattr(ResponseEnvelopeBuilder, "_build_envelope", envelope_probe)

    binding_store = CanaryBindingStore(canary)
    authentication = CanaryAuthentication(canary)
    gateway = CanaryGateway()
    work_objects = WorkObjectService(
        store=CanaryWorkObjectStore(),
        gateway=gateway,
        clock=lambda: NOW,
        id_factory=lambda: "credential-poll-canary",
    )
    acquirer = OAPasswordCredentialAcquirer(
        session_factory=CanaryCaptchaSession,
        authentication=authentication,
        binding_store=binding_store,
    )
    service = CredentialPollingService(
        binding_store=binding_store,
        acquirer=acquirer,
        work_objects=work_objects,
        policy=CredentialPollingPolicy(
            interval_seconds=600,
            maximum_backoff_seconds=3600,
            work_start_hour=8,
            work_end_hour=18,
            timezone_name="Asia/Shanghai",
            global_concurrency=4,
            scheduler_tick_seconds=60,
        ),
        clock=lambda: NOW,
    )

    assert asyncio.run(service.run_due()) == 1

    assert authentication.calls == 1
    assert binding_store.successes == 1
    assert len(gateway.calls) == 1
    rendered_gateway_call = json.dumps(gateway.calls, ensure_ascii=False, sort_keys=True)
    assert canary not in rendered_gateway_call
    assert "synthetic-login" not in rendered_gateway_call
    assert canary not in caplog.text
    for probe in trace_probes:
        probe.assert_not_awaited()
    envelope_probe.assert_not_called()

    with pytest.raises(AssertionError, match=canary):
        asyncio.run(trace_probes[0](object()))
    with pytest.raises(AssertionError, match=canary):
        envelope_probe()


def test_outside_work_hours_does_not_read_or_authenticate_candidates() -> None:
    store = FakeBindingStore()
    acquirer = FakeAcquirer()
    outside = datetime(2026, 8, 21, 23, 0, tzinfo=UTC)

    assert asyncio.run(_service(store, acquirer, clock=outside).run_due()) == 0
    assert store.refreshes == 0
    assert acquirer.calls == 0


@pytest.mark.parametrize(
    ("failure_count", "elapsed", "expected_calls"),
    [
        (1, timedelta(minutes=19, seconds=59), 0),
        (1, timedelta(minutes=20), 1),
        (20, timedelta(minutes=59, seconds=59), 0),
        (20, timedelta(hours=1), 1),
    ],
)
def test_failure_backoff_is_exponential_and_capped_by_configuration(
    failure_count: int,
    elapsed: timedelta,
    expected_calls: int,
) -> None:
    candidate = CANDIDATE.model_copy(
        update={
            "poll_failure_count": failure_count,
            "updated_at": NOW - elapsed,
        }
    )
    store = FakeBindingStore()
    store.listed_candidate = candidate
    store.candidate = candidate
    acquirer = FakeAcquirer()

    asyncio.run(_service(store, acquirer).run_due())

    assert acquirer.calls == expected_calls


class RecordingJobQueue:
    def __init__(
        self,
        *,
        status: JobStatus,
        failure: Exception | None = None,
    ) -> None:
        self.status = status
        self.failure = failure
        self.enqueued = asyncio.Event()
        self.calls: list[tuple[str, dict[str, Any], str | None]] = []

    async def enqueue(
        self,
        task_type: str,
        payload: dict[str, Any],
        task_id: str | None = None,
    ) -> str:
        self.calls.append((task_type, payload, task_id))
        self.enqueued.set()
        if self.failure is not None:
            raise self.failure
        return "credential-poll-job"

    async def get_status(self, job_id: str) -> JobStatus:
        assert job_id == "credential-poll-job"
        return self.status

    async def get_result(self, job_id: str) -> object | None:
        assert job_id == "credential-poll-job"
        return None


def test_scheduler_enqueues_job_queue_and_logs_only_fixed_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def exercise() -> None:
        queue = RecordingJobQueue(
            status="failed",
            failure=RuntimeError("PASSWORD-CANARY"),
        )
        scheduler = CredentialPollingScheduler(job_queue=queue, tick_seconds=60)
        await scheduler.start()
        await queue.enqueued.wait()
        await scheduler.stop()
        assert queue.calls == [(CREDENTIAL_POLLING_TASK_TYPE, {}, None)]

    asyncio.run(exercise())

    assert "credential_polling_tick_failed" in caplog.text
    assert "PASSWORD-CANARY" not in caplog.text
