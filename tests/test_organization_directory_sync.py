from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest

from app.organization_directory_sync import (
    OrganizationDirectoryScheduler,
    OrganizationDirectorySyncService,
)
from app.ports.organization_directory_sync import (
    DirectorySourceError,
    OrganizationDirectorySyncStatus,
)
from tests.infra.organization_directory.test_postgresql import _snapshot

NOW = datetime(2026, 9, 14, tzinfo=UTC)


class State:
    def __init__(self):
        self.now = NOW
        self.value = OrganizationDirectorySyncStatus(
            snapshot_version=0,
            source_fetched_at=None,
            last_success_at=None,
            last_attempt_started_at=None,
            last_attempt_finished_at=None,
            last_attempt_status="never",
            last_error_code=None,
            observed_at=NOW,
        )
        self.locked = False
        self.publishes = 0
        self.started = 0
        self.failure = None

    async def read_status(self):
        return self.value.model_copy(update={"observed_at": self.now})

    @asynccontextmanager
    async def try_acquire(self):
        if self.locked:
            yield None
            return
        self.locked = True
        try:
            yield self
        finally:
            self.locked = False

    async def start_attempt(self):
        self.started += 1
        self.value = self.value.model_copy(
            update={
                "last_attempt_status": "running",
                "last_attempt_started_at": self.now,
                "last_attempt_finished_at": None,
                "last_error_code": None,
            }
        )
        return self.now

    async def mark_failed(self, code):
        self.value = self.value.model_copy(
            update={
                "last_attempt_status": "failed",
                "last_attempt_finished_at": self.now,
                "last_error_code": code,
            }
        )

    async def replace_snapshot(self, snapshot):
        if self.failure:
            raise DirectorySourceError(self.failure)
        self.publishes += 1
        self.value = self.value.model_copy(
            update={
                "snapshot_version": self.value.snapshot_version + 1,
                "source_fetched_at": snapshot.fetched_at,
                "last_success_at": self.now,
                "last_attempt_status": "succeeded",
                "last_attempt_finished_at": self.now,
            }
        )


@asynccontextmanager
async def opener():
    yield object()


async def reader(source, fetched_at):
    return _snapshot().model_copy(update={"fetched_at": fetched_at})


def service(state, **kwargs):
    return OrganizationDirectorySyncService(
        store=state, reader=reader, source_opener=opener, **kwargs
    )


def test_daily_due_persists_across_restart():
    async def exercise():
        state = State()
        assert await service(state).run_due() is True
        state.now += timedelta(seconds=86399)
        assert await service(state).run_due() is False
        assert state.publishes == 1
        state.now += timedelta(seconds=1)
        assert await service(state).run_due() is True
        assert state.publishes == 2
        assert state.started == 2

    asyncio.run(exercise())


def test_failure_retries_without_extending_success():
    async def exercise():
        state = State()
        await service(state).run_due()
        first = state.value.last_success_at
        state.now += timedelta(days=1)
        broken = OrganizationDirectorySyncService(store=state, reader=reader)
        await broken.run_due()
        assert state.value.last_success_at == first
        assert state.value.last_error_code == "source_unconfigured"
        state.now += timedelta(seconds=3599)
        assert await broken.run_due() is False
        assert state.started == 2
        state.now += timedelta(seconds=1)
        assert await broken.run_due() is True
        assert state.started == 3
        assert state.value.last_success_at == first

    asyncio.run(exercise())


def test_logs_and_errors_never_contain_person_or_credentials(caplog):
    async def broken_reader(source, fetched_at):
        raise RuntimeError("Synthetic-name Synthetic-cookie Synthetic-user")

    state = State()
    instance = OrganizationDirectorySyncService(
        store=state, reader=broken_reader, source_opener=opener
    )
    asyncio.run(instance.run_due())
    assert "organization_directory_sync_failed code=source_unavailable" in caplog.text
    assert all(
        marker not in caplog.text
        for marker in ("Synthetic-name", "Synthetic-cookie", "Synthetic-user")
    )
    assert state.value.last_error_code == "source_unavailable"


def test_unconfirmed_publication_logs_failure_without_overwriting_running(caplog):
    state = State()
    state.failure = "storage_unavailable"
    asyncio.run(service(state).run_due())
    assert state.value.last_attempt_status == "running"
    assert state.value.snapshot_version == 0
    assert state.value.last_error_code is None
    assert "organization_directory_sync_failed code=storage_unavailable" in caplog.text
    assert "organization_directory_sync_succeeded" not in caplog.text


def test_stop_before_publish_and_drain_started_commit():
    async def exercise():
        state = State()
        entered, release = asyncio.Event(), asyncio.Event()

        async def waiting_reader(source, fetched_at):
            entered.set()
            await release.wait()
            return await reader(source, fetched_at)

        instance = OrganizationDirectorySyncService(
            store=state, reader=waiting_reader, source_opener=opener
        )
        scheduler = OrganizationDirectoryScheduler(instance)
        await scheduler.start()
        await entered.wait()
        stopping = asyncio.create_task(scheduler.stop())
        await asyncio.sleep(0)
        assert not stopping.done()
        release.set()
        await stopping
        assert state.publishes == 0
        assert state.value.last_error_code == "sync_interrupted"
        assert state.locked is False

        state = State()
        entered.clear()
        release.clear()
        original = state.replace_snapshot

        async def waiting_publish(snapshot):
            entered.set()
            await release.wait()
            await original(snapshot)

        state.replace_snapshot = waiting_publish
        scheduler = OrganizationDirectoryScheduler(service(state))
        await scheduler.start()
        await entered.wait()
        stopping = asyncio.create_task(scheduler.stop())
        await asyncio.sleep(0)
        assert not stopping.done()
        release.set()
        await stopping
        assert state.publishes == 1
        assert state.value.last_attempt_status == "succeeded"
        assert state.locked is False

    asyncio.run(exercise())


def test_async_source_deadline_blocks_publication(monkeypatch):
    import app.organization_directory_sync as module

    assert module.SOURCE_DEADLINE_S == 900
    observed = []
    actual_timeout = asyncio.timeout

    def immediate(seconds):
        observed.append(seconds)
        return actual_timeout(0)

    monkeypatch.setattr(module.asyncio, "timeout", immediate)

    async def hanging(source, fetched_at):
        await asyncio.Event().wait()

    state = State()
    instance = OrganizationDirectorySyncService(store=state, reader=hanging, source_opener=opener)
    asyncio.run(instance.run_due())
    assert observed == [900]
    assert state.value.last_error_code == "source_timeout"
    assert state.publishes == 0 and state.value.last_success_at is None


@pytest.mark.parametrize("age,healthy", [(899, True), (900, True), (901, False), (-1, False)])
def test_running_diagnostic_is_bounded_but_does_not_change_state(age, healthy):
    async def exercise():
        state = State()
        await service(state).run_due()
        await state.start_attempt()
        state.value = state.value.model_copy(
            update={"last_attempt_started_at": state.now - timedelta(seconds=age)}
        )
        assert await service(state).diagnostic() is healthy
        assert state.value.last_attempt_status == "running"

    asyncio.run(exercise())
