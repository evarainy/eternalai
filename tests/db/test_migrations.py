from __future__ import annotations

import hashlib
import importlib.util
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Iterator
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection, Engine, make_url
from sqlalchemy.exc import IntegrityError

from alembic import command
from app.db.config import normalize_database_url

REPO_ROOT = Path(__file__).resolve().parents[2]
REVOCATION_PARENT_REVISION = "20260724_090000"
REVOCATION_REVISION = "20260731_090000"
ORIGINAL_CREDENTIAL_COLUMNS = frozenset(
    {
        "ai_user_id",
        "cipher_version",
        "nonce",
        "encrypted_payload",
        "expires_at",
        "updated_at",
    }
)
WORK_OBJECT_MODEL_REVISION = "20260827_120000"
WORK_OBJECT_MODEL_MIGRATION_PATH = (
    REPO_ROOT
    / "alembic"
    / "versions"
    / "20260827_120000_internal_work_object_model.py"
)
WORK_OBJECT_PARENT_COLUMNS = frozenset(
    {
        "work_object_id",
        "source_system",
        "source_kind",
        "source_ref",
        "assignee_ai_user_id",
        "assignee_display_name",
        "due_at",
        "source_title",
        "source_status",
        "source_received_at",
        "source_created_at",
        "source_workflow_type_id",
        "source_fetched_at",
        "handling_mark",
        "handling_marked_by_ai_user_id",
        "handling_marked_at",
        "task_record_id",
        "created_at",
        "updated_at",
    }
)
OA_SNAPSHOT_COLUMN_NAMES = (
    "source_ref",
    "source_title",
    "source_status",
    "source_received_at",
    "source_created_at",
    "source_workflow_type_id",
    "source_fetched_at",
)

_PARENT_WORK_OBJECT_TABLE_SQL = """
CREATE TEMPORARY TABLE work_objects (
    work_object_id TEXT PRIMARY KEY,
    source_system TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    assignee_ai_user_id TEXT NOT NULL,
    assignee_display_name TEXT NOT NULL,
    due_at TIMESTAMPTZ NULL,
    source_title TEXT NOT NULL,
    source_status TEXT NOT NULL,
    source_received_at TEXT NOT NULL,
    source_created_at TEXT NOT NULL,
    source_workflow_type_id TEXT NOT NULL,
    source_fetched_at TIMESTAMPTZ NOT NULL,
    handling_mark TEXT NULL,
    handling_marked_by_ai_user_id TEXT NULL,
    handling_marked_at TIMESTAMPTZ NULL,
    task_record_id TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_work_objects_source_system CHECK (source_system = 'oa'),
    CONSTRAINT ck_work_objects_source_kind
        CHECK (source_kind = 'pending_workflow'),
    CONSTRAINT ck_work_objects_handling_mark CHECK (
        handling_mark IS NULL OR handling_mark IN (
            'pending_sync_confirmation', 'handled_elsewhere'
        )
    ),
    CONSTRAINT ck_work_objects_handling_record CHECK (
        (
            handling_mark IS NULL
            AND handling_marked_by_ai_user_id IS NULL
            AND handling_marked_at IS NULL
        ) OR (
            handling_mark IS NOT NULL
            AND handling_marked_by_ai_user_id IS NOT NULL
            AND handling_marked_at IS NOT NULL
        )
    ),
    CONSTRAINT uq_work_objects_assignee_source UNIQUE (
        assignee_ai_user_id, source_system, source_ref
    )
)
"""

_INSERT_WORK_OBJECT_SQL = """
INSERT INTO work_objects (
    work_object_id, state_authority, source_system, source_kind, source_ref,
    assignee_ai_user_id, assignee_display_name, due_at, source_title,
    source_status, source_received_at, source_created_at,
    source_workflow_type_id, source_fetched_at, handling_mark,
    handling_marked_by_ai_user_id, handling_marked_at, task_record_id,
    created_at, updated_at
) VALUES (
    :work_object_id, :state_authority, :source_system, :source_kind, :source_ref,
    :assignee_ai_user_id, :assignee_display_name, NULL, :source_title,
    :source_status, :source_received_at, :source_created_at,
    :source_workflow_type_id, :source_fetched_at, NULL, NULL, NULL, NULL,
    :created_at, :updated_at
)
"""


