"""S1 migration proof using synthetic rows in transaction-local schemas."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from app.db.config import normalize_database_url

BASE = "20260920_180000"
HEAD = "20260922_190000"
TABLES = ("sessions", "principal_roles", "oa_session_credentials")


@pytest.fixture
def migration_db():
    engine = create_engine(normalize_database_url(os.environ["DATABASE_URL"]))
    assert (engine.url.host, engine.url.port, engine.url.database) == (
        "127.0.0.1",
        15432,
        "eternalai_test",
    )
    with engine.connect() as connection:
        transaction = connection.begin()
        schema = "tenant_s1_" + uuid4().hex
        try:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            # Clone catalog only; never read shared credential or identity rows.
            for table in TABLES:
                connection.execute(
                    text(f"CREATE TABLE {table} (LIKE public.{table} INCLUDING ALL)")
                )
                source_indexes = inspect(connection).get_indexes(table, schema="public")
                cloned_indexes = inspect(connection).get_indexes(table, schema=schema)
                for source in source_indexes:
                    if source.get("duplicates_constraint"):
                        continue
                    clone = next(
                        item
                        for item in cloned_indexes
                        if item["column_names"] == source["column_names"]
                        and not item.get("duplicates_constraint")
                    )
                    connection.execute(
                        text(
                            f'ALTER INDEX "{schema}"."{clone["name"]}" RENAME TO "{source["name"]}"'
                        )
                    )
            connection.execute(
                text(
                    "ALTER TABLE oa_session_credentials RENAME CONSTRAINT "
                    "oa_session_credentials_pkey TO pk_oa_session_credentials"
                )
            )
            # Tests also work after the shared DB has been upgraded for integration.
            if "tenant_id" in {c["name"] for c in inspect(connection).get_columns("sessions")}:
                connection.execute(
                    text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)")
                )
                connection.execute(
                    text("INSERT INTO alembic_version VALUES (:head)"), {"head": HEAD}
                )
                migrate(connection, BASE)
            else:
                connection.execute(
                    text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)")
                )
                connection.execute(
                    text("INSERT INTO alembic_version VALUES (:head)"), {"head": BASE}
                )
            yield connection
        finally:
            transaction.rollback()
    engine.dispose()


def migrate(connection, target):
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    steps = script._upgrade_revs if target == HEAD else script._downgrade_revs
    context = MigrationContext.configure(
        connection, opts={"fn": lambda revs, ctx: steps(target, revs)}
    )
    with context.begin_transaction(), Operations.context(context):
        context.run_migrations()


def snapshot(connection):
    inspector = inspect(connection)
    return (
        connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one(),
        {t: list(connection.execute(text(f"SELECT * FROM {t}")).mappings()) for t in TABLES},
        {
            t: (
                [
                    (c["name"], str(c["type"]), c["nullable"], c["default"])
                    for c in inspector.get_columns(t)
                ],
                inspector.get_pk_constraint(t),
                inspector.get_check_constraints(t),
                inspector.get_indexes(t),
            )
            for t in TABLES
        },
    )


def seed(connection):
    connection.execute(text("INSERT INTO sessions (session_id) VALUES ('synthetic-session')"))
    connection.execute(
        text(
            "INSERT INTO principal_roles (ai_user_id, role)"
            " VALUES ('synthetic-user', 'audit_reader')"
        )
    )
    connection.execute(
        text(
            "INSERT INTO oa_session_credentials (ai_user_id, target_system, updated_at, "
            "cipher_version, nonce, encrypted_payload, expires_at) VALUES "
            "('synthetic-user', 'oa', '2026-09-22T00:00:00Z', 'synthetic-cipher', "
            "decode('010203', 'hex'), decode('040506', 'hex'), '2026-09-23T00:00:00Z')"
        )
    )


@pytest.mark.parametrize("with_rows", [False, True])
def test_default_backfill_roundtrip(migration_db, with_rows):
    db = migration_db
    if with_rows:
        seed(db)
    before = snapshot(db)
    migrate(db, HEAD)
    upgraded = snapshot(db)
    assert upgraded[0] == HEAD
    for table, old_keys in zip(
        TABLES,
        (["session_id"], ["ai_user_id", "role"], ["ai_user_id", "target_system"]),
        strict=True,
    ):
        columns = {c["name"]: c for c in inspect(db).get_columns(table)}
        assert columns["tenant_id"]["nullable"] is False
        assert columns["tenant_id"]["default"] is None
        assert inspect(db).get_pk_constraint(table)["constrained_columns"] == [
            "tenant_id",
            *old_keys,
        ]
        assert len(upgraded[1][table]) == int(with_rows)
        assert [
            {k: v for k, v in row.items() if k != "tenant_id"} for row in upgraded[1][table]
        ] == before[1][table]
        if with_rows:
            assert upgraded[1][table][0]["tenant_id"] == "default"
    migrate(db, BASE)
    assert snapshot(db) == before
    migrate(db, HEAD)
    assert snapshot(db) == upgraded


@pytest.mark.parametrize("table", TABLES)
def test_nondefault_refuses_downgrade(migration_db, table):
    db = migration_db
    seed(db)
    migrate(db, HEAD)
    db.execute(text(f"UPDATE {table} SET tenant_id = 'synthetic-TB'"))
    before = snapshot(db)
    assert before[0] == HEAD
    assert before[1][table][0]["tenant_id"] == "synthetic-TB"
    with db.begin_nested() as savepoint:
        with pytest.raises(RuntimeError, match="tenant_identity_downgrade_refused"):
            migrate(db, BASE)
        savepoint.rollback()
    assert snapshot(db) == before
