"""Procrastinate 3.10.0 official queue schema (vendored, hash-pinned).

Upgrade-only authorization: P2-BACKGROUND-FOUNDATION-001 (DECISIONS 2026-11-03).
On the authorized targets (``current_schema() = 'public'``) the official objects are
installed into the dedicated namespace ``eternalai_queue``. When the chain is replayed
inside an isolated test schema (``current_schema() <> 'public'``), the same objects are
installed into that schema so they are dropped together with it.

The file is executed exactly as vendored, after its SHA256 is checked. Downgrade is
only possible while all four queue tables are empty.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision = "20261103_120000"
down_revision = "20261026_120000"
branch_labels = None
depends_on = None

QUEUE_SCHEMA = "eternalai_queue"
_SCHEMA_SQL = Path(__file__).resolve().parents[1] / "sql/procrastinate/3.10.0/schema.sql"
_SCHEMA_SQL_SHA256 = "c70ec4b400a60ad9592787653aae5ae77ae41bf801d56b5bd2712751a07f2009"
_TABLES = (
    "procrastinate_events",
    "procrastinate_periodic_defers",
    "procrastinate_jobs",
    "procrastinate_workers",
)
_TYPES = (
    "procrastinate_job_to_defer_v1",
    "procrastinate_job_event_type",
    "procrastinate_job_status",
)


def _target_schema(connection: sa.Connection) -> tuple[str, bool]:
    current = connection.execute(sa.text("SELECT current_schema()")).scalar_one()
    if current is None:
        raise RuntimeError("procrastinate_queue_schema_unresolved")
    return (QUEUE_SCHEMA, True) if current == "public" else (str(current), False)


def upgrade() -> None:
    if op.get_context().as_sql:
        raise RuntimeError("procrastinate_queue_offline_unsupported")
    sql_bytes = _SCHEMA_SQL.read_bytes()
    if hashlib.sha256(sql_bytes).hexdigest() != _SCHEMA_SQL_SHA256:
        raise RuntimeError("procrastinate_schema_hash_mismatch")
    connection = op.get_bind()
    schema, dedicated = _target_schema(connection)
    previous_path = connection.execute(
        sa.text("SELECT current_setting('search_path')")
    ).scalar_one()
    if dedicated:
        connection.execute(sa.text(f"CREATE SCHEMA {schema}"))
        connection.execute(
            sa.text("SELECT set_config('search_path', :path, true)"), {"path": schema}
        )
    # SQLAlchemy always hands the DBAPI a parameter container, which makes psycopg parse
    # the official ``%`` text as placeholders. The driver cursor on the same connection
    # and transaction runs the file without parameters, as one simple-protocol script.
    with connection.connection.driver_connection.cursor() as cursor:
        cursor.execute(sql_bytes.decode("utf-8"))
    connection.execute(
        sa.text("SELECT set_config('search_path', :path, true)"), {"path": previous_path}
    )


def downgrade() -> None:
    connection = op.get_bind()
    schema, dedicated = _target_schema(connection)
    quoted = connection.dialect.identifier_preparer.quote_schema(schema)
    tables = ", ".join(f"{quoted}.{table}" for table in _TABLES)
    connection.execute(sa.text(f"LOCK TABLE {tables} IN ACCESS EXCLUSIVE MODE"))
    for table in _TABLES:
        exists = sa.text(f"SELECT EXISTS(SELECT 1 FROM {quoted}.{table})")
        if connection.execute(exists).scalar_one():
            raise RuntimeError("procrastinate_queue_downgrade_irreversible")
    if dedicated:
        connection.execute(sa.text(f"DROP SCHEMA {quoted} CASCADE"))
        return
    connection.execute(sa.text(f"DROP TABLE {tables} CASCADE"))
    functions = connection.execute(
        sa.text(
            "SELECT p.oid::regprocedure::text FROM pg_proc p "
            "WHERE p.pronamespace = CAST(:schema AS regnamespace) "
            "AND p.proname LIKE 'procrastinate\\_%'"
        ),
        {"schema": quoted},
    ).scalars()
    for function in list(functions):
        connection.execute(sa.text(f"DROP FUNCTION {function}"))
    connection.execute(
        sa.text("DROP TYPE " + ", ".join(f"{quoted}.{name}" for name in _TYPES))
    )
