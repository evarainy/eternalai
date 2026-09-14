from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta, timezone
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy import event, text

from app.api.v1.work_objects import WorkObjectService
from app.db.session import make_async_engine, make_async_session_factory
from app.infra.persistence.work_object.postgresql import PostgreSQLWorkObjectStore
from app.ports.auth import Principal, PrincipalOrgContext
from app.ports.capability_gateway import CapabilityGatewayPort
from app.ports.work_object import OAPendingWorkSnapshot
from app.ports.work_object_scope import AuthorizedWorkObjectScope
from app.ports.work_object_search import SEARCH_WHITESPACE
from tests.api.test_work_object_dispatch import dispatch_db as dispatch_db
from tests.runtime.registry_fakes import StaticCapabilityRegistry

pytestmark = pytest.mark.usefixtures("migrated_database_url")

DATABASE_URL = os.environ.get("DATABASE_URL")

if hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # type: ignore[attr-defined]


def _require_db() -> str:
    if not DATABASE_URL:
        raise AssertionError("DATABASE_URL must be set by the test runner environment")
    return DATABASE_URL


def _snapshot(
    *,
    title: str,
    status: str,
    source_ref: str = "shared-oa-todo",
) -> OAPendingWorkSnapshot:
    return OAPendingWorkSnapshot(
        source_ref=source_ref,
        title=title,
        status=status,
        received_at="2026-08-18",
        created_at="2026-08-17",
        workflow_type_id="workflow-1",
    )


_STABLE_LIST_ORDER_BY = (
    "ORDER BY due_at ASC NULLS LAST, created_at DESC NULLS LAST, "
    'work_object_id COLLATE "C" ASC'
)


def _collapsed_sql(statement: str) -> str:
    return " ".join(statement.split())


def _assert_list_sql_orders_before_limit(statement: str) -> None:
    collapsed = _collapsed_sql(statement)
    assert _STABLE_LIST_ORDER_BY in collapsed
    upper = collapsed.upper()
    assert upper.index("WHERE") < collapsed.index(_STABLE_LIST_ORDER_BY)
    assert collapsed.index(_STABLE_LIST_ORDER_BY) < upper.index("LIMIT")


def test_postgresql_store_is_idempotent_user_isolated_and_preserves_marks() -> None:
    database_url = _require_db()
    user_a = f"tenant-a-work-object-user-{uuid4().hex}"
    user_b = f"tenant-b-work-object-user-{uuid4().hex}"
    first_fetch = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
    second_fetch = first_fetch + timedelta(minutes=5)

    async def exercise() -> None:
        engine = make_async_engine(database_url)
        statements: list[str] = []

        def record_statement(
            _connection: object,
            _cursor: object,
            statement: str,
            _parameters: object,
            _context: object,
            _executemany: bool,
        ) -> None:
            statements.append(statement)

        event.listen(engine.sync_engine, "before_cursor_execute", record_statement)
        factory = make_async_session_factory(engine)
        store = PostgreSQLWorkObjectStore(factory)
        try:
            await store.upsert_oa_pending_workflows(
                assignee_ai_user_id=user_a,
                assignee_display_name="User A",
                snapshots=[_snapshot(title="Original", status="OA_PENDING")],
                fetched_at=first_fetch,
            )
            await store.upsert_oa_pending_workflows(
                assignee_ai_user_id=user_b,
                assignee_display_name="User B",
                snapshots=[
                    _snapshot(
                        source_ref="tenant-b-only-todo",
                        title="Other tenant user title",
                        status="OA_PENDING",
                    )
                ],
                fetched_at=first_fetch,
            )
            user_a_records = await store.list_for_scope(_scope(user_a))
            user_b_records = await store.list_for_scope(_scope(user_b))
            assert len(user_a_records) == len(user_b_records) == 1
            assert user_a_records[0].work_object_id != user_b_records[0].work_object_id

            service = WorkObjectService(
                store=store,
                gateway=cast(CapabilityGatewayPort, object()),
                capability_registry=StaticCapabilityRegistry(),
            )
            tenant_a_principal = Principal(
                ai_user_id=user_a,
                display_name="User A",
                roles=(),
                org_ctx=PrincipalOrgContext(tenant_id="tenant-a"),
            )
            tenant_b_principal = Principal(
                ai_user_id=user_b,
                display_name="User B",
                roles=(),
                org_ctx=PrincipalOrgContext(tenant_id="tenant-b"),
            )
            for tenant_b_query in (
                "other tenant",
                " TENANT-B-ONLY-TODO ",
                " user b ",
            ):
                tenant_a_candidates = (
                    await service.list_for_principal(
                        tenant_a_principal,
                        search_term=tenant_b_query,
                    )
                ).items
                tenant_b_candidates = (
                    await service.list_for_principal(
                        tenant_b_principal,
                        search_term=tenant_b_query,
                    )
                ).items
                assert tenant_a_candidates == []
                assert len(tenant_b_candidates) == 1

            marked = await store.set_handling_mark_for_scope(
                user_a_records[0].work_object_id,
                _scope(user_a),
                "handled_elsewhere",
                marked_at=first_fetch + timedelta(minutes=1),
            )
            assert marked is not None
            assert marked.source_status == "OA_PENDING"

            await store.upsert_oa_pending_workflows(
                assignee_ai_user_id=user_a,
                assignee_display_name="User A renamed",
                snapshots=[_snapshot(title="Refreshed", status="OA_STILL_PENDING")],
                fetched_at=second_fetch,
            )
            refreshed_records = await store.list_for_scope(_scope(user_a))
            assert len(refreshed_records) == 1
            refreshed = refreshed_records[0]
            assert refreshed.work_object_id == user_a_records[0].work_object_id
            assert refreshed.state_authority == "external_snapshot"
            assert refreshed.source_title == "Refreshed"
            assert refreshed.source_status == "OA_STILL_PENDING"
            assert refreshed.source_fetched_at == second_fetch
            assert refreshed.handling_mark == "handled_elsewhere"
            assert refreshed.handling_marked_at == first_fetch + timedelta(minutes=1)

            assert await store.get_for_scope(refreshed.work_object_id, _scope(user_b)) is None
            assert (
                await store.set_handling_mark_for_scope(
                    refreshed.work_object_id,
                    _scope(user_b),
                    "pending_sync_confirmation",
                    marked_at=second_fetch,
                )
                is None
            )
            list_statements = [
                statement
                for statement in statements
                if statement.lstrip().upper().startswith("SELECT")
                and "FROM work_objects" in statement
                and "LIMIT" in statement.upper()
            ]
            assert list_statements
            for statement in list_statements:
                _assert_list_sql_orders_before_limit(statement)
            search_statements = [
                statement for statement in list_statements if "STRPOS" in statement.upper()
            ]
            assert search_statements
            assert all("assignee_ai_user_id" in statement for statement in search_statements)
            assert all(
                "tenant-b-only-todo" not in statement.lower() for statement in search_statements
            )
        finally:
            async with factory() as session:
                await session.execute(
                    text(
                        "DELETE FROM work_objects WHERE assignee_ai_user_id IN (:user_a, :user_b)"
                    ),
                    {"user_a": user_a, "user_b": user_b},
                )
                await session.commit()
            await engine.dispose()

    asyncio.run(exercise())


