"""Recovery scheduling regressions; no provider or authorization proof is simulated."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Literal, cast

import pytest

from app.browser_skill.models import BrowserOwner
from app.browser_skill.runtime import BrowserReadWorker
from app.ports.browser_profile_store import BrowserProfileStorePort
from app.ports.browser_read_execution import BrowserReadExecutionPort, BrowserWorkerCheckpoint
from app.ports.browser_run_store import (
    BrowserRunStorePort,
    DispatchFailureCode,
    ProtectedRunEnvelope,
    RunAdmission,
    RunEffect,
    RunPhase,
    RunSnapshot,
    RunVerification,
)


def persisted_run(*, phase: RunPhase, effect: RunEffect = "not_sent") -> RunSnapshot:
    """Synthetic persisted state only; these bytes are never offered as provider proof."""
    deadline = datetime.now(UTC) + timedelta(minutes=5)
    owner = BrowserOwner(tenant_id="tenant", user_id="reader", session_id="conversation")
    admission = RunAdmission(
        owner=owner, task_id="task", run_id="run", target_system="oa",
        binding_id="binding", binding_revision=1, auth_fingerprint=b"a" * 32,
        auth_expires_at=deadline, publication_digest=b"p" * 32,
        input_revision=1, input_digest=b"i" * 32,
        protected_input=ProtectedRunEnvelope(
            "aes256gcm-browser-input-v1", "synthetic", b"n" * 12, b"c" * 16,
        ),
    )
    return RunSnapshot(
        admission=admission, state_revision=9,
        status="waiting_user" if phase == "waiting_user" else "running",
        phase=phase, worker_id="recovery", worker_epoch=2, worker_deadline=deadline,
        provider_key="provider", provider_manifest_digest=b"m" * 32, lease_epoch=1,
        profile_generation_id=None, capture_operation_id=None, capture_status="not_requested",
        cancel_requested=False, cancel_acknowledged=False, effect=effect, verification=None,
        verification_evidence_digest=None, cleanup="pending", error_code=None,
        dispatch_failure_code=None, terminal_revision=None, terminal_event_id=None,
        result_digest=None, protected_result=None,
    )


class RecordingStore:
    """Only persistence transitions under test; not a DB or current-auth implementation."""

    def __init__(self, run: RunSnapshot) -> None:
        self.run = run
        self.transitions: list[tuple[str, str, str | None]] = []

    async def claim_next(
        self, owner: BrowserOwner, *, worker_id: str, ttl_seconds: int,
    ) -> RunSnapshot:
        assert owner == self.run.owner and worker_id == self.run.worker_id
        assert ttl_seconds > 0
        return self.run

    async def get(self, owner: BrowserOwner, task_id: str, run_id: str) -> RunSnapshot:
        assert (owner, task_id, run_id) == (self.run.owner, self.run.task_id, self.run.run_id)
        return self.run

    async def renew(self, claim: RunSnapshot, *, ttl_seconds: int) -> RunSnapshot:
        assert claim == self.run and ttl_seconds > 0
        self.run = replace(self.run, state_revision=self.run.state_revision + 1)
        return self.run

    async def advance(
        self, claim: RunSnapshot, *, phase: RunPhase, effect: RunEffect,
    ) -> RunSnapshot:
        assert claim == self.run
        self.transitions.append(("advance", phase, effect))
        self.run = replace(
            self.run, phase=phase, effect=effect, state_revision=claim.state_revision + 1,
        )
        return self.run

    async def finalize(
        self, claim: RunSnapshot, *, status: Literal["completed", "failed", "cancelled"],
        error_code: str | None = None, verification: RunVerification | None = None,
        dispatch_failure_code: DispatchFailureCode | None = None,
    ) -> RunSnapshot:
        assert claim == self.run
        assert verification is None
        self.transitions.append(("finalize", status, error_code))
        self.run = replace(
            self.run, status=status, phase=None, error_code=error_code,
            dispatch_failure_code=dispatch_failure_code, state_revision=claim.state_revision + 1,
            terminal_revision=claim.state_revision + 1, terminal_event_id="terminal",
        )
        return self.run


class RejectBusinessIO:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute(self, run: RunSnapshot, checkpoint: BrowserWorkerCheckpoint) -> None:
        self.calls.append("execute")
        raise AssertionError("recovery must not reopen the factory or repeat the query")

    async def cleanup(self, run: RunSnapshot) -> Literal["pending"]:
        self.calls.append("cleanup")
        # No release/termination proof: leave the independent obligation pending.
        return "pending"


@pytest.mark.parametrize("phase,effect", [
    ("running", "not_sent"), ("verifying", "not_sent"), ("waiting_user", "not_sent"),
    ("acquiring", "unknown"), ("acquiring", "acknowledged"), ("queued", "unknown"),
])
def test_recovered_started_or_uncertain_run_fails_without_business_io(
    phase: RunPhase, effect: RunEffect,
) -> None:
    store = RecordingStore(persisted_run(phase=phase, effect=effect))
    execution = RejectBusinessIO()
    worker = BrowserReadWorker(
        cast(BrowserRunStorePort, store), cast(BrowserReadExecutionPort, execution),
        cast(BrowserProfileStorePort, object()), worker_id="recovery", enabled=True,
    )
    result = asyncio.run(worker.run_next(store.run.owner))
    assert result is not None
    assert (result.status, result.effect, result.error_code, result.dispatch_failure_code) == (
        "failed", "unknown", "browser_effect_unknown", "effect_unknown",
    )
    assert store.transitions == [
        ("advance", phase, "unknown"), ("finalize", "failed", "browser_effect_unknown"),
    ]
    assert execution.calls == ["cleanup"]
    assert result.cleanup == "pending" and result.protected_result is None


def test_recovered_verified_result_completes_without_business_io() -> None:
    protected = ProtectedRunEnvelope(
        "aes256gcm-browser-result-v1", "synthetic", b"r" * 12, b"v" * 16,
    )
    persisted = replace(
        persisted_run(phase="verifying", effect="acknowledged"), verification="verified",
        verification_evidence_digest=b"e" * 32, result_digest=b"r" * 32,
        protected_result=protected, capture_operation_id="capture", capture_status="failed",
    )
    store, execution = RecordingStore(persisted), RejectBusinessIO()
    worker = BrowserReadWorker(
        cast(BrowserRunStorePort, store), cast(BrowserReadExecutionPort, execution),
        cast(BrowserProfileStorePort, object()), worker_id="recovery", enabled=True,
    )
    result = asyncio.run(worker.run_next(persisted.owner))
    assert result is not None
    assert (result.status, result.verification, result.error_code) == (
        "completed", "verified", None,
    )
    assert result.protected_result is protected and result.result_digest == persisted.result_digest
    assert result.verification_evidence_digest == persisted.verification_evidence_digest
    assert result.capture_operation_id == "capture" and result.capture_status == "failed"
    assert store.transitions == [("finalize", "completed", None)]
    assert execution.calls == ["cleanup"]
    assert result.cleanup == "pending"
