"""One synthetic attempt, no live services, credential material or file writes."""

from __future__ import annotations

import asyncio
import secrets
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx2
import pytest
from pydantic import SecretStr

from app.browser_skill.models import BrowserOwner, DecisionSource
from app.browser_skill.run_contracts import BrowserAcceptedView
from app.infra.browser import synthetic_trial as trial
from app.infra.browser.openrouter_jev import _SingleAttemptTransport, open_openrouter_jev
from app.infra.browser.synthetic_configuration import synthetic_jev_manifest
from app.infra.browser.systemone_http import DecisionDeployment
from app.ports.browser_run_store import BrowserRunStoreError
from scripts import run_browser_synthetic_once as once


@pytest.mark.parametrize("amount", ["0", "0.02", "-0.01", "NaN", "Infinity", "invalid"])
def test_trial_budget_rejects_unapproved_amount(amount: str) -> None:
    with pytest.raises(ValueError, match="^browser_trial_budget_invalid$"):
        trial.SingleJevAttempt(amount)


def test_trial_reservation_burns_budget_even_across_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records: dict[str, dict] = {}

    def create(name: str, document: dict) -> None:
        if name in records:
            raise ValueError("browser_trial_already_attempted")
        records[name] = document

    monkeypatch.setattr(trial, "create_trial_file", create)
    first = trial.SingleJevAttempt("0.01")
    first.reserve()
    with pytest.raises(ValueError, match="^browser_trial_already_attempted$"):
        first.reserve()
    restarted = trial.SingleJevAttempt("0.01")
    with pytest.raises(ValueError, match="^browser_trial_already_attempted$"):
        restarted.reserve()
    assert first.request_count == 1
    assert restarted.request_count == 0
    assert records["trial.jev-attempt.json"] == {
        "jev_request_count": 1, "max_jev_requests": 1,
        "approved_budget_usd": "0.01", "actual_cost_usd": None,
        "cost_status": "unknown", "reservation": "before_transport_dispatch",
    }


def test_transport_failure_consumes_only_attempt_without_retry(monkeypatch: pytest.MonkeyPatch):
    events: list[str] = []

    def create(_name: str, _document: dict) -> None:
        events.append("reserve")

    def failing(request: httpx2.Request) -> httpx2.Response:
        events.append("send")
        raise httpx2.ConnectError("non-sensitive synthetic failure", request=request)

    monkeypatch.setattr(trial, "create_trial_file", create)
    budget = trial.SingleJevAttempt("0.01")

    async def scenario() -> None:
        transport = _SingleAttemptTransport(budget.reserve)
        await transport._transport.aclose()
        transport._transport = httpx2.MockTransport(failing)
        async with httpx2.AsyncClient(transport=transport, trust_env=False) as client:
            for _ in range(2):
                with pytest.raises(httpx2.RequestError):
                    await client.post("https://openrouter.ai/api/alpha/decisions", content=b"{}")
            with pytest.raises(httpx2.RequestError, match="^jev_trial_destination_denied$"):
                await client.post("https://unregistered.invalid/api/alpha/decisions")
        assert events == ["reserve", "send"]
        assert budget.request_count == 1

    asyncio.run(scenario())


def test_transport_does_not_follow_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    reserve = Mock()
    sent: list[str] = []

    def redirect(request: httpx2.Request) -> httpx2.Response:
        sent.append(str(request.url))
        return httpx2.Response(302, headers={"location": "https://unregistered.invalid"})

    async def scenario() -> None:
        transport = _SingleAttemptTransport(reserve)
        await transport._transport.aclose()
        transport._transport = httpx2.MockTransport(redirect)
        async with httpx2.AsyncClient(transport=transport, follow_redirects=False,
                                    trust_env=False) as client:
            result = await client.post("https://openrouter.ai/api/alpha/decisions", content=b"{}")
        assert result.status_code == 302
        assert sent == ["https://openrouter.ai/api/alpha/decisions"]
        reserve.assert_called_once_with()

    asyncio.run(scenario())


def test_operator_provider_installs_burn_once_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    reservations = Mock()
    sent: list[str] = []
    options: list[dict] = []

    def transport(**kwargs):
        options.append(kwargs)

        def respond(request):
            sent.append(str(request.url))
            return httpx2.Response(503)

        return httpx2.MockTransport(respond)

    monkeypatch.setattr(httpx2, "AsyncHTTPTransport", transport)
    monkeypatch.setattr(trial, "create_trial_file", reservations)
    budget = trial.SingleJevAttempt("0.01")
    manifest = synthetic_jev_manifest()
    deployment = DecisionDeployment(
        disposition="cloud_synthetic", endpoint_origin="https://openrouter.ai",
        manifest_digest=manifest.manifest_digest,
        registered_sources=(DecisionSource(source_id="synthetic_fixture",
            origin="https://synthetic.invalid", fixture_digest="2" * 64),),
    )

    async def scenario() -> None:
        async with open_openrouter_jev(
            manifest=manifest, deployment=deployment,
            api_key=SecretStr(secrets.token_urlsafe(24)), attempt_guard=budget.reserve,
        ) as provider:
            assert provider._client.trust_env is False
            assert provider._client.follow_redirects is False
            response = await provider._client.post("/api/alpha/decisions", content=b"{}")
            assert response.status_code == 503
            with pytest.raises(httpx2.RequestError, match="^jev_trial_call_budget_exhausted$"):
                await provider._client.post("/api/alpha/decisions", content=b"{}")
        assert options == [{"retries": 0, "trust_env": False}]
        assert sent == ["https://openrouter.ai/api/alpha/decisions"]
        assert budget.request_count == 1
        reservations.assert_called_once()

    asyncio.run(scenario())