def test_postgresql_search_matches_approved_fields_and_literal_wildcards() -> None:
    database_url = _require_db()
    user_a = f"tenant-a-search-user-{uuid4().hex}"
    user_b = f"tenant-b-search-user-{uuid4().hex}"
    fetched_at = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)

    async def exercise() -> None:
        engine = make_async_engine(database_url)
        factory = make_async_session_factory(engine)
        store = PostgreSQLWorkObjectStore(factory)
        try:
            await store.upsert_oa_pending_workflows(
                assignee_ai_user_id=user_a,
                assignee_display_name="Li Ming",
                snapshots=[
                    _snapshot(
                        source_ref="TITLE-001",
                        title="Quarterly Budget Review",
                        status="OA_PENDING",
                    ),
                    _snapshot(
                        source_ref=" OA-REF-002 ",
                        title="合同归档",
                        status="OA_PENDING",
                    ),
                    _snapshot(
                        source_ref="WILD-003",
                        title="Literal 100%_ready",
                        status="OA_PENDING",
                    ),
                    _snapshot(
                        source_ref="PLAIN-004",
                        title="Literal 100XYready",
                        status="OA_PENDING",
                    ),
                ],
                fetched_at=fetched_at,
            )
            await store.upsert_oa_pending_workflows(
                assignee_ai_user_id=user_b,
                assignee_display_name="User B",
                snapshots=[
                    _snapshot(
                        source_ref="TENANT-B-ONLY",
                        title="Other tenant only",
                        status="OA_PENDING",
                    )
                ],
                fetched_at=fetched_at,
            )
            service = WorkObjectService(
                store=store,
                gateway=cast(CapabilityGatewayPort, object()),
                capability_registry=StaticCapabilityRegistry(),
            )
            tenant_a_principal = Principal(
                ai_user_id=user_a,
                display_name="Li Ming",
                roles=(),
                org_ctx=PrincipalOrgContext(tenant_id="tenant-a"),
            )
            tenant_b_principal = Principal(
                ai_user_id=user_b,
                display_name="User B",
                roles=(),
                org_ctx=PrincipalOrgContext(tenant_id="tenant-b"),
            )

            async def refs(principal: Principal, term: str) -> set[str | None]:
                response = await service.list_for_principal(
                    principal,
                    search_term=term,
                )
                return {item.source_ref for item in response.items}

            assert await refs(tenant_a_principal, "bUdGeT") == {"TITLE-001"}
            assert await refs(tenant_a_principal, " oa-ref-002 ") == {" OA-REF-002 "}
            assert await refs(tenant_a_principal, " li ming ") == {
                "TITLE-001",
                " OA-REF-002 ",
                "WILD-003",
                "PLAIN-004",
            }
            assert await refs(tenant_a_principal, "%_") == {"WILD-003"}
            assert await refs(tenant_a_principal, "oa-ref") == set()
            assert await refs(tenant_a_principal, "ming") == set()
            assert await refs(tenant_a_principal, "tenant-b-only") == set()
            assert await refs(tenant_b_principal, "tenant-b-only") == {"TENANT-B-ONLY"}
        finally:
            async with factory() as session:
                await session.execute(
                    text(
                        "DELETE FROM work_objects WHERE assignee_ai_user_id IN (:user_a, :user_b)"
                    ),
                    {"user_a": user_a, "user_b": user_b},
                )
                await session.commit()
            await engine.dispose()

    asyncio.run(exercise())


