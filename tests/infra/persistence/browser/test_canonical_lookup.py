"""Read-only canonical lookup exercises the production MAC and authority checks."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from app.browser_skill.models import BrowserOwner
from app.infra.persistence.browser.authorization import PostgreSQLBrowserRunAuthority
from app.infra.persistence.browser.runs import _REQUEST_VERSION, PostgreSQLBrowserRunStore
from app.ports.browser_run_store import BrowserRunStoreError
from app.ports.credential_vault import BrowserAuthorizationError


@pytest.mark.parametrize("case", ["match", "changed_input", "revoked", "missing"])
def test_lookup_does_not_claim_parse_abandon_or_execute(case) -> None:
    async def scenario() -> None:
        owner = BrowserOwner(tenant_id="tenant", user_id="reader", session_id="conversation")
        session, result, authority = Mock(), Mock(), Mock()
        session.execute = AsyncMock(return_value=result)
        session.begin.return_value = MagicMock()
        authority.check_owner = AsyncMock()
        authority.check_run = AsyncMock()

        @asynccontextmanager
        async def sessions():
            yield session

        store = PostgreSQLBrowserRunStore(
            session_factory=sessions, authority=authority,
            digest_keys={"fixture": b"k" * 32}, active_digest_key_id="fixture",
        )
        semantic = b'{"skill_version":"v1","arguments":{"business_key":"CASE_1"}}'
        row = {
            "task_id": "task", "trace_id": "trace", "client_request_id": "request",
            "request_schema_version": "browser.request.v1",
            "request_digest_key_id": "fixture",
            "request_digest": store._digest(owner, "request", semantic, "fixture"),
            "processing_owner": None, "processing_deadline": None,
            "status": "running", "error_code": None,
        }
        # Obtain the durable schema from the actual producer's module constant,
        # so this test targets input MAC/authority behavior rather than a spelling.
        from app.infra.persistence.browser.runs import _REQUEST_VERSION
        row["request_schema_version"] = _REQUEST_VERSION
        result.mappings.return_value.one_or_none.return_value = None if case == "missing" else row
        run = Mock()
        store._task_run = AsyncMock(return_value=run)
        if case == "revoked":
            authority.check_run.side_effect = BrowserRunStoreError("browser_run_not_found")
        requested = semantic + b" " if case == "changed_input" else semantic
        if case in {"changed_input", "revoked"}:
            with pytest.raises(BrowserRunStoreError) as caught:
                await store.lookup_request(owner, "request", requested)
            assert caught.value.code == (
                "request_key_conflict" if case == "changed_input" else "browser_run_not_found"
            )
        else:
            canonical = await store.lookup_request(owner, "request", requested)
            if case == "missing":
                assert canonical is None
                store._task_run.assert_not_awaited()
            else:
                assert canonical.run is run and canonical.parse_winner is False
                authority.check_run.assert_awaited_once_with(session, run, "read")
        authority.check_owner.assert_awaited_once_with(session, owner)
        assert session.execute.await_count == 2
        statements = [str(call.args[0]) for call in session.execute.call_args_list]
        assert all(statement.startswith("SELECT") for statement in statements)
        assert "FOR SHARE" in statements[-1]
        assert all("UPDATE" not in statement and "INSERT" not in statement
                   for statement in statements)
    asyncio.run(scenario())


@pytest.mark.parametrize("case", [
    "expired", "no_deadline", "live", "failed", "wrong_input", "revoked", "missing",
    "run_exists", "read_only",
])
def test_pre_run_settlement_fails_only_expired_original_claim_and_never_reclaims(case):
    async def scenario() -> None:
        owner = BrowserOwner(tenant_id="tenant", user_id="reader", session_id="conversation")
        now = datetime.now(UTC)
        authority = Mock(check_owner=AsyncMock(), check_run=AsyncMock())
        session = Mock()
        session.begin.return_value = MagicMock()

        @asynccontextmanager
        async def sessions():
            yield session

        store = PostgreSQLBrowserRunStore(
            session_factory=sessions, authority=authority,
            digest_keys={"fixture": b"k" * 32}, active_digest_key_id="fixture",
        )
        semantic = b'{"skill_version":"v1","arguments":{"business_key":"CASE_1"}}'
        row = {
            "task_id": "original_task", "trace_id": "original_trace",
            "client_request_id": "request", "request_schema_version": _REQUEST_VERSION,
            "request_digest_key_id": "fixture",
            "request_digest": store._digest(owner, "request", semantic, "fixture"),
            "processing_owner": "original_parser",
            "processing_deadline": None if case == "no_deadline" else now + timedelta(
                seconds=30 if case == "live" else -30,
            ),
            "status": "failed" if case == "failed" else "created",
            "error_code": "browser_request_admission_failed" if case == "failed" else None,
        }
        failed = {**row, "status": "failed", "error_code": "browser_request_abandoned",
                  "processing_owner": None, "processing_deadline": None}

        async def execute(statement, params):
            result = Mock()
            sql = str(statement)
            if sql.startswith("SELECT * FROM tasks"):
                result.mappings.return_value.one_or_none.return_value = (
                    None if case == "missing" else row
                )
            elif sql.startswith("UPDATE tasks"):
                assert params == {"tenant_id": owner.tenant_id, "ai_user_id": owner.user_id,
                                  "session_id": owner.session_id, "task_id": "original_task"}
                result.mappings.return_value.one.return_value = failed
            else:
                assert sql.startswith("SELECT pg_advisory_xact_lock_shared")
            return result

        session.execute = AsyncMock(side_effect=execute)
        run = Mock() if case == "run_exists" else None
        store._task_run = AsyncMock(return_value=run)
        store._now = AsyncMock(return_value=now)
        if case == "revoked":
            authority.check_owner.side_effect = BrowserRunStoreError(
                "browser_owner_authorization_invalid",
            )
        if case in {"wrong_input", "revoked"}:
            with pytest.raises(BrowserRunStoreError) as caught:
                await store.lookup_request(owner, "request", semantic + b" "
                                           if case == "wrong_input" else semantic,
                                           settle_expired=True)
            assert caught.value.code == ("request_key_conflict" if case == "wrong_input"
                                         else "browser_owner_authorization_invalid")
        else:
            request = await store.lookup_request(
                owner, "request", semantic, settle_expired=case != "read_only",
            )
            if case == "missing":
                assert request is None
            else:
                assert request.task_id == "original_task" and request.parse_winner is False
                assert request.run is run
                if case in {"expired", "no_deadline"}:
                    assert request.task_status == "failed"
                    assert request.error_code == "browser_request_abandoned"
                    assert request.processing_owner is None and request.processing_deadline is None
                else:
                    assert request.task_status == row["status"]
                    assert request.processing_owner == "original_parser"
        statements = [str(call.args[0]) for call in session.execute.call_args_list]
        updates = [statement for statement in statements if statement.startswith("UPDATE")]
        assert len(updates) == (1 if case in {"expired", "no_deadline"} else 0)
        assert not any("INSERT" in statement for statement in statements)
        if updates:
            assert "status='failed'" in updates[0] and "status='created' RETURNING" in updates[0]
            assert "processing_owner=NULL,processing_deadline=NULL" in updates[0]
        selects = [statement for statement in statements
                   if statement.startswith("SELECT * FROM tasks")]
        if case != "revoked":
            assert len(selects) == 1
            assert ("FOR SHARE" if case == "read_only" else "FOR UPDATE") in selects[0]
        if case == "run_exists":
            authority.check_run.assert_awaited_once_with(session, run, "read")
        else:
            authority.check_run.assert_not_awaited()
    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["read", "claim", "advance", "finalize"])
@pytest.mark.parametrize("revoked", [False, True])
def test_pinned_run_read_keeps_live_auth_and_execution_keeps_active_requirement(action, revoked):
    async def scenario() -> None:
        auth, session, run = Mock(), Mock(), Mock()
        auth.validate = AsyncMock()
        authority = PostgreSQLBrowserRunAuthority(current_auth=auth)
        if revoked:
            auth.validate.side_effect = BrowserAuthorizationError("browser_binding_stale")
            expected = BrowserRunStoreError if action == "claim" else BrowserAuthorizationError
            with pytest.raises(expected) as caught:
                await authority._check_run_authorization(session, run, action)
            assert caught.value.code == (
                "browser_claim_candidate_ineligible" if action == "claim"
                else "browser_binding_stale"
            )
        else:
            await authority._check_run_authorization(session, run, action)
        auth.validate.assert_awaited_once_with(
            session, run.admission, require_active=action != "read",
        )
    asyncio.run(scenario())


@pytest.mark.parametrize("case", ["default", "installed", "revoked", "source_denied"])
def test_replay_source_reader_is_current_read_only_and_never_falls_back(case) -> None:
    async def scenario() -> None:
        session, authority, source = Mock(), Mock(), Mock()
        authority.check_owner = AsyncMock()
        authority.check_run = AsyncMock()

        @asynccontextmanager
        async def sessions():
            yield session

        store = PostgreSQLBrowserRunStore(
            session_factory=sessions, authority=authority,
            digest_keys={"fixture": b"k" * 32}, active_digest_key_id="fixture",
        )
        reader = AsyncMock()
        if case != "default":
            store.install_replay_source_reader(reader)
            with pytest.raises(BrowserRunStoreError) as caught:
                store.install_replay_source_reader(AsyncMock())
            assert caught.value.code == "browser_teaching_source_installation_invalid"
        if case == "revoked":
            authority.check_owner.side_effect = BrowserRunStoreError("browser_run_not_found")
        elif case == "source_denied":
            reader.side_effect = BrowserRunStoreError("browser_teaching_source_denied")
        if case in {"revoked", "source_denied"}:
            with pytest.raises(BrowserRunStoreError) as caught:
                await store._check_replay_source(session, source)
            assert caught.value.code == (
                "browser_run_not_found" if case == "revoked" else "browser_teaching_source_denied"
            )
        else:
            await store._check_replay_source(session, source)
        if case == "default":
            authority.check_run.assert_awaited_once_with(session, source, "read")
            reader.assert_not_awaited()
        else:
            authority.check_run.assert_not_awaited()
            authority.check_owner.assert_awaited_once_with(session, source.owner)
            if case == "revoked":
                reader.assert_not_awaited()
            else:
                reader.assert_awaited_once_with(session, source)
        session.execute.assert_not_called()
    asyncio.run(scenario())
