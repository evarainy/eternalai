from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from app.db.session import make_async_engine, make_async_session_factory
from app.infra.organization_directory.postgresql import PostgreSQLOrganizationDirectory
from app.ports.organization_directory import (
    OrganizationDepartment,
    OrganizationDirectoryError,
    OrganizationDirectoryPage,
    OrganizationDirectorySnapshot,
    OrganizationUserMembership,
)

DATABASE_URL = os.environ.get("DATABASE_URL")
FETCHED_AT = datetime(2026, 8, 31, tzinfo=UTC)

if hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # type: ignore[attr-defined]


def _snapshot(*, complete: bool = True) -> OrganizationDirectorySnapshot:
    return OrganizationDirectorySnapshot(
        departments=(
            OrganizationDepartment(
                department_id="synthetic-root",
                display_name="Synthetic root",
                subcompany_id="synthetic-subcompany",
            ),
            OrganizationDepartment(
                department_id="synthetic-child",
                parent_department_id="synthetic-root",
                display_name="Synthetic child",
                subcompany_id="synthetic-subcompany",
            ),
            OrganizationDepartment(
                department_id="synthetic-leaf",
                parent_department_id="synthetic-child",
                display_name="Synthetic leaf",
                subcompany_id="synthetic-subcompany",
            ),
        ),
        user_pages=(
            OrganizationDirectoryPage(
                current_page=1,
                next_page=None,
                is_end=True,
                memberships=(
                    OrganizationUserMembership(
                        user_id="synthetic-user",
                        department_id="synthetic-leaf",
                        organization_id="synthetic-org",
                        subcompany_id="synthetic-subcompany",
                    ),
                ),
            ),
        ),
        authoritative_user_count_before=1,
        authoritative_user_count_after=1,
        is_complete=complete,
        fetched_at=FETCHED_AT,
    )


def _membership_boundary_snapshot() -> OrganizationDirectorySnapshot:
    base = _snapshot()
    memberships = (
        OrganizationUserMembership(
            user_id="synthetic-user",
            department_id="synthetic-child",
            organization_id="synthetic-org-alpha",
            subcompany_id="synthetic-subcompany",
        ),
        OrganizationUserMembership(
            user_id="synthetic-user",
            department_id="synthetic-leaf",
            organization_id=None,
            subcompany_id="synthetic-subcompany",
        ),
        OrganizationUserMembership(
            user_id="synthetic-user",
            department_id="synthetic-root",
            organization_id="synthetic-org-beta",
            subcompany_id="synthetic-subcompany",
        ),
    )
    page = base.user_pages[0].model_copy(update={"memberships": memberships})
    return base.model_copy(
        update={
            "user_pages": (page,),
            "authoritative_user_count_before": len(memberships),
            "authoritative_user_count_after": len(memberships),
        }
    )


def _require_database_url() -> str:
    if not DATABASE_URL:
        raise AssertionError("DATABASE_URL must be set by the test runner environment")
    return DATABASE_URL


def test_replaces_and_queries_complete_directory_snapshot() -> None:
    async def exercise() -> None:
        engine = make_async_engine(_require_database_url())
        factory = make_async_session_factory(engine)
        directory = PostgreSQLOrganizationDirectory(factory)
        try:
            await directory.replace_snapshot(_snapshot())

            subtree = await directory.list_department_subtree("synthetic-root")
            memberships = await directory.list_user_memberships("synthetic-user")

            assert [item.department_id for item in subtree] == [
                "synthetic-root", "synthetic-child", "synthetic-leaf"
            ]
            assert memberships == list(_snapshot().memberships)
            assert await directory.get_department("synthetic-child") == _snapshot().departments[1]
        finally:
            async with factory() as session:
                await session.execute(text("DELETE FROM organization_user_memberships"))
                await session.execute(text("DELETE FROM organization_departments"))
                await session.commit()
            await engine.dispose()

    asyncio.run(exercise())