@asynccontextmanager
async def _search_store() -> AsyncIterator[
    tuple[PostgreSQLWorkObjectStore, WorkObjectService, Principal, str, list[str]]
]:
    engine = make_async_engine(_require_db())
    factory = make_async_session_factory(engine)
    store = PostgreSQLWorkObjectStore(factory)
    user_a, user_b = f"normalize-a-{uuid4().hex}", f"normalize-b-{uuid4().hex}"
    statements: list[str] = []

    def capture(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().upper().startswith("SELECT") and "work_objects" in statement:
            statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    principal = Principal(
        ai_user_id=user_a,
        display_name="Synthetic owner",
        roles=(),
        org_ctx=PrincipalOrgContext(tenant_id="synthetic-a"),
    )
    service = WorkObjectService(
        store=store,
        gateway=cast(CapabilityGatewayPort, object()),
        capability_registry=StaticCapabilityRegistry(),
    )
    try:
        yield store, service, principal, user_b, statements
    finally:
        async with factory() as session:
            await session.execute(
                text("DELETE FROM work_objects WHERE assignee_ai_user_id IN (:a, :b)"),
                {"a": user_a, "b": user_b},
            )
            await session.commit()
        await engine.dispose()


async def _seed_search(
    store: PostgreSQLWorkObjectStore,
    user: str,
    snapshots: list[OAPendingWorkSnapshot],
    name: str = "Synthetic owner",
) -> None:
    await store.upsert_oa_pending_workflows(
        assignee_ai_user_id=user,
        assignee_display_name=name,
        snapshots=snapshots,
        fetched_at=datetime(2026, 9, 9, tzinfo=UTC),
    )


def test_postgresql_search_matches_normalized_fields_without_cross_user_leak() -> None:
    async def exercise() -> None:
        async with _search_store() as (store, service, principal, other, statements):
            expected = set()
            for index, ws in enumerate(SEARCH_WHITESPACE):
                reference = f"{ws}OA{ws}{ws}REF-{index}{ws}"
                expected.add(reference)
                await _seed_search(
                    store,
                    principal.ai_user_id,
                    [
                        _snapshot(
                            source_ref=reference,
                            title=f"Quarterly{ws}{ws}Budget Review",
                            status="OA_PENDING",
                        )
                    ],
                    name=f"{ws}Li{ws}{ws}Ming{ws}",
                )
            await _seed_search(
                store,
                other,
                [
                    _snapshot(
                        source_ref="OTHER", title="Quarterly Budget Review", status="OA_PENDING"
                    )
                ],
                name="Li Ming",
            )
            for query in ("\u3000BUDGET\u00a0  REVIEW\u0085", "LI\t MING"):
                response = await service.list_for_principal(principal, search_term=query)
                assert {item.source_ref for item in response.items} == expected
                assert response.limit_exceeded is False
            for index, ws in enumerate(SEARCH_WHITESPACE):
                rows = await store.list_for_scope(
                    _scope(principal.ai_user_id), search_term=f"{ws}OA{ws}REF-{index}{ws}"
                )
                assert [row.source_ref for row in rows] == [f"{ws}OA{ws}{ws}REF-{index}{ws}"]
                assert all(row.assignee_ai_user_id == principal.ai_user_id for row in rows)
            for query in ("oa ref", "ming", "OTHER"):
                assert (
                    await store.list_for_scope(_scope(principal.ai_user_id), search_term=query)
                    == []
                )
            for query in (None, "", "\u0085\u00a0\ufeff"):
                rows = await store.list_for_scope(_scope(principal.ai_user_id), search_term=query)
                assert {row.source_ref for row in rows} == expected
            assert statements
            assert all(
                "assignee_ai_user_id =" in sql and "tenant_id =" in sql for sql in statements
            )
            assert all("regexp_replace" in sql for sql in statements if "STRPOS" in sql)

    asyncio.run(exercise())


def test_postgresql_search_applies_match_before_limit() -> None:
    async def exercise() -> None:
        async with _search_store() as (store, service, principal, other, statements):
            await _seed_search(
                store,
                principal.ai_user_id,
                [
                    _snapshot(source_ref=f"MISS-{i}", title="Unrelated", status="OA_PENDING")
                    for i in range(201)
                ],
            )
            await _seed_search(
                store,
                other,
                [_snapshot(source_ref="OTHER-HIT", title="Late Match", status="OA_PENDING")],
            )
            await _seed_search(
                store,
                principal.ai_user_id,
                [_snapshot(source_ref="LATE-HIT", title="Late\u0085 Match", status="OA_PENDING")],
            )
            response = await service.list_for_principal(principal, search_term="LATE MATCH")
            assert [item.source_ref for item in response.items] == ["LATE-HIT"]
            assert response.limit_exceeded is False
            sql = statements[-1]
            assert "assignee_ai_user_id =" in sql and "tenant_id =" in sql
            _assert_list_sql_orders_before_limit(sql)
            upper = sql.upper()
            assert upper.index("STRPOS") < upper.index("ORDER BY") < upper.index("LIMIT")

    asyncio.run(exercise())


def test_postgresql_search_keeps_literal_wildcards_and_overflow_contract() -> None:
    async def exercise() -> None:
        async with _search_store() as (store, service, principal, other, _statements):
            await _seed_search(
                store,
                principal.ai_user_id,
                [
                    _snapshot(
                        source_ref=f"MISS-{i}", title="Literal 100XYready", status="OA_PENDING"
                    )
                    for i in range(201)
                ],
            )
            await _seed_search(
                store,
                other,
                [
                    _snapshot(
                        source_ref="OTHER-WILD", title="Literal 100%_ready", status="OA_PENDING"
                    )
                ],
            )
            await _seed_search(
                store,
                principal.ai_user_id,
                [
                    _snapshot(
                        source_ref=f"HIT-{i}", title="Literal 100%_ready", status="OA_PENDING"
                    )
                    for i in range(200)
                ],
            )
            response = await service.list_for_principal(principal, search_term="%_")
            assert {item.source_ref for item in response.items} == {f"HIT-{i}" for i in range(200)}
            assert response.limit == 200
            assert response.limit_exceeded is False
            await _seed_search(
                store,
                principal.ai_user_id,
                [_snapshot(source_ref="HIT-200", title="Literal 100%_ready", status="OA_PENDING")],
            )
            response = await service.list_for_principal(principal, search_term="%_")
            assert len(response.items) == 200
            assert response.limit_exceeded is True
            assert all(item.source_ref.startswith("HIT-") for item in response.items)

    asyncio.run(exercise())


def _scope(actor: str) -> AuthorizedWorkObjectScope:
    return AuthorizedWorkObjectScope(
        principal_tenant_id="default",
        principal_ai_user_id=actor,
        principal_department_id=None,
    )


def test_no_job_keeps_only_existing_visibility(dispatch_db) -> None:
    from tests.api.test_work_object_dispatch import (
        assert_created,
        insert_synthetic_row,
        manual_row,
    )
    from tests.api.test_work_objects import _record
    from tests.auth_fakes import TEST_CSRF_HEADERS

    db = dispatch_db
    own_outgoing = assert_created(db, db.post())["work_object_id"]
    for item_id, department, tenant in (
        ("local", "office-a", "default"),
        ("other-department", "office-c", "default"),
        ("other-tenant", "office-a", "synthetic-other"),
    ):
        insert_synthetic_row(db, manual_row(
            db, work_object_id=item_id, owner_department_id=department,
            initiator_ai_user_id="ai-other", tenant_id=tenant,
        ))
    for user in ("ai-sender", "ai-other"):
        oa = _record(owner=user).model_dump()
        insert_synthetic_row(db, oa)
        legacy = {
            **oa, "work_object_id": "legacy-" + user, "state_authority": "internal",
            "source_system": "eternalai", "source_kind": "internal_task",
        }
        for field in ("source_ref", "source_title", "source_status", "source_received_at",
                      "source_created_at", "source_workflow_type_id", "source_fetched_at"):
            legacy[field] = None
        insert_synthetic_row(db, legacy)
    db.actor("sender", "office-a", None)
    expected = {own_outgoing, "local", "work-ai-sender-1", "legacy-ai-sender"}
    response = db.client.get("/api/v1/work-objects")
    assert response.status_code == 200
    assert {item["work_object_id"] for item in response.json()["items"]} == expected
    for row in db.rows("work_objects"):
        item_id = row["work_object_id"]
        assert db.client.get("/api/v1/work-objects/" + item_id).status_code == (
            200 if item_id in expected else 404
        )
        marked = db.client.patch(
            "/api/v1/work-objects/" + item_id + "/handling-mark",
            json={"mark": "handled_elsewhere"}, headers=TEST_CSRF_HEADERS,
        )
        assert marked.status_code == (200 if item_id == "work-ai-sender-1" else 404)
    assert {
        row["work_object_id"] for row in db.rows("work_objects")
        if row["handling_mark"] is not None
    } == {"work-ai-sender-1"}


def test_internal_scope_consumes_department_and_initiator_before_limit(
    dispatch_db, monkeypatch
) -> None:
    from app.ports import work_object_scope as policy
    from tests.api.test_work_object_dispatch import (
        assert_created,
        cross_department_body,
        insert_synthetic_row,
        manual_row,
        run,
    )

    monkeypatch.setattr(policy, "_CROSS_DEPARTMENT_DISPATCH_ALLOWED_IDS", frozenset({"office-a"}))
    db = dispatch_db
    assert_created(db, db.post(cross_department_body()))
    base = manual_row(db)
    # Insert unrelated rows before visible rows so a pre-filter LIMIT loses valid results.
    for index in range(205):
        insert_synthetic_row(
            db,
            {
                **base,
                "work_object_id": f"hidden-{index}",
                "owner_department_id": "office-c",
                "initiator_ai_user_id": "ai-neighbor",
            },
        )
    expected = {"same-department", "own-outgoing"}
    insert_synthetic_row(
        db,
        {
            **base,
            "work_object_id": "same-department",
            "owner_department_id": "office-a",
            "initiator_ai_user_id": "ai-neighbor",
        },
    )
    insert_synthetic_row(
        db,
        {
            **base,
            "work_object_id": "own-outgoing",
            "owner_department_id": "office-b",
            "initiator_ai_user_id": "ai-reader",
        },
    )
    for index in range(198):
        item_id = f"visible-{index}"
        expected.add(item_id)
        insert_synthetic_row(
            db, {**base, "work_object_id": item_id, "owner_department_id": "office-a"}
        )
    insert_synthetic_row(
        db,
        {
            **base,
            "work_object_id": "other-tenant",
            "tenant_id": "tenant-dispatch-b",
            "owner_department_id": "office-a",
            "initiator_ai_user_id": "ai-reader",
        },
    )
    scope = AuthorizedWorkObjectScope(
        principal_tenant_id="default",
        principal_ai_user_id="ai-reader",
        principal_department_id="office-a",
    )
    rows = run(db.store.list_for_scope(scope))
    assert {row.work_object_id for row in rows} == expected
    assert {row.tenant_id for row in rows} == {"default"}
    assert run(db.store.get_for_scope("other-tenant", scope)) is None
    insert_synthetic_row(
        db, {**base, "work_object_id": "visible-201", "owner_department_id": "office-a"}
    )
    rows = run(db.store.list_for_scope(scope))
    assert {row.work_object_id for row in rows} == expected | {"visible-201"}
    assert len(rows) == 201


def test_private_legacy_and_oa_rows_do_not_gain_department_visibility(
    dispatch_db, monkeypatch
) -> None:
    from app.ports import work_object_scope as policy
    from tests.api.test_work_object_dispatch import (
        assert_created,
        cross_department_body,
        insert_synthetic_row,
        manual_row,
        run,
    )
    from tests.api.test_work_objects import _record

    monkeypatch.setattr(policy, "_CROSS_DEPARTMENT_DISPATCH_ALLOWED_IDS", frozenset({"office-a"}))
    db = dispatch_db
    created = assert_created(db, db.post(cross_department_body()))
    oa = _record(owner="ai-private-owner").model_dump()
    insert_synthetic_row(db, oa)
    legacy = {
        **oa,
        "work_object_id": "legacy-private",
        "state_authority": "internal",
        "source_system": "eternalai",
        "source_kind": "internal_task",
    }
    for field in (
        "source_ref",
        "source_title",
        "source_status",
        "source_received_at",
        "source_created_at",
        "source_workflow_type_id",
        "source_fetched_at",
    ):
        legacy[field] = None
    insert_synthetic_row(db, legacy)
    private = AuthorizedWorkObjectScope(
        principal_tenant_id="default",
        principal_ai_user_id="ai-private-owner",
        principal_department_id=None,
    )
    assert {row.work_object_id for row in run(db.store.list_for_scope(private))} == {
        oa["work_object_id"],
        "legacy-private",
    }
    # Only this synthetic schema accepts the otherwise invalid, incomplete manual rows.
    db.execute("ALTER TABLE work_objects DROP CONSTRAINT ck_work_objects_manual_dispatch_complete")
    for field in ("tenant_id", "owner_department_id", "initiator_ai_user_id", "version"):
        insert_synthetic_row(
            db,
            manual_row(
                db,
                work_object_id="incomplete-" + field,
                assignee_ai_user_id="ai-private-owner",
                **{field: None},
            ),
        )
    for actor in ("ai-private-owner", "ai-admin", "ai-recipient"):
        scope = AuthorizedWorkObjectScope(
            principal_tenant_id="default",
            principal_ai_user_id=actor,
            principal_department_id="office-b",
        )
        visible = {row.work_object_id for row in run(db.store.list_for_scope(scope))}
        expected = {created["work_object_id"]}
        if actor == "ai-private-owner":
            expected |= {oa["work_object_id"], "legacy-private"}
        assert visible == expected
        for field in ("tenant_id", "owner_department_id", "initiator_ai_user_id", "version"):
            assert run(db.store.get_for_scope("incomplete-" + field, scope)) is None


@pytest.mark.parametrize("query", ["mIxEd", "\u3000mixed   100%_ready\ufeff", "%_", "100%_READY"])
def test_internal_search_filters_scope_and_title_before_limit(dispatch_db, query) -> None:
    from tests.api.test_work_object_dispatch import (
        assert_created,
        insert_synthetic_row,
        manual_row,
        run,
    )

    db = dispatch_db
    assert_created(db, db.post())
    base = manual_row(db)
    for index in range(205):
        insert_synthetic_row(
            db,
            {
                **base,
                "work_object_id": f"unrelated-{index}",
                "title": "unrelated",
                "owner_department_id": "office-a",
            },
        )
    insert_synthetic_row(
        db,
        {
            **base,
            "work_object_id": "hidden-title",
            "title": "Mixed 100%_ready",
            "owner_department_id": "office-c",
            "initiator_ai_user_id": "ai-neighbor",
        },
    )
    insert_synthetic_row(
        db,
        {
            **base,
            "work_object_id": "visible-title",
            "title": "MiXeD\u3000 100%_Ready",
            "owner_department_id": "office-a",
        },
    )
    scope = AuthorizedWorkObjectScope(
        principal_tenant_id="default",
        principal_ai_user_id="ai-reader",
        principal_department_id="office-a",
    )
    assert [
        row.work_object_id
        for row in run(db.store.list_for_scope(scope, search_term=query, limit=1))
    ] == ["visible-title"]
    assert [
        row.work_object_id for row in run(db.store.list_for_scope(scope, search_term=query))
    ] == ["visible-title"]
    assert run(db.store.list_for_scope(scope, search_term="100X_ready")) == []


def test_dispatch_concurrent_idempotency_and_restart_replay(dispatch_db, monkeypatch) -> None:
    from app.api.v1.work_objects import DispatchWorkObjectsRequest
    from tests.api.test_work_object_dispatch import request_body, run

    db = dispatch_db
    key = uuid4()
    principal = db.tokens.principal
    lookup = db.store.get_dispatch_receipt
    arrived = 0

    async def exercise():
        barrier = asyncio.Event()

        async def simultaneous(**kwargs):
            nonlocal arrived
            receipt = await lookup(**kwargs)
            if arrived < 2:
                arrived += 1
                if arrived == 2:
                    barrier.set()
                await barrier.wait()
            return receipt

        monkeypatch.setattr(db.store, "get_dispatch_receipt", simultaneous)
        body = DispatchWorkObjectsRequest.model_validate(request_body())
        first, second = await asyncio.wait_for(
            asyncio.gather(
                db.service.dispatch_for_principal(principal, body, key),
                db.service.dispatch_for_principal(principal, body, key),
            ),
            timeout=15,
        )
        assert sorted([first.replayed, second.replayed]) == [False, True]
        assert first.items == second.items and first.created_count == second.created_count == 1
        restarted = PostgreSQLWorkObjectStore(db.factory)
        saved = await restarted.get_dispatch_receipt(
            tenant_id="default", initiator_ai_user_id="ai-sender", idempotency_key=key
        )
        assert saved is not None
        assert saved.result["items"] == first.model_dump(mode="json")["items"]
        db.service._store = restarted
        replay = await db.service.dispatch_for_principal(principal, body, key)
        assert replay.replayed is True and replay.items == first.items

    run(exercise())
    assert arrived == 2
    assert db.counts() == (1, 1)


def test_dispatch_batch_failure_rolls_back_objects_and_receipt(dispatch_db, monkeypatch) -> None:
    from app.ports import work_object_scope as policy
    from tests.api.test_work_object_dispatch import assert_error, request_body

    monkeypatch.setattr(policy, "_CROSS_DEPARTMENT_DISPATCH_ALLOWED_IDS", frozenset({"office-a"}))
    db = dispatch_db
    inserts = 0

    def fail_second(_connection, _cursor, statement, _parameters, _context, _executemany):
        nonlocal inserts
        if statement.startswith("INSERT INTO work_objects "):
            inserts += 1
            if inserts == 2:
                raise RuntimeError("synthetic second insert failure")

    event.listen(db.engine.sync_engine, "before_cursor_execute", fail_second)
    key = str(uuid4())
    body = request_body(
        targets=[
            {"kind": "department", "department_id": "office-b"},
            {"kind": "department", "department_id": "office-c"},
        ]
    )
    try:
        assert_error(db.post(body, key=key), 503, "work_object_dispatch_failed")
    finally:
        event.remove(db.engine.sync_engine, "before_cursor_execute", fail_second)
    assert inserts == 2
    assert db.counts() == (0, 0)
    response = db.post(body, key=key)
    assert response.status_code == 201
    assert response.json()["created_count"] == 2
    assert db.counts() == (2, 1)


@pytest.mark.parametrize(
    "different_fingerprint", [False, True], ids=["same-body", "different-body"]
)
def test_dispatch_trace_failure_recovers_concurrent_receipt(
    dispatch_db, monkeypatch, caplog, different_fingerprint
) -> None:
    from fastapi import HTTPException

    from app.api.v1.work_objects import DispatchWorkObjectsRequest, WorkObjectService
    from tests.api.test_work_object_dispatch import request_body, run

    db = dispatch_db
    principal = db.tokens.principal
    key = uuid4()
    body = DispatchWorkObjectsRequest.model_validate(request_body())
    second_service = WorkObjectService(
        store=PostgreSQLWorkObjectStore(db.factory),
        gateway=db.service._gateway,
        capability_registry=StaticCapabilityRegistry(),
        organization_directory=db.directory,
        trace_port=db.trace,
    )

    async def exercise():
        trace_reached = asyncio.Event()
        winner_committed = asyncio.Event()

        async def delayed_failure(*_args, **_kwargs):
            trace_reached.set()
            await winner_committed.wait()
            raise RuntimeError("synthetic failing audit after concurrent commit")

        class FailingTrace:
            record_event = staticmethod(delayed_failure)

        monkeypatch.setattr(db.service, "_trace_port", FailingTrace())
        pending = asyncio.create_task(db.service.dispatch_for_principal(principal, body, key))
        await asyncio.wait_for(trace_reached.wait(), timeout=15)
        winner_body = (
            DispatchWorkObjectsRequest.model_validate(request_body(title="Other body"))
            if different_fingerprint
            else body
        )
        winner = await second_service.dispatch_for_principal(principal, winner_body, key)
        winner_committed.set()
        if different_fingerprint:
            with pytest.raises(HTTPException) as error:
                await asyncio.wait_for(pending, timeout=15)
            assert (
                error.value.status_code == 409
                and error.value.detail["code"] == "idempotency_key_reused"
            )
        else:
            recovered = await asyncio.wait_for(pending, timeout=15)
            assert recovered.replayed is True
            assert (
                recovered.items == winner.items
                and recovered.created_count == winner.created_count == 1
            )

    run(exercise())
    assert db.counts() == (1, 1)
    assert [
        record.getMessage() for record in caplog.records if record.name == "app.api.v1.work_objects"
    ] == ["work_object_audit_unavailable"]


_PLUS8 = timezone(timedelta(hours=8))
_DUE_EARLY = datetime(2026, 1, 1, tzinfo=UTC)
_DUE_LATE = datetime(2026, 6, 1, tzinfo=UTC)
_CREATED_OLDEST = datetime(2025, 6, 1, 12, tzinfo=UTC)
_CREATED_OLD = datetime(2026, 1, 10, 12, tzinfo=UTC)
_CREATED_MID = datetime(2026, 4, 1, 12, tzinfo=UTC)
_CREATED_NEW = datetime(2026, 8, 1, 12, tzinfo=UTC)
_KEY_ORDER = (
    "due-early",
    "due-new",
    "due-old",
    "offset-east",
    "offset-utc",
    "null-new",
    "A",
    "a",
    "中",
)
_KEY_SPECS = (
    ("due-early", _DUE_EARLY, _CREATED_OLD, "oa"),
    ("due-new", _DUE_LATE, _CREATED_NEW, "manual"),
    ("due-old", _DUE_LATE, _CREATED_MID, "legacy"),
    ("offset-east", datetime(2026, 6, 1, 8, tzinfo=_PLUS8), _CREATED_OLD, "oa"),
    ("offset-utc", _DUE_LATE, _CREATED_OLD, "oa"),
    ("null-new", None, _CREATED_NEW, "manual"),
    ("A", None, _CREATED_OLD, "oa"),
    ("a", None, _CREATED_OLD, "legacy"),
    ("中", None, _CREATED_OLD, "manual"),
)


def _row_ids(rows) -> list[str]:
    return [row.work_object_id for row in rows]


def _prepare_sorter(db):
    from tests.api.test_work_object_dispatch import manual_row, request_body, run

    response = db.post(
        request_body(targets=[{"kind": "department", "department_id": "office-a"}])
    )
    assert response.status_code == 201, response.text
    created_id = response.json()["items"][0]["work_object_id"]
    db.actor("sorter", "office-c", None)
    scope = AuthorizedWorkObjectScope(
        principal_tenant_id="default",
        principal_ai_user_id="ai-sorter",
        principal_department_id="office-c",
    )
    assert _row_ids(run(db.store.list_for_scope(scope))) == []
    return created_id, scope, manual_row(db)


def _insert_sorted_row(
    db,
    base: dict,
    *,
    item_id: str,
    due_at: datetime | None,
    created_at: datetime,
    kind: str,
    **extra: object,
) -> None:
    from tests.api.test_work_object_dispatch import insert_synthetic_row
    from tests.api.test_work_objects import _record

    if kind == "manual":
        row = {
            **base,
            "work_object_id": item_id,
            "owner_department_id": extra.get("owner_department_id", "office-c"),
            "initiator_ai_user_id": extra.get("initiator_ai_user_id", "ai-neighbor"),
            "due_at": due_at,
            "created_at": created_at,
            "updated_at": extra.get("updated_at", created_at),
            "title": extra.get("title", "Manual " + item_id),
        }
        insert_synthetic_row(db, row)
        return
    row = _record(owner="ai-sorter", source_ref="oa-" + item_id, index=1).model_dump()
    row.update(
        {
            "work_object_id": item_id,
            "due_at": due_at,
            "created_at": created_at,
            "updated_at": extra.get("updated_at", created_at),
            "assignee_ai_user_id": extra.get("assignee_ai_user_id", "ai-sorter"),
            "assignee_display_name": extra.get("assignee_display_name", "Sorter"),
            "source_title": extra.get("source_title", "OA " + item_id),
            "source_ref": extra.get("source_ref", "oa-" + item_id),
            "source_status": extra.get("source_status", "OA_PENDING"),
            "source_fetched_at": extra.get("source_fetched_at", created_at),
        }
    )
    if kind == "legacy":
        row.update(
            {
                "state_authority": "internal",
                "source_system": "eternalai",
                "source_kind": "internal_task",
            }
        )
        for field in (
            "source_ref",
            "source_title",
            "source_status",
            "source_received_at",
            "source_created_at",
            "source_workflow_type_id",
            "source_fetched_at",
        ):
            row[field] = None
    insert_synthetic_row(db, row)


def _capture_list_sql(db) -> list[str]:
    statements: list[str] = []

    def capture(_conn, _cursor, statement, _params, _context, _many) -> None:
        collapsed = _collapsed_sql(statement)
        if (
            collapsed.lstrip().upper().startswith("SELECT")
            and "FROM work_objects" in collapsed
            and "LIMIT" in collapsed.upper()
        ):
            statements.append(statement)

    event.listen(db.engine.sync_engine, "before_cursor_execute", capture)
    return statements


def _service_list(db, *, search_term: str | None = None):
    from tests.api.test_work_object_dispatch import run

    response = run(
        db.service.list_for_principal(db.tokens.principal, search_term=search_term)
    )
    return [item.work_object_id for item in response.items], response.limit_exceeded


def test_bounded_list_orders_mixed_authorities_before_limit(dispatch_db) -> None:
    from tests.api.test_work_object_dispatch import run

    db = dispatch_db
    statements = _capture_list_sql(db)
    _seed_id, scope, base = _prepare_sorter(db)
    kinds = ("oa", "manual", "legacy")
    for index in range(199, -1, -1):
        _insert_sorted_row(
            db,
            base,
            item_id=f"fill-{index:03d}",
            due_at=None,
            created_at=_CREATED_OLDEST,
            kind=kinds[index % 3],
        )
    for item_id, due_at, created_at, kind in reversed(_KEY_SPECS):
        _insert_sorted_row(
            db, base, item_id=item_id, due_at=due_at, created_at=created_at, kind=kind
        )
    expected = list(_KEY_ORDER) + [f"fill-{index:03d}" for index in range(192)]
    store_ids = _row_ids(run(db.store.list_for_scope(scope)))
    service_ids, overflow = _service_list(db)
    assert store_ids == expected
    assert store_ids[0] == "due-early"
    assert service_ids == expected[:200]
    assert overflow is True
    assert statements
    for statement in statements:
        _assert_list_sql_orders_before_limit(statement)


def test_bounded_list_ties_use_created_at_and_c_collated_id(dispatch_db) -> None:
    from tests.api.test_work_object_dispatch import run

    db = dispatch_db
    statements = _capture_list_sql(db)
    _seed_id, scope, base = _prepare_sorter(db)
    for item_id, due_at, created_at, kind in reversed(_KEY_SPECS):
        _insert_sorted_row(
            db, base, item_id=item_id, due_at=due_at, created_at=created_at, kind=kind
        )
    assert _row_ids(run(db.store.list_for_scope(scope))) == list(_KEY_ORDER)

    for index in range(198, -1, -1):
        _insert_sorted_row(
            db,
            base,
            item_id=f"early-{index:03d}",
            due_at=datetime(2025, 1, 1, tzinfo=UTC),
            created_at=_CREATED_OLD,
            kind="manual",
        )
    _insert_sorted_row(
        db,
        base,
        item_id="tie-out",
        due_at=datetime(2025, 6, 1, tzinfo=UTC),
        created_at=_CREATED_NEW,
        kind="oa",
    )
    _insert_sorted_row(
        db,
        base,
        item_id="tie-in",
        due_at=datetime(2025, 6, 1, tzinfo=UTC),
        created_at=_CREATED_NEW,
        kind="oa",
    )
    expected = [f"early-{index:03d}" for index in range(199)] + ["tie-in", "tie-out"]
    store_ids = _row_ids(run(db.store.list_for_scope(scope)))
    service_ids, overflow = _service_list(db)
    assert store_ids == expected
    assert "tie-in" in store_ids
    assert store_ids[199] == "tie-in"
    assert store_ids[200] == "tie-out"
    assert service_ids == expected[:200]
    assert "tie-out" not in service_ids
    assert overflow is True
    list_sql = " ".join(_collapsed_sql(sql) for sql in statements)
    assert _STABLE_LIST_ORDER_BY in list_sql
    assert 'COLLATE "C"' in list_sql
    assert "NULLS LAST" in list_sql


@pytest.mark.parametrize("count", [0, 1, 199, 200, 201, 205])
def test_bounded_list_limit_and_overflow_edges(dispatch_db, count: int) -> None:
    from tests.api.test_work_object_dispatch import run

    db = dispatch_db
    _seed_id, scope, base = _prepare_sorter(db)
    expected = [f"row-{index:03d}" for index in range(count)]
    for index in range(count - 1, -1, -1):
        _insert_sorted_row(
            db,
            base,
            item_id=expected[index],
            due_at=None,
            created_at=_CREATED_NEW - timedelta(seconds=index),
            kind="oa",
        )
    for limit in (1, 200, 201):
        assert _row_ids(run(db.store.list_for_scope(scope, limit=limit))) == expected[
            : min(count, limit)
        ]
    service_ids, overflow = _service_list(db)
    assert service_ids == expected[: min(count, 200)]
    assert overflow is (count >= 201)
    payload = db.client.get("/api/v1/work-objects").json()
    assert [item["work_object_id"] for item in payload["items"]] == expected[
        : min(count, 200)
    ]
    assert payload["limit"] == 200
    assert payload["limit_exceeded"] is (count >= 201)
    if count in {201, 205}:
        assert len(payload["items"]) == 200


def test_search_orders_matching_subset_before_limit(dispatch_db) -> None:
    from tests.api.test_work_object_dispatch import run

    db = dispatch_db
    statements = _capture_list_sql(db)
    _seed_id, scope, base = _prepare_sorter(db)
    for index in range(205):
        _insert_sorted_row(
            db,
            base,
            item_id=f"miss-{index:03d}",
            due_at=_DUE_EARLY,
            created_at=_CREATED_OLD,
            kind="oa",
            source_title="Unrelated",
            assignee_display_name="Other Person",
            source_ref=f"MISS-{index:03d}",
        )
    for index in range(205):
        _insert_sorted_row(
            db,
            base,
            item_id=f"hit-{index:03d}",
            due_at=_DUE_LATE,
            created_at=_CREATED_OLD,
            kind="oa",
            source_title="Needle 100%_ready item",
            assignee_display_name="Li Ming",
            source_ref="OA-REF-EQ" if index == 0 else f"HIT-{index:03d}",
        )
    _insert_sorted_row(
        db,
        base,
        item_id="late-hit",
        due_at=_DUE_LATE,
        created_at=_CREATED_NEW,
        kind="oa",
        source_title="Needle 100%_ready late",
        assignee_display_name="Li Ming",
        source_ref="LATE-HIT",
    )
    matching = ["late-hit"] + [f"hit-{index:03d}" for index in range(205)]
    for query in (
        "needle",
        "\u3000NEEDLE\u00a0",
        "%_",
        " li ming ",
    ):
        store_ids = _row_ids(run(db.store.list_for_scope(scope, search_term=query)))
        service_ids, overflow = _service_list(db, search_term=query)
        assert store_ids == matching[:201]
        assert store_ids[0] == "late-hit"
        assert service_ids == matching[:200]
        assert overflow is True
    assert _row_ids(run(db.store.list_for_scope(scope, search_term=" OA-REF-EQ "))) == [
        "hit-000"
    ]
    assert _row_ids(run(db.store.list_for_scope(scope, search_term="100X_ready"))) == []
    search_sql = [sql for sql in statements if "STRPOS" in sql.upper()]
    assert search_sql
    for statement in search_sql:
        _assert_list_sql_orders_before_limit(statement)
        upper = statement.upper()
        assert upper.index("STRPOS") < upper.index("ORDER BY") < upper.index("LIMIT")


def test_ordering_preserves_scope_before_limit(dispatch_db) -> None:
    from tests.api.test_work_object_dispatch import insert_synthetic_row, run
    from tests.api.test_work_objects import _record

    db = dispatch_db
    _seed_id, scope, base = _prepare_sorter(db)
    hidden_due = datetime(2025, 1, 1, tzinfo=UTC)
    for index in range(205):
        _insert_sorted_row(
            db,
            base,
            item_id=f"hid-dept-{index:03d}",
            due_at=hidden_due,
            created_at=_CREATED_NEW,
            kind="manual",
            owner_department_id="office-a",
            initiator_ai_user_id="ai-neighbor",
        )
    other_oa = _record(owner="ai-other", source_ref="other-oa", index=1).model_dump()
    other_oa.update(
        {
            "work_object_id": "hid-oa",
            "due_at": hidden_due,
            "created_at": _CREATED_NEW,
            "updated_at": _CREATED_NEW,
        }
    )
    insert_synthetic_row(db, other_oa)
    other_legacy = {
        **other_oa,
        "work_object_id": "hid-legacy",
        "state_authority": "internal",
        "source_system": "eternalai",
        "source_kind": "internal_task",
    }
    for field in (
        "source_ref",
        "source_title",
        "source_status",
        "source_received_at",
        "source_created_at",
        "source_workflow_type_id",
        "source_fetched_at",
    ):
        other_legacy[field] = None
    insert_synthetic_row(db, other_legacy)
    insert_synthetic_row(
        db,
        {
            **base,
            "work_object_id": "hid-tenant",
            "tenant_id": "synthetic-other",
            "owner_department_id": "office-c",
            "initiator_ai_user_id": "ai-sorter",
            "due_at": hidden_due,
            "created_at": _CREATED_NEW,
            "updated_at": _CREATED_NEW,
        },
    )
    visible = (
        ("vis-legacy", None, _CREATED_NEW, "legacy"),
        ("vis-self", _DUE_LATE, _CREATED_OLD, "manual"),
        ("vis-dept", _DUE_LATE, _CREATED_NEW, "manual"),
        ("vis-oa", _DUE_EARLY, _CREATED_OLD, "oa"),
    )
    for item_id, due_at, created_at, kind in visible:
        extras: dict[str, object] = {}
        if item_id == "vis-self":
            extras = {
                "owner_department_id": "office-a",
                "initiator_ai_user_id": "ai-sorter",
            }
        _insert_sorted_row(
            db,
            base,
            item_id=item_id,
            due_at=due_at,
            created_at=created_at,
            kind=kind,
            **extras,
        )
    expected = ["vis-oa", "vis-dept", "vis-self", "vis-legacy"]
    store_ids = _row_ids(run(db.store.list_for_scope(scope)))
    service_ids, overflow = _service_list(db)
    hidden = {
        "hid-oa",
        "hid-legacy",
        "hid-tenant",
        _seed_id,
        *[f"hid-dept-{index:03d}" for index in range(205)],
    }
    assert store_ids == expected
    assert set(store_ids).isdisjoint(hidden)
    assert service_ids == expected
    assert overflow is False


def test_non_sort_updates_preserve_order_and_new_rows_reselect(dispatch_db) -> None:
    from tests.api.test_work_object_dispatch import run

    db = dispatch_db
    _seed_id, scope, base = _prepare_sorter(db)
    for index in range(201):
        _insert_sorted_row(
            db,
            base,
            item_id=f"row-{index:03d}",
            due_at=None,
            created_at=_CREATED_NEW - timedelta(seconds=index),
            kind="oa",
            source_title="Stable title",
            source_ref=f"OA-STABLE-{index:03d}",
        )
    original = [f"row-{index:03d}" for index in range(201)]
    first = _row_ids(run(db.store.list_for_scope(scope)))
    second = _row_ids(run(db.store.list_for_scope(scope)))
    assert first == second == original
    run(
        db.store.upsert_oa_pending_workflows(
            assignee_ai_user_id="ai-sorter",
            assignee_display_name="Sorter renamed",
            snapshots=[
                _snapshot(
                    source_ref="OA-STABLE-000",
                    title="Refreshed title",
                    status="OA_STILL_PENDING",
                )
            ],
            fetched_at=_CREATED_NEW + timedelta(days=1),
        )
    )
    marked = run(
        db.store.set_handling_mark_for_scope(
            "row-000",
            scope,
            "handled_elsewhere",
            marked_at=_CREATED_NEW + timedelta(hours=2),
        )
    )
    assert marked is not None
    assert _row_ids(run(db.store.list_for_scope(scope))) == original
    _insert_sorted_row(
        db,
        base,
        item_id="row-new",
        due_at=None,
        created_at=_CREATED_NEW + timedelta(seconds=1),
        kind="oa",
        source_ref="OA-STABLE-NEW",
    )
    after_insert = _row_ids(run(db.store.list_for_scope(scope)))
    assert after_insert[0] == "row-new"
    assert after_insert[1:201] == original[:200]
    assert "row-200" not in after_insert
    db.execute(
        "UPDATE work_objects SET due_at=:due, updated_at=:updated "
        "WHERE work_object_id=:item_id",
        due=_DUE_EARLY,
        updated=_CREATED_NEW + timedelta(days=2),
        item_id="row-199",
    )
    after_due = _row_ids(run(db.store.list_for_scope(scope)))
    assert after_due[0] == "row-199"
    assert after_due[1] == "row-new"
    assert "row-198" in after_due
    titled = _row_ids(
        run(db.store.list_for_scope(scope, search_term="Refreshed title"))
    )
    assert titled == ["row-000"]
    still_old = _row_ids(
        run(db.store.list_for_scope(scope, search_term="Stable title"))
    )
    assert still_old[0] == "row-199"
    assert "row-000" not in still_old