def _database_url_from_environment() -> str:
    value = os.environ.get("DATABASE_URL")
    if value is None or not value.strip():
        raise AssertionError("DATABASE_URL must be set by the test runner environment")
    database_url = make_url(normalize_database_url(value))
    if database_url.host != "127.0.0.1" or database_url.port != 15432:
        raise AssertionError(
            "Migration tests are restricted to PostgreSQL at 127.0.0.1:15432"
        )
    return value


def _alembic_config() -> Config:
    _database_url_from_environment()
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    return config


def _vector_extension_version() -> str | None:
    engine = create_engine(normalize_database_url(_database_url_from_environment()))
    try:
        with engine.connect() as connection:
            result = connection.execute(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            )
            return result.scalar_one_or_none()
    finally:
        engine.dispose()


def _credential_columns() -> dict[str, dict[str, object]]:
    engine = create_engine(normalize_database_url(_database_url_from_environment()))
    try:
        return {
            str(column["name"]): dict(column)
            for column in inspect(engine).get_columns("oa_session_credentials")
        }
    finally:
        engine.dispose()


def _column_signature(column: dict[str, object]) -> tuple[str, bool, object]:
    return (
        str(column["type"]),
        bool(column["nullable"]),
        column.get("default"),
    )


def _credential_row(engine: Engine, ai_user_id: str) -> dict[str, object]:
    with engine.connect() as connection:
        return dict(
            connection.execute(
                text(
                    "SELECT * FROM oa_session_credentials"
                    " WHERE ai_user_id = :ai_user_id"
                ),
                {"ai_user_id": ai_user_id},
            )
            .mappings()
            .one()
        )


def _safe_credential_row_signature(
    row: dict[str, object],
) -> tuple[object, ...]:
    return (
        row["ai_user_id"],
        row["cipher_version"],
        hashlib.sha256(bytes(row["nonce"])).digest(),
        hashlib.sha256(bytes(row["encrypted_payload"])).digest(),
        row["expires_at"],
        row["updated_at"],
    )


def _load_work_object_model_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "internal_work_object_model_migration",
        WORK_OBJECT_MODEL_MIGRATION_PATH,
    )
    if spec is None or spec.loader is None:
        raise AssertionError("Work Object model migration must be importable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_work_object_model_upgrade(connection: Connection) -> None:
    migration = _load_work_object_model_migration()
    assert migration.revision == WORK_OBJECT_MODEL_REVISION
    assert migration.down_revision == "20260821_120000"
    migration.op = Operations(MigrationContext.configure(connection))
    migration.upgrade()


def _run_work_object_model_downgrade(connection: Connection) -> None:
    migration = _load_work_object_model_migration()
    migration.op = Operations(MigrationContext.configure(connection))
    migration.downgrade()


@pytest.fixture
def work_object_migration_connection() -> Iterator[Connection]:
    engine = create_engine(normalize_database_url(_database_url_from_environment()))
    try:
        with engine.begin() as connection:
            connection.execute(text(_PARENT_WORK_OBJECT_TABLE_SQL))
            yield connection
    finally:
        engine.dispose()


def _work_object_column_names(connection: Connection) -> frozenset[str]:
    return frozenset(
        str(name)
        for name in connection.execute(
            text(
                "SELECT attname FROM pg_attribute "
                "WHERE attrelid = 'work_objects'::regclass "
                "AND attnum > 0 AND NOT attisdropped"
            )
        ).scalars()
    )


def _work_object_not_null_columns(connection: Connection) -> frozenset[str]:
    return frozenset(
        str(name)
        for name in connection.execute(
            text(
                "SELECT attname FROM pg_attribute "
                "WHERE attrelid = 'work_objects'::regclass "
                "AND attnum > 0 AND NOT attisdropped AND attnotnull"
            )
        ).scalars()
    )


def _work_object_constraint_names(connection: Connection) -> frozenset[str]:
    return frozenset(
        str(name)
        for name in connection.execute(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'work_objects'::regclass"
            )
        ).scalars()
    )


