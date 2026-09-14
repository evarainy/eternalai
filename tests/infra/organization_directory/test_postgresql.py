from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

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
from tests.api.test_work_object_dispatch import dispatch_db as dispatch_db

FETCHED_AT = datetime(2026, 8, 31, tzinfo=UTC)


def test_read_view_never_mixes_generations(dispatch_db):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy import event

    from tests.api.test_work_object_dispatch import run
    from tests.infra.organization_directory.test_sync_state import snapshot
    db = dispatch_db
    old = run(db.directory.read_view())
    candidate = run(snapshot(db))
    departments = tuple(item.model_copy(update={"display_name": "Synthetic new generation"})
                        for item in candidate.departments)
    page = candidate.user_pages[0].model_copy(update={"memberships": tuple(
        member.model_copy(update={"display_name": "Synthetic new person"})
        for member in candidate.memberships
    )})
    candidate = candidate.model_copy(update={"departments": departments, "user_pages": (page,)})
    entered, release = threading.Event(), threading.Event()
    reading_thread = []
    def pause(connection, cursor, statement, parameters, context, many):
        if (threading.get_ident() in reading_thread
                and "SELECT snapshot_version,source_fetched_at" in statement):
            entered.set()
            assert release.wait(10), "concurrent publisher never released reader"
    event.listen(db.engine.sync_engine, "after_cursor_execute", pause)
    def read():
        reading_thread.append(threading.get_ident())
        return run(db.directory.read_view())
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(read)
            try:
                assert entered.wait(10), "reader never acquired its first snapshot"
                run(db.directory.replace_snapshot(candidate))
            finally:
                release.set()
            observed = future.result(timeout=10)
    finally:
        event.remove(db.engine.sync_engine, "after_cursor_execute", pause)
    assert observed.snapshot_version == old.snapshot_version
    assert observed.departments == old.departments
    assert observed.memberships == old.memberships
    current = run(db.directory.read_view())
    assert current.snapshot_version == old.snapshot_version+1
    assert current.departments == departments
    assert current.memberships == page.memberships

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
                        job_title="75",
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
    departments = base.departments + (
        OrganizationDepartment(
            department_id="synthetic-extra",
            parent_department_id="synthetic-root",
            display_name="Synthetic extra",
            subcompany_id="synthetic-subcompany-shared",
        ),
        OrganizationDepartment(
            department_id="synthetic-null",
            parent_department_id="synthetic-root",
            display_name="Synthetic null",
            subcompany_id=None,
        ),
    )
    memberships = (
        OrganizationUserMembership(
            user_id="synthetic-user",
            department_id="synthetic-child",
            organization_id="synthetic-org-shared",
            subcompany_id="synthetic-subcompany-shared",
        ),
        OrganizationUserMembership(
            user_id="synthetic-user",
            department_id="synthetic-extra",
            organization_id="synthetic-org-shared",
            subcompany_id="synthetic-subcompany-shared",
        ),
        OrganizationUserMembership(
            user_id="synthetic-user",
            department_id="synthetic-leaf",
            organization_id=None,
            subcompany_id=None,
        ),
        OrganizationUserMembership(
            user_id="synthetic-user",
            department_id="synthetic-null",
            organization_id=None,
            subcompany_id=None,
        ),
        OrganizationUserMembership(
            user_id="synthetic-user",
            department_id="synthetic-root",
            organization_id="synthetic-org-unique",
            subcompany_id="synthetic-subcompany-unique",
        ),
    )
    page = base.user_pages[0].model_copy(update={"memberships": memberships})
    return base.model_copy(
        update={
            "departments": departments,
            "user_pages": (page,),
            "authoritative_user_count_before": len(memberships),
            "authoritative_user_count_after": len(memberships),
        }
    )


def test_replaces_and_queries_complete_directory_snapshot(migrated_database_url: str) -> None:
    async def exercise() -> None:
        engine = make_async_engine(migrated_database_url)
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
            assert memberships[0].job_title == "75"
            assert await directory.get_department("synthetic-child") == _snapshot().departments[1]
            snapshot = _snapshot()
            revoked = snapshot.user_pages[0].model_copy(update={
                "memberships": (snapshot.memberships[0].model_copy(update={"job_title": None}),),
            })
            await directory.replace_snapshot(snapshot.model_copy(update={
                "user_pages": (revoked,), "fetched_at": snapshot.fetched_at + timedelta(seconds=1),
            }))
            refreshed = await directory.list_user_memberships("synthetic-user")
            assert len(refreshed) == 1 and refreshed[0].job_title is None
        finally:
            async with factory() as session:
                await session.execute(text("DELETE FROM organization_user_memberships"))
                await session.execute(text("DELETE FROM organization_departments"))
                await session.execute(text(
                    "UPDATE organization_directory_sync_state SET snapshot_version=0,"
                    "source_fetched_at=NULL,last_success_at=NULL,last_attempt_started_at=NULL,"
                    "last_attempt_finished_at=NULL,last_attempt_status='never',last_error_code=NULL"
                ))
                await session.commit()
            await engine.dispose()

    asyncio.run(exercise())


def test_list_user_memberships_returns_complete_set_across_organization_values(
    migrated_database_url: str,
) -> None:
    async def exercise() -> None:
        engine = make_async_engine(migrated_database_url)
        factory = make_async_session_factory(engine)
        directory = PostgreSQLOrganizationDirectory(factory)
        try:
            await directory.replace_snapshot(_membership_boundary_snapshot())

            memberships = await directory.list_user_memberships("synthetic-user")

            assert memberships == [
                OrganizationUserMembership(
                    user_id="synthetic-user",
                    department_id="synthetic-child",
                    organization_id="synthetic-org-shared",
                    subcompany_id="synthetic-subcompany-shared",
                ),
                OrganizationUserMembership(
                    user_id="synthetic-user",
                    department_id="synthetic-extra",
                    organization_id="synthetic-org-shared",
                    subcompany_id="synthetic-subcompany-shared",
                ),
                OrganizationUserMembership(
                    user_id="synthetic-user",
                    department_id="synthetic-leaf",
                    organization_id=None,
                    subcompany_id=None,
                ),
                OrganizationUserMembership(
                    user_id="synthetic-user",
                    department_id="synthetic-null",
                    organization_id=None,
                    subcompany_id=None,
                ),
                OrganizationUserMembership(
                    user_id="synthetic-user",
                    department_id="synthetic-root",
                    organization_id="synthetic-org-unique",
                    subcompany_id="synthetic-subcompany-unique",
                ),
            ]
        finally:
            async with factory() as session:
                await session.execute(text("DELETE FROM organization_user_memberships"))
                await session.execute(text("DELETE FROM organization_departments"))
                await session.execute(text(
                    "UPDATE organization_directory_sync_state SET snapshot_version=0,"
                    "source_fetched_at=NULL,last_success_at=NULL,last_attempt_started_at=NULL,"
                    "last_attempt_finished_at=NULL,last_attempt_status='never',last_error_code=NULL"
                ))
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


def test_query_fails_closed_if_stored_department_graph_contains_cycle(
    migrated_database_url: str,
) -> None:
    async def exercise() -> None:
        engine = make_async_engine(migrated_database_url)
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
                await session.execute(text(
                    "UPDATE organization_directory_sync_state SET snapshot_version=0,"
                    "source_fetched_at=NULL,last_success_at=NULL,last_attempt_started_at=NULL,"
                    "last_attempt_finished_at=NULL,last_attempt_status='never',last_error_code=NULL"
                ))
                await session.commit()
            await engine.dispose()

    asyncio.run(exercise())