def _components(rows: list[dict]) -> SimpleNamespace:
    owner = BrowserOwner(tenant_id="browser_fixture_tenant", user_id="browser_fixture_user",
                         session_id="bound_synthetic_session")
    mapped = SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows))
    session = SimpleNamespace(execute=AsyncMock(return_value=mapped))

    @asynccontextmanager
    async def sessions():
        yield session

    old = SimpleNamespace(admission=SimpleNamespace(task_id="trial_task", run_id="trial_run"),
                          owner=owner, task_id="trial_task", run_id="trial_run",
                          worker_id="browser_fixture_worker")
    fresh = SimpleNamespace(status="completed", effect="acknowledged", verification="verified",
                            cleanup="released", error_code=None)
    vertical = SimpleNamespace(_sessions=sessions,
                               worker=SimpleNamespace(run_next=AsyncMock(return_value=old),
                                                      _run_claimed=AsyncMock(return_value=old)),
                               runs=SimpleNamespace(get=AsyncMock(return_value=fresh),
                                                    _claim_candidate=AsyncMock(return_value=old)))
    return SimpleNamespace(vertical=vertical, publication_owner=owner)


def _queued() -> dict:
    return {"task_id": "trial_task", "run_id": "trial_run", "status": "running",
            "phase": "queued", "cancel_requested": False, "worker_deadline": None,
            "ai_user_id": "browser_fixture_user", "session_id": "bound_synthetic_session"}


def test_worker_executes_once_and_checks_fresh_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    components = _components([_queued()])
    marker = Mock()
    monkeypatch.setattr(once, "create_trial_file", marker)
    expected = BrowserAcceptedView(task_id="trial_task", run_id="trial_run", state_revision=0)
    assert asyncio.run(once.execute_once(components, expected)) is True
    marker.assert_called_once_with("trial.worker.json", {"worker_passes": 1})
    components.vertical.runs._claim_candidate.assert_awaited_once_with(
        components.publication_owner, "trial_task", "trial_run", "browser_fixture_worker",
        timedelta(seconds=60),
    )
    components.vertical.worker._run_claimed.assert_awaited_once_with(
        components.vertical.runs._claim_candidate.return_value,
    )
    components.vertical.worker.run_next.assert_not_awaited()
    components.vertical.runs.get.assert_awaited_once_with(
        components.publication_owner, "trial_task", "trial_run",
    )


@pytest.mark.parametrize("rows", [[], [_queued(), _queued()], [{**_queued(), "phase": "running"}],
                                  [{**_queued(), "run_id": "other_run"}]])
def test_worker_rejects_other_or_multiple_runs_before_execution(
    monkeypatch: pytest.MonkeyPatch, rows: list[dict],
) -> None:
    components = _components(rows)
    marker = Mock()
    monkeypatch.setattr(once, "create_trial_file", marker)
    expected = BrowserAcceptedView(task_id="trial_task", run_id="trial_run", state_revision=0)
    with pytest.raises(ValueError, match="^browser_trial_single_queued_run_required$"):
        asyncio.run(once.execute_once(components, expected))
    marker.assert_not_called()
    components.vertical.worker.run_next.assert_not_awaited()
    components.vertical.runs._claim_candidate.assert_not_awaited()


def test_worker_pending_cleanup_is_not_success(monkeypatch: pytest.MonkeyPatch) -> None:
    components = _components([_queued()])
    components.vertical.runs.get.return_value.cleanup = "pending"
    monkeypatch.setattr(once, "create_trial_file", Mock())
    expected = BrowserAcceptedView(task_id="trial_task", run_id="trial_run", state_revision=0)
    assert asyncio.run(once.execute_once(components, expected)) is False


def test_expected_claim_stale_never_executes_alternative_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    components = _components([_queued()])
    components.vertical.runs._claim_candidate = AsyncMock(
        side_effect=BrowserRunStoreError("browser_run_stale"),
    )
    components.vertical.worker.run_next.return_value.admission.run_id = "alternative_run"
    components.vertical.worker._run_claimed = AsyncMock()
    monkeypatch.setattr(once, "create_trial_file", Mock())
    expected = BrowserAcceptedView(task_id="trial_task", run_id="trial_run", state_revision=0)
    with pytest.raises(BrowserRunStoreError, match="browser run operation refused"):
        asyncio.run(once.execute_once(components, expected))
    components.vertical.worker.run_next.assert_not_awaited()
    components.vertical.worker._run_claimed.assert_not_awaited()


@pytest.mark.parametrize("field", ["run_id", "worker_id"])
def test_wrong_claim_is_rejected_before_worker_execution(
    monkeypatch: pytest.MonkeyPatch, field: str,
) -> None:
    components = _components([_queued()])
    setattr(components.vertical.runs._claim_candidate.return_value, field, "other_identity")
    monkeypatch.setattr(once, "create_trial_file", Mock())
    expected = BrowserAcceptedView(task_id="trial_task", run_id="trial_run", state_revision=0)
    with pytest.raises(ValueError, match="^browser_trial_worker_failed$"):
        asyncio.run(once.execute_once(components, expected))
    components.vertical.worker._run_claimed.assert_not_awaited()
    components.vertical.worker.run_next.assert_not_awaited()
