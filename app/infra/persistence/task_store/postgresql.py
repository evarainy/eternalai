"""PostgreSQL implementations of TaskStorePort and SessionStorePort."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, get_args

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infra.persistence.task_store.errors import DuplicateTaskError, TaskNotFoundError
from app.ports.task_store import (
    TASK_STORE_QUERY_LIMIT,
    SessionRecord,
    SessionStorePort,
    TaskEventRecord,
    TaskRecord,
    TaskStatus,
    TaskStorePort,
)

_VALID_TASK_STATUSES: frozenset[str] = frozenset(get_args(TaskStatus))


class PostgreSQLTaskStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_task(self, record: TaskRecord) -> TaskRecord:
        async with self._session_factory() as session:
            existing = (
                await session.execute(
                    text("SELECT task_id FROM tasks WHERE task_id = :task_id"),
                    {"task_id": record.task_id},
                )
            ).fetchone()
            if existing is not None:
                raise DuplicateTaskError(f"Task {record.task_id!r} already exists")
            try:
                await session.execute(
                    text(
                        "INSERT INTO tasks"
                        " (task_id, session_id, ai_user_id, status,"
                        " trace_id, capability_id, error_code)"
                        " VALUES"
                        " (:task_id, :session_id, :ai_user_id, :status,"
                        " :trace_id, :capability_id, :error_code)"
                    ),
                    {
                        "task_id": record.task_id,
                        "session_id": record.session_id,
                        "ai_user_id": record.ai_user_id,
                        "status": record.status,
                        "trace_id": record.trace_id,
                        "capability_id": record.capability_id,
                        "error_code": record.error_code,
                    },
                )
                await session.commit()
            except IntegrityError:
                await session.rollback()
                raise DuplicateTaskError(f"Task {record.task_id!r} already exists")
        return record

    async def get_task(self, task_id: str) -> TaskRecord | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT task_id, session_id, ai_user_id, status,"
                        " trace_id, capability_id, error_code"
                        " FROM tasks WHERE task_id = :task_id"
                    ),
                    {"task_id": task_id},
                )
            ).fetchone()
        if row is None:
            return None
        return _task_record_from_row(row)

    async def update_status(
        self,
        task_id: str,
        status: TaskStatus,
        error_code: str | None = None,
    ) -> TaskRecord:
        if status not in _VALID_TASK_STATUSES:
            raise ValueError(f"Invalid task status: {status!r}")
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    text(
                        "UPDATE tasks"
                        " SET status = :status, error_code = :error_code"
                        " WHERE task_id = :task_id"
                        " RETURNING task_id, session_id, ai_user_id, status,"
                        " trace_id, capability_id, error_code"
                    ),
                    {"task_id": task_id, "status": status, "error_code": error_code},
                )
            ).fetchone()
            if row is None:
                raise TaskNotFoundError(f"Task {task_id!r} not found")
            await session.commit()
        return _task_record_from_row(row)

    async def append_event(self, task_id: str, event: TaskEventRecord) -> None:
        async with self._session_factory() as session:
            existing = (
                await session.execute(
                    text("SELECT task_id FROM tasks WHERE task_id = :task_id"),
                    {"task_id": task_id},
                )
            ).fetchone()
            if existing is None:
                raise TaskNotFoundError(f"Task {task_id!r} not found")
            await session.execute(
                text(
                    "INSERT INTO task_events"
                    " (event_id, task_id, event_type, timestamp, payload)"
                    " VALUES"
                    " (:event_id, :task_id, :event_type, :timestamp, CAST(:payload AS JSONB))"
                ),
                {
                    "event_id": event.event_id,
                    "task_id": task_id,
                    "event_type": event.event_type,
                    "timestamp": event.timestamp,
                    "payload": json.dumps(event.payload),
                },
            )
            await session.commit()

    async def list_tasks(
        self,
        *,
        session_id: str | None = None,
        ai_user_id: str | None = None,
    ) -> list[TaskRecord]:
        filters: list[str] = []
        parameters: dict[str, str | int] = {"limit": TASK_STORE_QUERY_LIMIT}
        if session_id is not None:
            filters.append("session_id = :session_id")
            parameters["session_id"] = session_id
        if ai_user_id is not None:
            filters.append("ai_user_id = :ai_user_id")
            parameters["ai_user_id"] = ai_user_id
        if not filters:
            raise ValueError("session_id or ai_user_id is required")

        query = (
            "SELECT task_id, session_id, ai_user_id, status,"
            " trace_id, capability_id, error_code"
            " FROM tasks WHERE "
            + " AND ".join(filters)
            + " ORDER BY task_id ASC LIMIT :limit"
        )
        async with self._session_factory() as session:
            rows = (await session.execute(text(query), parameters)).fetchall()
        return [_task_record_from_row(row) for row in rows]

    async def list_events(self, task_id: str) -> list[TaskEventRecord]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT event_id, task_id, event_type, timestamp, payload"
                        " FROM task_events WHERE task_id = :task_id"
                        " ORDER BY timestamp ASC, event_id ASC LIMIT :limit"
                    ),
                    {"task_id": task_id, "limit": TASK_STORE_QUERY_LIMIT},
                )
            ).fetchall()
        return [
            TaskEventRecord(
                event_id=row.event_id,
                task_id=row.task_id,
                event_type=row.event_type,
                timestamp=row.timestamp,
                payload=(
                    row.payload if isinstance(row.payload, dict) else json.loads(row.payload)
                ),
            )
            for row in rows
        ]


def _task_record_from_row(row: Any) -> TaskRecord:
    return TaskRecord(
        task_id=row.task_id,
        session_id=row.session_id,
        ai_user_id=row.ai_user_id,
        status=row.status,
        trace_id=row.trace_id,
        capability_id=row.capability_id,
        error_code=row.error_code,
    )


class PostgreSQLSessionStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_session(self, record: SessionRecord) -> SessionRecord:
        # Idempotent: return existing record if session_id already exists.
        # SessionStorePort does not require duplicate rejection (unlike TaskStorePort).
        async with self._session_factory() as session:
            existing = (
                await session.execute(
                    text("SELECT session_id FROM sessions WHERE session_id = :session_id"),
                    {"session_id": record.session_id},
                )
            ).fetchone()
            if existing is not None:
                return SessionRecord(session_id=existing.session_id)
            await session.execute(
                text("INSERT INTO sessions (session_id) VALUES (:session_id)"),
                {"session_id": record.session_id},
            )
            await session.commit()
        return record

    async def get_session(self, session_id: str) -> SessionRecord | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    text("SELECT session_id FROM sessions WHERE session_id = :session_id"),
                    {"session_id": session_id},
                )
            ).fetchone()
        if row is None:
            return None
        return SessionRecord(session_id=row.session_id)


if TYPE_CHECKING:

    def _task_store_protocol_check(store: PostgreSQLTaskStore) -> TaskStorePort:
        return store

    def _session_store_protocol_check(store: PostgreSQLSessionStore) -> SessionStorePort:
        return store
