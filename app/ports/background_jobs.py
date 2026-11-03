"""Background job ledger port: user-visible job records and their DTOs.

The ledger (``public.background_jobs``) is the business authority; the queue only
dispatches. These DTOs are independent of ``app.ports.job_queue.JobStatus`` and of the
frozen ``ResponseEnvelopeStatus`` enumeration.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict

BackgroundJobStatus = Literal[
    "accepted", "running", "cancel_requested", "succeeded", "failed", "cancelled"
]
ExternalEffect = Literal["none", "read_only"]
ACTIVE_STATUSES: frozenset[str] = frozenset({"accepted", "running", "cancel_requested"})
TERMINAL_STATUSES: frozenset[str] = frozenset({"succeeded", "failed", "cancelled"})
MAX_ACTIVE_JOBS_PER_USER = 5


class BackgroundJobError(Exception):
    """Contract failure carrying a stable error code and no input echo."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class BackgroundJobOwner(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str
    ai_user_id: str


class BackgroundJobView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: UUID
    kind: str
    status: BackgroundJobStatus
    result_code: str | None
    result_summary: dict[str, Any] | None
    created_at: datetime
    started_at: datetime | None
    cancel_requested_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime


class BackgroundJobAcceptance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job: BackgroundJobView
    deduplicated: bool


class BackgroundJobCancellation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job: BackgroundJobView
    # True only when the running execution was asked to stop (202).
    pending: bool


class BackgroundJobAcceptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    owner: BackgroundJobOwner
    session_id: str | None
    kind: str
    external_effect: ExternalEffect
    params: Mapping[str, Any]
    idempotency_key: UUID
    request_fingerprint: bytes


class BackgroundJobLedgerPort(Protocol):
    async def accept(self, request: BackgroundJobAcceptRequest) -> BackgroundJobAcceptance: ...

    async def get(self, owner: BackgroundJobOwner, job_id: UUID) -> BackgroundJobView | None: ...

    async def list(self, owner: BackgroundJobOwner, limit: int) -> list[BackgroundJobView]: ...

    async def cancel(
        self, owner: BackgroundJobOwner, job_id: UUID
    ) -> BackgroundJobCancellation | None: ...

    async def worker_alive(self) -> bool: ...


__all__ = (
    "ACTIVE_STATUSES",
    "BackgroundJobAcceptRequest",
    "BackgroundJobAcceptance",
    "BackgroundJobCancellation",
    "BackgroundJobError",
    "BackgroundJobLedgerPort",
    "BackgroundJobOwner",
    "BackgroundJobStatus",
    "BackgroundJobView",
    "ExternalEffect",
    "MAX_ACTIVE_JOBS_PER_USER",
    "TERMINAL_STATUSES",
)
