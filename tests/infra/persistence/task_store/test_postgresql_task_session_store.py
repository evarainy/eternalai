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
        status="created",
    )
    duplicate = TaskRecord(
        task_id=task_id,
        session_id=str(uuid.uuid4()),
        ai_user_id="different-user",
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
                row = (await session.execute(
                    text(
                        "SELECT event_id, event_type, payload"
                        " FROM task_events WHERE event_id = :eid"
                    ),
                    {"eid": event_id},
                )).fetchone()
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
