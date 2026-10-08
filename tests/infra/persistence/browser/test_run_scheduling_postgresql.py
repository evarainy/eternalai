"""Real SQL scheduling regressions; synthetic retained rows, no provider execution."""

from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.browser_skill.models import BrowserOwner
from app.infra.browser.composition import BrowserVerticalComponents, _OwnerScan
from app.ports.auth import AuthenticatedSessionContext, authenticated_session
from app.ports.browser_run_store import BrowserRunStoreError, RunAdmission
from app.ports.policy_guard import PolicyDecision, PolicyRequestContext
from tests.infra.persistence.browser.test_runs_postgresql import RunHarness, harness


async def _admit(h: RunHarness, request_id: str) -> RunAdmission:
    token = authenticated_session.set(
        AuthenticatedSessionContext(h.principal, h.fingerprint, h.expires),
    )
    try:
        request = await h.store.get_or_create_request(
            h.owner, request_id, b"synthetic scheduling query", processing_owner="parser",
        )
        admission = h.admission(request)
        await h.store.accept(request, admission)
        return admission
    finally:
        authenticated_session.reset(token)


async def _revoke(h: RunHarness, fingerprint: bytes) -> None:
    async with h.sessions() as session, session.begin():
        await session.execute(text(
            "INSERT INTO auth_session_revocations(token_fingerprint,expires_at)"
            " VALUES(:fingerprint,:expires)",
        ), {"fingerprint": fingerprint, "expires": h.expires})


async def _epoch(h: RunHarness, admission: RunAdmission) -> int:
    async with h.sessions() as session:
        return int((await session.execute(text(
            "SELECT worker_epoch FROM browser_runs WHERE tenant_id=:tenant AND ai_user_id=:user"
            " AND session_id=:session AND task_id=:task AND run_id=:run",
        ), {"tenant": h.tenant, "user": h.user, "session": h.owner.session_id,
            "task": admission.task_id, "run": admission.run_id})).scalar_one())


@pytest.mark.parametrize("reject", ["not_ready", "installed_failure"])
def test_same_session_readiness_rejects_before_authority_locks_without_fallback(
    migrated_database_url: str, reject: str,
) -> None:
    async def scenario() -> None:
        async with harness(migrated_database_url) as h:
            admission = await _admit(h, "same_session_readiness")
            seen = []

            async def ready(session, run):
                assert session.in_transaction()
                assert run.admission == admission
                first = (await session.execute(text("SELECT pg_backend_pid()"))).scalar_one()
                second = (await session.execute(text("SELECT pg_backend_pid()"))).scalar_one()
                assert first == second
                seen.append(session)
                if reject == "installed_failure":
                    raise RuntimeError("synthetic installed readiness failure")
                return False

            generic = AsyncMock(side_effect=AssertionError("forbidden_fallback"))
            h.store._claim_ready_session = ready
            h.store._claim_ready = generic
            before_lock = AsyncMock(wraps=h.authority.before_run_lock)
            h.authority.before_run_lock = before_lock
            token = authenticated_session.set(None)
            try:
                if reject == "installed_failure":
                    with pytest.raises(RuntimeError, match="synthetic installed readiness failure"):
                        await h.store.claim_next(h.owner, worker_id="worker")
                else:
                    assert await h.store.claim_next(h.owner, worker_id="worker") is None
                assert len(seen) == 1
                generic.assert_not_awaited()
                before_lock.assert_not_awaited()
                assert await _epoch(h, admission) == 0
            finally:
                authenticated_session.reset(token)

    asyncio.run(scenario())


def test_exact_older_run_remains_authorized_when_latest_is_revoked(
    migrated_database_url: str,
) -> None:
    async def scenario() -> None:
        async with harness(migrated_database_url) as h:
            older = await _admit(h, "older")
            h.fingerprint = hashlib.sha256((h.suffix + "new_session").encode()).digest()
            newer = await _admit(h, "newer")
            await _revoke(h, newer.auth_fingerprint)
            token = authenticated_session.set(None)
            try:
                async with h.sessions() as session, session.begin():
                    with pytest.raises(BrowserRunStoreError) as denied:
                        await h.authority.check_owner(session, h.owner)
                    assert denied.value.code == "browser_owner_authorization_invalid"
                persisted = await h.store.get(h.owner, older.task_id, older.run_id)
                assert persisted.admission == older
                claimed = await h.store.claim_next(h.owner, worker_id="worker")
                assert claimed is not None and claimed.run_id == older.run_id
                renewed = await h.store.renew(claimed)
                assert renewed.worker_epoch == 1 and renewed.state_revision > claimed.state_revision
                cancelled = await h.store.request_cancel(h.owner, older.task_id, older.run_id)
                assert cancelled.cancel_requested and not cancelled.cancel_acknowledged
                assert await _epoch(h, newer) == 0
                wrong_actor = authenticated_session.set(AuthenticatedSessionContext(
                    h.principal.model_copy(update={"ai_user_id": "other_actor"}),
                    older.auth_fingerprint, h.expires,
                ))
                try:
                    with pytest.raises(BrowserRunStoreError) as wrong_owner:
                        await h.store.get(h.owner, older.task_id, older.run_id)
                    assert wrong_owner.value.code == "browser_owner_authorization_invalid"
                finally:
                    authenticated_session.reset(wrong_actor)
            finally:
                authenticated_session.reset(token)

    asyncio.run(scenario())


