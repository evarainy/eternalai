"""Lifecycle migration roundtrip and refusal with actual synthetic PG rows."""

from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from tests.api.test_work_object_dispatch import ROOT, insert_synthetic_row, request_body
from tests.api.test_work_object_dispatch import dispatch_db as dispatch_db
from tests.db.test_internal_work_object_dispatch_migration import _legacy_row

NEW_COLUMNS = {"accepted_by_ai_user_id", "accepted_at", "completed_by_ai_user_id", "completed_at"}


def apply(db, direction):
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    migration = ScriptDirectory.from_config(config).get_revision("20260920_120000").module
    with db.sql.begin() as connection, Operations.context(MigrationContext.configure(connection)):
        getattr(migration, direction)()


def test_upgrade_preserves_oa_legacy_dispatch_and_receipt(dispatch_db):
    db = dispatch_db
    apply(db, "downgrade")
    for external in (True, False):
        insert_synthetic_row(db, _legacy_row(external=external))
    # Publishing needs the new projection columns; create the initial row before the roundtrip.
    apply(db, "upgrade")
    response = db.post()
    assert response.status_code == 201
    before = db.rows("work_objects")
    receipts = db.rows("work_object_dispatch_receipts")
    apply(db, "downgrade")
    assert not NEW_COLUMNS.intersection(
        column["name"] for column in inspect(db.sql).get_columns("work_objects", schema=db.schema)
    )
    apply(db, "upgrade")
    assert db.rows("work_objects") == before
    assert db.rows("work_object_dispatch_receipts") == receipts
    with db.sql.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM work_object_lifecycle_events")
            ).scalar_one()
            == 0
        )


@pytest.mark.parametrize("completed", [False, True])
def test_empty_downgrade_roundtrip_and_populated_refusal(dispatch_db, completed):
    db = dispatch_db
    response = db.post()
    assert response.status_code == 201
    object_id = response.json()["items"][0]["work_object_id"]
    with db.sql.begin() as connection:
        connection.execute(
            text(
                "UPDATE work_objects SET status='in_progress',version=2, "
                "accepted_by_ai_user_id='ai-recipient',accepted_at=updated_at "
                "WHERE work_object_id=:id"
            ),
            {"id": object_id},
        )
        if completed:
            connection.execute(
                text(
                    "UPDATE work_objects SET status='completed',version=3, "
                    "completed_by_ai_user_id=accepted_by_ai_user_id,completed_at=updated_at "
                    "WHERE work_object_id=:id"
                ),
                {"id": object_id},
            )
        connection.execute(
            text(
                (
                    'INSERT INTO work_object_lifecycle_events (event_id,work'
                    '_object_id,tenant_id,actor_ai_user_id,operation,idempot'
                    'ency_key,request_fingerprint,from_status,to_status,resu'
                    'lt_version,occurred_at,text) SELECT :event,work_object_'
                    "id,tenant_id,accepted_by_ai_user_id,'accept',:key,:fing"
                    "erprint,'assigned','in_progress',2,accepted_at,NULL FRO"
                    'M work_objects WHERE work_object_id=:id'
                )
            ),
            {"event": uuid4(), "key": uuid4(), "fingerprint": "a" * 64, "id": object_id},
        )
    before = db.rows("work_objects")
    with pytest.raises(
        RuntimeError, match="^Lifecycle data exists; downgrade is refused without data changes\\.$"
    ):
        apply(db, "downgrade")
    assert db.rows("work_objects") == before
    with db.sql.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM work_object_lifecycle_events")
            ).scalar_one()
            == 1
        )
    # Only fixture-owned synthetic rows; preserve the initial publication and receipt.
    with db.sql.begin() as connection:
        connection.execute(
            text("DELETE FROM work_object_lifecycle_events WHERE work_object_id=:id"),
            {"id": object_id},
        )
        connection.execute(
            text(
                (
                    "UPDATE work_objects SET status='assigned',version=1,acc"
                    'epted_by_ai_user_id=NULL,accepted_at=NULL,completed_by_'
                    'ai_user_id=NULL,completed_at=NULL WHERE work_object_id='
                    ':id'
                )
            ),
            {"id": object_id},
        )
    receipts = db.rows("work_object_dispatch_receipts")
    apply(db, "downgrade")
    apply(db, "upgrade")
    assert db.rows("work_object_dispatch_receipts") == receipts
    assert db.rows("work_objects")[0]["status"] == "assigned"


