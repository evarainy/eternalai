"""Nonlocking hint SQL and exact reservation predicates; not a live DB claim."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from app.ports.browser_store import BrowserLeaseError
from tests.browser_skill.test_runtime import persisted_run
from tests.infra.persistence.browser.test_leases import Harness


@pytest.mark.parametrize("busy,global_count,tenant_count,expected", [
    (False, 0, 0, True), (True, 0, 0, False), (False, 2, 0, False), (False, 0, 1, False),
])
def test_capacity_hint_uses_supplied_session_without_locks_or_writes(busy, global_count, tenant_count, expected) -> None:
    h, session, result = Harness(), Mock(), Mock()
    h.store._sessions = Mock(side_effect=AssertionError("extra_session"))
    result.mappings.return_value.one.return_value = {
        "global_limit": 2, "tenant_limit": 1, "global_count": global_count,
        "tenant_count": tenant_count, "binding_busy": busy,
    }
    session.execute = AsyncMock(return_value=result)
    assert asyncio.run(h.store._has_capacity_in_session(session, h.binding, "endpoint-a")) is expected
    h.store._sessions.assert_not_called()
    assert session.execute.await_count == 1
    sql = str(session.execute.await_args.args[0])
    assert "SELECT" in sql and "capacity_held" in sql
    assert all(word not in sql for word in ("FOR UPDATE", "advisory", "INSERT", "UPDATE ", "DELETE"))
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


def test_missing_quota_fails_without_trying_to_reserve() -> None:
    h, session, result = Harness(), Mock(), Mock()
    result.mappings.return_value.one.return_value = {
        "global_limit": None, "tenant_limit": 1, "global_count": 0,
        "tenant_count": 0, "binding_busy": False,
    }
    session.execute = AsyncMock(return_value=result)
    with pytest.raises(BrowserLeaseError) as caught:
        asyncio.run(h.store._has_capacity_in_session(session, h.binding, "endpoint-a"))
    assert caught.value.code == "browser_capacity_unconfigured"
    assert h.db.rows == {}


@pytest.mark.parametrize("attached", [True, False])
def test_reservation_hint_carries_all_exact_authority_fields(attached) -> None:
    h, session, result = Harness(), Mock(), Mock()
    run = persisted_run(phase="running")
    if not attached:
        from dataclasses import replace
        run = replace(run, lease_epoch=None, provider_key=None)
    h.store._sessions = Mock(side_effect=AssertionError("extra_session"))
    result.scalar_one.return_value = True
    session.execute = AsyncMock(return_value=result)
    assert asyncio.run(h.store._has_run_lease_in_session(session, run)) is True
    sql, params = session.execute.await_args.args
    assert params == {
        "tenant_id": run.owner.tenant_id, "ai_user_id": run.owner.user_id,
        "target_system": run.admission.target_system, "binding_id": run.admission.binding_id,
        "binding_revision": run.admission.binding_revision, "session_id": run.owner.session_id,
        "run_id": run.run_id, "fingerprint": run.admission.auth_fingerprint,
        "expires_at": run.admission.auth_expires_at,
        **({"lease_epoch": run.lease_epoch, "provider_key": run.provider_key} if attached else {}),
    }
    assert "authorization_revision IS NULL" in str(sql)
    assert "capacity_held AND state IN ('held','quarantined')" in str(sql)
    assert "FOR UPDATE" not in str(sql) and "advisory" not in str(sql)
    assert session.execute.await_count == 1
    h.store._sessions.assert_not_called()
