"""Real PostgreSQL migration verification, reserved for the neutral verifier."""
from __future__ import annotations

from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from tests.api.test_work_object_dispatch import NOW, ROOT, insert_synthetic_row
from tests.api.test_work_object_dispatch import dispatch_db as dispatch_db
from tests.db.test_internal_work_object_dispatch_migration import _legacy_row


def _migration():
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    return ScriptDirectory.from_config(config).get_revision("20260915_120000").module


def _apply(db, direction):
    with db.sql.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            getattr(_migration(), direction)()


def _rows(db, table):
    assert table in {"work_objects", "oa_work_sync_state", "oa_work_pending_observations"}
    with db.sql.connect() as connection:
        return [dict(r) for r in connection.execute(text("SELECT * FROM " + table)).mappings()]


def test_upgrade_keeps_legacy_rows_unverified(dispatch_db):
    db = dispatch_db
    _apply(db, "downgrade")
    legacy = _legacy_row(external=True)
    insert_synthetic_row(db, legacy)
    before = _rows(db, "work_objects")
    _apply(db, "upgrade")
    assert _rows(db, "work_objects") == before
    assert _rows(db, "oa_work_sync_state") == []
    assert _rows(db, "oa_work_pending_observations") == []
    from app.ports.work_object_scope import compute_visibility_scope
    from tests.api.test_work_object_dispatch import run

    scope = compute_visibility_scope(principal_tenant_id="default",
        principal_ai_user_id=legacy["assignee_ai_user_id"], principal_department_id=None)
    batch = run(db.store.list_with_oa_sync_for_scope(scope))
    assert batch.oa_sync.status == "never"
    assert batch.oa_sync.revision == 0
    assert batch.records[0].oa_observation.pending_state == "legacy_unverified"
    assert batch.records[0].oa_observation.last_checked_at is None
    assert batch.records[0].oa_observation.last_seen_at == legacy["source_fetched_at"]


def test_empty_downgrade_is_exact_and_revision_matches_filename(dispatch_db):
    db = dispatch_db
    tables = set(inspect(db.sql).get_table_names(schema=db.schema))
    _apply(db, "downgrade")
    assert set(inspect(db.sql).get_table_names(schema=db.schema)) == tables - {
        "oa_work_sync_state", "oa_work_pending_observations",
    }
    _apply(db, "upgrade")
    assert set(inspect(db.sql).get_table_names(schema=db.schema)) == tables
    assert _migration().revision == "20260915_120000"
    assert _migration().down_revision == "20260914_120000"
    assert {i["name"] for i in inspect(db.sql).get_indexes(
        "oa_work_pending_observations", schema=db.schema)} >= {"ix_owpo_subject_state"}


@pytest.mark.parametrize("changes,expected", [
    ({"tenant_id": "other"}, "ck_owss_scope"),
    ({"ai_user_id": " "}, "ck_owss_scope"),
    ({"stream": "other"}, "ck_owss_stream"),
    ({"issued_generation": -1}, "ck_owss_attempt"),
    ({"applied_generation": 2}, "ck_owss_attempt"),
    ({"last_attempt_status": "unsupported_scope"}, "ck_owss_attempt"),
    ({"last_attempt_status": "failed", "issued_generation": 1,
      "last_attempt_started_at": NOW, "last_attempt_finished_at": NOW}, "ck_owss_attempt"),
    ({"last_attempt_status": "succeeded", "issued_generation": 1,
      "applied_generation": 1, "last_attempt_started_at": NOW,
      "last_attempt_finished_at": NOW}, "ck_owss_attempt"),
    ({"last_attempt_status": "failed", "issued_generation": 1,
      "last_attempt_started_at": NOW, "last_attempt_finished_at": NOW,
      "last_error_code": "arbitrary"}, "ck_owss_failure"),
])
def test_status_constraints_reject_null_traps_and_invalid_values(dispatch_db, changes, expected):
    row = dict(tenant_id="default", ai_user_id=uuid4().hex, stream="pending",
               issued_generation=0, applied_generation=0, last_attempt_status="never",
               last_attempt_started_at=None, last_attempt_finished_at=None,
               last_success_at=None, last_error_code=None)
    row.update(changes)
    with pytest.raises(IntegrityError) as error:
        with dispatch_db.sql.begin() as connection:
            connection.execute(text("INSERT INTO oa_work_sync_state (" + ", ".join(row)
                + ") VALUES (" + ", ".join(":" + key for key in row) + ")"), row)
    assert error.value.orig.diag.constraint_name == expected
    assert _rows(dispatch_db, "oa_work_sync_state") == []


