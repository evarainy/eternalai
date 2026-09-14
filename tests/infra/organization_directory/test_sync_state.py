"""Real PostgreSQL locks, publication rollback and ordinary-tick recovery."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import event, text

from app.infra.organization_directory.sync_state import PostgreSQLOrganizationDirectorySync
from app.organization_directory_sync import OrganizationDirectorySyncService
from app.ports.organization_directory import (
    OrganizationDirectoryError,
    OrganizationDirectoryPage,
    OrganizationDirectorySnapshot,
)
from app.ports.organization_directory_sync import DirectorySourceError
from tests.api.test_work_object_dispatch import dispatch_db as dispatch_db
from tests.api.test_work_object_dispatch import run


async def snapshot(db):
    view = await db.directory.read_view()
    state = await PostgreSQLOrganizationDirectorySync(db.factory).read_status()
    return OrganizationDirectorySnapshot(
        departments=view.departments,
        user_pages=(
            OrganizationDirectoryPage(
                current_page=1, next_page=None, is_end=True, memberships=view.memberships
            ),
        ),
        authoritative_user_count_before=len(view.memberships),
        authoritative_user_count_after=len(view.memberships),
        is_complete=True,
        fetched_at=state.observed_at,
    )


def test_source_fetch_has_no_open_transaction_and_only_one_worker_publishes(dispatch_db):
    from app.infra.organization_directory.reader import read_directory_snapshot
    db = dispatch_db
    async def exercise():
        candidate = await snapshot(db)
        db.execute(
            "UPDATE organization_directory_sync_state SET snapshot_version=0,"
            "source_fetched_at=NULL,"
            "last_success_at=NULL,last_attempt_started_at=NULL,last_attempt_finished_at=NULL,"
            "last_attempt_status='never',last_error_code=NULL"
        )
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        class Source:
            async def fetch_departments(self):
                calls.append(True)
                entered.set()
                await release.wait()
                return candidate.departments
            async def fetch_authoritative_user_count(self):
                return len(candidate.memberships)
            async def fetch_user_page(self, current_page):
                assert current_page == 1
                return candidate.user_pages[0]
        @asynccontextmanager
        async def opener():
            yield Source()
        async def reader(source, fetched_at):
            return await read_directory_snapshot(source=source, fetched_at=fetched_at)
        sync = PostgreSQLOrganizationDirectorySync(db.factory)
        service = OrganizationDirectorySyncService(store=sync, reader=reader, source_opener=opener)
        first = asyncio.create_task(service.run_due())
        await entered.wait()
        try:
            assert await service.run_due() is False
            with db.sql.connect() as connection:
                states = connection.execute(text(
                    "SELECT a.state FROM pg_stat_activity a JOIN pg_locks l ON a.pid=l.pid "
                    "WHERE l.locktype='advisory' AND l.classid=73142 AND l.objid=1 AND l.granted"
                )).scalars().all()
            assert states == ["idle"]
        finally:
            release.set()
        assert await first is True
        assert calls == [True]
        assert (await sync.read_status()).snapshot_version == 1
        assert await service.run_due() is False
        assert calls == [True]
    run(exercise())


def test_two_connections_allow_only_one_fetch_and_publish(dispatch_db):
    db = dispatch_db

    async def exercise():
        sync = PostgreSQLOrganizationDirectorySync(db.factory)
        before = await sync.read_status()
        candidate = await snapshot(db)
        async with sync.try_acquire() as first:
            assert first is not None
            async with sync.try_acquire() as second:
                assert second is None
            untouched = await sync.read_status()
            assert untouched.last_attempt_started_at == before.last_attempt_started_at
            await first.start_attempt()
            await first.replace_snapshot(candidate)
        after = await sync.read_status()
        assert after.snapshot_version == before.snapshot_version + 1
        async with sync.try_acquire() as third:
            assert third is not None

    run(exercise())


def test_snapshot_and_success_metadata_commit_atomically(dispatch_db):
    db = dispatch_db

    async def exercise():
        before = await db.directory.read_view()
        candidate = await snapshot(db)

        def fail(connection, cursor, statement, parameters, context, many):
            if statement.startswith("INSERT INTO organization_user_memberships"):
                raise RuntimeError("Synthetic SQL failure")

        event.listen(db.engine.sync_engine, "before_cursor_execute", fail)
        try:
            with pytest.raises(OrganizationDirectoryError):
                await db.directory.replace_snapshot(candidate)
        finally:
            event.remove(db.engine.sync_engine, "before_cursor_execute", fail)
        after = await db.directory.read_view()
        assert after.departments == before.departments
        assert after.memberships == before.memberships
        assert after.snapshot_version == before.snapshot_version
        assert after.last_success_at == before.last_success_at
        await db.directory.replace_snapshot(await snapshot(db))
        assert (await db.directory.read_view()).snapshot_version == before.snapshot_version + 1

    run(exercise())


def test_old_snapshot_cannot_renew_freshness(dispatch_db):
    db = dispatch_db

    async def exercise():
        candidate = await snapshot(db)
        await db.directory.replace_snapshot(candidate)
        first = await db.directory.read_view()
        with pytest.raises(OrganizationDirectoryError):
            await db.directory.replace_snapshot(candidate)
        second = await db.directory.read_view()
        assert (second.snapshot_version, second.last_success_at, second.source_fetched_at) == (
            first.snapshot_version,
            first.last_success_at,
            first.source_fetched_at,
        )

    run(exercise())


def test_connection_loss_releases_lock_and_recovers_running(dispatch_db):
    db = dispatch_db

    async def exercise():
        sync = PostgreSQLOrganizationDirectorySync(db.factory)
        async with db.engine.connect() as connection:
            await connection.execute(text("SELECT pg_advisory_lock(73142,1)"))
            await connection.execute(
                text(
                    "UPDATE organization_directory_sync_state SET last_attempt_status='running',"
                    "last_attempt_started_at=clock_timestamp(),last_attempt_finished_at=NULL,last_error_code=NULL"
                )
            )
            await connection.commit()
            await connection.invalidate()
        before = await sync.read_status()

        async def unused(source, fetched_at):
            raise AssertionError("interrupted attempt must wait for retry")

        service = OrganizationDirectorySyncService(store=sync, reader=unused)
        assert await service.run_due() is False
        after = await sync.read_status()
        assert after.last_error_code == "sync_interrupted"
        assert after.last_success_at == before.last_success_at
        assert after.snapshot_version == before.snapshot_version
        assert await service.run_due() is False

    run(exercise())


@pytest.mark.parametrize("committed", [False, True])
def test_commit_response_loss_is_observed_on_next_regular_tick(
    dispatch_db, monkeypatch, caplog, committed
):
    from app.infra.organization_directory.sync_state import PostgreSQLDirectorySyncLease

    db = dispatch_db
    original = PostgreSQLDirectorySyncLease.replace_snapshot

    async def lost(self, candidate):
        if committed:
            await original(self, candidate)
        raise DirectorySourceError("storage_unavailable")

    async def exercise():
        sync = PostgreSQLOrganizationDirectorySync(db.factory)
        candidate = await snapshot(db)
        before = await sync.read_status()
        monkeypatch.setattr(PostgreSQLDirectorySyncLease, "replace_snapshot", lost)
        with pytest.raises(OrganizationDirectoryError):
            await db.directory.replace_snapshot(candidate)
        after = await sync.read_status()
        assert after.snapshot_version == before.snapshot_version + int(committed)
        assert after.last_attempt_status == ("succeeded" if committed else "running")

        async def unused(source, fetched_at):
            raise AssertionError("ordinary recovery cannot immediately fetch")

        service = OrganizationDirectorySyncService(store=sync, reader=unused)
        assert await service.run_due() is False
        observed = await sync.read_status()
        assert observed.last_attempt_status == ("succeeded" if committed else "failed")
        assert observed.last_error_code == (None if committed else "sync_interrupted")
        assert "organization_directory_sync_succeeded" not in caplog.text

    run(exercise())