def _insert_oa_work_object(
    connection: Connection,
    *,
    work_object_id: str = "work-oa",
) -> None:
    now = datetime(2026, 8, 27, 1, 2, 3, tzinfo=UTC)
    connection.execute(
        text(_INSERT_WORK_OBJECT_SQL),
        {
            "work_object_id": work_object_id,
            "state_authority": "external_snapshot",
            "source_system": "oa",
            "source_kind": "pending_workflow",
            "source_ref": "OA-REF-001",
            "assignee_ai_user_id": "user-oa",
            "assignee_display_name": "OA User",
            "source_title": "Original OA title",
            "source_status": "OA_PENDING",
            "source_received_at": "2026-08-26 09:00:00",
            "source_created_at": "2026-08-26 08:00:00",
            "source_workflow_type_id": "workflow-type-1",
            "source_fetched_at": now,
            "created_at": now,
            "updated_at": now,
        },
    )


def _insert_parent_oa_work_object(connection: Connection) -> None:
    now = datetime(2026, 8, 27, 1, 2, 3, tzinfo=UTC)
    connection.execute(
        text(
            "INSERT INTO work_objects ("
            "work_object_id, source_system, source_kind, source_ref, "
            "assignee_ai_user_id, assignee_display_name, due_at, source_title, "
            "source_status, source_received_at, source_created_at, "
            "source_workflow_type_id, source_fetched_at, handling_mark, "
            "handling_marked_by_ai_user_id, handling_marked_at, task_record_id, "
            "created_at, updated_at"
            ") VALUES ("
            ":work_object_id, 'oa', 'pending_workflow', :source_ref, "
            ":assignee_ai_user_id, :assignee_display_name, NULL, :source_title, "
            ":source_status, :source_received_at, :source_created_at, "
            ":source_workflow_type_id, :source_fetched_at, NULL, NULL, NULL, NULL, "
            ":created_at, :updated_at"
            ")"
        ),
        {
            "work_object_id": "work-oa",
            "source_ref": "OA-REF-001",
            "assignee_ai_user_id": "user-oa",
            "assignee_display_name": "OA User",
            "source_title": "Original OA title",
            "source_status": "OA_PENDING",
            "source_received_at": "2026-08-26 09:00:00",
            "source_created_at": "2026-08-26 08:00:00",
            "source_workflow_type_id": "workflow-type-1",
            "source_fetched_at": now,
            "created_at": now,
            "updated_at": now,
        },
    )


def _insert_internal_work_object(
    connection: Connection,
    *,
    source_title: str | None = None,
) -> None:
    now = datetime(2026, 8, 27, 2, 3, 4, tzinfo=UTC)
    connection.execute(
        text(_INSERT_WORK_OBJECT_SQL),
        {
            "work_object_id": "work-internal",
            "state_authority": "internal",
            "source_system": "eternalai",
            "source_kind": "internal_task",
            "source_ref": None,
            "assignee_ai_user_id": "user-internal",
            "assignee_display_name": "Internal User",
            "source_title": source_title,
            "source_status": None,
            "source_received_at": None,
            "source_created_at": None,
            "source_workflow_type_id": None,
            "source_fetched_at": None,
            "created_at": now,
            "updated_at": now,
        },
    )


def _assert_parent_work_object_shape(connection: Connection) -> None:
    assert _work_object_column_names(connection) == WORK_OBJECT_PARENT_COLUMNS
    assert set(OA_SNAPSHOT_COLUMN_NAMES) <= _work_object_not_null_columns(connection)
    assert {
        "ck_work_objects_source_system",
        "ck_work_objects_source_kind",
        "uq_work_objects_assignee_source",
    } <= _work_object_constraint_names(connection)


