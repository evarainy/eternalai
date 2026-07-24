"""Real PostgreSQL coverage for persistent trace writes and bounded reads."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any, get_args
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.infra.observability.postgresql_trace import (
    PostgreSQLTraceReader,
    PostgreSQLTraceWriter,
    TraceSanitizationError,
)
from app.ports.trace import (
    TRACE_QUERY_LIMIT,
    TraceEvent,
    TraceEventType,
)

DATABASE_URL = os.environ.get("DATABASE_URL")

if hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # type: ignore[attr-defined]


def _require_db() -> None:
    if not DATABASE_URL:
        raise AssertionError("DATABASE_URL must be set by the test runner environment")


def _make_engine() -> Any:
    from app.db.session import make_async_engine

    return make_async_engine(DATABASE_URL)


def _make_factory(engine: Any) -> Any:
    from app.db.session import make_async_session_factory

    return make_async_session_factory(engine)


def test_all_20_trace_event_types_persist_and_read_back() -> None:
    _require_db()
    trace_id = f"all-types-{uuid4().hex}"
    event_types = list(get_args(TraceEventType))

    async def exercise() -> None:
        engine = _make_engine()
        try:
            factory = _make_factory(engine)
            writer = PostgreSQLTraceWriter(factory)
            reader = PostgreSQLTraceReader(factory)
            for event_type in event_types:
                await writer.record_event(
                    TraceEvent(
                        trace_id=trace_id,
                        task_id="task-all-types",
                        session_id="session-all-types",
                        event_type=event_type,
                        status="ok",
                        attributes={"event_type": event_type},
                    )
                )

            persisted = await reader.list_events_by_trace(trace_id)

            assert len(event_types) == 20
            assert len(persisted) == 20
            assert {event.event_type for event in persisted} == set(event_types)
            assert all(event.created_at.utcoffset() is not None for event in persisted)
        finally:
            await engine.dispose()

    asyncio.run(exercise())


def test_default_sanitizer_removes_plaintext_from_raw_database_row() -> None:
    _require_db()
    trace_id = f"redaction-{uuid4().hex}"
    token = f"SYNTHETIC-BEARER-{uuid4().hex}"
    oa_password = f"SYNTHETIC-OA-PASSWORD-{uuid4().hex}"
    oa_cookie = f"SYNTHETIC-OA-COOKIE-{uuid4().hex}"
    identity_number = "1" * 17 + "X"

    async def exercise() -> None:
        engine = _make_engine()
        try:
            factory = _make_factory(engine)
            writer = PostgreSQLTraceWriter(factory)
            reader = PostgreSQLTraceReader(factory)
            await writer.record_event(
                TraceEvent(
                    trace_id=trace_id,
                    task_id="task-redaction",
                    session_id="session-redaction",
                    event_type="adapter_called",
                    status="ok",
                    attributes={
                        "authorization": f"Bearer {token}",
                        "nested": {"access_token": token},
                        "userpassword": oa_password,
                        "oa_cookies": {"loginuuids": oa_cookie},
                        "message": identity_number,
                        "tuple_nested": (
                            {"userpassword": oa_password},
                            {"message": identity_number},
                        ),
                        "safe": "visible",
                    },
                )
            )

            persisted = await reader.list_events_by_trace(trace_id)
            async with factory() as session:
                raw_attributes = (
                    await session.execute(
                        text(
                            "SELECT attributes::text FROM trace_events"
                            " WHERE trace_id = :trace_id"
                        ),
                        {"trace_id": trace_id},
                    )
                ).scalar_one()

            assert persisted[0].attributes == {
                "authorization": "[REDACTED]",
                "nested": {"access_token": "[REDACTED]"},
                "userpassword": "[REDACTED]",
                "oa_cookies": "[REDACTED]",
                "message": "[REDACTED]",
                "tuple_nested": [
                    {"userpassword": "[REDACTED]"},
                    {"message": "[REDACTED]"},
                ],
                "safe": "visible",
            }
            assert token not in raw_attributes
            assert oa_password not in raw_attributes
            assert oa_cookie not in raw_attributes
            assert identity_number not in raw_attributes
            assert "[REDACTED]" in raw_attributes
        finally:
            await engine.dispose()

    asyncio.run(exercise())


def test_identity_sanitizer_override_cannot_bypass_final_redaction() -> None:
    _require_db()
    trace_id = f"identity-hook-{uuid4().hex}"
    token = f"SYNTHETIC-IDENTITY-HOOK-{uuid4().hex}"
    direct_token = f"SYNTHETIC-DIRECT-TOKEN-{uuid4().hex}"
    csrf_token = f"SYNTHETIC-CSRF-TOKEN-{uuid4().hex}"
    uri_password = f"SYNTHETIC-URI-PASSWORD-{uuid4().hex}"
    api_key = f"SYNTHETIC-API-KEY-{uuid4().hex}"

    async def exercise() -> None:
        engine = _make_engine()
        try:
            factory = _make_factory(engine)
            writer = PostgreSQLTraceWriter(factory)
            writer.set_sanitizer(lambda attributes: attributes)
            await writer.record_event(
                TraceEvent(
                    trace_id=trace_id,
                    task_id="task-identity-hook",
                    session_id="session-identity-hook",
                    event_type="adapter_called",
                    status="ok",
                    attributes={
                        "authorization": f"Bearer {token}",
                        "token": direct_token,
                        "dsn": (
                            "postgresql+psycopg://alice:"
                            f"{uri_password}@db.example.test/app"
                        ),
                        "headers": {
                            "X-Api-Key": api_key,
                            "X-CSRF-Token": csrf_token,
                        },
                    },
                )
            )

            async with factory() as session:
                raw_attributes = (
                    await session.execute(
                        text(
                            "SELECT attributes::text FROM trace_events"
                            " WHERE trace_id = :trace_id"
                        ),
                        {"trace_id": trace_id},
                    )
                ).scalar_one()

            assert token not in raw_attributes
            assert direct_token not in raw_attributes
            assert csrf_token not in raw_attributes
            assert uri_password not in raw_attributes
            assert api_key not in raw_attributes
            assert json.loads(raw_attributes) == {
                "authorization": "[REDACTED]",
                "token": "[REDACTED]",
                "dsn": "[REDACTED]",
                "headers": {
                    "X-Api-Key": "[REDACTED]",
                    "X-CSRF-Token": "[REDACTED]",
                },
            }
        finally:
            await engine.dispose()

    asyncio.run(exercise())


def test_only_record_event_is_a_persistence_landing_point() -> None:
    _require_db()
    trace_id = f"single-landing-{uuid4().hex}"

    async def exercise() -> None:
        engine = _make_engine()
        try:
            factory = _make_factory(engine)
            writer = PostgreSQLTraceWriter(factory)
            reader = PostgreSQLTraceReader(factory)
            await writer.start_task_trace(trace_id, "task-landing", "session-landing")
            await writer.record_step(
                trace_id,
                "task-landing",
                "session-landing",
                "task_created",
                "ok",
            )
            await writer.record_policy_decision(
                trace_id,
                "task-landing",
                "session-landing",
                "ok",
            )
            await writer.record_gateway_call(
                trace_id,
                "task-landing",
                "session-landing",
                "ok",
            )
            await writer.finalize_task_trace(
                trace_id,
                "task-landing",
                "session-landing",
                "ok",
            )

            persisted = await reader.list_events_by_trace(trace_id)

            assert [event.event_type for event in persisted] == [
                "task_created",
                "policy_checked",
                "gateway_pre_recorded",
            ]
        finally:
            await engine.dispose()

    asyncio.run(exercise())


def test_reader_filters_sorting_and_cross_session_isolation() -> None:
    _require_db()
    prefix = uuid4().hex
    trace_id = f"trace-{prefix}"
    task_id = f"task-{prefix}"
    session_id = f"session-{prefix}"
    other_session_id = f"other-session-{prefix}"
    created_at = datetime(2026, 7, 23, tzinfo=UTC)

    async def exercise() -> None:
        engine = _make_engine()
        try:
            factory = _make_factory(engine)
            async with factory() as session:
                await session.execute(
                    text(
                        "INSERT INTO trace_events"
                        " (event_id, trace_id, task_id, session_id, event_type, status,"
                        " capability_id, error_code, attributes, created_at)"
                        " VALUES"
                        " (:event_id, :trace_id, :task_id, :session_id,"
                        " 'task_created', 'ok', NULL, NULL,"
                        " CAST(:attributes AS JSONB), :created_at)"
                    ),
                    [
                        {
                            "event_id": f"{prefix}-z",
                            "trace_id": trace_id,
                            "task_id": task_id,
                            "session_id": session_id,
                            "attributes": json.dumps({"order": "created-first"}),
                            "created_at": created_at - timedelta(seconds=1),
                        },
                        {
                            "event_id": f"{prefix}-b",
                            "trace_id": trace_id,
                            "task_id": task_id,
                            "session_id": session_id,
                            "attributes": json.dumps({"order": "b"}),
                            "created_at": created_at,
                        },
                        {
                            "event_id": f"{prefix}-a",
                            "trace_id": trace_id,
                            "task_id": task_id,
                            "session_id": session_id,
                            "attributes": json.dumps({"order": "a"}),
                            "created_at": created_at,
                        },
                        {
                            "event_id": f"{prefix}-other",
                            "trace_id": f"other-{trace_id}",
                            "task_id": f"other-{task_id}",
                            "session_id": other_session_id,
                            "attributes": json.dumps({"order": "other"}),
                            "created_at": created_at + timedelta(seconds=1),
                        },
                        {
                            "event_id": f"{prefix}-trace-only",
                            "trace_id": trace_id,
                            "task_id": f"trace-conflict-{task_id}",
                            "session_id": f"trace-conflict-{session_id}",
                            "attributes": json.dumps({"order": "trace-only"}),
                            "created_at": created_at + timedelta(seconds=2),
                        },
                        {
                            "event_id": f"{prefix}-task-only",
                            "trace_id": f"task-conflict-{trace_id}",
                            "task_id": task_id,
                            "session_id": f"task-conflict-{session_id}",
                            "attributes": json.dumps({"order": "task-only"}),
                            "created_at": created_at + timedelta(seconds=3),
                        },
                        {
                            "event_id": f"{prefix}-session-only",
                            "trace_id": f"session-conflict-{trace_id}",
                            "task_id": f"session-conflict-{task_id}",
                            "session_id": session_id,
                            "attributes": json.dumps({"order": "session-only"}),
                            "created_at": created_at + timedelta(seconds=4),
                        },
                    ],
                )
                await session.commit()

            reader = PostgreSQLTraceReader(factory)
            by_trace = await reader.list_events_by_trace(
                trace_id,
                task_id=task_id,
                session_id=session_id,
            )
            by_task = await reader.list_events_by_task(
                task_id,
                trace_id=trace_id,
                session_id=session_id,
            )
            by_session = await reader.list_events_by_session(
                session_id,
                trace_id=trace_id,
                task_id=task_id,
            )
            other_session = await reader.list_events_by_session(other_session_id)

            expected_ids = [f"{prefix}-z", f"{prefix}-a", f"{prefix}-b"]
            assert [event.event_id for event in by_trace] == expected_ids
            assert [event.event_id for event in by_task] == expected_ids
            assert [event.event_id for event in by_session] == expected_ids
            assert [event.event_id for event in other_session] == [f"{prefix}-other"]
            assert all(event.session_id == session_id for event in by_session)
        finally:
            await engine.dispose()

    asyncio.run(exercise())


def test_reader_enforces_exact_limit_for_default_huge_and_negative_values() -> None:
    _require_db()
    prefix = uuid4().hex
    session_id = f"bounded-session-{prefix}"
    start = datetime(2026, 7, 23, tzinfo=UTC)

    async def exercise() -> None:
        engine = _make_engine()
        try:
            factory = _make_factory(engine)
            async with factory() as session:
                await session.execute(
                    text(
                        "INSERT INTO trace_events"
                        " (event_id, trace_id, task_id, session_id, event_type, status,"
                        " capability_id, error_code, attributes, created_at)"
                        " VALUES"
                        " (:event_id, :trace_id, :task_id, :session_id,"
                        " 'task_created', 'ok', NULL, NULL,"
                        " CAST(:attributes AS JSONB), :created_at)"
                    ),
                    [
                        {
                            "event_id": f"{prefix}-{index:03d}",
                            "trace_id": f"trace-{prefix}",
                            "task_id": f"task-{prefix}",
                            "session_id": session_id,
                            "attributes": json.dumps({"index": index}),
                            "created_at": start + timedelta(microseconds=index),
                        }
                        for index in range(TRACE_QUERY_LIMIT + 1)
                    ],
                )
                await session.commit()

            reader = PostgreSQLTraceReader(factory)
            default = await reader.list_events_by_session(session_id)
            huge = await reader.list_events_by_session(session_id, limit=10**9)
            negative = await reader.list_events_by_session(session_id, limit=-1)

            assert len(default) == TRACE_QUERY_LIMIT == 100
            assert len(huge) == TRACE_QUERY_LIMIT
            assert len(negative) == 1
            assert default == huge
            assert negative == default[:1]
        finally:
            await engine.dispose()

    asyncio.run(exercise())


def test_writer_fails_closed_when_sanitizer_raises() -> None:
    writer = PostgreSQLTraceWriter(object())  # type: ignore[arg-type]

    def fail(_attributes: dict[str, Any]) -> dict[str, Any]:
        raise ValueError("synthetic sanitizer failure")

    writer.set_sanitizer(fail)
    event = TraceEvent(
        trace_id="trace-fail-closed",
        task_id="task-fail-closed",
        session_id="session-fail-closed",
        event_type="task_created",
        status="ok",
    )

    with pytest.raises(
        TraceSanitizationError,
        match="trace attribute sanitization failed",
    ):
        asyncio.run(writer.record_event(event))
