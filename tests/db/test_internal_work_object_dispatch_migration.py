"""Exercise dispatch DDL with real PostgreSQL and synthetic, isolated rows."""

from __future__ import annotations

import re
from datetime import datetime
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
    _apply_revision(db, "20260910_120000", direction)


def _lifecycle(db, direction):
    _apply_revision(db, "20260920_120000", direction)


def _isolate_connection(db, connection):
    assert re.fullmatch(r"dispatch_test_[0-9a-f]{32}", db.schema)
    connection.execute(text('SET LOCAL search_path TO "' + db.schema + '"'))
    assert connection.execute(text("SELECT current_schema()")).scalar_one() == db.schema


def _apply_revision(db, revision, direction):
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    module = ScriptDirectory.from_config(config).get_revision(revision).module
    with db.sql.begin() as connection:
        _isolate_connection(db, connection)
        with Operations.context(MigrationContext.configure(connection)):
            getattr(module, direction)()


def _historical_schema(db):
    # Only this empty fixture schema is rolled back; public remains at current head.
    _apply_revision(db, "20261022_120000", "downgrade")


def _publish_legacy(db, body=None):
    """Insert actual legacy publication/receipt facts, without a v2 HTTP/old-DDL mix."""
    body = request_body() if body is None else body
    before_objects, before_receipts = db.counts()
    object_ids = []
    for target in body["targets"]:
        personal = target["kind"] == "user"
        due_at = body["due_at"]
        if isinstance(due_at, str):
            due_at = datetime.fromisoformat(due_at.replace("Z", "+00:00"))
        row = {
            **_legacy_row(external=False),
            "source_kind": "manual_dispatch",
            "assignee_ai_user_id": None,
            "assignee_display_name": None if personal else "Synthetic department",
            "tenant_id": "default",
            "owner_department_id": "office-a",
            "initiator_ai_user_id": "ai-sender",
            "target_kind": target["kind"],
            "assignee_directory_user_id": target["directory_user_id"] if personal else None,
            "title": body["title"],
            "kind": body["kind"],
            "requirement": body["requirement"],
            "receipt_requirement": body["receipt_requirement"],
            "due_at": due_at,
            "status": "assigned" if personal else "department_pending",
            "reminder_choices": body["reminder_choices"],
            "version": 1,
        }
        insert_synthetic_row(db, row)
        object_ids.append(row["work_object_id"])
    statement = text(
        "INSERT INTO work_object_dispatch_receipts "
        "(tenant_id,initiator_ai_user_id,operation,idempotency_key,request_fingerprint,"
        "result,authorization_summary,created_at) VALUES "
        "('default','ai-sender','dispatch',:key,:fingerprint,:result,:authorization,:now)"
    ).bindparams(bindparam("result", type_=JSONB), bindparam("authorization", type_=JSONB))
    with db.sql.begin() as connection:
        _isolate_connection(db, connection)
        inserted = connection.execute(
            statement,
            {
                "key": uuid4(),
                "fingerprint": "a" * 64,
                "result": {"items": [{"work_object_id": item} for item in object_ids]},
                "authorization": {"synthetic": True},
                "now": NOW,
            },
        )
        assert inserted.rowcount == 1
    assert db.counts() == (before_objects + len(body["targets"]), before_receipts + 1)
    assert set(object_ids) <= {item["work_object_id"] for item in db.rows("work_objects")}
    return object_ids


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
    _historical_schema(db)
    assert db.counts() == (0, 0)
    _lifecycle(db, "downgrade")
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
    _lifecycle(db, "upgrade")
    for kind in ("通知", "督办令", "工作任务", "提醒"):
        object_ids = _publish_legacy(
            db,
            request_body(
                kind=kind,
                targets=[
                    *request_body()["targets"],
                    {"kind": "department", "department_id": "office-c"},
                ],
            )
        )
        assert len(object_ids) == 2
    _lifecycle(db, "downgrade")
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
    _historical_schema(db)
    _lifecycle(db, "downgrade")
    _apply(db, "downgrade")
    assert not CHECKS.intersection(
        check["name"]
        for check in inspect(db.sql).get_check_constraints("work_objects", schema=db.schema)
    )
    _apply(db, "upgrade")
    _lifecycle(db, "upgrade")
    assert len(_publish_legacy(db)) == 1
    original_objects = db.rows("work_objects")
    original_receipts = db.rows("work_object_dispatch_receipts")
    # Isolate the two refusal arms inside a transaction which is always rolled back.
    with db.sql.connect() as connection:
        transaction = connection.begin()
        try:
            _isolate_connection(db, connection)
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
    _historical_schema(db)
    assert len(_publish_legacy(db, request_body(due_at="2026-09-11T12:00:00Z"))) == 1
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
