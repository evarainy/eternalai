"""Read-only canonical lookup exercises the production MAC and authority checks."""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from app.browser_skill.models import BrowserOwner
from app.infra.persistence.browser.authorization import PostgreSQLBrowserRunAuthority
from app.infra.persistence.browser.runs import PostgreSQLBrowserRunStore
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
