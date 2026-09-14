"""Persistent, credential-free directory synchronization contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import AsyncContextManager, Literal, Protocol, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.ports.organization_directory import OrganizationDirectorySnapshot

DirectoryErrorCode = Literal[
    "source_unconfigured",
    "source_binding_unavailable",
    "source_authentication_failed",
    "source_timeout",
    "source_unavailable",
    "snapshot_incomplete",
    "snapshot_invalid",
    "storage_unavailable",
    "sync_interrupted",
]


class DirectorySourceError(RuntimeError):
    def __init__(self, code: DirectoryErrorCode) -> None:
        self.code: DirectoryErrorCode = (
            code if code in get_args(DirectoryErrorCode) else "source_unavailable"
        )
        super().__init__(self.code)


class OrganizationDirectorySyncStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    singleton_id: Literal[1] = 1
    snapshot_version: int = Field(ge=0, strict=True)
    source_fetched_at: datetime | None
    last_success_at: datetime | None
    last_attempt_started_at: datetime | None
    last_attempt_finished_at: datetime | None
    last_attempt_status: Literal["never", "running", "succeeded", "failed"]
    last_error_code: DirectoryErrorCode | None
    observed_at: datetime

    @field_validator(
        "source_fetched_at",
        "last_success_at",
        "last_attempt_started_at",
        "last_attempt_finished_at",
        "observed_at",
    )
    @classmethod
    def _utc_time(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("directory timestamps must be timezone aware")
        return value.astimezone(UTC)


class OrganizationDirectorySyncLease(Protocol):
    async def read_status(self) -> OrganizationDirectorySyncStatus: ...
    async def start_attempt(self) -> datetime: ...
    async def replace_snapshot(self, snapshot: OrganizationDirectorySnapshot) -> None: ...
    async def mark_failed(self, code: DirectoryErrorCode) -> None: ...


class OrganizationDirectorySyncPort(Protocol):
    async def read_status(self) -> OrganizationDirectorySyncStatus: ...
    def try_acquire(self) -> AsyncContextManager[OrganizationDirectorySyncLease | None]: ...
