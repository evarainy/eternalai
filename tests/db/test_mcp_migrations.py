"""Authorized migrations in a transaction-local synthetic schema only."""

from __future__ import annotations

from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from app.db.config import normalize_database_url

BASE, FOUNDATION, HEAD = "20260922_190000", "20260930_090000", "20260930_100000"
TABLES = {
    "mcp_service_configs",
    "mcp_registrations",
    "mcp_connections",
    "mcp_oauth_transactions",
    "mcp_grants",
    "mcp_capability_bindings",
    "mcp_workflow_runs",
    "mcp_operations",
}


def migrate(connection, target):
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    steps = script._downgrade_revs if target == BASE else script._upgrade_revs
    context = MigrationContext.configure(
        connection, opts={"fn": lambda revisions, _: steps(target, revisions)}
    )
    with context.begin_transaction(), Operations.context(context):
        context.run_migrations()


def test_exact_eight_tables_roundtrip_and_retained_data_refuses_downgrade(migrated_database_url):
    engine = create_engine(normalize_database_url(migrated_database_url))
    assert (engine.url.host, engine.url.port, engine.url.database) == (
        "127.0.0.1",
        15432,
        "eternalai_test",
    )
    with engine.connect() as conn:
        transaction = conn.begin()
        schema = "mcp_migration_" + uuid4().hex
        try:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            conn.execute(
                text(
                    "CREATE TABLE capabilities (capability_id TEXT PRIMARY K"
                    "EY, target_system TEXT, "
                    "CONSTRAINT ck_capabilities_target_system CHECK "
                    "(target_system IS NULL OR target_system IN ('oa','u8','hikvision_ivms')))"
                )
            )
            conn.execute(text("INSERT INTO capabilities VALUES ('synthetic-existing','oa')"))
            conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"))
            conn.execute(text("INSERT INTO alembic_version VALUES (:base)"), {"base": BASE})
            migrate(conn, HEAD)
            assert set(inspect(conn).get_table_names(schema=schema)) == TABLES | {
                "capabilities",
                "alembic_version",
            }
            assert conn.execute(text("SELECT target_system FROM capabilities")).scalar_one() == "oa"
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == HEAD
            )
            foreign_keys = inspect(conn).get_foreign_keys("mcp_operations", schema=schema)
            assert foreign_keys[0]["constrained_columns"] == [
                "operation_id",
                "tenant_id",
                "user_id",
                "service_config_id",
            ]
            checks = {
                item["name"]
                for item in inspect(conn).get_check_constraints("mcp_operations", schema=schema)
            }
            assert checks == {"ck_mcp_operation_state", "ck_mcp_operation_revision"}
            migrate(conn, BASE)
            assert set(inspect(conn).get_table_names(schema=schema)) == {
                "capabilities",
                "alembic_version",
            }
            migrate(conn, HEAD)
            with conn.begin_nested() as savepoint:
                conn.execute(
                    text(
                        "INSERT INTO mcp_service_configs (tenant_id,service_conf"
                        "ig_id,version,config) "
                        "VALUES ('synthetic','synthetic',1,'{}')"
                    )
                )
                with pytest.raises(RuntimeError, match="mcp_downgrade_refused"):
                    migrate(conn, BASE)
                savepoint.rollback()
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == HEAD
            )
        finally:
            transaction.rollback()
    engine.dispose()
