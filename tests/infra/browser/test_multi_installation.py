"""Pinned canonical retries; source prepared for the later execution window."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.infra.browser.multi_installation import BrowserVerticalCollection
from app.ports.auth import authenticated_session
from app.ports.browser_chat import BrowserChatError
from app.ports.browser_run_store import BrowserRunStoreError, CanonicalRequest
from tests.browser_skill.test_submission import _authentication, _service, _write_run


@pytest.mark.parametrize("outcome", [
    "accepted", "conflict", "revoked", "new", "abandoned", "pending",
])
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
                parse_winner=False,
                task_status="failed" if outcome == "abandoned" else "created"
                if outcome == "pending" else "running",
                error_code="browser_request_abandoned" if outcome == "abandoned" else None,
                run=None if outcome in {"abandoned", "pending"} else run,
            )
            pinned = SimpleNamespace(submission=service, runs=Mock(
                lookup_request=AsyncMock(return_value=canonical),
                get=AsyncMock(return_value=run),
            ))
            service._store = pinned.runs
            service.submit = AsyncMock(side_effect=AssertionError("old request resubmitted"))
            if outcome in {"conflict", "revoked"}:
                pinned.runs.lookup_request.side_effect = BrowserRunStoreError(
                    "request_key_conflict" if outcome == "conflict" else "browser_run_not_found",
                )
            result = Mock()
            result.scalars.return_value.all.return_value = (
                [] if outcome == "new" else [None] if outcome in {"abandoned", "pending"}
                else [run.admission.publication_digest]
            )
            session = Mock(execute=AsyncMock(return_value=result))

            @asynccontextmanager
            async def sessions():
                yield session

            collection = object.__new__(BrowserVerticalCollection)
            collection._default = SimpleNamespace(
                submission=service, _tenant_id=run.owner.tenant_id, _sessions=sessions,
                runs=pinned.runs,
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
                elif outcome in {"abandoned", "pending"}:
                    assert response.data["kind"] == (
                        "failed" if outcome == "abandoned" else "pending"
                    )
                    assert response.task_id == canonical.task_id
                    if outcome == "abandoned":
                        assert response.data["error_code"] == "browser_request_abandoned"
                    pinned.runs.get.assert_not_awaited()
                else:
                    assert response == "new_version"
                    active_submission.submit.assert_awaited_once()
            if outcome == "new":
                collection._active.assert_awaited_once()
                pinned.runs.lookup_request.assert_not_awaited()
            else:
                collection._active.assert_not_awaited()
                active_submission.submit.assert_not_awaited()
                assert pinned.runs.lookup_request.call_args.kwargs == {"settle_expired": True}
            service.submit.assert_not_awaited()
        finally:
            authenticated_session.reset(token)
    asyncio.run(scenario())


@pytest.mark.parametrize("case", [
    "queued", "sent", "revoked", "wrong_publication", "wrong_version",
])
def test_collection_canonical_retry_routes_only_unsent_exact_write_to_preparation_recovery(case):
    async def scenario() -> None:
        service, _, _, principal = _service(source_kind="registered_site")
        token = _authentication(principal)
        try:
            run = _write_run(service._owner(principal, "session_1"))
            if case != "sent":
                run = replace(run, phase="queued", worker_id=None, lease_epoch=None,
                              effect="not_sent")
            canonical = CanonicalRequest(
                owner=run.owner, task_id=run.task_id, trace_id="trace_1",
                client_request_id="request_1", request_digest_key_id="fixture",
                request_digest=b"d" * 32, processing_owner=None, processing_deadline=None,
                parse_winner=False, task_status="running", error_code=None, run=run,
            )
            manifest = SimpleNamespace(
                digest="c" * 64 if case == "wrong_publication"
                else run.admission.publication_digest.hex(),
                skill=SimpleNamespace(skill_id="skill_1", version="v2" if case == "wrong_version"
                                      else "v1"),
                effect="controlled_write", capability=SimpleNamespace(input_schema={
                    "type": "object", "properties": {"business_key": {"type": "string"}},
                    "required": ["business_key"], "additionalProperties": False,
                }),
            )
            service._publications = Mock(
                get_frozen=AsyncMock(return_value=SimpleNamespace(
                    manifest=manifest, state="active",
                )),
                assert_current=AsyncMock(),
            )
            service._ensure_write_preparation = AsyncMock()
            runs = Mock(lookup_request=AsyncMock(return_value=canonical),
                        get=AsyncMock(return_value=run))
            if case == "revoked":
                runs.get.side_effect = BrowserRunStoreError("browser_run_not_found")
            service._store = runs
            service.submit = AsyncMock(side_effect=AssertionError("canonical write readmitted"))
            lane = SimpleNamespace(submission=service, runs=runs)
            result = Mock()
            result.scalars.return_value.all.return_value = [run.admission.publication_digest]
            session = Mock(execute=AsyncMock(return_value=result))

            @asynccontextmanager
            async def sessions():
                yield session

            collection = object.__new__(BrowserVerticalCollection)
            collection._default = SimpleNamespace(
                submission=service, runs=runs, _tenant_id=run.owner.tenant_id, _sessions=sessions,
            )
            collection._by_digest = {run.admission.publication_digest.hex(): lane}
            collection._active = AsyncMock(side_effect=AssertionError("active selected for retry"))
            params = dict(principal=principal, bound_session="session_1", skill_id="skill_1",
                          skill_version="v1", arguments={"business_key": "CASE_1"},
                          client_request_id="request_1")
            if case in {"revoked", "wrong_publication", "wrong_version"}:
                with pytest.raises(BrowserChatError) as caught:
                    await collection.submit(**params)
                assert caught.value.code == ("browser_run_not_found" if case == "revoked"
                                             else "browser_publication_unavailable")
            else:
                response = await collection.submit(**params)
                assert response.data["run_id"] == run.run_id
            if case == "queued":
                service._ensure_write_preparation.assert_awaited_once_with(
                    run.owner, run.task_id, run.run_id, manifest, {"business_key": "CASE_1"},
                )
            else:
                service._ensure_write_preparation.assert_not_awaited()
            if case in {"sent", "revoked"}:
                service._publications.get_frozen.assert_not_awaited()
            service.submit.assert_not_awaited()
            collection._active.assert_not_awaited()
            assert runs.lookup_request.call_args.kwargs == {"settle_expired": True}
        finally:
            authenticated_session.reset(token)
    asyncio.run(scenario())
