"""Synthetic Run/consumer/response fences; not a real browser login attestation."""

import asyncio
from copy import copy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import SecretStr

from app.browser_skill.models import BrowserOwner
from app.infra.browser.local_resource_lifecycle import registered_subject_digest
from app.infra.browser.oa_browser_login import RunBoundOACredentialVault
from app.ports.auth import Principal, PrincipalOrgContext
from app.ports.credential_binding import PasswordBindingCredential
from app.ports.credential_vault import BrowserAuthorizationError, BrowserBindingFact


def setup():
    owner = BrowserOwner(tenant_id="synthetic-tenant", user_id="synthetic-user",
                         session_id="sid_v1.synthetic.signature")
    deadline = datetime.now(UTC) + timedelta(minutes=10)
    admission = SimpleNamespace(binding_id="synthetic-binding", binding_revision=3,
                                target_system="oa", auth_fingerprint=bytes(range(32)),
                                auth_expires_at=deadline)
    run = SimpleNamespace(owner=owner, task_id="synthetic-task", run_id="synthetic-run",
                          admission=admission, worker_epoch=1, worker_id="synthetic-worker",
                          lease_epoch=2, status="running", phase="acquiring",
                          worker_deadline=deadline, cancel_requested=False,
                          cancel_acknowledged=False, cleanup="pending", effect="not_sent",
                          verification=None)
    canonical = copy(run)
    binding = BrowserBindingFact(owner.tenant_id, owner.user_id, "oa", "synthetic-binding", 3,
                                 registered_subject_digest("oa", "synthetic-subject"))
    runs, auth, reader, store = Mock(), Mock(), Mock(), Mock()
    runs.get = AsyncMock(return_value=canonical)
    auth.check_current = AsyncMock()
    auth.input.return_value = SimpleNamespace(principal=Principal(
        ai_user_id=owner.user_id, display_name="Synthetic", roles=(),
        org_ctx=PrincipalOrgContext(tenant_id=owner.tenant_id,
                                   directory_user_id="synthetic-subject"),
    ))
    reader.check_binding = AsyncMock()
    material = PasswordBindingCredential(login_id=SecretStr("synthetic-login"),
                                         password=SecretStr("synthetic-password"))
    store.load_for_browser = AsyncMock(return_value=material)
    bound = RunBoundOACredentialVault(run=run, binding=binding, origin="https://synthetic.invalid",
                                    runs=runs, current_auth=auth,
                                    binding_reader=reader, credential_store=store,
                                    check_lease=AsyncMock())
    return bound, run, canonical, binding, runs, auth, reader, store, material


def test_login_consumer_is_once_and_only_uses_exact_run_browser_login_purpose():
    async def exercise():
        bound, run, _, binding, runs, _, _, store, material = setup()
        consumer = AsyncMock()
        await bound.consume_login(consumer)
        store.load_for_browser.assert_awaited_once_with(binding, "browser_login")
        consumer.assert_awaited_once_with(material)
        for call in runs.get.await_args_list:
            assert call.args == (run.owner, run.task_id, run.run_id)
        with pytest.raises(BrowserAuthorizationError) as error:
            await bound.consume_login(consumer)
        assert error.value.code == "browser_credential_grant_invalid"
        assert store.load_for_browser.await_count == 1

    asyncio.run(exercise())


@pytest.mark.parametrize("field,value", [
    ("cancel_requested", True), ("worker_epoch", 2), ("lease_epoch", 3),
    ("cleanup", "released"), ("status", "completed"), ("effect", "acknowledged"),
])
def test_stale_canonical_run_stops_before_secret_io(field, value):
    async def exercise():
        bound, _, canonical, _, _, _, _, store, _ = setup()
        setattr(canonical, field, value)
        consumer = AsyncMock()
        with pytest.raises(BrowserAuthorizationError) as error:
            await bound.consume_login(consumer)
        assert error.value.code == "browser_login_run_stale"
        store.load_for_browser.assert_not_awaited()
        consumer.assert_not_awaited()

    asyncio.run(exercise())


