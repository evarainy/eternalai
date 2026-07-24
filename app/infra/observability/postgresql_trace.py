"""PostgreSQL TracePort and TraceQueryPort implementations."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ports.capability_gateway import ErrorCode
from app.ports.trace import (
    TRACE_QUERY_LIMIT,
    SanitizerHookFn,
    TraceEvent,
    TraceEventStatus,
    TraceEventType,
    TracePersistedEvent,
    redact_trace_attributes,
)


class TraceSanitizationError(RuntimeError):
    """Raised when trace attributes cannot be sanitized before persistence."""


class PostgreSQLTraceWriter:
    """Persist every semantic TraceEvent exactly once."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._sanitizer: SanitizerHookFn = redact_trace_attributes

    def set_sanitizer(self, hook: SanitizerHookFn) -> None:
        self._sanitizer = hook

    async def record_event(self, event: TraceEvent) -> None:
        attributes = self._sanitize_attributes(event.attributes)
        if attributes is None:
            raise TraceSanitizationError("trace attribute sanitization failed")

        async with self._session_factory() as session:
            await session.execute(
                text(
                    "INSERT INTO trace_events"
                    " (event_id, trace_id, task_id, session_id, event_type, status,"
                    " capability_id, error_code, attributes, created_at)"
                    " VALUES"
                    " (:event_id, :trace_id, :task_id, :session_id, :event_type,"
                    " :status, :capability_id, :error_code,"
                    " CAST(:attributes AS JSONB), :created_at)"
                ),
                {
                    "event_id": uuid4().hex,
                    "trace_id": event.trace_id,
                    "task_id": event.task_id,
                    "session_id": event.session_id,
                    "event_type": event.event_type,
                    "status": event.status,
                    "capability_id": event.capability_id,
                    "error_code": event.error_code,
                    "attributes": json.dumps(attributes),
                    "created_at": datetime.now(UTC),
                },
            )
            await session.commit()

    def _sanitize_attributes(
        self,
        attributes: dict[str, Any],
    ) -> dict[str, Any] | None:
        try:
            custom_sanitized = self._sanitizer(attributes)
            return redact_trace_attributes(custom_sanitized)
        except Exception:
            return None

    async def start_task_trace(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
    ) -> None:
        return None

    async def record_step(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
        event_type: TraceEventType,
        status: TraceEventStatus,
        capability_id: str | None = None,
        error_code: ErrorCode | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        await self.record_event(
            TraceEvent(
                trace_id=trace_id,
                task_id=task_id,
                session_id=session_id,
                event_type=event_type,
                status=status,
                capability_id=capability_id,
                error_code=error_code,
                attributes=attributes or {},
            )
        )

    async def record_policy_decision(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
        status: TraceEventStatus,
        capability_id: str | None = None,
        error_code: ErrorCode | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        await self.record_event(
            TraceEvent(
                trace_id=trace_id,
                task_id=task_id,
                session_id=session_id,
                event_type="policy_checked",
                status=status,
                capability_id=capability_id,
                error_code=error_code,
                attributes=attributes or {},
            )
        )

    async def record_gateway_call(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
        status: TraceEventStatus,
        capability_id: str | None = None,
        error_code: ErrorCode | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        await self.record_event(
            TraceEvent(
                trace_id=trace_id,
                task_id=task_id,
                session_id=session_id,
                event_type="gateway_pre_recorded",
                status=status,
                capability_id=capability_id,
                error_code=error_code,
                attributes=attributes or {},
            )
        )

    async def finalize_task_trace(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
        status: TraceEventStatus,
        capability_id: str | None = None,
        error_code: ErrorCode | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        return None


class PostgreSQLTraceReader:
    """Read bounded persisted trace events through parameterized queries."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list_events_by_trace(
        self,
        trace_id: str,
        *,
        task_id: str | None = None,
        session_id: str | None = None,
        limit: int = TRACE_QUERY_LIMIT,
    ) -> list[TracePersistedEvent]:
        return await self._list_events(
            trace_id=trace_id,
            task_id=task_id,
            session_id=session_id,
            limit=limit,
        )

    async def list_events_by_task(
        self,
        task_id: str,
        *,
        trace_id: str | None = None,
        session_id: str | None = None,
        limit: int = TRACE_QUERY_LIMIT,
    ) -> list[TracePersistedEvent]:
        return await self._list_events(
            trace_id=trace_id,
            task_id=task_id,
            session_id=session_id,
            limit=limit,
        )

    async def list_events_by_session(
        self,
        session_id: str,
        *,
        trace_id: str | None = None,
        task_id: str | None = None,
        limit: int = TRACE_QUERY_LIMIT,
    ) -> list[TracePersistedEvent]:
        return await self._list_events(
            trace_id=trace_id,
            task_id=task_id,
            session_id=session_id,
            limit=limit,
        )

    async def _list_events(
        self,
        *,
        trace_id: str | None,
        task_id: str | None,
        session_id: str | None,
        limit: int,
    ) -> list[TracePersistedEvent]:
        filters: list[str] = []
        parameters: dict[str, str | int] = {
            "limit": max(1, min(limit, TRACE_QUERY_LIMIT))
        }
        for column, value in (
            ("trace_id", trace_id),
            ("task_id", task_id),
            ("session_id", session_id),
        ):
            if value is not None:
                filters.append(f"{column} = :{column}")
                parameters[column] = value
        if not filters:
            raise ValueError("at least one trace filter is required")

        query = (
            "SELECT event_id, trace_id, task_id, session_id, event_type, status,"
            " capability_id, error_code, attributes, created_at"
            " FROM trace_events WHERE "
            + " AND ".join(filters)
            + " ORDER BY created_at ASC, event_id ASC LIMIT :limit"
        )
        async with self._session_factory() as session:
            rows = (await session.execute(text(query), parameters)).fetchall()
        return [_persisted_event_from_row(row) for row in rows]


def _persisted_event_from_row(row: Any) -> TracePersistedEvent:
    mapping = row._mapping
    attributes = mapping["attributes"]
    if isinstance(attributes, str):
        attributes = json.loads(attributes)
    return TracePersistedEvent(
        event_id=mapping["event_id"],
        trace_id=mapping["trace_id"],
        task_id=mapping["task_id"],
        session_id=mapping["session_id"],
        event_type=mapping["event_type"],
        status=mapping["status"],
        capability_id=mapping["capability_id"],
        error_code=mapping["error_code"],
        attributes=attributes,
        created_at=mapping["created_at"],
    )


__all__ = (
    "PostgreSQLTraceReader",
    "PostgreSQLTraceWriter",
    "TraceSanitizationError",
)
