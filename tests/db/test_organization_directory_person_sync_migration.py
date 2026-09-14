"""Approved upgrade/downgrade round trip on synthetic fixed-test-DB schemas."""

from __future__ import annotations

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from tests.api.test_work_object_dispatch import ROOT, assert_error
from tests.api.test_work_object_dispatch import dispatch_db as dispatch_db


def apply(db, direction):
    config = Config(str(ROOT / "alembic.ini"))
    migration = ScriptDirectory.from_config(config).get_revision("20260914_120000").module
    with db.sql.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            getattr(migration, direction)()


def rows(db, table):
    assert table in {
        "organization_departments",
        "organization_user_memberships",
        "organization_directory_sync_state",
    }
    with db.sql.connect() as connection:
        return [dict(row) for row in connection.execute(text("SELECT * FROM " + table)).mappings()]


def test_upgrade_preserves_rows_and_requires_first_success(dispatch_db):
    db = dispatch_db
    apply(db, "downgrade")
    before = {
        table: rows(db, table)
        for table in ("organization_departments", "organization_user_memberships")
    }
    apply(db, "upgrade")
    assert rows(db, "organization_departments") == before["organization_departments"]
    after = rows(db, "organization_user_memberships")
    assert [
        {key: value for key, value in row.items() if key != "display_name"} for row in after
    ] == before["organization_user_memberships"]
    assert all(row["display_name"] is None for row in after)
    state = rows(db, "organization_directory_sync_state")[0]
    assert state == {
        "singleton_id": 1,
        "snapshot_version": 0,
        "source_fetched_at": None,
        "last_success_at": None,
        "last_attempt_started_at": None,
        "last_attempt_finished_at": None,
        "last_attempt_status": "never",
        "last_error_code": None,
    }
    assert_error(
        db.client.get("/api/v1/work-objects/dispatch-options?kind=department"),
        503,
        "organization_directory_missing",
    )
    response = db.client.get("/api/v1/work-objects")
    assert response.status_code == 200
    assert response.json()["items"] == []


def test_downgrade_removes_only_added_objects(dispatch_db):
    db = dispatch_db
    before_tables = set(inspect(db.sql).get_table_names(schema=db.schema))
    before = rows(db, "organization_user_memberships")
    apply(db, "downgrade")
    inspector = inspect(db.sql)
    assert set(inspector.get_table_names(schema=db.schema)) == before_tables - {
        "organization_directory_sync_state"
    }
    assert "display_name" not in {
        column["name"]
        for column in inspector.get_columns("organization_user_memberships", schema=db.schema)
    }
    assert rows(db, "organization_user_memberships") == [
        {key: value for key, value in row.items() if key != "display_name"} for row in before
    ]
    apply(db, "upgrade")
    assert set(inspect(db.sql).get_table_names(schema=db.schema)) == before_tables


@pytest.mark.parametrize(
    "name", ["", " ", " Synthetic", "Synthetic ", "<Synthetic>", "x" * 201, "Synthetic\n"]
)
def test_checks_reject_inconsistent_success_and_invalid_names(dispatch_db, name):
    db = dispatch_db
    with pytest.raises(IntegrityError) as error:
        db.execute(
            "UPDATE organization_user_memberships SET display_name=:name WHERE user_id='sender'",
            name=name,
        )
    assert error.value.orig.diag.constraint_name == "ck_org_membership_display_name"
    for assignment, constraint in (
        ("snapshot_version=-1", "ck_org_sync_success"),
        ("last_success_at=NULL", "ck_org_sync_success"),
        ("last_attempt_started_at=NULL", "ck_org_sync_attempt"),
        (
            "last_attempt_finished_at=last_success_at+interval '1 second'",
            "ck_org_sync_success_attempt",
        ),
        ("last_error_code='synthetic-unknown',last_attempt_status='failed'", "ck_org_sync_error"),
    ):
        with pytest.raises(IntegrityError) as state_error:
            db.execute("UPDATE organization_directory_sync_state SET " + assignment)
        assert state_error.value.orig.diag.constraint_name == constraint
    for valid in (None, "Synthetic name", "合成姓名", "x" * 200):
        db.execute(
            "UPDATE organization_user_memberships SET display_name=:name WHERE user_id='sender'",
            name=valid,
        )
        assert (
            next(
                row
                for row in rows(db, "organization_user_memberships")
                if row["user_id"] == "sender"
            )["display_name"]
            == valid
        )
