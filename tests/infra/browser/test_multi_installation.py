"""Pinned canonical retries; source prepared for the later execution window."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.infra.browser.multi_installation import BrowserVerticalCollection
from app.ports.auth import authenticated_session
from app.ports.browser_chat import BrowserChatError
from app.ports.browser_run_store import BrowserRunStoreError, CanonicalRequest
from tests.browser_skill.test_submission import _authentication, _service, _write_run


@pytest.mark.parametrize("outcome", ["accepted", "conflict", "revoked", "new"])
def test_retry_checks_pinned_version_before_active_and_never_resubmits(outcome) -> None:
    async def scenario() -> None:
        service, _, _, principal = _service()
        token = _authentication(principal)
        try:
            run = _write_run(service._owner(principal, "session_1"))
            canonical = CanonicalRequest(
                owner=run.owner, task_id=run.task_id, trace_id="trace_1",
                client_request_id="request_1", request_digest_key_id="fixture",
                request_digest=b"d" * 32, processing_owner=None, processing_deadline=None,
                parse_winner=False, task_status="running", error_code=None, run=run,
            )
            pinned = SimpleNamespace(submission=service, runs=Mock(
                lookup_request=AsyncMock(return_value=canonical),
            ))
            service.submit = AsyncMock(side_effect=AssertionError("old request resubmitted"))
            if outcome in {"conflict", "revoked"}:
                pinned.runs.lookup_request.side_effect = BrowserRunStoreError(
                    "request_key_conflict" if outcome == "conflict" else "browser_run_not_found",
                )
            result = Mock()
            result.scalars.return_value.all.return_value = (
                [] if outcome == "new" else [run.admission.publication_digest]
            )
            session = Mock(execute=AsyncMock(return_value=result))

            @asynccontextmanager
            async def sessions():
                yield session

            collection = object.__new__(BrowserVerticalCollection)
            collection._default = SimpleNamespace(
                submission=service, _tenant_id=run.owner.tenant_id, _sessions=sessions,
            )
            collection._by_digest = {run.admission.publication_digest.hex(): pinned}
            active_submission = Mock(submit=AsyncMock(return_value="new_version"))
            collection._active = AsyncMock(return_value=SimpleNamespace(
                _seed=SimpleNamespace(skill=SimpleNamespace(version="v2")),
                submission=active_submission,
            ))
            params = dict(
                principal=principal, bound_session="session_1", skill_id="skill_1",
                skill_version="v1" if outcome != "new" else "v2",
                arguments={"business_key": "CASE_1"}, client_request_id="request_1",
            )
            if outcome in {"conflict", "revoked"}:
                with pytest.raises(BrowserChatError) as caught:
                    await collection.submit(**params)
                assert caught.value.code == (
                    "request_key_conflict" if outcome == "conflict" else "browser_run_not_found"
                )
            else:
                response = await collection.submit(**params)
                if outcome == "accepted":
                    assert response.data["run_id"] == run.run_id
                    owner, request_id, semantic = pinned.runs.lookup_request.call_args.args
                    assert owner == run.owner and request_id == "request_1"
                    assert b'"skill_version":"v1"' in semantic
                else:
                    assert response == "new_version"
                    active_submission.submit.assert_awaited_once()
            if outcome == "new":
                collection._active.assert_awaited_once()
                pinned.runs.lookup_request.assert_not_awaited()
            else:
                collection._active.assert_not_awaited()
                active_submission.submit.assert_not_awaited()
            service.submit.assert_not_awaited()
        finally:
            authenticated_session.reset(token)
    asyncio.run(scenario())
