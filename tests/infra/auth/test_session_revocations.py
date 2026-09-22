"""Revocation persistence and real transaction failure boundaries."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session

from app.infra.auth.session_revocations import PostgreSQLSessionRevocationStore
from app.ports.auth import SessionRevocationStoreError
from tests.api.test_work_object_dispatch import dispatch_db as dispatch_db
from tests.api.test_work_object_dispatch import run


def fingerprint() -> bytes:
    return uuid4().bytes + uuid4().bytes


def test_committed_revocation_is_visible_to_another_instance(dispatch_db):
    async def scenario():
        first = PostgreSQLSessionRevocationStore(dispatch_db.factory)
        second_factory = async_sessionmaker(dispatch_db.engine, expire_on_commit=False)
        second = PostgreSQLSessionRevocationStore(second_factory)
        fp = fingerprint()
        assert not await second.is_revoked(fp)
        await first.revoke(fp, expires_at=datetime.now(UTC) + timedelta(hours=1))
        assert await second.is_revoked(fp)
        assert await PostgreSQLSessionRevocationStore(second_factory).is_revoked(fp)
        assert not await second.is_revoked(fingerprint())

    run(scenario())


def test_duplicate_concurrent_revocations_keep_one_row(dispatch_db):
    async def scenario():
        store = PostgreSQLSessionRevocationStore(dispatch_db.factory)
        fp, expiry = fingerprint(), datetime.now(UTC) + timedelta(hours=1)
        await asyncio.gather(*(store.revoke(fp, expires_at=expiry) for _ in range(2)))
        async with dispatch_db.factory() as session:
            result = await session.execute(
                text("SELECT count(*), min(expires_at) FROM auth_session_revocations")
            )
            assert result.one() == (1, expiry)

    run(scenario())


@pytest.mark.parametrize("phase", ["read", "write", "commit"])
def test_write_or_commit_failure_never_reports_completion(dispatch_db, phase):
    class FaultSyncSession(Session):
        pass

    @event.listens_for(FaultSyncSession, "before_commit")
    def fail_commit(_session):
        if phase == "commit":
            raise RuntimeError("synthetic-commit-before-durability")

    class FaultSession(AsyncSession):
        sync_session_class = FaultSyncSession

        async def execute(self, *args, **kwargs):
            if phase in {"read", "write"}:
                raise RuntimeError("synthetic-driver-detail")
            return await super().execute(*args, **kwargs)

    async def scenario():
        fp = fingerprint()
        faulty = PostgreSQLSessionRevocationStore(
            async_sessionmaker(dispatch_db.engine, class_=FaultSession)
        )
        with pytest.raises(SessionRevocationStoreError) as captured:
            if phase == "read":
                await faulty.is_revoked(fp)
            else:
                await faulty.revoke(fp, expires_at=datetime.now(UTC))
        assert str(captured.value) == "session revocation storage is unavailable"
        assert captured.value.__context__ is None
        assert captured.value.__cause__ is None
        assert not await PostgreSQLSessionRevocationStore(dispatch_db.factory).is_revoked(fp)

    run(scenario())


def test_commit_ack_loss_preserves_revocation_and_retry_is_idempotent(dispatch_db):
    class AckLossSession(AsyncSession):
        async def __aexit__(self, *args):
            await super().__aexit__(*args)
            raise RuntimeError("synthetic-commit-ack-lost")

    async def scenario():
        fp, expiry = fingerprint(), datetime.now(UTC)
        faulty = PostgreSQLSessionRevocationStore(
            async_sessionmaker(dispatch_db.engine, class_=AckLossSession)
        )
        with pytest.raises(SessionRevocationStoreError) as captured:
            await faulty.revoke(fp, expires_at=expiry)
        assert captured.value.__context__ is None
        healthy = PostgreSQLSessionRevocationStore(dispatch_db.factory)
        assert await healthy.is_revoked(fp)
        await healthy.revoke(fp, expires_at=expiry)
        async with dispatch_db.factory() as session:
            assert (
                await session.execute(text("SELECT count(*) FROM auth_session_revocations"))
            ).scalar_one() == 1

    run(scenario())
