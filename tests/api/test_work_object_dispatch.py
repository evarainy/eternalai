"""Dispatch contract tests use synthetic directory facts and real isolated PG tables."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.v1 import work_objects as api
from app.db.config import normalize_database_url
from app.event_loop import make_event_loop
from app.infra.observability.postgresql_trace import PostgreSQLTraceWriter
from app.infra.organization_directory.postgresql import PostgreSQLOrganizationDirectory
from app.infra.persistence.work_object.postgresql import PostgreSQLWorkObjectStore
from app.main import create_app
from app.ports import work_object_scope as policy
from app.ports.auth import Principal, PrincipalOrgContext
from app.ports.work_object_scope import DispatchAuthorizationDecision
from tests.auth_fakes import (
    TEST_CSRF_ALLOWED_ORIGINS,
    TEST_CSRF_HEADERS,
    StaticSessionTokens,
    auth_cookies,
    make_session_binder,
)
from tests.runtime.registry_fakes import StaticCapabilityRegistry

NOW = datetime(2026, 9, 10, 12, tzinfo=UTC)
TENANT = "default"
ROOT = Path(__file__).resolve().parents[2]
INTERNAL_KEYS = {
    "work_object_id",
    "state_authority",
    "source_system",
    "source_kind",
    "source_ref",
    "source_title",
    "source_status",
    "source_received_at",
    "source_created_at",
    "source_workflow_type_id",
    "source_fetched_at",
    "assignee_display_name",
    "due_at",
    "handling_mark",
    "handling_marked_at",
    "task_record_id",
    "handling_action",
    "handling_capability_id",
    "title",
    "requirement",
    "receipt_requirement",
    "owner_department_id",
    "initiator_ai_user_id",
    "kind",
    "target_kind",
    "status",
    "reminder_choices",
    "reminder_delivery",
    "version",
    "created_at",
    "updated_at",
}


def run(coroutine):
    with asyncio.Runner(loop_factory=make_event_loop) as runner:
        return runner.run(coroutine)


class DispatchHarness:
    def __init__(self, database_url: str, schema: str) -> None:
        from tests.api.test_work_objects import RecordingGateway

        self.schema = schema
        options = {"options": f"-csearch_path={schema},public"}
        self.sql = create_engine(normalize_database_url(database_url), connect_args=options)
        self.engine = create_async_engine(
            normalize_database_url(database_url),
            connect_args=options,
            poolclass=NullPool,
        )
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.store = PostgreSQLWorkObjectStore(self.factory)
        self.directory = PostgreSQLOrganizationDirectory(self.factory)
        self.trace = PostgreSQLTraceWriter(self.factory)
        self.service = api.WorkObjectService(
            store=self.store,
            gateway=RecordingGateway(),
            capability_registry=StaticCapabilityRegistry(),
            organization_directory=self.directory,
            trace_port=self.trace,
            clock=lambda: NOW,
        )
        self.tokens = StaticSessionTokens(roles=("user",))
        self.execute(
            "WITH moment AS (SELECT clock_timestamp() AS now) "
            "UPDATE organization_directory_sync_state SET snapshot_version=1, "
            "source_fetched_at=moment.now,last_success_at=moment.now,"
            "last_attempt_started_at=moment.now,last_attempt_finished_at=moment.now,"
            "last_attempt_status='succeeded',last_error_code=NULL FROM moment WHERE singleton_id=1"
        )
        self.actor("sender", "office-a", "75")
        self.client = TestClient(
            create_app(
                work_object_service=self.service,
                session_tokens=self.tokens,
                session_binder=make_session_binder(),
                session_cookie_ttl_seconds=3600,
                csrf_allowed_origins=TEST_CSRF_ALLOWED_ORIGINS,
            ),
            base_url="https://testserver",
            backend_options={"loop_factory": make_event_loop},
        )
        self.client.cookies.update(auth_cookies())

    def execute(self, statement: str, **parameters: Any) -> None:
        with self.sql.begin() as connection:
            connection.execute(text(statement), parameters)

    def rows(self, table: str) -> list[dict[str, Any]]:
        assert table in {"work_objects", "work_object_dispatch_receipts", "trace_events"}
        with self.sql.connect() as connection:
            return [
                dict(row) for row in connection.execute(text("SELECT * FROM " + table)).mappings()
            ]

    def membership(self, user: str, department: str, job: str | None = None) -> None:
        self.execute(
            "INSERT INTO organization_user_memberships "
            "(user_id, department_id, job_title, fetched_at) VALUES (:user, "
            ":department, :job, :now) "
            "ON CONFLICT (user_id, department_id) DO UPDATE SET job_title = EXCLUDED.job_title",
            user=user,
            department=department,
            job=job,
            now=NOW,
        )

    def actor(self, user: str, department: str, job: str | None, *, tenant: str = TENANT) -> None:
        self.membership(user, department, job)
        self.tokens.principal = Principal(
            ai_user_id="ai-" + user,
            display_name="Synthetic sender",
            roles=("user",),
            org_ctx=PrincipalOrgContext(
                tenant_id=tenant,
                directory_user_id=user,
                department_id="stale-token-department",
            ),
        )

    def post(self, body: dict[str, Any] | None = None, *, key: str | None = None):
        return self.client.post(
            "/api/v1/work-objects/dispatch",
            json=body or request_body(),
            headers={**TEST_CSRF_HEADERS, "Idempotency-Key": key or str(uuid4())},
        )

    def counts(self) -> tuple[int, int]:
        return len(self.rows("work_objects")), len(self.rows("work_object_dispatch_receipts"))


@pytest.fixture
def dispatch_db(migrated_database_url: str) -> Iterator[DispatchHarness]:
    """Each schema begins without tables; actual repository migrations build it."""
    schema = "dispatch_test_" + uuid4().hex
    admin = create_engine(normalize_database_url(migrated_database_url))
    with admin.begin() as connection:
        connection.execute(text('CREATE SCHEMA "' + schema + '"'))
    db = None
    try:
        migration_engine = create_engine(
            normalize_database_url(migrated_database_url),
            connect_args={"options": f"-csearch_path={schema},public"},
        )
        try:
            with migration_engine.begin() as connection:
                assert (
                    connection.execute(
                        text(
                            "SELECT count(*) FROM information_schema.tables "
                            "WHERE table_schema=:schema"
                        ),
                        {"schema": schema},
                    ).scalar_one()
                    == 0
                )
                config = Config(str(ROOT / "alembic.ini"))
                config.set_main_option("script_location", str(ROOT / "alembic"))
                scripts = ScriptDirectory.from_config(config)
                with Operations.context(MigrationContext.configure(connection)):
                    for revision in reversed(list(scripts.walk_revisions())):
                        revision.module.upgrade()
                for department in ("office-a", "office-b", "office-c", "572", "575"):
                    connection.execute(
                        text(
                            "INSERT INTO organization_departments "
                            "(department_id, display_name, fetched_at) "
                            "VALUES (:department, :name, :now)"
                        ),
                        {"department": department, "name": "Synthetic " + department, "now": NOW},
                    )
        finally:
            migration_engine.dispose()
        db = DispatchHarness(migrated_database_url, schema)
        db.membership("recipient", "office-b")
        db.membership("local-recipient", "office-a")
        yield db
    finally:
        if db is not None:
            db.client.close()
            run(db.engine.dispose())
            db.sql.dispose()
        # Only the synthetic schema created by this fixture is removed.
        assert re.fullmatch(r"dispatch_test_[0-9a-f]{32}", schema)
        with admin.begin() as connection:
            connection.execute(text('DROP SCHEMA "' + schema + '" CASCADE'))
        admin.dispose()


def request_body(**updates: Any) -> dict[str, Any]:
    return {
        "kind": "工作任务",
        "title": "Synthetic dispatch",
        "requirement": "",
        "receipt_requirement": "",
        "due_at": None,
        "reminder_choices": [],
        "targets": [
            {"kind": "user", "directory_user_id": "local-recipient", "department_id": "office-a"}
        ],
        **updates,
    }


def cross_department_body(**updates):
    return request_body(
        **{
            "targets": [
                {"kind": "user", "directory_user_id": "recipient", "department_id": "office-b"}
            ],
            **updates,
        }
    )


def expire_directory(db):
    db.execute(
        "UPDATE organization_directory_sync_state SET "
        "source_fetched_at=source_fetched_at-interval '3 days',"
        "last_success_at=last_success_at-interval '3 days',"
        "last_attempt_started_at=last_attempt_started_at-interval '3 days',"
        "last_attempt_finished_at=last_attempt_finished_at-interval '3 days'"
    )


def test_stale_directory_blocks_initial_dispatch_and_receipt_replay(dispatch_db):
    db = dispatch_db
    key = str(uuid4())
    assert_created(db, db.post(key=key))
    assert db.post(key=key).status_code == 200
    expire_directory(db)
    for current in (key, str(uuid4())):
        assert_error(db.post(key=current), 503, "organization_directory_stale")
        assert db.counts() == (1, 1)


def test_expiry_during_request_blocks_publication(dispatch_db, monkeypatch):
    db = dispatch_db
    ticks = [0.0]
    db.service._monotonic = lambda: ticks[0]
    original = db.trace.record_event
    async def slow_audit(event):
        await original(event)
        ticks[0] = 172801.0
    monkeypatch.setattr(db.trace, "record_event", slow_audit)
    assert_error(db.post(), 503, "organization_directory_stale")
    assert db.counts() == (0, 0)


def test_request_uses_one_directory_generation_for_all_targets(dispatch_db, monkeypatch):
    monkeypatch.setattr(
        policy, "_CROSS_DEPARTMENT_DISPATCH_ALLOWED_IDS", frozenset({"office-a", "office-c"})
    )
    db = dispatch_db
    reads = []
    original_read = db.directory.read_view
    async def read():
        reads.append(True)
        return await original_read()
    monkeypatch.setattr(db.directory, "read_view", read)
    original_audit = db.trace.record_event
    async def revoke_after_decision(event):
        await original_audit(event)
        db.membership("sender", "office-a", None)
    monkeypatch.setattr(db.trace, "record_event", revoke_after_decision)
    result = db.post(cross_department_body(targets=[
        *cross_department_body()["targets"], {"kind": "department", "department_id": "office-c"},
    ]))
    assert result.status_code == 201
    assert len(reads) == 1
    assert db.counts() == (2, 1)
    assert_error(db.post(cross_department_body()), 403, "not_department_head")
    assert len(reads) == 2


@pytest.mark.parametrize("zero", [0, "0", None])
@pytest.mark.parametrize("actor_department", ["office-a", "572"])
def test_zero_jobtitle_imported_to_pg_denies_dispatch_and_replay(
    dispatch_db, zero, actor_department
):
    from app.infra.organization_directory.importer import (
        build_directory_page,
        build_directory_snapshot,
    )
    from tests.infra.organization_directory.test_sync_state import snapshot
    db = dispatch_db
    db.actor("zero-actor", actor_department, "75")
    db.membership("zero-target", actor_department)
    same_department = request_body(
        targets=[{"kind": "user", "directory_user_id": "zero-target",
                  "department_id": actor_department}]
    )
    key = str(uuid4())
    assert_created(db, db.post(same_department, key=key))
    async def import_zero():
        base = await snapshot(db)
        rows = [
            {"id": member.user_id, "departmentid": member.department_id,
             "jobtitle": zero if member.user_id == "zero-actor" else member.job_title,
             "lastname": "Synthetic person"}
            for member in base.memberships
        ]
        if zero is None:
            for row in rows:
                if row["id"] == "zero-actor":
                    row.pop("jobtitle")
        page = build_directory_page(current_page=1, next_page=None, is_end=True, user_rows=rows)
        imported = build_directory_snapshot(
            departments=base.departments,
            user_pages=[page],
            authoritative_user_count_before=len(rows),
            authoritative_user_count_after=len(rows),
            fetched_at=base.fetched_at,
        )
        assert imported.is_complete and imported.returned_user_count == len(rows)
        await db.directory.replace_snapshot(imported)
    run(import_zero())
    with db.sql.connect() as connection:
        assert (
            connection.execute(
                text("SELECT job_title FROM organization_user_memberships "
                     "WHERE user_id='zero-actor'")
            ).scalar_one()
            is None
        )
    for current in (key, str(uuid4())):
        assert_error(db.post(same_department, key=current), 403, "not_department_head")
    assert_error(db.post(), 403, "not_department_head")
    for query in ("kind=department", "kind=user&department_id=" + actor_department,
                  "kind=user&department_id=office-b"):
        assert_error(db.client.get("/api/v1/work-objects/dispatch-options?"+query),
                     403, "not_department_head")
    assert db.counts() == (1, 1)


def test_other_tenant_cannot_use_global_directory_for_authorization(dispatch_db, monkeypatch):
    db = dispatch_db
    assert_created(db, db.post())
    db.actor("sender", "office-a", "75", tenant="synthetic-other")
    async def forbidden():
        pytest.fail("nondefault identity read the shared directory")
    monkeypatch.setattr(db.directory, "read_view", forbidden)
    assert_error(db.post(), 503, "organization_directory_unavailable")
    response = db.client.get("/api/v1/work-objects")
    assert response.status_code == 200 and response.json()["items"] == []
    assert db.counts() == (1, 1)
    from tests.api.test_work_objects import RecordingGateway, _success_result
    db.service._gateway = RecordingGateway(_success_result())
    synced = db.client.post("/api/v1/work-objects/sync", headers=TEST_CSRF_HEADERS)
    assert synced.status_code == 200
    own_oa = next(item for item in synced.json()["items"] if item["source_ref"] == "oa-todo-1")
    assert db.client.get("/api/v1/work-objects/"+own_oa["work_object_id"]).status_code == 200


def assert_error(response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    assert set(response.json()) == {"detail"}
    assert set(response.json()["detail"]) == {"code", "message"}
    assert response.json()["detail"]["code"] == code
    assert isinstance(response.json()["detail"]["message"], str)
    assert response.json()["detail"]["message"]


def assert_created(db: DispatchHarness, response) -> dict[str, Any]:
    assert response.status_code == 201, response.text
    item = response.json()["items"][0]
    assert item["work_object_id"] in {row["work_object_id"] for row in db.rows("work_objects")}
    assert item["assignee_display_name"] is None
    return item


def insert_synthetic_row(db: DispatchHarness, row: dict[str, Any]) -> None:
    """Raw SQL fixture path for constraint and scope tests, never production data."""
    assert re.fullmatch(r"dispatch_test_[0-9a-f]{32}", db.schema)
    columns = list(row)
    assert all(re.fullmatch(r"[a-z_]+", column) for column in columns)
    statement = text(
        "INSERT INTO work_objects ("
        + ", ".join(columns)
        + ") VALUES ("
        + ", ".join(":" + column for column in columns)
        + ")"
    )
    if "reminder_choices" in row:
        statement = statement.bindparams(bindparam("reminder_choices", type_=JSONB))
    with db.sql.begin() as connection:
        connection.execute(statement, row)


def manual_row(db: DispatchHarness, **updates: Any) -> dict[str, Any]:
    """Clone an actual API-created row; every override is explicitly synthetic."""
    source = next(row for row in db.rows("work_objects") if row["source_kind"] == "manual_dispatch")
    return {**source, "work_object_id": uuid4().hex, **updates}


@pytest.mark.parametrize(
    "case", ["session", "csrf", "duplicate-csrf", "origin", "duplicate-origin"]
)
def test_dispatch_requires_session_and_csrf(dispatch_db, case):
    db = dispatch_db
    headers = list({**TEST_CSRF_HEADERS, "Idempotency-Key": str(uuid4())}.items())
    if case == "session":
        db.client.cookies.clear()
    elif case == "csrf":
        headers = [(key, value) for key, value in headers if key.lower() != "x-eternalai-csrf"]
    elif case == "duplicate-csrf":
        headers.append(("X-EternalAI-CSRF", "1"))
    elif case == "origin":
        headers = [
            (key, "https://outside.invalid" if key.lower() == "origin" else value)
            for key, value in headers
        ]
    else:
        headers.append(("Origin", "https://testserver"))
    response = db.client.post("/api/v1/work-objects/dispatch", json=request_body(), headers=headers)
    assert_error(
        response,
        401 if case == "session" else 403,
        "authentication_required" if case == "session" else "csrf_validation_failed",
    )
    assert db.counts() == (0, 0)


@pytest.mark.parametrize(
    "updates",
    [
        {"title": 12},
        {"tenant_id": "SYNTHETIC-SECRET-MARKER"},
        {"kind": "invalid"},
        {"targets": []},
        {"targets": [{"kind": "department", "department_id": "office-b"}] * 101},
        {"title": "x" * 201},
        {"due_at": "2026-09-10T12:00:00"},
        {"due_at": 123456},
        {
            "targets": [
                {
                    "kind": "user",
                    "directory_user_id": "SYNTHETIC-SECRET-MARKER",
                    "department_id": "",
                }
            ]
        },
        {"idempotency_values": []},
        {"idempotency_values": ["invalid"]},
        {"idempotency_values": ["00000000-0000-4000-8000-000000000001"] * 2},
        {"idempotency_values": [""]},
    ],
    ids=[
        "type",
        "extra",
        "kind",
        "empty",
        "batch-limit",
        "title-limit",
        "naive",
        "epoch",
        "target",
        "missing-key",
        "bad-key",
        "duplicate-key",
        "empty-key",
    ],
)
def test_dispatch_validates_dto_without_echoing_input(dispatch_db, updates, caplog):
    if "idempotency_values" in updates:
        headers = [
            *TEST_CSRF_HEADERS.items(),
            *[("Idempotency-Key", value) for value in updates["idempotency_values"]],
        ]
        response = dispatch_db.client.post(
            "/api/v1/work-objects/dispatch", json=request_body(), headers=headers
        )
        code = "idempotency_key_invalid"
    else:
        response = dispatch_db.post(request_body(**updates))
        code = "dispatch_request_invalid"
    assert_error(response, 422, code)
    assert "SYNTHETIC-SECRET-MARKER" not in response.text
    assert "SYNTHETIC-SECRET-MARKER" not in caplog.text
    assert dispatch_db.counts() == (0, 0)


def test_dispatch_resolves_both_actor_and_target_server_side(dispatch_db, monkeypatch):
    monkeypatch.setattr(
        policy, "_CROSS_DEPARTMENT_DISPATCH_ALLOWED_IDS", frozenset({"office-a", "office-c"})
    )
    db = dispatch_db
    assert_error(
        db.post(
            cross_department_body(
                targets=[
                    {
                        "kind": "user",
                        "directory_user_id": "recipient",
                        "department_id": "office-c",
                    }
                ]
            )
        ),
        404,
        "dispatch_target_not_found",
    )
    assert db.counts() == (0, 0)
    assert_created(db, db.post(cross_department_body()))
    row = db.rows("work_objects")[0]
    assert (row["owner_department_id"], row["initiator_ai_user_id"], row["tenant_id"]) == (
        "office-b",
        "ai-sender",
        "default",
    )


@pytest.mark.parametrize("case", ["missing-key", "missing", "ambiguous", "wrong-user"])
def test_dispatch_missing_or_ambiguous_actor_denies_and_alerts(
    dispatch_db, monkeypatch, caplog, case
):
    db = dispatch_db
    if case == "missing-key":
        db.tokens.principal = db.tokens.principal.model_copy(
            update={"org_ctx": PrincipalOrgContext(tenant_id=TENANT)}
        )
    elif case == "missing":
        db.execute("DELETE FROM organization_user_memberships WHERE user_id='sender'")
    elif case == "ambiguous":
        db.membership("sender", "office-c", "75")
    else:

        original = db.directory.read_view
        async def wrong():
            view = await original()
            return view.model_copy(
                update={
                    "memberships": tuple(
                        member.model_copy(update={"user_id": "wrong-user"})
                        if member.user_id == "sender"
                        else member
                        for member in view.memberships
                    )
                }
            )

        monkeypatch.setattr(db.directory, "read_view", wrong)
    code = (
        "directory_membership_ambiguous" if case == "ambiguous" else "directory_membership_missing"
    )
    assert_error(db.post(), 403, code)
    warnings = [
        r for r in caplog.records if r.name == "app.api.v1.work_objects" and r.getMessage() == code
    ]
    assert len(warnings) == 1
    assert warnings[0].levelno == logging.WARNING
    assert warnings[0].exc_info is None and warnings[0].stack_info is None
    assert db.counts() == (0, 0)
    trace = db.rows("trace_events")[0]
    assert trace["status"] == "blocked" and trace["error_code"] is None
    assert trace["attributes"]["reason_code"] == code


@pytest.mark.parametrize("job", [None, "999", "0"])
@pytest.mark.parametrize("roles", [("user",), ("admin",)])
@pytest.mark.parametrize("department", ["office-approved", "572", "office-a"])
def test_dispatch_non_head_including_admin_is_denied(
    dispatch_db, monkeypatch, job, roles, department
):
    monkeypatch.setattr(
        policy, "_CROSS_DEPARTMENT_DISPATCH_ALLOWED_IDS", frozenset({"office-approved"})
    )
    dispatch_db.execute(
        "INSERT INTO organization_departments (department_id, display_name, fetched_at) "
        "VALUES ('office-approved', 'Synthetic', clock_timestamp())"
    )
    db = dispatch_db
    db.actor("matrix-head", department, job)
    db.tokens.principal = db.tokens.principal.model_copy(update={"roles": roles})
    assert_error(db.post(), 403, "not_department_head")
    assert db.counts() == (0, 0)
    db.membership("matrix-head", department, "75")
    success = db.post(request_body(targets=[{"kind": "department", "department_id": department}]))
    assert success.status_code == 201
    assert db.counts() == (1, 1)


@pytest.mark.parametrize("job", ["75", "380", "1405", "1701", "1999"])
def test_prison_head_cross_department_is_atomic_denial(dispatch_db, job):
    db = dispatch_db
    db.actor("prison-head", "572", job)
    body = request_body(
        targets=[
            {"kind": "department", "department_id": "572"},
            {"kind": "department", "department_id": "575"},
        ]
    )
    assert_error(db.post(body), 403, "cross_department_dispatch_denied")
    assert db.counts() == (0, 0)


@pytest.mark.parametrize("job", ["75", "380", "1405", "1701", "1999"])
def test_office_head_creates_visible_objects_for_verified_targets(dispatch_db, monkeypatch, job):
    monkeypatch.setattr(
        policy, "_CROSS_DEPARTMENT_DISPATCH_ALLOWED_IDS", frozenset({"office-a", "office-c"})
    )
    db = dispatch_db
    db.actor("sender", "office-a", job)
    body = cross_department_body(
        targets=[
            *cross_department_body()["targets"],
            {"kind": "department", "department_id": "office-c"},
        ]
    )
    response = db.post(body)
    assert response.status_code == 201, response.text
    assert db.counts() == (2, 1)
    items = response.json()["items"]
    assert [item["status"] for item in items] == ["department_pending", "assigned"]
    assert [item["assignee_display_name"] for item in items] == ["Synthetic office-c", None]
    assert {item["work_object_id"] for item in items} == {
        row["work_object_id"] for row in db.rows("work_objects")
    }
    assert {row["tenant_id"] for row in db.rows("work_objects")} == {"default"}
    assert {row["initiator_ai_user_id"] for row in db.rows("work_objects")} == {"ai-sender"}
    assert {row["owner_department_id"] for row in db.rows("work_objects")} == {
        "office-b",
        "office-c",
    }


def test_unresolved_actor_department_keeps_same_department_user_dispatch(dispatch_db, monkeypatch):
    db = dispatch_db
    db.membership("local-recipient", "office-a")

    original = db.directory.read_view
    async def unresolved():
        view = await original()
        return view.model_copy(update={"departments": ()})

    monkeypatch.setattr(db.directory, "read_view", unresolved)
    assert_created(
        db,
        db.post(
            request_body(
                targets=[
                    {
                        "kind": "user",
                        "directory_user_id": "local-recipient",
                        "department_id": "office-a",
                    }
                ]
            )
        ),
    )
    assert_error(db.post(cross_department_body()), 403, "cross_department_dispatch_denied")
    assert_error(
        db.post(request_body(targets=[{"kind": "department", "department_id": "office-a"}])),
        404,
        "dispatch_target_not_found",
    )
    assert db.counts() == (1, 1)


def test_dispatch_rechecks_membership_and_jobtitle_each_request(dispatch_db):
    db = dispatch_db
    key = str(uuid4())
    assert_created(db, db.post(key=key))
    db.membership("sender", "office-a", None)
    assert_error(db.post(), 403, "not_department_head")
    assert_error(db.post(key=key), 403, "not_department_head")
    assert db.counts() == (1, 1)
    db.execute("DELETE FROM organization_user_memberships WHERE user_id='sender'")
    db.actor("sender", "572", "75")
    assert_error(db.post(), 403, "cross_department_dispatch_denied")
    assert_error(db.post(key=key), 403, "cross_department_dispatch_denied")
    assert db.counts() == (1, 1)


def test_due_at_and_reminder_contract_round_trip(dispatch_db):
    db = dispatch_db
    key = str(uuid4())
    body = request_body(
        due_at="2026-09-11T01:30:00+09:00", reminder_choices=["提前 1 天", "提前 7 天"]
    )
    first = assert_created(db, db.post(body, key=key))
    assert first["due_at"] == "2026-09-10T16:30:00.000000Z"
    assert first["reminder_choices"] == ["提前 7 天", "提前 1 天"]
    assert first["reminder_delivery"] == "not_enabled"
    assert db.rows("work_objects")[0]["due_at"] == datetime(2026, 9, 10, 16, 30, tzinfo=UTC)
    body["due_at"] = "2026-09-10T16:30:00Z"
    assert db.post(body, key=key).status_code == 200
    assert_error(
        db.post(request_body(due_at="2026-09-10T16:30:00")), 422, "dispatch_request_invalid"
    )
    assert_error(
        db.post(request_body(reminder_choices=["提前 1 天"])), 422, "dispatch_request_invalid"
    )


def test_idempotency_fingerprint_dedupe_and_owner_scope(dispatch_db):
    db = dispatch_db
    key = str(uuid4())
    first = assert_created(db, db.post(key=key))
    assert db.post(request_body(targets=request_body()["targets"] * 2), key=key).json()[
        "items"
    ] == [first]
    assert_error(db.post(request_body(title="changed"), key=key), 409, "idempotency_key_reused")
    db.actor("sender", "office-a", "75", tenant="tenant-dispatch-b")
    assert_error(db.post(key=key), 503, "organization_directory_unavailable")
    assert db.counts() == (1, 1)
    assert run(db.store.get_dispatch_receipt(
        tenant_id="tenant-dispatch-b", initiator_ai_user_id="ai-sender", idempotency_key=UUID(key),
    )) is None
    db.actor("another-sender", "office-a", "75")
    third = assert_created(db, db.post(key=key))
    assert third["work_object_id"] != first["work_object_id"]
    assert {
        (row["tenant_id"], row["initiator_ai_user_id"])
        for row in db.rows("work_object_dispatch_receipts")
    } == {
        ("default", "ai-sender"),
        ("default", "ai-another-sender"),
    }


@pytest.mark.parametrize("department,allowed,resolved,expected_type,rule", [
    ("office-a", True, True, "office", "cross_department_allowlist"),
    ("office-a", False, True, "unknown", "department_not_allowlisted"),
    ("572", False, True, "prison_area", "prison_area_id"),
    ("office-a", True, False, "unknown", "department_unresolved"),
])
def test_dispatch_authorization_trace_is_safe_and_precedes_write(
    dispatch_db, monkeypatch, department, allowed, resolved, expected_type, rule,
):
    db = dispatch_db
    monkeypatch.setattr(
        policy, "_CROSS_DEPARTMENT_DISPATCH_ALLOWED_IDS",
        frozenset({department}) if allowed else frozenset(),
    )
    db.execute(
        "DELETE FROM organization_user_memberships WHERE user_id IN ('sender', 'local-recipient')"
    )
    db.actor("sender", department, "75")
    db.membership("local-recipient", department)
    if not resolved:
        original_view = db.directory.read_view

        async def unresolved_view():
            view = await original_view()
            return view.model_copy(update={
                "departments": tuple(
                    item for item in view.departments if item.department_id != department
                ),
            })

        monkeypatch.setattr(db.directory, "read_view", unresolved_view)
    body = request_body(targets=[{
        "kind": "user", "directory_user_id": "local-recipient", "department_id": department,
    }])
    original = db.store.create_internal_dispatch

    async def require_persisted_trace(**kwargs):
        assert db.counts() == (0, 0)
        assert len(db.rows("trace_events")) == 1
        return await original(**kwargs)

    monkeypatch.setattr(db.store, "create_internal_dispatch", require_persisted_trace)
    assert_created(db, db.post(body))
    trace = db.rows("trace_events")[0]
    assert trace["tenant_id"] == "default" and trace["ai_user_id"] == "ai-sender"
    assert trace["event_type"] == "user_action" and trace["status"] == "ok"
    assert trace["error_code"] is None and trace["capability_id"] is None
    assert trace["attributes"] == {
        "operation": "dispatch",
        "phase": "authorization_decided",
        "department_id": department,
        "department_type": expected_type,
        "matched_rule": rule,
        "reason_code": None,
    }

    async def fail(_event):
        raise RuntimeError("SYNTHETIC-AUDIT-FAILURE")

    monkeypatch.setattr(db.trace, "record_event", fail)
    assert_error(db.post(body), 503, "work_object_audit_unavailable")
    assert db.counts() == (1, 1)


def test_dispatch_never_grants_visibility_of_target_department(dispatch_db, monkeypatch):
    monkeypatch.setattr(
        policy, "_CROSS_DEPARTMENT_DISPATCH_ALLOWED_IDS", frozenset({"office-a", "office-c"})
    )
    db = dispatch_db
    own = assert_created(db, db.post(cross_department_body()))
    db.actor("another-sender", "office-c", "75")
    unrelated = assert_created(db, db.post(cross_department_body()))
    db.actor("sender", "office-a", "75")
    for suffix in ("", "?q=Synthetic"):
        response = db.client.get("/api/v1/work-objects" + suffix)
        assert response.status_code == 200
        assert {item["work_object_id"] for item in response.json()["items"]} == {
            own["work_object_id"]
        }
    denied = db.client.get("/api/v1/work-objects/" + unrelated["work_object_id"])
    missing = db.client.get("/api/v1/work-objects/" + uuid4().hex)
    assert_error(denied, 404, "work_object_not_found")
    assert denied.json() == missing.json()
    assert db.client.get("/api/v1/work-objects/" + own["work_object_id"]).status_code == 200


@pytest.mark.parametrize(
    "reason",
    [
        "directory_membership_missing",
        "directory_membership_ambiguous",
        "not_department_head",
        "cross_department_dispatch_denied",
        "dispatch_target_membership_ambiguous",
    ],
)
def test_dispatch_deny_trace_and_replay_failure_policy(dispatch_db, monkeypatch, caplog, reason):
    db = dispatch_db
    key = str(uuid4())
    assert_created(db, db.post(key=key))
    if reason == "directory_membership_missing":
        db.execute("DELETE FROM organization_user_memberships WHERE user_id='sender'")
    elif reason == "directory_membership_ambiguous":
        db.membership("sender", "office-c", "75")
    elif reason == "not_department_head":
        db.membership("sender", "office-a", None)
    elif reason == "cross_department_dispatch_denied":
        db.actor("prison-head", "572", "75")
    else:
        db.membership("local-recipient", "office-c")
    assert_error(db.post(), 403, reason)
    traces = [row for row in db.rows("trace_events") if row["status"] == "blocked"]
    assert len(traces) == 1
    trace = traces[0]
    assert trace["error_code"] is None
    assert trace["tenant_id"] == "default"
    unresolved = reason in {"directory_membership_missing", "directory_membership_ambiguous"}
    prison = reason == "cross_department_dispatch_denied"
    assert trace["attributes"] == {
        "operation": "dispatch",
        "phase": "authorization_decided",
        "department_id": None if unresolved else "572" if prison else "office-a",
        "department_type": "prison_area" if prison else "unknown",
        "matched_rule": "department_unresolved"
        if unresolved
        else "prison_area_id"
        if prison
        else "department_not_allowlisted",
        "reason_code": reason,
    }

    async def fail(_event):
        raise RuntimeError("synthetic audit unavailable")

    monkeypatch.setattr(db.trace, "record_event", fail)
    assert_error(db.post(), 503, "work_object_audit_unavailable")
    assert db.counts() == (1, 1)
    # Restore one current sender membership, then revoke the role on the committed key.
    db.execute("DELETE FROM organization_user_memberships WHERE user_id='sender'")
    db.actor("sender", "office-a", None)
    caplog.clear()
    assert_error(db.post(key=key), 403, "not_department_head")
    assert db.counts() == (1, 1)
    assert [
        record.getMessage() for record in caplog.records if record.name == "app.api.v1.work_objects"
    ] == ["work_object_audit_unavailable"]


@pytest.mark.parametrize(
    "field",
    [
        "title",
        "requirement",
        "receipt_requirement",
        "owner_department_id",
        "initiator_ai_user_id",
        "kind",
        "target_kind",
        "status",
        "reminder_choices",
        "reminder_delivery",
        "version",
        "created_at",
        "updated_at",
    ],
)
def test_manual_projection_and_post_invariants_precede_commit(dispatch_db, monkeypatch, field):
    db = dispatch_db
    calls = []

    async def registry_canary(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("synthetic registry unavailable")

    monkeypatch.setattr(db.service._capability_registry, "list", registry_canary)
    key = str(uuid4())
    first = assert_created(db, db.post(key=key))
    replay = db.post(key=key)
    assert replay.status_code == 200 and replay.json()["items"] == [first]
    assert db.client.get("/api/v1/work-objects/" + first["work_object_id"]).json() == {
        **first, "accepted_at": None, "completed_at": None,
    }
    assert db.client.get("/api/v1/work-objects").json()["items"] == [
        {**first, "accepted_at": None, "completed_at": None},
    ]
    assert first["handling_action"] == "view_only" and first["handling_capability_id"] is None
    assert calls == []
    original = api._view_from_record

    def incomplete(record, capabilities):
        return original(record, capabilities).model_copy(update={field: None})

    monkeypatch.setattr(api, "_view_from_record", incomplete)
    assert_error(db.post(), 503, "work_object_dispatch_failed")
    assert db.counts() == (1, 1)
    assert calls == []


def test_committed_dispatch_survives_lost_response_without_second_creation(
    dispatch_db, monkeypatch, caplog
):
    db = dispatch_db
    key = str(uuid4())
    original = db.store.create_internal_dispatch
    saved = []

    async def lose(**kwargs):
        result = await original(**kwargs)
        saved.append(result[0].result)
        raise ConnectionError("synthetic lost response after commit")

    monkeypatch.setattr(db.store, "create_internal_dispatch", lose)
    assert_error(db.post(key=key), 503, "work_object_dispatch_failed")
    assert db.counts() == (1, 1)
    events = []
    lookup = db.store.get_dispatch_receipt

    async def read(**kwargs):
        events.append("receipt")
        return await lookup(**kwargs)

    async def fail(_event):
        events.append("trace")
        raise RuntimeError("synthetic trace failure")

    monkeypatch.setattr(db.store, "get_dispatch_receipt", read)
    monkeypatch.setattr(db.trace, "record_event", fail)
    replay = db.post(key=key)
    assert replay.status_code == 200, replay.text
    assert replay.json() == {**saved[0], "replayed": True}
    assert events == ["receipt", "trace"]
    assert db.counts() == (1, 1)
    assert [r.getMessage() for r in caplog.records if r.name == "app.api.v1.work_objects"] == [
        "work_object_audit_unavailable"
    ]


def test_dispatch_rejects_multi_membership_recipient_atomically(dispatch_db, monkeypatch, caplog):
    monkeypatch.setattr(
        policy, "_CROSS_DEPARTMENT_DISPATCH_ALLOWED_IDS", frozenset({"office-a", "office-c"})
    )
    db = dispatch_db
    item = assert_created(db, db.post(cross_department_body()))
    db.actor("recipient", "office-b", None)
    assert db.client.get("/api/v1/work-objects/" + item["work_object_id"]).status_code == 200
    assert [
        x["work_object_id"]
        for x in db.client.get("/api/v1/work-objects?q=Synthetic").json()["items"]
    ] == [item["work_object_id"]]
    db.actor("sender", "office-a", "75")
    db.membership("recipient", "office-c")
    for department in ("office-b", "office-c"):
        targets = [
            {"kind": "user", "directory_user_id": "recipient", "department_id": department},
            {"kind": "department", "department_id": "office-b"},
        ]
        for ordered in (targets, list(reversed(targets))):
            caplog.clear()
            assert_error(
                db.post(cross_department_body(targets=ordered)),
                403,
                "dispatch_target_membership_ambiguous",
            )
            assert db.counts() == (1, 1)
            assert [
                r.getMessage() for r in caplog.records if r.name == "app.api.v1.work_objects"
            ] == ["dispatch_target_membership_ambiguous"]


def test_recipient_visibility_tracks_later_membership_changes(dispatch_db, monkeypatch):
    monkeypatch.setattr(
        policy, "_CROSS_DEPARTMENT_DISPATCH_ALLOWED_IDS", frozenset({"office-a", "office-c"})
    )
    db = dispatch_db
    item = assert_created(db, db.post(cross_department_body()))
    path = "/api/v1/work-objects/" + item["work_object_id"]
    db.actor("recipient", "office-b", None)
    assert db.client.get(path).status_code == 200
    db.membership("recipient", "office-c")
    assert_error(db.client.get(path), 404, "work_object_not_found")
    assert db.client.get("/api/v1/work-objects?q=Synthetic").json()["items"] == []
    db.actor("sender", "office-a", "75")
    assert db.client.get(path).status_code == 200
    db.execute(
        "DELETE FROM organization_user_memberships WHERE "
        "user_id='recipient' AND department_id='office-c'"
    )
    db.actor("recipient", "office-b", None)
    assert db.client.get(path).status_code == 200


def test_dispatch_canonical_order_and_mixed_errors_are_deterministic(dispatch_db, monkeypatch):
    monkeypatch.setattr(
        policy, "_CROSS_DEPARTMENT_DISPATCH_ALLOWED_IDS", frozenset({"office-a", "office-c"})
    )
    db = dispatch_db
    body = cross_department_body(
        title="\ufeff 中文标题\u3000",
        due_at="2026-09-10T21:00:00+09:00",
        targets=[
            *cross_department_body()["targets"],
            {"kind": "department", "department_id": "office-c"},
        ],
    )
    canonical = (
        '{"due_at":"2026-09-10T12:00:00.000000Z","kind":"工作任务",'
        '"receipt_requirement":"","reminder_choices":[],"requirement":"",'
        '"targets":[{"department_id":"office-c","kind":"department"},'
        '{"department_id":"office-b","directory_user_id":"recipient","kind":"user"}],'
        '"title":"中文标题"}'
    ).encode("utf-8")
    assert api.DispatchWorkObjectsRequest.model_validate(body).canonical_bytes() == canonical
    key = str(uuid4())
    response = db.post(body, key=key)
    assert response.status_code == 201
    assert [x["target_kind"] for x in response.json()["items"]] == ["department", "user"]
    assert all(re.fullmatch("[0-9a-f]{32}", x["work_object_id"]) for x in response.json()["items"])
    assert (
        db.rows("work_object_dispatch_receipts")[0]["request_fingerprint"]
        == hashlib.sha256(canonical).hexdigest()
    )
    equivalent = {
        **body,
        "title": "中文标题",
        "due_at": "2026-09-10T12:00:00Z",
        "targets": list(reversed(body["targets"])),
    }
    replay = db.post(equivalent, key=key)
    assert replay.status_code == 200
    assert replay.json() == {**response.json(), "replayed": True}
    assert_error(
        db.post({**equivalent, "title": "中文 标题"}, key=key), 409, "idempotency_key_reused"
    )
    assert db.counts() == (2, 1)
    db.actor("prison-head", "572", "75")
    targets = [
        {"kind": "department", "department_id": "missing"},
        {"kind": "department", "department_id": "575"},
    ]
    for order in (targets, list(reversed(targets))):
        assert_error(
            db.post(cross_department_body(targets=order)), 403, "cross_department_dispatch_denied"
        )
    assert_error(
        db.post(cross_department_body(targets=targets[1:])), 403, "cross_department_dispatch_denied"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("decision", "unknown"),
        ("reason_code", "unknown"),
        ("matched_rule", "unknown"),
        ("matched_rule", "resolved_non_prison_id"),
        ("dispatcher_department_type", "invalid"),
    ],
)
def test_invalid_dispatch_decision_never_reaches_write(dispatch_db, monkeypatch, field, value):
    invalid = DispatchAuthorizationDecision.model_construct(
        decision="allow",
        reason_code=None,
        department_id="office-a",
        dispatcher_department_type="office",
        matched_rule="department_not_allowlisted",
    ).model_copy(update={field: value})
    monkeypatch.setattr(api, "compute_dispatch_authorization", lambda **_kwargs: invalid)
    assert_error(dispatch_db.post(), 503, "work_object_dispatch_failed")
    assert dispatch_db.counts() == (0, 0)
    assert dispatch_db.rows("trace_events") == []


def test_openapi_and_read_view_expose_approved_contract(dispatch_db):
    db = dispatch_db
    response = db.post()
    item = assert_created(db, response)
    assert set(item) == INTERNAL_KEYS and len(INTERNAL_KEYS) == 31
    assert "etag" not in response.headers
    detail = db.client.get("/api/v1/work-objects/" + item["work_object_id"])
    assert detail.json() == {**item, "accepted_at": None, "completed_at": None}
    assert detail.headers["etag"] == '"wo:' + item["work_object_id"] + ':1"'
    assert item["created_at"] == item["updated_at"] == "2026-09-10T12:00:00.000000Z"
    schema = db.client.app.openapi()
    assert set(schema["paths"]["/api/v1/work-objects/dispatch"]["post"]["responses"]) == {
        "200",
        "201",
        "401",
        "403",
        "404",
        "409",
        "422",
        "503",
    }
    view = schema["components"]["schemas"]["InternalWorkObjectView"]
    assert set(view["properties"]) == INTERNAL_KEYS | {"accepted_at", "completed_at"}
    assert set(
        schema["components"]["schemas"]["DispatchInitialWorkObjectView"]["properties"]
    ) == INTERNAL_KEYS
    assert view["properties"]["assignee_display_name"]["anyOf"] == [
        {"type": "string"},
        {"type": "null"},
    ]
    assert (
        schema["components"]["schemas"]["DispatchWorkObjectsRequest"]["additionalProperties"]
        is False
    )


@pytest.mark.parametrize("kind", ["user", "department"])
def test_unlisted_actor_cross_department_batch_is_atomic_denial(dispatch_db, kind):
    db = dispatch_db
    target = {"kind": kind, "department_id": "office-b"}
    if kind == "user":
        target["directory_user_id"] = "recipient"
    local = {"kind": kind, "department_id": "office-a"}
    if kind == "user":
        local["directory_user_id"] = "local-recipient"
    body = request_body(targets=[local, target])
    assert (
        api.DispatchWorkObjectsRequest.model_validate(body).targets[0].department_id == "office-a"
    )
    for batch in ([target], [local, target], [target, local]):
        response = db.post(request_body(targets=batch))
        assert_error(response, 403, "cross_department_dispatch_denied")
        assert response.json() == {
            "detail": {
                "code": "cross_department_dispatch_denied",
                "message": "Work Object operation is not permitted.",
            }
        }
        assert db.counts() == (0, 0)
    success = db.post(request_body(targets=[local]))
    assert success.status_code == 201
    assert success.json()["items"][0]["owner_department_id"] == "office-a"
    assert db.counts() == (1, 1)


def test_allowlisted_actor_success_and_policy_revoked_replay(dispatch_db, monkeypatch):
    db = dispatch_db
    monkeypatch.setattr(policy, "_CROSS_DEPARTMENT_DISPATCH_ALLOWED_IDS", frozenset({"office-a"}))
    cross_key, local_key = str(uuid4()), str(uuid4())
    cross = assert_created(db, db.post(cross_department_body(), key=cross_key))
    local = assert_created(db, db.post(key=local_key))
    # Synthetic historical summaries remain opaque evidence, never authorization input.
    db.execute("UPDATE work_object_dispatch_receipts SET authorization_summary="
               "jsonb_set(jsonb_set(authorization_summary, '{department_type}', '\"office\"'), "
               "'{matched_rule}', '\"resolved_non_prison_id\"')")
    old_objects = db.rows("work_objects")
    old_receipts = db.rows("work_object_dispatch_receipts")
    monkeypatch.setattr(policy, "_CROSS_DEPARTMENT_DISPATCH_ALLOWED_IDS", frozenset())
    assert_error(
        db.post(cross_department_body(), key=cross_key), 403, "cross_department_dispatch_denied"
    )
    replay = db.post(key=local_key)
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    assert replay.json()["items"] == [local]
    assert cross["owner_department_id"] == "office-b"
    assert db.counts() == (2, 2)
    assert db.rows("work_objects") == old_objects
    assert db.rows("work_object_dispatch_receipts") == old_receipts


@pytest.mark.parametrize("case,status,code", [
    ("missing", 503, "organization_directory_missing"),
    ("stale", 503, "organization_directory_stale"),
    ("bad-metadata", 503, "organization_directory_unavailable"),
    ("membership-missing", 403, "directory_membership_missing"),
    ("ambiguous", 403, "directory_membership_ambiguous"),
    ("other-tenant", 503, "organization_directory_unavailable"),
])
def test_freshness_and_identity_guards_precede_allowlist(
    dispatch_db, monkeypatch, case, status, code
):
    db = dispatch_db
    monkeypatch.setattr(policy, "_CROSS_DEPARTMENT_DISPATCH_ALLOWED_IDS", frozenset({"office-a"}))
    key = str(uuid4())
    body = cross_department_body()
    assert_created(db, db.post(body, key=key))
    if case == "missing":
        db.execute(
            "UPDATE organization_directory_sync_state SET snapshot_version=0, "
            "source_fetched_at=NULL, last_success_at=NULL, last_attempt_started_at=NULL, "
            "last_attempt_finished_at=NULL, last_attempt_status='never', last_error_code=NULL"
        )
    elif case == "bad-metadata":
        original = db.directory.read_view

        async def invalid():
            view = await original()
            return view.model_copy(update={"last_success_at": None})

        monkeypatch.setattr(db.directory, "read_view", invalid)
    elif case == "stale":
        expire_directory(db)
    elif case == "membership-missing":
        db.execute("DELETE FROM organization_user_memberships WHERE user_id='sender'")
    elif case == "ambiguous":
        db.membership("sender", "office-c", "75")
    else:
        db.actor("sender", "office-a", "75", tenant="synthetic-other")

        async def forbidden():
            pytest.fail("nondefault identity read the shared directory")

        monkeypatch.setattr(db.directory, "read_view", forbidden)
    for current in (key, str(uuid4())):
        assert_error(db.post(body, key=current), status, code)
        assert db.counts() == (1, 1)
    get = db.client.get("/api/v1/work-objects/dispatch-options?kind=user&department_id=office-b")
    assert_error(
        get, 403 if case == "other-tenant" else status,
        "directory_scope_denied" if case == "other-tenant" else code,
    )
