"""Exercise dispatch DDL with real PostgreSQL and synthetic, isolated rows."""

from __future__ import annotations

from itertools import combinations
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import bindparam, inspect, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError

from tests.api.test_work_object_dispatch import (
    NOW,
    ROOT,
    insert_synthetic_row,
    request_body,
)
from tests.api.test_work_object_dispatch import dispatch_db as dispatch_db

NEW_COLUMNS = {
    "tenant_id",
    "owner_department_id",
    "initiator_ai_user_id",
    "target_kind",
    "assignee_directory_user_id",
    "title",
    "kind",
    "requirement",
    "receipt_requirement",
    "status",
    "reminder_choices",
    "version",
}
CHECKS = {
    "ck_work_objects_manual_dispatch_complete",
    "ck_work_objects_external_dispatch_fields",
    "ck_work_objects_legacy_internal_dispatch_fields",
}
REMINDERS = ["提前 7 天", "提前 3 天", "提前 1 天", "逾期当天"]


def _migration():
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    return ScriptDirectory.from_config(config).get_revision("20260910_120000").module


def _apply(db, direction):
    with db.sql.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            getattr(_migration(), direction)()


def _legacy_row(*, external):
    row = {
        "work_object_id": uuid4().hex,
        "state_authority": "external_snapshot" if external else "internal",
        "source_system": "oa" if external else "eternalai",
        "source_kind": "pending_workflow" if external else "internal_task",
        "assignee_ai_user_id": "ai-synthetic-legacy",
        "assignee_display_name": "Synthetic legacy name",
        "created_at": NOW,
        "updated_at": NOW,
    }
    if external:
        row.update(
            source_ref=uuid4().hex,
            source_title="Synthetic OA snapshot",
            source_status="pending",
            source_received_at="synthetic received",
            source_created_at="synthetic created",
            source_workflow_type_id="synthetic type",
            source_fetched_at=NOW,
        )
    return row


def _reject(db, base, updates, constraint, *, sql_null=False):
    row = {**base, "work_object_id": uuid4().hex, **updates}
    if row["state_authority"] == "external_snapshot":
        row["source_ref"] = uuid4().hex
    columns = list(row)
    statement = text(
        "INSERT INTO work_objects ("
        + ", ".join(columns)
        + ") VALUES ("
        + ", ".join(":" + column for column in columns)
        + ")"
    )
    if "reminder_choices" in row:
        statement = statement.bindparams(
            bindparam(
                "reminder_choices",
                type_=JSONB(none_as_null=sql_null),
            )
        )
    with pytest.raises(IntegrityError) as error:
        with db.sql.begin() as connection:
            connection.execute(statement, row)
    assert error.value.orig.diag.constraint_name == constraint
    assert row["work_object_id"] not in {item["work_object_id"] for item in db.rows("work_objects")}


def test_dispatch_schema_enforces_new_records_without_rewriting_oa(dispatch_db):
    db = dispatch_db
    assert db.counts() == (0, 0)
    _apply(db, "downgrade")
    for external in (True, False):
        insert_synthetic_row(db, _legacy_row(external=external))
    before = {row["work_object_id"]: row for row in db.rows("work_objects")}
    _apply(db, "upgrade")
    inspector = inspect(db.sql)
    columns = {
        column["name"]: column for column in inspector.get_columns("work_objects", schema=db.schema)
    }
    assert set(columns) - set(next(iter(before.values()))) == NEW_COLUMNS
    assert all(columns[name]["nullable"] for name in NEW_COLUMNS)
    assert columns["assignee_ai_user_id"]["nullable"] is True
    assert columns["assignee_display_name"]["nullable"] is True
    checks = {
        check["name"] for check in inspector.get_check_constraints("work_objects", schema=db.schema)
    }
    assert CHECKS <= checks
    for row in db.rows("work_objects"):
        assert {name: value for name, value in row.items() if name not in NEW_COLUMNS} == before[
            row["work_object_id"]
        ]
        assert all(row[name] is None for name in NEW_COLUMNS)
    for kind in ("通知", "督办令", "工作任务", "提醒"):
        response = db.post(
            request_body(
                kind=kind,
                targets=[
                    *request_body()["targets"],
                    {"kind": "department", "department_id": "office-c"},
                ],
            )
        )
        assert response.status_code == 201, response.text
        assert response.json()["created_count"] == 2
    rows = db.rows("work_objects")
    assert len(rows) == 10
    manual = next(row for row in rows if row["target_kind"] == "user")
    department = next(row for row in rows if row["target_kind"] == "department")
    invalid = [{name: None} for name in NEW_COLUMNS - {"assignee_directory_user_id"}] + [
        {"assignee_directory_user_id": None},
        {"assignee_directory_user_id": ""},
        {"assignee_display_name": "fabricated"},
        {"assignee_ai_user_id": "fabricated"},
        {"kind": "invalid"},
        {"target_kind": "invalid"},
        {"status": "invalid"},
        {"status": "department_pending"},
        {"version": 0},
        {"tenant_id": ""},
        {"owner_department_id": ""},
        {"owner_department_id": "x" * 129},
        {"initiator_ai_user_id": ""},
        {"title": ""},
        {"title": "x" * 201},
        {"requirement": "x" * 10001},
        {"receipt_requirement": "x" * 2001},
        {"source_system": "oa"},
        {
            "handling_mark": "handled_elsewhere",
            "handling_marked_by_ai_user_id": "ai-sender",
            "handling_marked_at": NOW,
        },
    ]
    for updates in invalid:
        _reject(db, manual, updates, "ck_work_objects_manual_dispatch_complete", sql_null=True)
    for updates in (
        {"assignee_directory_user_id": "recipient"},
        {"assignee_display_name": None},
        {"status": "assigned"},
    ):
        _reject(db, department, updates, "ck_work_objects_manual_dispatch_complete")
    _reject(
        db,
        manual,
        {"source_title": "synthetic forbidden OA title"},
        "ck_work_objects_internal_fields",
    )
    for external, constraint in (
        (True, "ck_work_objects_external_dispatch_fields"),
        (False, "ck_work_objects_legacy_internal_dispatch_fields"),
    ):
        base = next(
            row
            for row in rows
            if row["state_authority"] == ("external_snapshot" if external else "internal")
            and row["source_kind"] != "manual_dispatch"
        )
        for name in ("assignee_ai_user_id", "assignee_display_name"):
            _reject(db, base, {name: None}, constraint)
        for name in NEW_COLUMNS:
            value = [] if name == "reminder_choices" else 1 if name == "version" else "synthetic"
            _reject(db, base, {name: value}, constraint)
    assert db.counts() == (10, 4)