def test_work_object_model_downgrade_succeeds_for_empty_table(
    work_object_migration_connection: Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALLOW_DESTRUCTIVE_DOWNGRADE", raising=False)
    _run_work_object_model_upgrade(work_object_migration_connection)
    assert (
        work_object_migration_connection.execute(
            text("SELECT count(*) FROM work_objects")
        ).scalar_one()
        == 0
    ), "本用例的前提是空表；有数据时走的是另外两条 downgrade 用例"

    _run_work_object_model_downgrade(work_object_migration_connection)

    _assert_parent_work_object_shape(work_object_migration_connection)
    assert (
        work_object_migration_connection.execute(
            text("SELECT count(*) FROM work_objects")
        ).scalar_one()
        == 0
    )


def test_work_object_model_downgrade_preserves_oa_rows(
    work_object_migration_connection: Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALLOW_DESTRUCTIVE_DOWNGRADE", raising=False)
    _run_work_object_model_upgrade(work_object_migration_connection)
    _insert_oa_work_object(work_object_migration_connection)

    _run_work_object_model_downgrade(work_object_migration_connection)

    _assert_parent_work_object_shape(work_object_migration_connection)
    row = work_object_migration_connection.execute(
        text(
            "SELECT work_object_id, source_title, source_status "
            "FROM work_objects"
        )
    ).one()
    assert tuple(row) == ("work-oa", "Original OA title", "OA_PENDING")


def test_work_object_model_downgrade_rejects_internal_rows_without_guard(
    work_object_migration_connection: Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALLOW_DESTRUCTIVE_DOWNGRADE", raising=False)
    _run_work_object_model_upgrade(work_object_migration_connection)
    _insert_internal_work_object(work_object_migration_connection)

    with pytest.raises(RuntimeError, match="ALLOW_DESTRUCTIVE_DOWNGRADE=1"):
        _run_work_object_model_downgrade(work_object_migration_connection)

    assert work_object_migration_connection.execute(
        text("SELECT count(*) FROM work_objects")
    ).scalar_one() == 1
    assert work_object_migration_connection.execute(
        text(
            "SELECT state_authority FROM work_objects "
            "WHERE work_object_id = 'work-internal'"
        )
    ).scalar_one() == "internal"


def test_work_object_model_downgrade_deletes_internal_rows_with_guard(
    work_object_migration_connection: Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALLOW_DESTRUCTIVE_DOWNGRADE", "1")
    _run_work_object_model_upgrade(work_object_migration_connection)
    _insert_oa_work_object(work_object_migration_connection)
    _insert_internal_work_object(work_object_migration_connection)

    _run_work_object_model_downgrade(work_object_migration_connection)

    _assert_parent_work_object_shape(work_object_migration_connection)
    assert work_object_migration_connection.execute(
        text("SELECT work_object_id FROM work_objects")
    ).scalar_one() == "work-oa"


def test_work_object_model_check_rejects_mixed_internal_snapshot_fields(
    work_object_migration_connection: Connection,
) -> None:
    _run_work_object_model_upgrade(work_object_migration_connection)
    savepoint = work_object_migration_connection.begin_nested()
    try:
        with pytest.raises(
            IntegrityError,
            match="ck_work_objects_internal_fields",
        ):
            _insert_internal_work_object(
                work_object_migration_connection,
                source_title="must-be-null",
            )
    finally:
        savepoint.rollback()


def test_work_object_model_upgrade_preserves_existing_oa_snapshot_values(
    work_object_migration_connection: Connection,
) -> None:
    _insert_parent_oa_work_object(work_object_migration_connection)
    before = work_object_migration_connection.execute(
        text(
            "SELECT source_ref, source_title, source_status, "
            "source_received_at, source_created_at, source_workflow_type_id, "
            "source_fetched_at FROM work_objects WHERE work_object_id = 'work-oa'"
        )
    ).one()

    _run_work_object_model_upgrade(work_object_migration_connection)

    after = work_object_migration_connection.execute(
        text(
            "SELECT source_ref, source_title, source_status, "
            "source_received_at, source_created_at, source_workflow_type_id, "
            "source_fetched_at, state_authority FROM work_objects "
            "WHERE work_object_id = 'work-oa'"
        )
    ).one()
    assert tuple(after[:7]) == tuple(before)
    assert after.state_authority == "external_snapshot"


def test_alembic_upgrade_downgrade_upgrade_cycle() -> None:
    config = _alembic_config()

    command.upgrade(config, "head")
    assert _vector_extension_version() is not None

    command.downgrade(config, "base")
    assert _vector_extension_version() is None

    command.upgrade(config, "head")
    assert _vector_extension_version() is not None


def test_revocation_migration_preserves_schema_and_sentinel_row() -> None:
    config = _alembic_config()
    database_url = normalize_database_url(_database_url_from_environment())
    ai_user_id = f"usr_v1_{uuid4().hex}{uuid4().hex[:11]}"
    nonce = os.urandom(12)
    encrypted_payload = os.urandom(32)
    expires_at = datetime.now(UTC) + timedelta(hours=2)
    updated_at = datetime.now(UTC)

    command.upgrade(config, "head")
    command.downgrade(config, REVOCATION_PARENT_REVISION)
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO oa_session_credentials"
                    " (ai_user_id, cipher_version, nonce, encrypted_payload,"
                    " expires_at, updated_at)"
                    " VALUES"
                    " (:ai_user_id, :cipher_version, :nonce, :encrypted_payload,"
                    " :expires_at, :updated_at)"
                ),
                {
                    "ai_user_id": ai_user_id,
                    "cipher_version": "aes256gcm-v1",
                    "nonce": nonce,
                    "encrypted_payload": encrypted_payload,
                    "expires_at": expires_at,
                    "updated_at": updated_at,
                },
            )

        before_columns = _credential_columns()
        assert set(before_columns) == ORIGINAL_CREDENTIAL_COLUMNS
        before_row = _credential_row(engine, ai_user_id)
        assert set(before_row) == ORIGINAL_CREDENTIAL_COLUMNS
        before_row_signature = _safe_credential_row_signature(before_row)

        command.upgrade(config, REVOCATION_REVISION)
        upgraded_columns = _credential_columns()
        assert set(upgraded_columns) == ORIGINAL_CREDENTIAL_COLUMNS | {"revoked_at"}
        for column_name in ORIGINAL_CREDENTIAL_COLUMNS:
            assert _column_signature(upgraded_columns[column_name]) == _column_signature(
                before_columns[column_name]
            )
        revoked_column = upgraded_columns["revoked_at"]
        assert isinstance(revoked_column["type"], sa.DateTime)
        assert revoked_column["type"].timezone is True
        assert revoked_column["nullable"] is True
        assert revoked_column.get("default") is None

        upgraded_row = _credential_row(engine, ai_user_id)
        assert set(upgraded_row) == ORIGINAL_CREDENTIAL_COLUMNS | {"revoked_at"}
        assert upgraded_row["revoked_at"] is None
        assert _safe_credential_row_signature(upgraded_row) == before_row_signature

        revoked_at = datetime.now(UTC)
        with engine.begin() as connection:
            persisted_revoked_at = connection.execute(
                text(
                    "UPDATE oa_session_credentials"
                    " SET revoked_at = :revoked_at"
                    " WHERE ai_user_id = :ai_user_id"
                    " RETURNING revoked_at"
                ),
                {"ai_user_id": ai_user_id, "revoked_at": revoked_at},
            ).scalar_one()
            assert persisted_revoked_at == revoked_at

        command.downgrade(config, REVOCATION_PARENT_REVISION)
        downgraded_columns = _credential_columns()
        assert set(downgraded_columns) == ORIGINAL_CREDENTIAL_COLUMNS
        for column_name in ORIGINAL_CREDENTIAL_COLUMNS:
            assert _column_signature(downgraded_columns[column_name]) == _column_signature(
                before_columns[column_name]
            )
        downgraded_row = _credential_row(engine, ai_user_id)
        assert set(downgraded_row) == ORIGINAL_CREDENTIAL_COLUMNS
        assert _safe_credential_row_signature(downgraded_row) == before_row_signature

        command.upgrade(config, REVOCATION_REVISION)
        reupgraded_row = _credential_row(engine, ai_user_id)
        assert set(reupgraded_row) == ORIGINAL_CREDENTIAL_COLUMNS | {"revoked_at"}
        assert reupgraded_row["revoked_at"] is None
        assert _safe_credential_row_signature(reupgraded_row) == before_row_signature
    finally:
        try:
            command.upgrade(config, "head")
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "DELETE FROM oa_session_credentials"
                        " WHERE ai_user_id = :ai_user_id"
                    ),
                    {"ai_user_id": ai_user_id},
                )
        finally:
            engine.dispose()
