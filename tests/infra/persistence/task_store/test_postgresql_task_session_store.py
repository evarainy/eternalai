"""Integration tests for PostgreSQLTaskStore and PostgreSQLSessionStore.

Requires DATABASE_URL environment variable pointing to a live PostgreSQL instance.
Run: uv run alembic upgrade head  before executing these tests.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone

import pytest

DATABASE_URL = os.environ.get("DATABASE_URL")

if hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # type: ignore[attr-defined]


def _require_db() -> None:
    """Fail loudly instead of skipping: a silent skip reads as a pass.

    Matches tests/db/. To run without a database, exclude these paths
    explicitly (`--ignore=...`) so the omission is visible in the command.
    """
    if not DATABASE_URL:
        raise AssertionError("DATABASE_URL must be set by the test runner environment")


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_engine():  # type: ignore[return]
    from app.db.session import make_async_engine

    return make_async_engine(DATABASE_URL)


def _make_factory(engine):  # type: ignore[return]
    from app.db.session import make_async_session_factory

    return make_async_session_factory(engine)


def _task_store(factory):  # type: ignore[return]
    from app.infra.persistence.task_store.postgresql import PostgreSQLTaskStore

    return PostgreSQLTaskStore(factory)


def _session_store(factory):  # type: ignore[return]
    from app.infra.persistence.task_store.postgresql import PostgreSQLSessionStore

    return PostgreSQLSessionStore(factory)


# ── TaskStore tests ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("tenant_id", [None, "", " "])
def test_create_task_rejects_missing_or_blank_tenant_before_opening_a_db_session(
    tenant_id: str | None,
):
    from app.infra.persistence.task_store.postgresql import PostgreSQLTaskStore
    from app.ports.task_store import TaskRecord

    class ExplodingSessionFactory:
        def __call__(self):  # type: ignore[no-untyped-def]
            raise AssertionError("create_task reached the database session factory")

    store = PostgreSQLTaskStore(ExplodingSessionFactory())  # type: ignore[arg-type]
    record = TaskRecord.model_construct(
        task_id="historical-only-record",
        session_id="historical-only-session",
        ai_user_id="historical-only-user",
        tenant_id=tenant_id,
        status="completed",
    )

    with pytest.raises(ValueError, match="tenant_id is required when creating a task"):
        asyncio.run(store.create_task(record))


def test_create_task_returns_task_record_and_round_trips_arbitrary_strings():
    """
    Open-str lock: task_id, session_id, ai_user_id, error_code, trace_id, capability_id
    must all accept arbitrary strings and round-trip unchanged.
    """
    _require_db()
    from app.ports.task_store import TaskRecord

    task_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    ai_user_id = str(uuid.uuid4())
    trace_id = f"trace-{uuid.uuid4()}"
    capability_id = f"cap-{uuid.uuid4()}"
    error_code = f"err-{uuid.uuid4()}"

    record = TaskRecord(
        task_id=task_id,
        session_id=session_id,
        ai_user_id=ai_user_id,
        tenant_id="tenant-roundtrip",
        status="created",
        trace_id=trace_id,
        capability_id=capability_id,
        error_code=error_code,
    )

    async def _run() -> None:
        engine = _make_engine()
        try:
            store = _task_store(_make_factory(engine))
            result = await store.create_task(record)
            assert isinstance(result, TaskRecord)
            assert result.task_id == task_id

            fetched = await store.get_task(task_id)
            assert fetched is not None
            assert isinstance(fetched, TaskRecord)
            assert fetched.task_id == task_id
            assert fetched.session_id == session_id
            assert fetched.ai_user_id == ai_user_id
            assert fetched.tenant_id == "tenant-roundtrip"
            assert fetched.status == "created"
            assert fetched.trace_id == trace_id
            assert fetched.capability_id == capability_id
            assert fetched.error_code == error_code
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_create_task_duplicate_rejection_preserves_original_record():
    _require_db()
    from app.infra.persistence.task_store.errors import DuplicateTaskError
    from app.ports.task_store import TaskRecord

    task_id = str(uuid.uuid4())
    original = TaskRecord(
        task_id=task_id,
        session_id=str(uuid.uuid4()),
        ai_user_id="original-user",
        tenant_id="tenant-original",
        status="created",
    )
    duplicate = TaskRecord(
        task_id=task_id,
        session_id=str(uuid.uuid4()),
        ai_user_id="different-user",
        tenant_id="tenant-duplicate",
        status="running",
    )

    async def _run() -> None:
        engine = _make_engine()
        try:
            store = _task_store(_make_factory(engine))
            await store.create_task(original)
            with pytest.raises(DuplicateTaskError):
                await store.create_task(duplicate)
            fetched = await store.get_task(task_id)
            assert fetched is not None
            assert fetched.ai_user_id == "original-user"
            assert fetched.tenant_id == "tenant-original"
            assert fetched.status == "created"
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_get_task_returns_none_for_unknown_task_id():
    _require_db()

    async def _run() -> None:
        engine = _make_engine()
        try:
            store = _task_store(_make_factory(engine))
            result = await store.get_task(str(uuid.uuid4()))
            assert result is None
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_historical_null_tenant_task_can_be_read_and_updated_without_backfill():
    _require_db()
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker

    task_id = str(uuid.uuid4())

    async def _run() -> None:
        engine = _make_engine()
        try:
            async with engine.connect() as connection:
                transaction = await connection.begin()
                factory = async_sessionmaker(
                    bind=connection,
                    expire_on_commit=False,
                    join_transaction_mode="create_savepoint",
                )
                try:
                    async with factory() as session:
                        await session.execute(
                            text(
                                "INSERT INTO tasks"
                                " (task_id, session_id, ai_user_id, tenant_id, status)"
                                " VALUES (:task_id, :session_id, :ai_user_id, NULL, 'created')"
                            ),
                            {
                                "task_id": task_id,
                                "session_id": str(uuid.uuid4()),
                                "ai_user_id": "historical-user",
                            },
                        )
                        await session.commit()

                    store = _task_store(factory)
                    fetched = await store.get_task(task_id)
                    assert fetched is not None
                    assert fetched.tenant_id is None

                    updated = await store.update_status(task_id, "completed")
                    assert updated.status == "completed"
                    assert updated.tenant_id is None
                finally:
                    await transaction.rollback()
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_update_status_round_trips_all_task_status_values():
    """
    HARD MANDATE (P2-1): Every allowed TaskStatus value must be positively constructed
    and verified to round-trip through update_status and get_task.
    """
    _require_db()
    from typing import get_args

    from app.ports.task_store import TaskRecord, TaskStatus

    all_statuses = list(get_args(TaskStatus))

    async def _run() -> None:
        engine = _make_engine()
        try:
            store = _task_store(_make_factory(engine))
            for status in all_statuses:
                task_id = str(uuid.uuid4())
                record = TaskRecord(
                    task_id=task_id,
                    session_id=str(uuid.uuid4()),
                    ai_user_id="u1",
                    tenant_id="tenant-status",
                    status="created",
                )
                await store.create_task(record)
                result = await store.update_status(task_id, status)  # type: ignore[arg-type]
                assert isinstance(result, TaskRecord), (
                    f"update_status({status!r}) did not return TaskRecord"
                )
                assert result.status == status, f"expected {status!r}, got {result.status!r}"
                fetched = await store.get_task(task_id)
                assert fetched is not None
                assert fetched.status == status, (
                    f"get_task after update_status({status!r}) mismatch"
                )
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_update_status_sets_error_code_correctly():
    _require_db()
    from app.ports.task_store import TaskRecord

    task_id = str(uuid.uuid4())
    error_code = f"err-{uuid.uuid4()}"

    async def _run() -> None:
        engine = _make_engine()
        try:
            store = _task_store(_make_factory(engine))
            await store.create_task(
                TaskRecord(
                    task_id=task_id,
                    session_id=str(uuid.uuid4()),
                    ai_user_id="u1",
                    tenant_id="tenant-error",
                    status="created",
                )
            )
            result = await store.update_status(task_id, "failed", error_code=error_code)
            assert isinstance(result, TaskRecord)
            assert result.error_code == error_code
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_update_status_raises_task_not_found_error_for_unknown_task_id():
    _require_db()
    from app.infra.persistence.task_store.errors import TaskNotFoundError

    async def _run() -> None:
        engine = _make_engine()
        try:
            store = _task_store(_make_factory(engine))
            with pytest.raises(TaskNotFoundError):
                await store.update_status(str(uuid.uuid4()), "running")
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_update_status_raises_value_error_for_invalid_status_before_db_write():
    """ValueError must be raised before any DB write — use unknown task_id to prove ordering."""
    _require_db()

    async def _run() -> None:
        engine = _make_engine()
        try:
            store = _task_store(_make_factory(engine))
            with pytest.raises(ValueError):
                await store.update_status(str(uuid.uuid4()), "INVALID_STATUS")  # type: ignore[arg-type]
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_append_event_returns_none_and_verifies_event_id_type_payload():
    """
    HARD MANDATE (P3-1): direct SQL verify must assert event_id, event_type, and payload.
    Open-str lock: event_type uses arbitrary value.
    """
    _require_db()
    from sqlalchemy import text

    from app.ports.task_store import TaskEventRecord, TaskRecord

    task_id = str(uuid.uuid4())
    event_id = str(uuid.uuid4())
    event_type = f"evt-{uuid.uuid4()}"
    payload = {"key": "value", "nested": {"n": 1}}

    async def _run() -> None:
        engine = _make_engine()
        try:
            factory = _make_factory(engine)
            store = _task_store(factory)
            await store.create_task(
                TaskRecord(
                    task_id=task_id,
                    session_id=str(uuid.uuid4()),
                    ai_user_id="u1",
                    tenant_id="tenant-event",
                    status="created",
                )
            )
            event = TaskEventRecord(
                event_id=event_id,
                task_id=task_id,
                event_type=event_type,
                timestamp=datetime.now(tz=timezone.utc),
                payload=payload,
            )
            result = await store.append_event(task_id, event)
            assert result is None

            # Direct SQL verify: event_id, event_type, payload all round-trip
            async with factory() as session:
                row = (
                    await session.execute(
                        text(
                            "SELECT event_id, event_type, payload"
                            " FROM task_events WHERE event_id = :eid"
                        ),
                        {"eid": event_id},
                    )
                ).fetchone()
            assert row is not None
            assert row.event_id == event_id
            assert row.event_type == event_type
            # psycopg3 may return dict or str; normalise
            persisted_payload = (
                row.payload if isinstance(row.payload, dict) else json.loads(row.payload)
            )
            assert persisted_payload == payload
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_append_event_raises_task_not_found_error_for_unknown_task_id():
    _require_db()
    from app.infra.persistence.task_store.errors import TaskNotFoundError
    from app.ports.task_store import TaskEventRecord

    async def _run() -> None:
        engine = _make_engine()
        try:
            store = _task_store(_make_factory(engine))
            event = TaskEventRecord(
                event_id=str(uuid.uuid4()),
                task_id=str(uuid.uuid4()),
                event_type="any-type",
                timestamp=datetime.now(tz=timezone.utc),
                payload={},
            )
            with pytest.raises(TaskNotFoundError):
                await store.append_event(str(uuid.uuid4()), event)
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_list_tasks_supports_session_user_and_intersection_filters():
    _require_db()
    from app.ports.task_store import TaskRecord

    session_id = str(uuid.uuid4())
    other_session_id = str(uuid.uuid4())
    user_id = f"user-{uuid.uuid4()}"
    other_user_id = f"user-{uuid.uuid4()}"

    records = [
        TaskRecord(
            task_id=str(uuid.uuid4()),
            session_id=session_id,
            ai_user_id=user_id,
            tenant_id="tenant-a",
            status="completed",
        ),
        TaskRecord(
            task_id=str(uuid.uuid4()),
            session_id=session_id,
            ai_user_id=other_user_id,
            tenant_id="tenant-b",
            status="failed",
            error_code="adapter_error",
        ),
        TaskRecord(
            task_id=str(uuid.uuid4()),
            session_id=other_session_id,
            ai_user_id=user_id,
            tenant_id="tenant-a",
            status="running",
        ),
    ]

    async def _run() -> None:
        engine = _make_engine()
        try:
            store = _task_store(_make_factory(engine))
            for record in records:
                await store.create_task(record)

            by_session = await store.list_tasks(session_id=session_id)
            by_user = await store.list_tasks(ai_user_id=user_id)
            intersection = await store.list_tasks(
                session_id=session_id,
                ai_user_id=user_id,
            )
            tenant_session = await store.list_tasks(
                session_id=session_id,
                tenant_id="tenant-a",
            )
            tenant_user = await store.list_tasks(
                ai_user_id=user_id,
                tenant_id="tenant-a",
            )
            cross_tenant = await store.list_tasks(
                ai_user_id=user_id,
                tenant_id="tenant-b",
            )

            assert {item.task_id for item in by_session} == {
                records[0].task_id,
                records[1].task_id,
            }
            assert {item.task_id for item in by_user} == {
                records[0].task_id,
                records[2].task_id,
            }
            assert intersection == [records[0]]
            assert tenant_session == [records[0]]
            assert {item.task_id for item in tenant_user} == {
                records[0].task_id,
                records[2].task_id,
            }
            assert cross_tenant == []
            with pytest.raises(ValueError, match="session_id or ai_user_id"):
                await store.list_tasks()
            with pytest.raises(ValueError, match="session_id or ai_user_id"):
                await store.list_tasks(tenant_id="tenant-a")
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_list_tasks_enforces_exact_fixed_limit_in_postgresql():
    _require_db()
    from sqlalchemy import text

    from app.ports.task_store import TASK_STORE_QUERY_LIMIT

    session_id = str(uuid.uuid4())
    user_id = f"user-{uuid.uuid4()}"
    prefix = f"bounded-{uuid.uuid4()}"

    async def _run() -> None:
        engine = _make_engine()
        try:
            factory = _make_factory(engine)
            async with factory() as session:
                await session.execute(
                    text(
                        "INSERT INTO tasks"
                        " (task_id, session_id, ai_user_id, tenant_id, status,"
                        " trace_id, capability_id, error_code)"
                        " VALUES"
                        " (:task_id, :session_id, :ai_user_id, :tenant_id, 'completed',"
                        " NULL, 'oa.leave.apply', NULL)"
                    ),
                    [
                        {
                            "task_id": f"{prefix}-{index:03d}",
                            "session_id": session_id,
                            "ai_user_id": user_id,
                            "tenant_id": "tenant-limit",
                        }
                        for index in range(TASK_STORE_QUERY_LIMIT + 1)
                    ],
                )
                await session.commit()

            store = _task_store(factory)
            tasks = await store.list_tasks(
                session_id=session_id,
                ai_user_id=user_id,
            )

            assert len(tasks) == TASK_STORE_QUERY_LIMIT == 100
            assert [task.task_id for task in tasks] == [
                f"{prefix}-{index:03d}" for index in range(TASK_STORE_QUERY_LIMIT)
            ]
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_list_events_round_trips_payload_in_order_with_exact_fixed_limit():
    _require_db()
    from sqlalchemy import text

    from app.ports.task_store import TASK_STORE_QUERY_LIMIT, TaskRecord

    task_id = str(uuid.uuid4())
    start = datetime(2026, 7, 23, tzinfo=timezone.utc)

    async def _run() -> None:
        engine = _make_engine()
        try:
            factory = _make_factory(engine)
            store = _task_store(factory)
            await store.create_task(
                TaskRecord(
                    task_id=task_id,
                    session_id=str(uuid.uuid4()),
                    ai_user_id="bounded-event-user",
                    tenant_id="tenant-events",
                    status="completed",
                )
            )
            async with factory() as session:
                await session.execute(
                    text(
                        "INSERT INTO task_events"
                        " (event_id, task_id, event_type, timestamp, payload)"
                        " VALUES"
                        " (:event_id, :task_id, 'workflow_step_finished',"
                        " :timestamp, CAST(:payload AS JSONB))"
                    ),
                    [
                        {
                            "event_id": f"event-{task_id}-{index:03d}",
                            "task_id": task_id,
                            "timestamp": start.replace(microsecond=index),
                            "payload": json.dumps({"step_index": index}),
                        }
                        for index in range(TASK_STORE_QUERY_LIMIT + 1)
                    ],
                )
                await session.commit()

            events = await store.list_events(task_id)

            assert len(events) == TASK_STORE_QUERY_LIMIT == 100
            assert [event.payload for event in events] == [
                {"step_index": index} for index in range(TASK_STORE_QUERY_LIMIT)
            ]
        finally:
            await engine.dispose()

    asyncio.run(_run())


# ── SessionStore tests ─────────────────────────────────────────────────────────


def test_create_session_returns_session_record_and_round_trips():
    _require_db()
    from app.ports.task_store import SessionRecord

    session_id = str(uuid.uuid4())

    async def _run() -> None:
        engine = _make_engine()
        try:
            store = _session_store(_make_factory(engine))
            result = await store.create_session(SessionRecord(session_id=session_id))
            assert isinstance(result, SessionRecord)
            assert result.session_id == session_id

            fetched = await store.get_session(session_id)
            assert fetched is not None
            assert isinstance(fetched, SessionRecord)
            assert fetched.session_id == session_id
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_get_session_returns_none_for_unknown_session_id():
    _require_db()

    async def _run() -> None:
        engine = _make_engine()
        try:
            store = _session_store(_make_factory(engine))
            result = await store.get_session(str(uuid.uuid4()))
            assert result is None
        finally:
            await engine.dispose()

    asyncio.run(_run())