def test_candidate_cursor_passes_an_entire_rejected_batch(migrated_database_url: str) -> None:
    async def scenario() -> None:
        async with harness(migrated_database_url) as h:
            denied_fingerprint = h.fingerprint
            denied = [await _admit(h, f"denied_{index}") for index in range(16)]
            h.fingerprint = hashlib.sha256((h.suffix + "valid_session").encode()).digest()
            valid = await _admit(h, "valid")
            await _revoke(h, denied_fingerprint)
            token = authenticated_session.set(None)
            try:
                assert await h.store.claim_next(h.owner, worker_id="worker") is None
                claimed = await h.store.claim_next(h.owner, worker_id="worker")
                assert claimed is not None and claimed.run_id == valid.run_id
                assert claimed.worker_epoch == 1
                assert all([await _epoch(h, item) == 0 for item in denied])
            finally:
                authenticated_session.reset(token)

    asyncio.run(scenario())


def test_busy_canonical_task_is_skipped_and_revisited(migrated_database_url: str) -> None:
    async def scenario() -> None:
        async with harness(migrated_database_url) as h:
            older = await _admit(h, "older")
            newer = await _admit(h, "newer")
            token = authenticated_session.set(None)
            try:
                async with h.sessions() as blocker, blocker.begin():
                    await blocker.execute(text(
                        "SELECT task_id FROM tasks WHERE tenant_id=:tenant AND ai_user_id=:user"
                        " AND session_id=:session AND task_id=:task FOR UPDATE",
                    ), {"tenant": h.tenant, "user": h.user, "session": h.owner.session_id,
                        "task": older.task_id})
                    claimed = await asyncio.wait_for(
                        h.store.claim_next(h.owner, worker_id="worker"), timeout=5,
                    )
                    assert claimed is not None and claimed.run_id == newer.run_id
                    assert await _epoch(h, older) == 0
                revisited = await h.store.claim_next(h.owner, worker_id="worker")
                assert revisited is not None and revisited.run_id == older.run_id
                assert revisited.worker_epoch == 1
            finally:
                authenticated_session.reset(token)

    asyncio.run(scenario())


def test_policy_service_failure_is_not_an_empty_queue(
    migrated_database_url: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unavailable(
        ai_user_id: str, capability_id: str, arguments: dict[str, Any],
        request_context: PolicyRequestContext,
    ) -> PolicyDecision:
        raise RuntimeError("synthetic_policy_service_unavailable")

    async def scenario() -> None:
        async with harness(migrated_database_url) as h:
            admission = await _admit(h, "request")
            monkeypatch.setattr(h.policy, "decide", unavailable)
            token = authenticated_session.set(None)
            try:
                with pytest.raises(RuntimeError, match="^synthetic_policy_service_unavailable$"):
                    await h.store.claim_next(h.owner, worker_id="worker")
                assert await _epoch(h, admission) == 0
            finally:
                authenticated_session.reset(token)

    asyncio.run(scenario())


def test_supervisor_owner_cycle_excludes_new_arrivals_until_wrap(
    migrated_database_url: str,
) -> None:
    """Real discovery SQL and orchestration only; worker/provider execution is excluded."""

    class DiscoveryWorker:
        def __init__(self) -> None:
            self.seen: list[BrowserOwner] = []

        async def run_next(self, owner: BrowserOwner) -> None:
            # Recording this call proves scheduling only, never an authority grant.
            self.seen.append(owner)

    async def add_owner(h: RunHarness) -> BrowserOwner:
        h.owner = BrowserOwner(
            tenant_id=h.tenant, user_id=h.user,
            session_id=h.binder.bind(h.principal, uuid4().hex),
        )
        async with h.sessions() as session, session.begin():
            await session.execute(text(
                "INSERT INTO sessions(tenant_id,session_id) VALUES(:tenant,:session)",
            ), {"tenant": h.tenant, "session": h.owner.session_id})
        await _admit(h, "supervisor")
        return h.owner

    async def scenario() -> None:
        async with harness(migrated_database_url) as h:
            initial = {await add_owner(h) for _ in range(3)}
            worker = DiscoveryWorker()
            # Supply only the supervisor's real discovery dependencies. This does
            # not instantiate or claim to test publication/provider composition.
            components = cast(BrowserVerticalComponents, SimpleNamespace(
                _sessions=h.sessions, _tenant_id=h.tenant,
                _seed=SimpleNamespace(digest=h.publication_digest.hex()),
                _owner_scan=_OwnerScan(), worker=worker, runs=h.store,
            ))
            assert await BrowserVerticalComponents.run_ready(components, maximum_owners=2) == ()
            first = set(worker.seen)
            assert len(first) == 2 and first < initial
            newcomer = await add_owner(h)
            worker.seen.clear()
            assert await BrowserVerticalComponents.run_ready(components, maximum_owners=2) == ()
            assert set(worker.seen) == initial - first
            assert newcomer not in worker.seen
            # The next finite cycle includes all four owners, with no repetition
            # of the first two owners starving the remaining owner/newcomer.
            worker.seen.clear()
            await BrowserVerticalComponents.run_ready(components, maximum_owners=2)
            await BrowserVerticalComponents.run_ready(components, maximum_owners=2)
            assert len(worker.seen) == 4
            assert set(worker.seen) == initial | {newcomer}

    asyncio.run(scenario())