@pytest.mark.parametrize("target", ["user", "department"])
def test_constraints_reject_invalid_rows_and_allow_both_targets(dispatch_db, target):
    db = dispatch_db
    body = request_body()
    if target == "department":
        body["targets"] = [{"kind": "department", "department_id": "office-a"}]
    response = db.post(body)
    assert response.status_code == 201
    object_id = response.json()["items"][0]["work_object_id"]
    initial = "assigned" if target == "user" else "department_pending"

    def update(expression):
        with db.sql.begin() as connection:
            connection.execute(
                text("UPDATE work_objects SET " + expression + " WHERE work_object_id=:id"),
                {"id": object_id},
            )

    for expression in (
        "accepted_by_ai_user_id='actor'",
        "accepted_at=updated_at",
        "status='in_progress'",
        "status='completed'",
    ):
        with pytest.raises(IntegrityError):
            update(expression)
        assert db.rows("work_objects")[0]["status"] == initial
    update("status='in_progress',version=2,accepted_by_ai_user_id='actor',accepted_at=updated_at")
    for expression in (
        "version=1",
        "accepted_at=created_at-interval '1 microsecond'",
        "completed_at=updated_at",
        "accepted_by_ai_user_id=''",
        "accepted_by_ai_user_id=NULL",
    ):
        with pytest.raises(IntegrityError):
            update(expression)
    update("status='completed',version=3,completed_by_ai_user_id='actor',completed_at=updated_at")
    for expression in (
        "version=2",
        "completed_by_ai_user_id='other'",
        "completed_at=NULL",
        "updated_at=completed_at+interval '1 microsecond'",
        "completed_at=accepted_at-interval '1 microsecond'",
    ):
        with pytest.raises(IntegrityError):
            update(expression)
    assert db.rows("work_objects")[0]["status"] == "completed"
    base = dict(
        event=uuid4(),
        id=object_id,
        tenant="default",
        actor="actor",
        operation="accept",
        key=uuid4(),
        fingerprint="a" * 64,
        before=initial,
        after="in_progress",
        version=2,
        body=None,
    )
    statement = text(
        (
            'INSERT INTO work_object_lifecycle_events (event_id,work_object_i'
            'd,tenant_id,actor_ai_user_id,operation,idempotency_key,request_f'
            'ingerprint,from_status,to_status,result_version,occurred_at,text'
            ') VALUES (:event,:id,:tenant,:actor,:operation,:key,:fingerprint'
            ',:before,:after,:version,clock_timestamp(),:body)'
        )
    )
    for changes in (
        {"tenant": "foreign"},
        {"actor": ""},
        {"fingerprint": "bad"},
        {"version": 1},
        {"body": "extra"},
        {"operation": "feedback"},
        {"after": "completed"},
    ):
        with pytest.raises(IntegrityError), db.sql.begin() as connection:
            connection.execute(statement, {**base, **changes})
    with db.sql.begin() as connection:
        connection.execute(statement, base)
    for changes in ({"event": uuid4(), "key": uuid4()}, {"event": uuid4(), "version": 3}):
        with pytest.raises(IntegrityError), db.sql.begin() as connection:
            connection.execute(statement, {**base, **changes})
    with db.sql.begin() as connection:
        connection.execute(
            statement,
            {
                **base,
                "event": uuid4(),
                "key": uuid4(),
                "version": 3,
                "operation": "complete",
                "before": "in_progress",
                "after": "completed",
                "body": "Done",
            },
        )
    with db.sql.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM work_object_lifecycle_events WHERE work_object_id=:id"),
                {"id": object_id},
            ).scalar_one()
            == 2
        )
