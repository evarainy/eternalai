from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
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
from app.ports.work_object_search import SEARCH_WHITESPACE
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
            user_a_records = await store.list_for_assignee(user_a)
            user_b_records = await store.list_for_assignee(user_b)
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

            marked = await store.set_handling_mark_for_assignee(
                user_a_records[0].work_object_id,
                user_a,
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
            refreshed_records = await store.list_for_assignee(user_a)
            assert len(refreshed_records) == 1
            refreshed = refreshed_records[0]
            assert refreshed.work_object_id == user_a_records[0].work_object_id
            assert refreshed.state_authority == "external_snapshot"
            assert refreshed.source_title == "Refreshed"
            assert refreshed.source_status == "OA_STILL_PENDING"
            assert refreshed.source_fetched_at == second_fetch
            assert refreshed.handling_mark == "handled_elsewhere"
            assert refreshed.handling_marked_at == first_fetch + timedelta(minutes=1)

            assert (
                await store.get_for_assignee(refreshed.work_object_id, user_b)
                is None
            )
            assert (
                await store.set_handling_mark_for_assignee(
                    refreshed.work_object_id,
                    user_b,
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
            assert all("ORDER BY" not in statement.upper() for statement in list_statements)
            search_statements = [
                statement for statement in list_statements if "STRPOS" in statement.upper()
            ]
            assert search_statements
            assert all(
                "assignee_ai_user_id" in statement for statement in search_statements
            )
            assert all(
                "tenant-b-only-todo" not in statement.lower()
                for statement in search_statements
            )
        finally:
            async with factory() as session:
                await session.execute(
                    text(
                        "DELETE FROM work_objects "
                        "WHERE assignee_ai_user_id IN (:user_a, :user_b)"
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
            assert await refs(tenant_a_principal, " oa-ref-002 ") == {
                " OA-REF-002 "
            }
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
            assert await refs(tenant_b_principal, "tenant-b-only") == {
                "TENANT-B-ONLY"
            }
        finally:
            async with factory() as session:
                await session.execute(
                    text(
                        "DELETE FROM work_objects "
                        "WHERE assignee_ai_user_id IN (:user_a, :user_b)"
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
                rows = await store.list_for_assignee(
                    principal.ai_user_id, search_term=f"{ws}OA{ws}REF-{index}{ws}"
                )
                assert [row.source_ref for row in rows] == [f"{ws}OA{ws}{ws}REF-{index}{ws}"]
                assert all(row.assignee_ai_user_id == principal.ai_user_id for row in rows)
            for query in ("oa ref", "ming", "OTHER"):
                assert await store.list_for_assignee(principal.ai_user_id, search_term=query) == []
            for query in (None, "", "\u0085\u00a0\ufeff"):
                rows = await store.list_for_assignee(principal.ai_user_id, search_term=query)
                assert {row.source_ref for row in rows} == expected
            assert statements
            assert all("WHERE assignee_ai_user_id =" in sql for sql in statements)
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
            assert "WHERE assignee_ai_user_id =" in sql
            assert sql.upper().index("STRPOS") < sql.upper().index("LIMIT")

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
