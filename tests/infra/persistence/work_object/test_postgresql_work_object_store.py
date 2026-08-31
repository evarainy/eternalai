from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

from sqlalchemy import event, text

from app.api.v1.work_objects import WorkObjectService, WorkObjectView
from app.db.session import make_async_engine, make_async_session_factory
from app.infra.persistence.work_object.postgresql import PostgreSQLWorkObjectStore
from app.ports.auth import Principal, PrincipalOrgContext
from app.ports.capability_gateway import CapabilityGatewayPort
from app.ports.work_object import OAPendingWorkSnapshot
from tests.runtime.registry_fakes import StaticCapabilityRegistry

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


def _matches_search_contract(item: WorkObjectView, term: str) -> bool:
    normalized = term.strip().lower()
    return (
        (item.source_title is not None and normalized in item.source_title.lower())
        or (
            item.source_ref is not None
            and item.source_ref.strip().lower() == normalized
        )
        or (item.assignee_display_name.strip().lower() == normalized)
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
            tenant_a_candidates = (await service.list_for_principal(tenant_a_principal)).items
            tenant_b_candidates = (await service.list_for_principal(tenant_b_principal)).items
            for tenant_b_query in (
                "other tenant",
                " TENANT-B-ONLY-TODO ",
                " user b ",
            ):
                assert not any(
                    _matches_search_contract(item, tenant_b_query)
                    for item in tenant_a_candidates
                )
                assert any(
                    _matches_search_contract(item, tenant_b_query)
                    for item in tenant_b_candidates
                )

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