def test_cancel_during_secret_load_stops_before_private_consumer():
    async def exercise():
        bound, _, canonical, _, _, _, _, store, material = setup()

        async def load(_binding, _purpose):
            canonical.cancel_requested = True
            return material

        store.load_for_browser.side_effect = load
        consumer = AsyncMock()
        with pytest.raises(BrowserAuthorizationError) as error:
            await bound.consume_login(consumer)
        assert error.value.code == "browser_login_run_stale"
        store.load_for_browser.assert_awaited_once()
        consumer.assert_not_awaited()

    asyncio.run(exercise())


@pytest.mark.parametrize("fault", ["none", "other_frame", "other_origin", "subject", "rejected"])
def test_login_response_requires_own_frame_origin_success_and_server_subject(fault):
    async def exercise():
        bound, _, _, _, _, _, _, _, _ = setup()
        page = Mock()
        page.is_closed.return_value = False
        frame = page.main_frame
        frame.page = page
        response = Mock()
        response.request.frame = Mock() if fault == "other_frame" else frame
        response.request.method = "POST"
        response.status = 200
        response.url = ("https://other.invalid" if fault == "other_origin" else
                        "https://synthetic.invalid") + "/api/hrm/login/checkLogin"
        response.json = AsyncMock(return_value={
            "msgcode": 0, "loginstatus": fault != "rejected",
            "userid": "other-subject" if fault == "subject" else "synthetic-subject",
        })
        if fault == "none":
            assert await bound.verify_login_response(
                page=page, response=response,
            ) is None
        else:
            with pytest.raises(BrowserAuthorizationError) as error:
                await bound.verify_login_response(
                    page=page, response=response,
                )
            assert error.value.code == (
                "browser_login_subject_mismatch" if fault == "subject" else
                "browser_login_response_invalid"
            )
            if fault in {"other_frame", "other_origin"}:
                response.json.assert_not_awaited()

    asyncio.run(exercise())


@pytest.mark.parametrize("expired_after_load", [False, True])
def test_unchanged_run_invalid_exact_lease_stops_secret_io_or_consumer(expired_after_load):
    async def exercise():
        bound, _, _, _, _, _, _, store, _ = setup()
        async def lease():
            if not expired_after_load or store.load_for_browser.await_count:
                raise BrowserAuthorizationError("browser_login_lease_stale")
        bound._check_lease = lease
        consumer = AsyncMock()
        with pytest.raises(BrowserAuthorizationError) as error:
            await bound.consume_login(consumer)
        assert error.value.code == "browser_login_lease_stale"
        assert store.load_for_browser.await_count == (1 if expired_after_load else 0)
        consumer.assert_not_awaited()
    asyncio.run(exercise())


def test_production_lease_check_renews_runtime_current_claim_and_saves_revision(tmp_path):
    from dataclasses import replace

    from app.infra.browser.d_form_execution import DFormRuntime, _State
    from app.ports.browser_store import BrowserLeaseClaim

    async def exercise():
        bound, run, _, binding, _, _, _, _, _ = setup()
        claim = BrowserLeaseClaim(
            bound._fact(run), binding, run.lease_epoch, 1, "synthetic-holder",
            "synthetic-operation", "d_form_fill_stop", run.admission.auth_expires_at,
        )
        state = _State(run, binding, None, tmp_path / "unused.json", "synthetic-event")
        state.claim = claim
        runtime = object.__new__(DFormRuntime)
        runtime._states = {run.run_id: state}
        runtime.options = SimpleNamespace(provider_key="d_form_fill_stop")
        calls = []
        async def renew(current, *, ttl_seconds):
            assert ttl_seconds == 120
            calls.append(current)
            return replace(current, lease_revision=current.lease_revision + 1)
        runtime._leases = SimpleNamespace(renew=renew)
        await runtime.check_login_lease(run)
        await runtime.check_login_lease(run)
        assert [current.lease_revision for current in calls] == [1, 2]
        assert state.claim.lease_revision == 3
    asyncio.run(exercise())