@pytest.mark.parametrize("state", ["never", "running", "succeeded", "failed"])
def test_status_constraints_accept_each_legal_state(dispatch_db, state):
    row = dict(tenant_id="default", ai_user_id=uuid4().hex, stream="pending",
               issued_generation=0 if state == "never" else 1,
               applied_generation=1 if state == "succeeded" else 0, last_attempt_status=state,
               last_attempt_started_at=None if state == "never" else NOW,
               last_attempt_finished_at=NOW if state in {"succeeded", "failed"} else None,
               last_success_at=NOW if state == "succeeded" else None,
               last_error_code="invalid_response" if state == "failed" else None)
    with dispatch_db.sql.begin() as connection:
        connection.execute(text("INSERT INTO oa_work_sync_state (" + ", ".join(row)
            + ") VALUES (" + ", ".join(":" + key for key in row) + ")"), row)
    assert _rows(dispatch_db, "oa_work_sync_state") == [row]


@pytest.mark.parametrize("populated", ["state", "observation"])
def test_populated_downgrade_refuses_without_any_data_change(dispatch_db, populated):
    db = dispatch_db
    if populated == "state":
        db.execute("INSERT INTO oa_work_sync_state (tenant_id, ai_user_id, stream) "
                   "VALUES ('default', 'synthetic', 'pending')")
    else:
        row = _legacy_row(external=True)
        insert_synthetic_row(db, row)
        db.execute("INSERT INTO oa_work_pending_observations "
            "(tenant_id, ai_user_id, source_ref, work_object_id, pending_state, revision, "
            "last_seen_at, last_checked_at) VALUES ('default', :owner, :ref, :id, "
            "'current', 1, :now, :now)", owner=row["assignee_ai_user_id"],
            ref=row["source_ref"], id=row["work_object_id"], now=NOW)
    before = {table: _rows(db, table) for table in
              ("work_objects", "oa_work_sync_state", "oa_work_pending_observations")}
    with pytest.raises(RuntimeError, match=r"^OA reconciliation data exists; downgrade refused\.$"):
        _apply(db, "downgrade")
    assert {table: _rows(db, table) for table in before} == before


@pytest.mark.parametrize("changes,expected", [
    ({"tenant_id": "other"}, "ck_owpo_scope"),
    ({"pending_state": "legacy_unverified"}, "ck_owpo_state"),
    ({"revision": 0}, "ck_owpo_revision"),
    ({"work_object_id": "absent-synthetic"}, "fk_owpo_work_object"),
    ({"pending_state": "current", "last_seen_at": NOW.replace(year=2025)}, "ck_owpo_times"),
    ({"pending_state": "unconfirmed", "last_seen_at": NOW.replace(year=2027)}, "ck_owpo_times"),
])
def test_observation_constraints_and_foreign_key_are_exact(dispatch_db, changes, expected):
    db = dispatch_db
    work = _legacy_row(external=True)
    insert_synthetic_row(db, work)
    row = dict(tenant_id="default", ai_user_id=work["assignee_ai_user_id"],
               source_ref=work["source_ref"], work_object_id=work["work_object_id"],
               pending_state="current", revision=1, last_seen_at=NOW, last_checked_at=NOW)
    row.update(changes)
    with pytest.raises(IntegrityError) as error:
        with db.sql.begin() as connection:
            connection.execute(text("INSERT INTO oa_work_pending_observations (" + ", ".join(row)
                + ") VALUES (" + ", ".join(":" + key for key in row) + ")"), row)
    assert error.value.orig.diag.constraint_name == expected
    assert _rows(db, "oa_work_pending_observations") == []


def test_downgrade_checks_empty_under_writer_exclusion(dispatch_db):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from sqlalchemy import event

    db = dispatch_db
    writer_inserted = Event()
    release_writer = Event()
    lock_attempted = Event()
    statements = []

    def record(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)
        if statement == "LOCK TABLE oa_work_sync_state IN ACCESS EXCLUSIVE MODE":
            lock_attempted.set()

    def writer():
        with db.sql.begin() as connection:
            connection.execute(text("INSERT INTO oa_work_sync_state "
                "(tenant_id,ai_user_id,stream) VALUES ('default','concurrent-writer','pending')"))
            writer_inserted.set()
            assert release_writer.wait(10)

    event.listen(db.sql, "before_cursor_execute", record)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            writing = pool.submit(writer)
            assert writer_inserted.wait(10)
            downgrading = pool.submit(_apply, db, "downgrade")
            try:
                assert lock_attempted.wait(10)
            finally:
                release_writer.set()
            writing.result()
            with pytest.raises(
                RuntimeError, match="OA reconciliation data exists; downgrade refused"
            ):
                downgrading.result()
    finally:
        release_writer.set()
        event.remove(db.sql, "before_cursor_execute", record)
    assert [r["ai_user_id"] for r in _rows(db, "oa_work_sync_state")] == ["concurrent-writer"]
    locks = [s for s in statements if s.startswith("LOCK TABLE")]
    assert locks == ["LOCK TABLE oa_work_sync_state IN ACCESS EXCLUSIVE MODE",
                     "LOCK TABLE oa_work_pending_observations IN ACCESS EXCLUSIVE MODE"]
    check = next(i for i, value in enumerate(statements) if value.startswith("SELECT EXISTS"))
    assert all(statements.index(lock) < check for lock in locks)