@pytest.mark.parametrize("populated", ["object", "receipt"])
def test_dispatch_downgrade_refuses_populated_rows(dispatch_db, populated):
    db = dispatch_db
    _apply(db, "downgrade")
    assert not CHECKS.intersection(
        check["name"]
        for check in inspect(db.sql).get_check_constraints("work_objects", schema=db.schema)
    )
    _apply(db, "upgrade")
    assert db.post().status_code == 201
    original_objects = db.rows("work_objects")
    original_receipts = db.rows("work_object_dispatch_receipts")
    # Isolate the two refusal arms inside a transaction which is always rolled back.
    with db.sql.connect() as connection:
        transaction = connection.begin()
        try:
            other = "work_object_dispatch_receipts" if populated == "object" else "work_objects"
            connection.execute(text("DELETE FROM " + other))
            with Operations.context(MigrationContext.configure(connection)):
                with pytest.raises(
                    RuntimeError, match="Internal dispatch data exists; downgrade is refused"
                ):
                    _migration().downgrade()
            assert CHECKS <= {
                check["name"]
                for check in inspect(connection).get_check_constraints(
                    "work_objects", schema=db.schema
                )
            }
            remaining = "work_objects" if populated == "object" else "work_object_dispatch_receipts"
            assert connection.execute(text("SELECT count(*) FROM " + remaining)).scalar_one() == 1
        finally:
            transaction.rollback()
    assert db.rows("work_objects") == original_objects
    assert db.rows("work_object_dispatch_receipts") == original_receipts


def test_dispatch_reminder_check_accepts_only_canonical_subsets(dispatch_db):
    db = dispatch_db
    response = db.post(request_body(due_at="2026-09-11T12:00:00Z"))
    assert response.status_code == 201
    source = db.rows("work_objects")[0]
    for size in range(5):
        for subset in combinations(REMINDERS, size):
            insert_synthetic_row(
                db, {**source, "work_object_id": uuid4().hex, "reminder_choices": list(subset)}
            )
    assert len(db.rows("work_objects")) == 17
    insert_synthetic_row(
        db, {**source, "work_object_id": uuid4().hex, "due_at": None, "reminder_choices": []}
    )
    for value in (
        ["unknown"],
        [REMINDERS[0], REMINDERS[0]],
        list(reversed(REMINDERS)),
        [[REMINDERS[0]]],
        None,
        {},
        "invalid",
    ):
        _reject(db, source, {"reminder_choices": value}, "ck_work_objects_manual_dispatch_complete")
    _reject(
        db,
        source,
        {"reminder_choices": None},
        "ck_work_objects_manual_dispatch_complete",
        sql_null=True,
    )
    _reject(
        db,
        source,
        {"due_at": None, "reminder_choices": [REMINDERS[0]]},
        "ck_work_objects_manual_dispatch_complete",
    )
    assert db.counts() == (18, 1)
