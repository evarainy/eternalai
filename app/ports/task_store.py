"""Task and session store interface contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Protocol, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator

TaskStatus: TypeAlias = Literal[
    "created",
    "running",
    "waiting_user",
    "completed",
    "failed",
    "no_capability_found",
    "cancelled",
    "confirmation_invalidated",
]

TASK_STORE_QUERY_LIMIT = 100


class TaskRecord(BaseModel):
    task_id: str
    session_id: str
    ai_user_id: str
    tenant_id: str | None
    status: TaskStatus
    trace_id: str | None = None
    capability_id: str | None = None
    error_code: str | None = None

    @field_validator("tenant_id")
    @classmethod
    def _tenant_id_must_be_non_blank_when_known(
        cls,
        value: str | None,
    ) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("tenant_id must not be blank")
        return value


class TaskEventRecord(BaseModel):
    event_id: str
    task_id: str
    event_type: str
    timestamp: datetime
    payload: dict[str, Any]


class SessionRecord(BaseModel):
    tenant_id: str
    session_id: str


class DFormCollectionRecord(BaseModel):
    """Private, bounded three-field state in existing owner-scoped Task events."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    schema_version: Literal["d_form_collection.v1"] = "d_form_collection.v1"
    task_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,96}$")
    trace_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,96}$")
    capability_version: str = Field(min_length=1, max_length=100)
    capability_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    version: int = Field(strict=True, ge=1, le=32)
    expires_at: datetime
    arguments: dict[str, str] = Field(repr=False)
    request_id: str = Field(pattern=r"^[0-9a-f-]{36}$", repr=False)
    request_digest: str = Field(pattern=r"^[0-9a-f]{64}$", repr=False)
    consumed: bool = Field(strict=True, default=False)

    @field_validator("expires_at")
    @classmethod
    def _aware_expiry(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("d_form_collection_expiry_invalid")
        return value

    @field_validator("arguments")
    @classmethod
    def _bounded_arguments(cls, value: dict[str, str]) -> dict[str, str]:
        if not set(value) <= {"category", "position", "description"}:
            raise ValueError("d_form_collection_arguments_invalid")
        for name, item in value.items():
            if (not item.strip() or len(item) > (100 if name == "category" else 500)
                    or "\x00" in item or (name == "category" and len(item.splitlines()) != 1)):
                raise ValueError("d_form_collection_arguments_invalid")
        return value


class DFormCollectionSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    record: DFormCollectionRecord = Field(repr=False)
    task_status: TaskStatus
    handoff: dict[str, Any] | None = None


class DFormCollectionError(RuntimeError):
    def __init__(self, code: Literal[
        "collection_not_found", "collection_expired", "collection_version_conflict",
        "collection_consumed", "collection_request_conflict", "collection_limit_reached",
    ]) -> None:
        self.code = code
        super().__init__(code)


class DFormCollectionStorePort(Protocol):
    """D-only support; the generic TaskStorePort and its existing substitutes stay stable."""

    async def get_d_form_collection(
        self, *, tenant_id: str, ai_user_id: str, session_id: str, task_id: str,
    ) -> DFormCollectionSnapshot | None: ...

    async def find_d_form_collection_request(
        self, *, tenant_id: str, ai_user_id: str, session_id: str,
        request_id: str, request_digest: str,
    ) -> DFormCollectionSnapshot | None: ...

    async def save_d_form_collection(
        self, record: DFormCollectionRecord, *, tenant_id: str, ai_user_id: str,
        session_id: str, expected_version: int | None,
    ) -> DFormCollectionSnapshot: ...


class TaskStorePort(Protocol):
    async def create_task(self, record: TaskRecord) -> TaskRecord: ...

    async def get_task(self, task_id: str) -> TaskRecord | None: ...

    async def update_status(
        self,
        task_id: str,
        status: TaskStatus,
        error_code: str | None = None,
    ) -> TaskRecord: ...

    async def append_event(self, task_id: str, event: TaskEventRecord) -> None: ...

    async def list_tasks(
        self,
        *,
        session_id: str | None = None,
        ai_user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> list[TaskRecord]: ...

    async def list_events(self, task_id: str) -> list[TaskEventRecord]: ...


class SessionStorePort(Protocol):
    async def create_session(self, record: SessionRecord) -> SessionRecord: ...

    async def get_session(self, session_id: str, *, tenant_id: str) -> SessionRecord | None: ...
