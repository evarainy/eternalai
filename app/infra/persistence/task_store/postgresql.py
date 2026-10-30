"""PostgreSQL implementations of TaskStorePort and SessionStorePort."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from hmac import compare_digest
from typing import TYPE_CHECKING, Any, get_args
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infra.persistence.task_store.errors import DuplicateTaskError, TaskNotFoundError
from app.infra.persistence.task_store.transactional import (
    append_owned_event,
    lock_owned_task,
    transition_owned_task,
)
from app.ports.task_store import (
    TASK_STORE_QUERY_LIMIT,
    DFormCollectionError,
    DFormCollectionRecord,
    DFormCollectionSnapshot,
    SessionRecord,
    SessionStorePort,
    TaskEventRecord,
    TaskRecord,
    TaskStatus,
    TaskStorePort,
)

_VALID_TASK_STATUSES: frozenset[str] = frozenset(get_args(TaskStatus))


def d_form_continuation_conflict(
    previous: DFormCollectionRecord, checked: DFormCollectionRecord, *,
    expected_version: int | None, task_status: TaskStatus,
) -> bool:
    """Whether ``checked`` may not follow ``previous`` as the next D record version.

    The version and status guard stale cards and concurrent replies; expiry and
    capability stay fixed. Collected words are never rewritten or dropped: a
    description may only grow by appending (AgentScope continuation supplement).
    """
    return (task_status != "waiting_user" or expected_version != previous.version
            or checked.version != previous.version + 1
            or checked.expires_at != previous.expires_at
            or checked.capability_version != previous.capability_version
            or checked.capability_fingerprint != previous.capability_fingerprint
            or any(not (checked.arguments.get(key) == value or (
                       key == "description"
                       and checked.arguments.get(key, "").startswith(value)))
                   for key, value in previous.arguments.items()))


class PostgreSQLTaskStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    @staticmethod
    async def _d_form_snapshot(
        session: AsyncSession, task: TaskRecord,
    ) -> DFormCollectionSnapshot | None:
        # Read this fixed record by version, independently of list_events' first 100 rows.
        payload = (await session.execute(text(
            "SELECT payload FROM task_events WHERE task_id=:task_id"
            " AND event_type='d_form_collection.v1'"
            " ORDER BY CAST(payload->>'version' AS INTEGER) DESC LIMIT 1"
        ), {"task_id": task.task_id})).scalar_one_or_none()
        if payload is None:
            return None
        record = DFormCollectionRecord.model_validate(payload)
        if record.task_id != task.task_id or record.trace_id != task.trace_id:
            raise DFormCollectionError("collection_not_found")
        handoff = None
        if record.consumed and task.status == "completed":
            linked = (await session.execute(text(
                "SELECT e.payload,t.trace_id,r.state_revision FROM task_events e"
                " JOIN tasks t ON t.task_id=e.payload->>'canonical_task_id'"
                " LEFT JOIN browser_runs r ON r.task_id=t.task_id"
                " AND r.run_id=e.payload->>'canonical_run_id'"
                " AND r.tenant_id=t.tenant_id AND r.ai_user_id=t.ai_user_id"
                " AND r.session_id=t.session_id"
                " WHERE e.task_id=:task_id AND e.event_type='browser.intent_handoff.v1'"
                " AND t.tenant_id=:tenant_id AND t.ai_user_id=:ai_user_id"
                " AND t.session_id=:session_id AND t.client_request_id=:request_id"
                " LIMIT 1"
            ), {"task_id": task.task_id, "tenant_id": task.tenant_id,
                "ai_user_id": task.ai_user_id, "session_id": task.session_id,
                "request_id": record.request_id})).mappings().one_or_none()
            if linked is not None:
                handoff = dict(linked["payload"])
                if handoff.get("canonical_trace_id") != linked["trace_id"]:
                    raise DFormCollectionError("collection_not_found")
                handoff["state_revision"] = linked["state_revision"]
        return DFormCollectionSnapshot(record=record, task_status=task.status, handoff=handoff)

    async def get_d_form_collection(
        self, *, tenant_id: str, ai_user_id: str, session_id: str, task_id: str,
    ) -> DFormCollectionSnapshot | None:
        async with self._session_factory() as session, session.begin():
            task = await lock_owned_task(
                session, tenant_id=tenant_id, ai_user_id=ai_user_id,
                session_id=session_id, task_id=task_id,
            )
            return None if task is None else await self._d_form_snapshot(session, task)

    async def find_d_form_collection_request(
        self, *, tenant_id: str, ai_user_id: str, session_id: str,
        request_id: str, request_digest: str,
    ) -> DFormCollectionSnapshot | None:
        async with self._session_factory() as session, session.begin():
            row = (await session.execute(text(
                "SELECT t.task_id,t.session_id,t.ai_user_id,t.tenant_id,t.status,t.trace_id,"
                " t.capability_id,t.error_code,e.payload->>'request_digest' AS request_digest"
                " FROM tasks t JOIN task_events e ON e.task_id=t.task_id"
                " WHERE t.tenant_id=:tenant_id AND t.ai_user_id=:ai_user_id"
                " AND t.session_id=:session_id AND e.event_type='d_form_collection.v1'"
                " AND e.payload->>'request_id'=:request_id LIMIT 1"
            ), {"tenant_id": tenant_id, "ai_user_id": ai_user_id,
                "session_id": session_id, "request_id": request_id})).mappings().one_or_none()
            if row is None:
                return None
            if not compare_digest(row["request_digest"], request_digest):
                raise DFormCollectionError("collection_request_conflict")
            task = await lock_owned_task(
                session, tenant_id=tenant_id, ai_user_id=ai_user_id,
                session_id=session_id, task_id=row["task_id"],
            )
            return None if task is None else await self._d_form_snapshot(session, task)

    async def save_d_form_collection(
        self, record: DFormCollectionRecord, *, tenant_id: str, ai_user_id: str,
        session_id: str, expected_version: int | None,
    ) -> DFormCollectionSnapshot:
        owner = {"tenant_id": tenant_id, "ai_user_id": ai_user_id, "session_id": session_id}
        checked = DFormCollectionRecord.model_validate(record.model_dump())
        async with self._session_factory() as session, session.begin():
            task = await lock_owned_task(session, **owner, task_id=checked.task_id)
            if task is None or task.trace_id != checked.trace_id:
                raise DFormCollectionError("collection_not_found")
            current = await self._d_form_snapshot(session, task)
            if current is None:
                if (expected_version is not None or checked.version != 1 or checked.consumed
                        or task.status != "running"):
                    raise DFormCollectionError("collection_version_conflict")
            else:
                previous = current.record
                if previous.consumed:
                    raise DFormCollectionError("collection_consumed")
                if previous.expires_at <= datetime.now(UTC):
                    raise DFormCollectionError("collection_expired")
                if previous.version >= 32:
                    raise DFormCollectionError("collection_limit_reached")
                if d_form_continuation_conflict(
                    previous, checked, expected_version=expected_version, task_status=task.status,
                ):
                    raise DFormCollectionError("collection_version_conflict")
            if checked.expires_at <= datetime.now(UTC):
                raise DFormCollectionError("collection_expired")
            if (checked.consumed
                    and set(checked.arguments) != {"category", "position", "description"}):
                raise DFormCollectionError("collection_version_conflict")
            await append_owned_event(session, **owner, task_id=task.task_id, event=TaskEventRecord(
                event_id=str(uuid4()), task_id=task.task_id, event_type="d_form_collection.v1",
                timestamp=datetime.now(UTC), payload=checked.model_dump(mode="json"),
            ))
            status: TaskStatus = "running" if checked.consumed else "waiting_user"
            changed = await transition_owned_task(
                session, **owner, task_id=task.task_id,
                expected_statuses=(task.status,), status=status,
            )
            if changed is None:
                raise DFormCollectionError("collection_version_conflict")
            return DFormCollectionSnapshot(record=checked, task_status=status)

    async def create_task(self, record: TaskRecord) -> TaskRecord:
        if record.tenant_id is None or not record.tenant_id.strip():
            raise ValueError("tenant_id is required when creating a task")
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
                        " (task_id, session_id, ai_user_id, tenant_id, status,"
                        " trace_id, capability_id, error_code)"
                        " VALUES"
                        " (:task_id, :session_id, :ai_user_id, :tenant_id, :status,"
                        " :trace_id, :capability_id, :error_code)"
                    ),
                    {
                        "task_id": record.task_id,
                        "session_id": record.session_id,
                        "ai_user_id": record.ai_user_id,
                        "tenant_id": record.tenant_id,
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
                        "SELECT task_id, session_id, ai_user_id, tenant_id, status,"
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
                        " RETURNING task_id, session_id, ai_user_id, tenant_id, status,"
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
        tenant_id: str | None = None,
    ) -> list[TaskRecord]:
        filters: list[str] = []
        parameters: dict[str, str | int] = {"limit": TASK_STORE_QUERY_LIMIT}
        if session_id is not None:
            filters.append("session_id = :session_id")
            parameters["session_id"] = session_id
        if ai_user_id is not None:
            filters.append("ai_user_id = :ai_user_id")
            parameters["ai_user_id"] = ai_user_id
        if session_id is None and ai_user_id is None:
            raise ValueError("session_id or ai_user_id is required")
        if tenant_id is not None:
            filters.append("tenant_id = :tenant_id")
            parameters["tenant_id"] = tenant_id

        query = (
            "SELECT task_id, session_id, ai_user_id, tenant_id, status,"
            " trace_id, capability_id, error_code"
            " FROM tasks WHERE " + " AND ".join(filters) + " ORDER BY task_id ASC LIMIT :limit"
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
                payload=(row.payload if isinstance(row.payload, dict) else json.loads(row.payload)),
            )
            for row in rows
        ]


def _task_record_from_row(row: Any) -> TaskRecord:
    return TaskRecord(
        task_id=row.task_id,
        session_id=row.session_id,
        ai_user_id=row.ai_user_id,
        tenant_id=row.tenant_id,
        status=row.status,
        trace_id=row.trace_id,
        capability_id=row.capability_id,
        error_code=row.error_code,
    )


class PostgreSQLSessionStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_session(self, record: SessionRecord) -> SessionRecord:
        if not record.tenant_id.strip():
            raise ValueError("tenant_id must not be blank")
        async with self._session_factory() as session:
            await session.execute(
                text(
                    "INSERT INTO sessions (tenant_id, session_id) VALUES (:tenant_id, :session_id) "
                    "ON CONFLICT (tenant_id, session_id) DO NOTHING"
                ),
                {"tenant_id": record.tenant_id, "session_id": record.session_id},
            )
            await session.commit()
        return record

    async def get_session(self, session_id: str, *, tenant_id: str) -> SessionRecord | None:
        if not tenant_id.strip():
            raise ValueError("tenant_id must not be blank")
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT tenant_id, session_id FROM sessions "
                        "WHERE tenant_id = :tenant_id AND session_id = :session_id"
                    ),
                    {"tenant_id": tenant_id, "session_id": session_id},
                )
            ).fetchone()
        if row is None:
            return None
        return SessionRecord(tenant_id=row.tenant_id, session_id=row.session_id)


if TYPE_CHECKING:

    def _task_store_protocol_check(store: PostgreSQLTaskStore) -> TaskStorePort:
        return store

    def _session_store_protocol_check(store: PostgreSQLSessionStore) -> SessionStorePort:
        return store
