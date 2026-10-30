"""Synthetic exact-context and retention regressions; no real OA login proof."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.browser_skill.models import BrowserOwner
from app.infra.browser.d_form_execution import DFormRuntime, _State
from app.infra.browser.d_form_owned_browser import OwnedDFormBrowser, OwnedOALoginContract
from app.ports.browser_read_execution import BrowserReadExecutionError
from app.ports.browser_store import BrowserLeaseClaim
from app.ports.credential_vault import BrowserAuthFact, BrowserBindingFact


def contract():
    return OwnedOALoginContract(
        login_path="/synthetic-login", login_id="#synthetic-user",
        password="#synthetic-password", submit="#synthetic-submit",
        stops={name: "#synthetic-" + name for name in ("captcha", "mfa", "sso", "warning")},
    )


def owned(tmp_path):
    return OwnedDFormBrowser(
        chromium=tmp_path / "chromium.exe", root=tmp_path / "runs",
        origin="https://synthetic.invalid", contract=contract(),
    )


@pytest.mark.parametrize("path", ["//other.invalid/x", "https://other.invalid/x",
                                  "/\\other.invalid", "/login?redirect=x", "/x\n"])
def test_login_navigation_cannot_replace_origin(path):
    with pytest.raises(ValueError, match="browser_login_contract_invalid"):
        replace(contract(), login_path=path)


def test_stop_contract_accepts_optional_subset_and_is_immutable():
    value = replace(contract(), stops={"captcha": "#synthetic-captcha"})
    assert dict(value.stops) == {"captcha": "#synthetic-captcha"}
    with pytest.raises(TypeError):
        value.stops["mfa"] = "#replacement"


@pytest.mark.parametrize("closed", [True, False])
def test_close_owned_terminates_own_job_and_refuses_without_closed_handles(tmp_path, closed):
    async def exercise():
        owner = owned(tmp_path)
        owner.job = Mock(job_ref="synthetic-job")
        owner.job.close_handles.return_value = closed
        if closed:
            await owner.close_owned()
        else:
            with pytest.raises(BrowserReadExecutionError):
                await owner.close_owned()
        owner.job.stop_and_wait.assert_called_once()
        assert owner.owned_resource_cleanup_proven is closed
    asyncio.run(exercise())


def test_close_owned_without_created_job_has_nothing_to_terminate(tmp_path):
    owner = owned(tmp_path)
    asyncio.run(owner.close_owned())
    assert owner.owned_resource_cleanup_proven and owner.closed_job_identity is None


def state_runtime(tmp_path):
    owner = BrowserOwner(tenant_id="synthetic-tenant", user_id="synthetic-user",
                         session_id="sid_v1.synthetic.signature")
    binding = BrowserBindingFact(owner.tenant_id, owner.user_id, "oa", "synthetic-binding", 3,
                                 bytes(range(32)))
    auth = BrowserAuthFact(owner, None, bytes(range(32)),
                           datetime.now(UTC) + timedelta(minutes=5), "synthetic-run",
                           "verified-session-v1")
    claim = BrowserLeaseClaim(auth, binding, 2, 1, "synthetic-holder", "synthetic-operation",
                              "d_form_fill_stop", auth.expires_at)
    run = SimpleNamespace(run_id="synthetic-run", task_id="synthetic-task", owner=owner,
                          admission=object(), worker_epoch=1, lease_epoch=2, status="completed")
    state = _State(run, binding, b"synthetic-resource",
                   tmp_path / "receipt.json", "synthetic-event")
    state.claim = claim
    state.owned_browser = SimpleNamespace(
        attachment_cleanup_proven=True, owned_resource_cleanup_proven=False,
        close_owned=AsyncMock(), closed_job_identity={"job": "synthetic-job"},
    )
    runtime = object.__new__(DFormRuntime)
    runtime._states = {run.run_id: state}
    runtime._exited = Mock(return_value=True)
    runtime._cleanup_authorize = AsyncMock()
    runtime.options = SimpleNamespace(proof_context=Mock())
    runtime.options.proof_context.digest.return_value = b"synthetic-release-proof"
    runtime._leases = SimpleNamespace(
        quarantine=AsyncMock(return_value=replace(claim, lease_revision=2)),
        cleanup=AsyncMock(return_value=True),
    )
    return runtime, state, run


def test_portal_stop_timeout_keeps_cleanup_responsibility_and_quarantines(tmp_path, monkeypatch):
    from app.infra.browser.d_portal_flow import PortalFlow

    async def exercise():
        owner = owned(tmp_path)
        owner.native = {
            "pid": 1, "created": 1.0, "executable": "synthetic-chromium",
            "profile": "synthetic-profile", "profile_root": "synthetic-root",
            "port": 1, "context_id": "synthetic-context", "target_id": "synthetic-target",
            "host": "127.0.0.1", "scheme": "https",
        }
        driver = SimpleNamespace(stop=AsyncMock(side_effect=TimeoutError))

        async def connect(portal):
            portal.pw = driver

        monkeypatch.setattr(PortalFlow, "connect", connect)
        monkeypatch.setattr(PortalFlow, "prepare_page", AsyncMock())
        monkeypatch.setattr(owner, "protect_context", AsyncMock(
            return_value=SimpleNamespace(detach=AsyncMock()),
        ))
        monkeypatch.setattr(owner, "enter_portal_once", AsyncMock())
        with pytest.raises(BrowserReadExecutionError):
            await owner.prepare_portal(AsyncMock())
        assert owner.preparation_error == "CLEANUP_FAILED"
        assert owner.playwright is None and owner.portal.pw is driver
        assert not owner.attachment_cleanup_proven
        driver.stop.assert_awaited_once()

        runtime, state, run = state_runtime(tmp_path)
        state.owned_browser = owner
        await runtime._stop(state)
        assert driver.stop.await_count == 2
        assert not owner.attachment_cleanup_proven
        owner.job = Mock(job_ref="synthetic-job")
        owner.job.close_handles.return_value = True
        assert await runtime.cleanup(run) == "quarantined"
        assert owner.owned_resource_cleanup_proven and not state.cleanup_proven
        assert driver.stop.await_count == 3
        runtime._leases.cleanup.assert_not_awaited()
        runtime._leases.quarantine.assert_awaited_once()

        # Only an actual successful stop of that same driver restores the proof.
        driver.stop.side_effect = None
        await owner.stop_automation()
        assert driver.stop.await_count == 4 and owner.attachment_cleanup_proven
        assert await runtime.cleanup(run) == "released"
        assert state.released and state.cleanup_proven
        runtime._leases.cleanup.assert_awaited_once()

    asyncio.run(exercise())


def test_attachment_exit_attempts_owned_close_but_quarantines_without_proof(tmp_path):
    async def exercise():
        runtime, state, run = state_runtime(tmp_path)
        assert await runtime.cleanup(run) == "quarantined"
        assert state.attachment_cleanup_proven and not state.cleanup_proven
        assert state.claim.lease_revision == 2
        assert await runtime.cleanup(run) == "quarantined"
        runtime._leases.quarantine.assert_awaited_once()
        runtime._leases.cleanup.assert_not_awaited()
        assert state.owned_browser.close_owned.await_count == 2
        assert runtime._cleanup_authorize.await_count == 4
        assert state.owned_browser.owned_resource_cleanup_proven is False
    asyncio.run(exercise())


def test_explicit_close_requires_current_independent_authority_before_job_action(tmp_path):
    async def exercise():
        runtime, state, run = state_runtime(tmp_path)
        runtime._cleanup_authorize.side_effect = BrowserReadExecutionError("denied")
        with pytest.raises(BrowserReadExecutionError):
            await runtime.close_owned_browser(run)
        state.owned_browser.close_owned.assert_not_awaited()
        runtime._leases.cleanup.assert_not_awaited()
    asyncio.run(exercise())


def test_explicit_close_releases_only_after_owned_and_attachment_proof(tmp_path):
    async def exercise():
        runtime, state, run = state_runtime(tmp_path)
        order = []
        async def authorize(_run):
            order.append("authorize")
        async def close():
            order.append("close-job")
            state.owned_browser.owned_resource_cleanup_proven = True
        async def release(_claim, evidence):
            assert state.attachment_cleanup_proven and state.owned_resource_cleanup_proven
            assert evidence == b"synthetic-release-proof"
            order.append("release")
            return True
        runtime._cleanup_authorize.side_effect = authorize
        state.owned_browser.close_owned.side_effect = close
        runtime._leases.cleanup.side_effect = release
        assert await runtime.close_owned_browser(run) == "released"
        assert order == ["authorize", "close-job", "authorize", "release"]
        assert state.released and state.cleanup_proven
    asyncio.run(exercise())
