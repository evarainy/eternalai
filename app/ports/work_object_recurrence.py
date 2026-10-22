"""Credential-free periodic dispatch contracts and calendar rules."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal, Protocol
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator

from app.ports.auth import Principal
from app.ports.work_object_attachment import AttachmentView
from app.ports.work_object_dispatch import (
    DispatchModel,
    DispatchRequest,
    DispatchSubject,
    PreparedDispatch,
    WorkDispatchError,
    fingerprint,
)
from app.ports.work_object_scope import AuthorizedWorkObjectScope


class SeriesTemplate(DispatchRequest):
    """Dispatch shape with occurrence-owned due time, never a saved absolute due date."""

    due_at: None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def canonicalize(self) -> SeriesTemplate:
        # Validate exactly the ordinary dispatch contract with a calculated deadline.
        values = self.model_dump(mode="python", exclude={"due_at"})
        validated = DispatchRequest.model_validate(
            {**values, "due_at": datetime(2000, 1, 1, tzinfo=UTC)}
        )
        for name in ("targets", "lead_target", "reminder_choices", "attachment_ids"):
            object.__setattr__(self, name, getattr(validated, name))
        return self


def local_clock(value: Any) -> time:
    parsed = time.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(parsed, time) or parsed.tzinfo is not None or parsed.microsecond:
        raise ValueError("local clock must be HH:MM[:SS]")
    return parsed


class DeadlineSpec(DispatchModel):
    days_after: int = Field(ge=0)
    local_time: time

    @field_validator("local_time", mode="before")
    @classmethod
    def parse_clock(cls, value: Any) -> time:
        return local_clock(value)


class SeriesRequest(DispatchModel):
    weekdays: list[int] = Field(min_length=1, max_length=7)
    local_time: time
    timezone: str = Field(min_length=1, max_length=128)
    start_date: date
    deadline_spec: DeadlineSpec
    template: SeriesTemplate
    enabled: bool = True

    @field_validator("weekdays")
    @classmethod
    def canonical_days(cls, value: list[int]) -> list[int]:
        if any(day < 1 or day > 7 for day in value):
            raise ValueError("weekdays must be 1..7")
        return sorted(set(value))

    @field_validator("local_time", mode="before")
    @classmethod
    def parse_clock(cls, value: Any) -> time:
        return local_clock(value)

    @field_validator("start_date", mode="before")
    @classmethod
    def parse_date(cls, value: Any) -> Any:
        return date.fromisoformat(value) if isinstance(value, str) else value

    @field_validator("timezone")
    @classmethod
    def known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("unknown IANA timezone") from exc
        return value


class SeriesView(SeriesRequest):
    series_id: UUID
    version: int
    next_due_at: datetime | None
    created_at: datetime
    updated_at: datetime
    attachments: list[AttachmentView] = Field(default_factory=list)

    @property
    def etag(self) -> str:
        return (
            '"wosr-'
            + fingerprint(
                self.model_dump(
                    mode="json",
                    exclude={"next_due_at", "attachments", "created_at", "updated_at"},
                )
            )
            + '"'
        )


class SeriesMutationResult(DispatchModel):
    series: SeriesView
    replayed: bool

    @property
    def etag(self) -> str:
        return self.series.etag


class SeriesPage(DispatchModel):
    items: list[SeriesView]
    next_cursor: str | None = None


class SeriesHistoryEntry(DispatchModel):
    version: int
    operation: Literal["create", "update", "enable", "disable"]
    occurred_at: datetime
    snapshot: SeriesView


class SeriesHistoryPage(DispatchModel):
    items: list[SeriesHistoryEntry]
    next_cursor: str | None = None


class SeriesOccurrenceItem(DispatchModel):
    work_object_id: str
    title: str
    status: str
    due_at: datetime | None


class SeriesOccurrenceView(DispatchModel):
    occurrence_id: UUID
    scheduled_at: datetime
    series_version: int
    state: Literal["generated", "blocked"]
    blocked_code: str | None
    generated_at: datetime | None
    items: list[SeriesOccurrenceItem]
    visible_count: int


class SeriesOccurrencePage(DispatchModel):
    items: list[SeriesOccurrenceView]
    next_cursor: str | None = None


class GenerationSummary(DispatchModel):
    generated: int
    blocked: int


def local_instant(day: date, clock: time, timezone: str) -> datetime:
    """Earliest fold; reject nonexistent wall times by an explicit UTC round trip."""
    zone = ZoneInfo(timezone)
    wall = datetime.combine(day, clock)
    instant = wall.replace(tzinfo=zone, fold=0).astimezone(UTC)
    if instant.astimezone(zone).replace(tzinfo=None) != wall:
        raise WorkDispatchError("work_object_series_local_time_invalid")
    return instant


def next_scheduled(request: SeriesRequest, after: datetime, *, inclusive: bool = False) -> datetime:
    zone = ZoneInfo(request.timezone)
    day = max(request.start_date, after.astimezone(zone).date())
    while True:
        if day.isoweekday() in request.weekdays:
            candidate = local_instant(day, request.local_time, request.timezone)
            if candidate > after or (inclusive and candidate == after):
                return candidate
        day += timedelta(days=1)


def occurrence_request(request: SeriesRequest, scheduled_at: datetime) -> DispatchRequest:
    day = scheduled_at.astimezone(ZoneInfo(request.timezone)).date()
    deadline = local_instant(
        day + timedelta(days=request.deadline_spec.days_after),
        request.deadline_spec.local_time,
        request.timezone,
    )
    return DispatchRequest.model_validate(
        {**request.template.model_dump(mode="python"), "due_at": deadline}
    )


class DispatchPreparationPort(Protocol):
    async def build_scope(self, principal: Principal) -> AuthorizedWorkObjectScope: ...
    async def prepare_dispatch(
        self,
        subject: DispatchSubject,
        *,
        request: DispatchRequest,
        idempotency_key: UUID,
        scheduled_at: datetime | None = None,
    ) -> PreparedDispatch: ...


PrepareOccurrence = Callable[
    [DispatchSubject, DispatchRequest, UUID, datetime], Awaitable[PreparedDispatch]
]


class WorkObjectSeriesStorePort(Protocol):
    async def series_id_for_task(
        self,
        scope: AuthorizedWorkObjectScope,
        work_object_id: str,
    ) -> UUID | None: ...

    async def create(
        self, subject: DispatchSubject, request: SeriesRequest, idempotency_key: UUID, now: datetime
    ) -> SeriesMutationResult: ...
    async def get(self, subject: DispatchSubject, series_id: UUID) -> SeriesView: ...
    async def list(
        self, subject: DispatchSubject, *, cursor: str | None, limit: int
    ) -> SeriesPage: ...
    async def update(
        self,
        subject: DispatchSubject,
        series_id: UUID,
        request: SeriesRequest,
        if_match: str,
        idempotency_key: UUID,
        now: datetime,
    ) -> SeriesMutationResult: ...
    async def set_enabled(
        self,
        subject: DispatchSubject,
        series_id: UUID,
        enabled: bool,
        if_match: str,
        idempotency_key: UUID,
        now: datetime,
    ) -> SeriesMutationResult: ...
    async def history(
        self, subject: DispatchSubject, series_id: UUID, *, cursor: str | None, limit: int
    ) -> SeriesHistoryPage: ...
    async def occurrences(
        self,
        subject: DispatchSubject,
        scope: AuthorizedWorkObjectScope,
        series_id: UUID,
        *,
        cursor: str | None,
        limit: int,
    ) -> SeriesOccurrencePage: ...
    async def generate_due(
        self, now: datetime, prepare: PrepareOccurrence
    ) -> GenerationSummary: ...
