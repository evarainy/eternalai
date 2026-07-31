from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, make_url

from alembic import command
from app.db.config import normalize_database_url

REPO_ROOT = Path(__file__).resolve().parents[2]
REVOCATION_PARENT_REVISION = "20260724_090000"
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

        command.upgrade(config, "head")
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

        command.upgrade(config, "head")
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
