"""Real PostgreSQL checks of the new table and guarded empty-table rollback."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from tests.api.test_work_object_dispatch import ROOT
from tests.api.test_work_object_dispatch import dispatch_db as dispatch_db


def apply(db, direction):
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    migration = ScriptDirectory.from_config(config).get_revision("20260920_180000").module
    with db.sql.begin() as connection, Operations.context(MigrationContext.configure(connection)):
        getattr(migration, direction)()


def test_upgrade_adds_only_declared_table_and_preserves_legacy_rows(dispatch_db):
    db = dispatch_db
    assert db.post().status_code == 201
    before = db.rows("work_objects")
    tables = set(inspect(db.sql).get_table_names(schema=db.schema))
    apply(db, "downgrade")
    assert set(inspect(db.sql).get_table_names(schema=db.schema)) == tables - {
        "auth_session_revocations"
    }
    apply(db, "upgrade")
    assert set(inspect(db.sql).get_table_names(schema=db.schema)) == tables
    assert db.rows("work_objects") == before
    columns = inspect(db.sql).get_columns("auth_session_revocations", schema=db.schema)
    assert {c["name"] for c in columns} == {"token_fingerprint", "expires_at", "revoked_at"}
    assert all(not c["nullable"] for c in columns)
    assert str(columns[0]["type"]) == "BYTEA"
    assert all(c["type"].timezone for c in columns[1:])
    assert columns[2]["default"] == "CURRENT_TIMESTAMP"
    assert inspect(db.sql).get_pk_constraint("auth_session_revocations", schema=db.schema)[
        "constrained_columns"
    ] == ["token_fingerprint"]
    assert rows(db, "auth_session_revocations") == []


@pytest.mark.parametrize(
    "invalid",
    [
        "short",
        "null_fp",
        "null_exp",
        "null_revoked",
        "infinite_exp",
        "infinite_revoked",
        "duplicate",
    ],
)
def test_constraints_accept_valid_and_reject_invalid_rows(dispatch_db, invalid):
    db = dispatch_db
    fp = uuid4().bytes + uuid4().bytes
    params = {
        "fp": fp,
        "expiry": datetime.now(UTC) - timedelta(hours=1),
        "revoked": datetime.now(UTC),
    }
    sql = text("INSERT INTO auth_session_revocations VALUES (:fp, :expiry, :revoked)")
    with db.sql.begin() as c:
        c.execute(sql, params)
    assert len(rows(db, "auth_session_revocations")) == 1
    if invalid != "duplicate":
        params["fp"] = uuid4().bytes + uuid4().bytes
    if invalid == "short":
        params["fp"] = b"short"
    elif invalid == "null_fp":
        params["fp"] = None
    elif invalid == "null_exp":
        params["expiry"] = None
    elif invalid == "null_revoked":
        params["revoked"] = None
    elif invalid == "infinite_exp":
        params["expiry"] = "infinity"
    elif invalid == "infinite_revoked":
        params["revoked"] = "-infinity"
    with pytest.raises(IntegrityError), db.sql.begin() as c:
        c.execute(sql, params)
    assert len(rows(db, "auth_session_revocations")) == 1


@pytest.mark.parametrize("expired", [True, False])
def test_downgrade_refuses_nonempty_and_drops_only_empty_new_table(dispatch_db, expired):
    db = dispatch_db
    fp = uuid4().bytes + uuid4().bytes
    expiry = datetime.now(UTC) + timedelta(hours=-1 if expired else 1)
    before = rows(db, "organization_departments")
    with db.sql.begin() as c:
        c.execute(
            text(
                "INSERT INTO auth_session_revocations (token_fingerprint, expires_at) "
                "VALUES (:fp, :expiry)"
            ),
            {"fp": fp, "expiry": expiry},
        )
    with pytest.raises(RuntimeError, match="^Cannot downgrade nonempty auth_session_revocations$"):
        apply(db, "downgrade")
    assert len(rows(db, "auth_session_revocations")) == 1
    with db.sql.begin() as c:
        c.execute(
            text("DELETE FROM auth_session_revocations WHERE token_fingerprint=:fp"), {"fp": fp}
        )
    apply(db, "downgrade")
    apply(db, "upgrade")
    assert rows(db, "auth_session_revocations") == []
    assert rows(db, "organization_departments") == before


def rows(db, table):
    assert table in {"auth_session_revocations", "organization_departments"}
    with db.sql.connect() as c:
        return list(c.execute(text("SELECT * FROM " + table)).mappings())
