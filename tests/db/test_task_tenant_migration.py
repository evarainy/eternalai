"""PostgreSQL proof for conservative Task tenant migration."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection, make_url

from app.db.config import normalize_database_url

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = REPO_ROOT / "alembic" / "versions" / "20260901_120000_task_tenant.py"
SYNTHETIC_TASK_COUNT = 3


def _database_url() -> str:
    value = os.environ.get("DATABASE_URL")
    if value is None:
        raise AssertionError("DATABASE_URL must be set by the test runner environment")
    url = make_url(normalize_database_url(value))
    if url.host != "127.0.0.1" or url.port != 15432:
        raise AssertionError("task tenant migration tests require 127.0.0.1:15432")
    return value


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "task_tenant_migration",
        MIGRATION_PATH,
    )
    if spec is None or spec.loader is None:
        raise AssertionError("task tenant migration must be importable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _synthetic_task_rows(count: int) -> list[dict[str, str]]:
    if count <= 0:
        raise AssertionError("migration roundtrip requires non-empty synthetic pre-upgrade Tasks")
    prefix = f"task-tenant-migration-{uuid4().hex}"
    return [
        {
            "task_id": f"{prefix}-{index}",
            "session_id": f"{prefix}-session-{index}",
            "ai_user_id": f"{prefix}-user-{index}",
            "status": "completed",
        }
        for index in range(count)
    ]


def _task_snapshot(connection: Connection) -> tuple[int, set[str]]:
    task_ids = list(connection.execute(text("SELECT task_id FROM tasks")).scalars())
    return len(task_ids), set(task_ids)


def _assert_tenant_schema(connection: Connection) -> None:
    inspector = inspect(connection)
    columns = {column["name"]: column for column in inspector.get_columns("tasks")}
    assert columns["tenant_id"]["nullable"] is True
    assert columns["tenant_id"]["default"] is None
    assert {constraint["name"] for constraint in inspector.get_check_constraints("tasks")} >= {
        "ck_tasks_tenant_id_non_blank"
    }
    assert {index["name"] for index in inspector.get_indexes("tasks")} >= {
        "ix_tasks_tenant_session",
        "ix_tasks_tenant_ai_user",
    }


def test_zero_synthetic_tasks_are_rejected_as_non_discriminating() -> None:
    with pytest.raises(AssertionError, match="non-empty synthetic pre-upgrade Tasks"):
        _synthetic_task_rows(0)


def test_non_empty_historical_tasks_survive_upgrade_downgrade_upgrade() -> None:
    rows = _synthetic_task_rows(SYNTHETIC_TASK_COUNT)
    expected_ids = {row["task_id"] for row in rows}
    assert len(expected_ids) == SYNTHETIC_TASK_COUNT > 0

    engine = create_engine(normalize_database_url(_database_url()))
    migration = _load_migration()
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                migration.op = Operations(MigrationContext.configure(connection))
                migration.downgrade()
                assert "tenant_id" not in {
                    column["name"] for column in inspect(connection).get_columns("tasks")
                }

                connection.execute(
                    text(
                        "INSERT INTO tasks"
                        " (task_id, session_id, ai_user_id, status)"
                        " VALUES (:task_id, :session_id, :ai_user_id, :status)"
                    ),
                    rows,
                )
                expected_snapshot = _task_snapshot(connection)
                assert expected_snapshot[0] == len(expected_snapshot[1])
                assert expected_ids <= expected_snapshot[1]
                assert expected_snapshot[0] >= SYNTHETIC_TASK_COUNT > 0

                migration.upgrade()
                _assert_tenant_schema(connection)
                assert _task_snapshot(connection) == expected_snapshot
                assert (
                    connection.execute(
                        text(
                            "SELECT COUNT(*) FROM tasks"
                            " WHERE task_id = ANY(:task_ids) AND tenant_id IS NULL"
                        ),
                        {"task_ids": sorted(expected_ids)},
                    ).scalar_one()
                    == SYNTHETIC_TASK_COUNT
                )

                migration.downgrade()
                assert "tenant_id" not in {
                    column["name"] for column in inspect(connection).get_columns("tasks")
                }
                assert _task_snapshot(connection) == expected_snapshot

                migration.upgrade()
                _assert_tenant_schema(connection)
                assert _task_snapshot(connection) == expected_snapshot
                assert (
                    connection.execute(
                        text(
                            "SELECT COUNT(*) FROM tasks"
                            " WHERE task_id = ANY(:task_ids) AND tenant_id IS NULL"
                        ),
                        {"task_ids": sorted(expected_ids)},
                    ).scalar_one()
                    == SYNTHETIC_TASK_COUNT
                )
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


def test_migration_source_has_no_task_backfill_or_row_deletion() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8").upper()

    assert "UPDATE TASKS" not in source
    assert "DELETE FROM TASKS" not in source
    assert "TRUNCATE TASKS" not in source
    assert 'DROP_TABLE("TASKS"' not in source
