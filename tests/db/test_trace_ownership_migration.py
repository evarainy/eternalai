"""PostgreSQL proof for conservative Trace ownership backfill."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from alembic import command
from app.db.config import normalize_database_url

REPO_ROOT = Path(__file__).resolve().parents[2]
PREVIOUS_REVISION = "20260831_120000"


def _database_url() -> str:
    value = os.environ.get("DATABASE_URL")
    if value is None:
        raise AssertionError("DATABASE_URL must be set by the test runner environment")
    url = make_url(normalize_database_url(value))
    if url.host != "127.0.0.1" or url.port != 15432:
        raise AssertionError("trace migration tests require 127.0.0.1:15432")
    return value


def _alembic_config() -> Config:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    return config


def _seed_previous_revision(prefix: str) -> None:
    engine = create_engine(normalize_database_url(_database_url()))
    now = datetime.now(UTC)
    digest = "a" * 64
    task_rows = [
        {
            "task_id": f"{prefix}-unique",
            "session_id": f"{prefix}-session-unique",
            "ai_user_id": "user-unique",
            "trace_id": f"{prefix}-trace-unique",
        },
        {
            "task_id": f"{prefix}-conflict",
            "session_id": f"{prefix}-session-conflict",
            "ai_user_id": "user-conflict",
            "trace_id": f"{prefix}-trace-conflict",
        },
        {
            "task_id": f"{prefix}-unassigned",
            "session_id": f"{prefix}-session-unassigned",
            "ai_user_id": "user-unassigned",
            "trace_id": f"{prefix}-trace-unassigned",
        },
    ]
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO tasks"
                    " (task_id, session_id, ai_user_id, status, trace_id)"
                    " VALUES (:task_id, :session_id, :ai_user_id, 'completed', :trace_id)"
                ),
                task_rows,
            )
            connection.execute(
                text(
                    "INSERT INTO task_version_binding_manifests"
                    " (task_id, manifest_digest, bindings, locked_at)"
                    " VALUES (:task_id, :digest, CAST(:bindings AS JSONB), :locked_at)"
                ),
                [
                    {
                        "task_id": row["task_id"],
                        "digest": digest,
                        "bindings": json.dumps({}),
                        "locked_at": now,
                    }
                    for row in task_rows[:2]
                ],
            )
            gate_rows = [
                {
                    "request_id": f"{prefix}-gate-unique",
                    "task_id": task_rows[0]["task_id"],
                    "ai_user_id": task_rows[0]["ai_user_id"],
                    "session_id": task_rows[0]["session_id"],
                    "tenant_id": "tenant-unique",
                },
                {
                    "request_id": f"{prefix}-gate-conflict-a",
                    "task_id": task_rows[1]["task_id"],
                    "ai_user_id": task_rows[1]["ai_user_id"],
                    "session_id": task_rows[1]["session_id"],
                    "tenant_id": "tenant-conflict-a",
                },
                {
                    "request_id": f"{prefix}-gate-conflict-b",
                    "task_id": task_rows[1]["task_id"],
                    "ai_user_id": task_rows[1]["ai_user_id"],
                    "session_id": task_rows[1]["session_id"],
                    "tenant_id": "tenant-conflict-b",
                },
            ]
            connection.execute(
                text(
                    "INSERT INTO human_gate_requests"
                    " (request_id, task_id, requested_for_ai_user_id,"
                    " requested_session_id, requested_tenant_id, action_digest,"
                    " request_digest, binding_manifest_digest, requested_at, expires_at)"
                    " VALUES (:request_id, :task_id, :ai_user_id, :session_id,"
                    " :tenant_id, :digest, :digest, :digest, :requested_at, :expires_at)"
                ),
                [
                    {
                        **row,
                        "digest": digest,
                        "requested_at": now,
                        "expires_at": now + timedelta(minutes=5),
                    }
                    for row in gate_rows
                ],
            )
            trace_rows = [
                {
                    "event_id": f"{prefix}-event-unique",
                    "trace_id": task_rows[0]["trace_id"],
                    "task_id": task_rows[0]["task_id"],
                    "session_id": task_rows[0]["session_id"],
                    "event_type": "task_created",
                },
                {
                    "event_id": f"{prefix}-event-conflict",
                    "trace_id": task_rows[1]["trace_id"],
                    "task_id": task_rows[1]["task_id"],
                    "session_id": task_rows[1]["session_id"],
                    "event_type": "task_created",
                },
                {
                    "event_id": f"{prefix}-event-unassigned",
                    "trace_id": task_rows[2]["trace_id"],
                    "task_id": task_rows[2]["task_id"],
                    "session_id": task_rows[2]["session_id"],
                    "event_type": "task_created",
                },
                {
                    "event_id": f"{prefix}-event-orphan-action",
                    "trace_id": f"{prefix}-trace-orphan",
                    "task_id": f"{prefix}-task-orphan",
                    "session_id": f"{prefix}-session-orphan",
                    "event_type": "user_action",
                },
            ]
            connection.execute(
                text(
                    "INSERT INTO trace_events"
                    " (event_id, trace_id, task_id, session_id, event_type, status,"
                    " attributes, created_at)"
                    " VALUES (:event_id, :trace_id, :task_id, :session_id,"
                    " :event_type, 'ok', CAST('{}' AS JSONB), :created_at)"
                ),
                [{**row, "created_at": now} for row in trace_rows],
            )
    finally:
        engine.dispose()


def _ownership(prefix: str) -> dict[str, tuple[str | None, str | None]]:
    engine = create_engine(normalize_database_url(_database_url()))
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT event_id, tenant_id, ai_user_id FROM trace_events"
                    " WHERE event_id LIKE :prefix ORDER BY event_id"
                ),
                {"prefix": f"{prefix}-event-%"},
            ).mappings()
            return {row["event_id"]: (row["tenant_id"], row["ai_user_id"]) for row in rows}
    finally:
        engine.dispose()


def _cleanup(prefix: str) -> None:
    engine = create_engine(normalize_database_url(_database_url()))
    try:
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM trace_events WHERE event_id LIKE :prefix"),
                {"prefix": f"{prefix}-%"},
            )
            connection.execute(
                text("DELETE FROM tasks WHERE task_id LIKE :prefix"),
                {"prefix": f"{prefix}-%"},
            )
    finally:
        engine.dispose()


def test_backfill_assigns_only_unique_exact_owner_and_roundtrip_loses_no_rows() -> None:
    config = _alembic_config()
    prefix = f"trace-owner-{uuid4().hex}"
    command.upgrade(config, "head")
    command.downgrade(config, PREVIOUS_REVISION)
    try:
        _seed_previous_revision(prefix)
        command.upgrade(config, "head")

        expected = {
            f"{prefix}-event-unique": ("tenant-unique", "user-unique"),
            f"{prefix}-event-conflict": (None, None),
            f"{prefix}-event-unassigned": (None, None),
            f"{prefix}-event-orphan-action": (None, None),
        }
        assert _ownership(prefix) == expected

        engine = create_engine(normalize_database_url(_database_url()))
        try:
            inspector = inspect(engine)
            assert "tenant_id" not in {
                column["name"] for column in inspector.get_columns("tasks")
            }
            with engine.connect() as connection:
                persisted = connection.execute(
                    text(
                        "SELECT event_id, attributes FROM trace_events"
                        " WHERE event_id LIKE :prefix ORDER BY event_id"
                    ),
                    {"prefix": f"{prefix}-event-%"},
                ).mappings()
                assert {
                    row["event_id"]: row["attributes"] for row in persisted
                } == {event_id: {} for event_id in expected}

            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE trace_events SET tenant_id = 'tenant-half'"
                            " WHERE event_id = :event_id"
                        ),
                        {"event_id": f"{prefix}-event-unassigned"},
                    )
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE trace_events"
                            " SET tenant_id = ' ', ai_user_id = 'user-blank'"
                            " WHERE event_id = :event_id"
                        ),
                        {"event_id": f"{prefix}-event-unassigned"},
                    )
        finally:
            engine.dispose()

        command.downgrade(config, PREVIOUS_REVISION)
        engine = create_engine(normalize_database_url(_database_url()))
        try:
            assert "tenant_id" not in {
                column["name"] for column in inspect(engine).get_columns("trace_events")
            }
            with engine.connect() as connection:
                assert connection.execute(
                    text("SELECT COUNT(*) FROM trace_events WHERE event_id LIKE :prefix"),
                    {"prefix": f"{prefix}-%"},
                ).scalar_one() == len(expected)
        finally:
            engine.dispose()

        command.upgrade(config, "head")
        assert _ownership(prefix) == expected
    finally:
        command.upgrade(config, "head")
        _cleanup(prefix)
