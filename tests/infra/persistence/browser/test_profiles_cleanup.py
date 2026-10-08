"""Profile store cleanup preconditions, with SQL results controlled at the seam."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.infra.persistence.browser import profiles as module
from app.ports.browser_profile_store import BrowserProfileError


def _context() -> SimpleNamespace:
    owner = SimpleNamespace(tenant_id="tenant", user_id="user", session_id="session")
    auth = SimpleNamespace(owner=owner)
    binding = SimpleNamespace(target_system="oa", binding_id="binding")
    return SimpleNamespace(claim=SimpleNamespace(auth=auth, binding=binding),
                           task_id="task", run_id="run")


@pytest.mark.asyncio
async def test_cleanup_candidate_refuses_recent_head_and_live_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(timezone.utc)
    row = {"disposition": "retired", "created_at": now - timedelta(days=3),
           "lease_epoch": 2}
    store = object.__new__(module.PostgreSQLBrowserProfileStore)
    store._retention_seconds = 86_400
    store._get_cleanup_row = AsyncMock(return_value=row)
    session = Mock()
    session.execute = AsyncMock()
    session.execute.return_value.scalar_one.return_value = now
    with pytest.raises(BrowserProfileError) as pending:
        await store._cleanup_candidate(
            session, _context(), "generation",
            {"active_generation_id": None, "updated_at": now}, None,
        )
    assert pending.value.code == "browser_profile_retention_pending"
    assert session.execute.await_count == 1

    session.execute.reset_mock()
    result_now = Mock()
    result_now.scalar_one.return_value = now
    result_runs = Mock()
    result_runs.all.return_value = [("run",)]
    session.execute.side_effect = [result_now, result_runs]
    old = now - timedelta(days=3)
    with pytest.raises(BrowserProfileError) as referenced:
        await store._cleanup_candidate(
            session, _context(), "generation",
            {"active_generation_id": None, "updated_at": old}, None,
        )
    assert referenced.value.code == "browser_profile_cleanup_referenced"
    assert session.execute.await_count == 2
    assert "profile_generation_id=:generation_id OR" in str(
        session.execute.await_args_list[1].args[0]
    )


@pytest.mark.asyncio
async def test_cleanup_candidate_never_accepts_orphan_without_transition_time() -> None:
    now = datetime.now(timezone.utc)
    store = object.__new__(module.PostgreSQLBrowserProfileStore)
    store._retention_seconds = 86_400
    store._get_cleanup_row = AsyncMock(return_value={
        "disposition": "orphan", "created_at": now - timedelta(days=3),
    })
    session = Mock()
    session.execute = AsyncMock()
    session.execute.return_value.scalar_one.return_value = now
    with pytest.raises(BrowserProfileError) as pending:
        await store._cleanup_candidate(
            session, _context(), "generation",
            {"active_generation_id": None,
             "updated_at": now - timedelta(days=3)}, None,
        )
    assert pending.value.code == "browser_profile_retention_pending"


@pytest.mark.asyncio
async def test_retired_unreferenced_candidate_is_prepared_only() -> None:
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=3)
    row = {"disposition": "retired", "created_at": old, "lease_epoch": 2}
    store = object.__new__(module.PostgreSQLBrowserProfileStore)
    store._retention_seconds = 86_400
    store._get_cleanup_row = AsyncMock(return_value=row)
    session = Mock()
    clock = Mock()
    clock.scalar_one.return_value = now
    references = Mock()
    references.all.return_value = []
    session.execute = AsyncMock(side_effect=[clock, references])
    prepared = await store._cleanup_candidate(
        session, _context(), "generation",
        {"active_generation_id": None, "updated_at": old},
        {"capacity_held": False},
    )
    assert prepared is None
    assert session.execute.await_count == 2


@pytest.mark.asyncio
async def test_cleanup_cannot_mark_retained_ciphertext_as_destroyed() -> None:
    store = object.__new__(module.PostgreSQLBrowserProfileStore)
    store._cleanup = Mock()
    store._cleanup.verify_cleanup = AsyncMock()
    with pytest.raises(BrowserProfileError) as refused:
        await store.cleanup(_context(), "generation", b"synthetic-evidence")
    assert refused.value.code == "browser_profile_ciphertext_retained"
    store._cleanup.verify_cleanup.assert_not_awaited()