def test_list_user_memberships_returns_complete_set_across_organization_values() -> None:
    async def exercise() -> None:
        engine = make_async_engine(_require_database_url())
        factory = make_async_session_factory(engine)
        directory = PostgreSQLOrganizationDirectory(factory)
        try:
            await directory.replace_snapshot(_membership_boundary_snapshot())

            memberships = await directory.list_user_memberships("synthetic-user")

            assert memberships == [
                OrganizationUserMembership(
                    user_id="synthetic-user",
                    department_id="synthetic-child",
                    organization_id="synthetic-org-alpha",
                    subcompany_id="synthetic-subcompany",
                ),
                OrganizationUserMembership(
                    user_id="synthetic-user",
                    department_id="synthetic-leaf",
                    organization_id=None,
                    subcompany_id="synthetic-subcompany",
                ),
                OrganizationUserMembership(
                    user_id="synthetic-user",
                    department_id="synthetic-root",
                    organization_id="synthetic-org-beta",
                    subcompany_id="synthetic-subcompany",
                ),
            ]
        finally:
            async with factory() as session:
                await session.execute(text("DELETE FROM organization_user_memberships"))
                await session.execute(text("DELETE FROM organization_departments"))
                await session.commit()
            await engine.dispose()

    asyncio.run(exercise())


def test_incomplete_snapshot_is_rejected_before_database_use() -> None:
    def forbidden_factory():
        raise AssertionError("incomplete snapshot must not access database")

    directory = PostgreSQLOrganizationDirectory(forbidden_factory)  # type: ignore[arg-type]
    with pytest.raises(OrganizationDirectoryError, match="incomplete"):
        asyncio.run(directory.replace_snapshot(_snapshot(complete=False)))


def test_declared_complete_snapshot_must_match_actual_membership_rows() -> None:
    def forbidden_factory():
        raise AssertionError("count mismatch must not access database")

    inconsistent_page = _snapshot().user_pages[0].model_copy(update={"memberships": ()})
    inconsistent = _snapshot().model_copy(update={"user_pages": (inconsistent_page,)})
    directory = PostgreSQLOrganizationDirectory(forbidden_factory)  # type: ignore[arg-type]
    with pytest.raises(OrganizationDirectoryError, match="incomplete"):
        asyncio.run(directory.replace_snapshot(inconsistent))


def test_cyclic_snapshot_is_rejected_before_database_use() -> None:
    def forbidden_factory():
        raise AssertionError("cyclic snapshot must not access database")

    cyclic = _snapshot().model_copy(
        update={
            "departments": (
                OrganizationDepartment(
                    department_id="synthetic-a",
                    parent_department_id="synthetic-b",
                    display_name="Synthetic A",
                ),
                OrganizationDepartment(
                    department_id="synthetic-b",
                    parent_department_id="synthetic-a",
                    display_name="Synthetic B",
                ),
            )
        }
    )
    directory = PostgreSQLOrganizationDirectory(forbidden_factory)  # type: ignore[arg-type]
    with pytest.raises(OrganizationDirectoryError, match="cycle"):
        asyncio.run(directory.replace_snapshot(cyclic))


def test_query_fails_closed_if_stored_department_graph_contains_cycle() -> None:
    async def exercise() -> None:
        engine = make_async_engine(_require_database_url())
        factory = make_async_session_factory(engine)
        directory = PostgreSQLOrganizationDirectory(factory)
        try:
            await directory.replace_snapshot(_snapshot())
            async with factory() as session:
                await session.execute(
                    text(
                        "UPDATE organization_departments SET parent_department_id = "
                        "'synthetic-leaf' WHERE department_id = 'synthetic-root'"
                    )
                )
                await session.commit()

            with pytest.raises(OrganizationDirectoryError, match="cycle"):
                await directory.list_department_subtree("synthetic-root")
        finally:
            async with factory() as session:
                await session.execute(text("DELETE FROM organization_user_memberships"))
                await session.execute(text("DELETE FROM organization_departments"))
                await session.commit()
            await engine.dispose()

    asyncio.run(exercise())
