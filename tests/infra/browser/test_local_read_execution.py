"""Admission/fencing denials only; no simulated browser or authorization success."""

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest

from app.browser_skill.models import (
    BrowserOwner,
    DecisionCallContext,
    DecisionRequest,
    ModelManifest,
)
from app.infra.browser.fixed_synthetic_seed import (
    SYNTHETIC_TENANT,
    SYNTHETIC_USER,
    build_fixed_synthetic_query_source,
)
from app.infra.browser.local_read_execution import LocalBrowserReadExecutionFactory, _FencedDecision
from app.infra.browser.local_resource_lifecycle import (
    LocalBrowserResources,
    LocalChromiumDeployment,
    _Resource,
    local_subject_digest,
)
from app.infra.browser.playwright_actions import LiveBrowser
from app.infra.persistence.browser.crypto import BrowserClaimProofContext
from app.ports.browser import DecisionProvider
from app.ports.browser_read_execution import BrowserReadExecutionError, BrowserWorkerCheckpoint
from app.ports.browser_store import (
    BrowserCleanupAuthorityPort,
    BrowserLeaseClaim,
    BrowserLeaseStorePort,
    BrowserProviderExpectation,
)
from app.ports.credential_vault import (
    BrowserBindingFact,
    BrowserBindingReaderPort,
    BrowserCurrentAuthPort,
)
from tests.browser_skill.test_runtime import persisted_run


def factory_at(path: Path) -> LocalBrowserReadExecutionFactory:
    source = build_fixed_synthetic_query_source(
        ModelManifest(
            request_model="synthetic_model",
            deployment_model="synthetic_deployment",
            manifest_digest="a" * 64,
        )
    )
    resources = LocalBrowserResources(
        LocalChromiumDeployment(
            "local",
            b"m" * 32,
            path / "node",
            path / "package",
            path / "browsers",
            b"h" * 32,
            b"n" * 32,
            b"c" * 32,
            enabled=True,
        ),
        BrowserClaimProofContext(b"p" * 32),
        Mock(spec=BrowserCleanupAuthorityPort),
    )
    return LocalBrowserReadExecutionFactory(
        source,
        resources,
        BrowserBindingFact(
            SYNTHETIC_TENANT,
            SYNTHETIC_USER,
            "oa",
            "binding",
            1,
            local_subject_digest(SYNTHETIC_TENANT, SYNTHETIC_USER),
        ),
        Mock(spec=DecisionProvider),
    )


def test_authority_is_required_and_cannot_be_replaced(tmp_path: Path) -> None:
    factory = factory_at(tmp_path)
    with pytest.raises(BrowserReadExecutionError) as denied:
        asyncio.run(factory.verify_source(factory.source.manifest))
    assert denied.value.code == "unavailable"
    leases, auth, bindings = (
        Mock(spec=BrowserLeaseStorePort),
        Mock(spec=BrowserCurrentAuthPort),
        Mock(spec=BrowserBindingReaderPort),
    )
    factory.install_authority(leases=leases, current_auth=auth, binding_reader=bindings)
    with pytest.raises(ValueError, match="authority_installation_invalid"):
        factory.install_authority(leases=leases, current_auth=auth, binding_reader=bindings)
    assert factory.authority() == (leases, auth, bindings)


@pytest.mark.parametrize("recovered", [False, True])
def test_invalid_input_or_recovered_lease_never_starts_provider(
    tmp_path: Path,
    recovered: bool,
) -> None:
    factory = factory_at(tmp_path)
    leases, auth, bindings = (
        Mock(spec=BrowserLeaseStorePort),
        Mock(spec=BrowserCurrentAuthPort),
        Mock(spec=BrowserBindingReaderPort),
    )
    factory.install_authority(leases=leases, current_auth=auth, binding_reader=bindings)
    checkpoint = Mock(spec=BrowserWorkerCheckpoint)
    original = persisted_run(phase="acquiring")
    run = replace(
        original,
        admission=replace(
            original.admission,
            owner=BrowserOwner(
                tenant_id=SYNTHETIC_TENANT,
                user_id=SYNTHETIC_USER,
                session_id="synthetic_conversation",
            ),
            publication_digest=bytes.fromhex(factory.source.manifest.digest),
        ),
        provider_key="local" if recovered else None,
        lease_epoch=1 if recovered else None,
        provider_manifest_digest=b"m" * 32 if recovered else None,
    )
    # A zero-argument/v1 envelope cannot silently gain the query-detail key.
    with pytest.raises(BrowserReadExecutionError) as denied:
        asyncio.run(
            factory.open(
                run,
                factory.source.manifest,
                {"schema_version": "browser.request.input.v1"},
                checkpoint,
            )
        )
    assert denied.value.code == "denied"
    leases.reserve.assert_not_awaited()
    auth.check_current.assert_not_awaited()
    checkpoint.refresh.assert_not_awaited()
    assert factory.resources._resources == {}


@pytest.mark.parametrize("revoke_after_call", [False, True])
def test_model_call_cannot_cross_revoked_authority(revoke_after_call: bool) -> None:
    # Only fence ordering is isolated here. No model or verifier fact is trusted.
    factory = Mock()
    error = BrowserReadExecutionError("denied")
    factory._fence = AsyncMock(side_effect=[None, error] if revoke_after_call else error)
    factory.decision.decide = AsyncMock(return_value=object())
    bridge = _FencedDecision(factory, Mock())
    with pytest.raises(BrowserReadExecutionError) as denied:
        asyncio.run(
            bridge.decide(cast(DecisionRequest, object()), cast(DecisionCallContext, object()))
        )
    assert denied.value.code == "denied"
    assert factory.decision.decide.await_count == int(revoke_after_call)
    assert factory._fence.await_count == 1 + int(revoke_after_call)


def test_factory_order_and_real_subject_proof_hooks_with_simulated_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wiring evidence only: native status/DOM and store transport are synthetic."""
    factory = factory_at(tmp_path)
    source, resources = factory.source, factory.resources
    original = persisted_run(phase="acquiring")
    current = replace(
        original,
        admission=replace(
            original.admission,
            owner=BrowserOwner(
                tenant_id=SYNTHETIC_TENANT,
                user_id=SYNTHETIC_USER,
                session_id="synthetic_conversation",
            ),
            publication_digest=bytes.fromhex(source.manifest.digest),
        ),
        provider_key=None,
        provider_manifest_digest=None,
        lease_epoch=None,
    )
    events: list[str] = []
    leases, auth, bindings, checkpoint = Mock(), Mock(), Mock(), Mock()

    async def refresh(**kwargs):
        return current

    async def check_auth(fact):
        assert fact.authorization_run_id == current.run_id
        events.append("auth")

    async def check_binding(binding):
        assert binding == factory.binding

    async def reserve(auth_fact, binding, provider_key, **kwargs):
        events.append("reserve")
        return BrowserLeaseClaim(
            auth_fact, binding, 1, 1, "holder", "operation", provider_key, original.worker_deadline
        )

    async def attach(**kwargs):
        nonlocal current
        events.append("attach")
        current = replace(current, **kwargs)
        return current

    async def start(claim):
        events.append("start")
        return claim

    async def renew(claim, **kwargs):
        return replace(claim, lease_revision=claim.lease_revision + 1)

    async def launch(claim, session, given_source, *, guard, subject_guard):
        events.append("launch")
        assert current.lease_epoch == claim.lease_epoch and given_source is source

        async def native_status(operation):
            assert operation == "status"
            events.append("native_status")

        async def dom_subject(*args):
            events.append("subject_dom")
            return [SYNTHETIC_TENANT, SYNTHETIC_USER, "system_message_collection"]

        page = Mock(url=source.manifest.site.source.origin + "/")
        page.is_closed.return_value = False
        page.evaluate = AsyncMock(side_effect=dom_subject)
        native = Mock(exited=False)
        native.call = AsyncMock(side_effect=native_status)
        live = LiveBrowser(session, Mock(), Mock(pages=[page]), Mock())
        item = _Resource(
            claim, session, native, b"synthetic_resource", live, page, source, subject_guard
        )
        resources._resources["synthetic_instance"] = item
        return item

    async def record_acquired(claim, evidence):
        events.append("record_acquired")
        fact = await resources.verify(
            BrowserProviderExpectation(
                claim,
                resources._proof.challenge(claim, resources.deployment.manifest_digest),
                resources.deployment.manifest_digest,
                None,
            ),
            evidence,
        )
        assert fact.outcome == "acquired"
        await resources.check_subject(claim, fact.resource_ref)
        return claim

    async def authorize_resource(claim):
        events.append("authorize_resource")
        await resources.check_subject(claim, b"synthetic_resource")
        return b"synthetic_resource"

    checkpoint.refresh, checkpoint.attach_lease = refresh, attach
    auth.check_current, bindings.check_binding = check_auth, check_binding
    leases.reserve, leases.start_acquisition, leases.renew = reserve, start, renew
    leases.record_acquired, leases.authorize_resource = record_acquired, authorize_resource
    factory.install_authority(leases=leases, current_auth=auth, binding_reader=bindings)
    monkeypatch.setattr(resources, "launch", launch)
    result = asyncio.run(
        factory.open(
            current,
            source.manifest,
            {
                "schema_version": "browser.request.input.v2",
                "channel": "web",
                "capability_id": source.manifest.capability.capability_id,
                "principal": {
                    "ai_user_id": SYNTHETIC_USER,
                    "display_name": "Synthetic user",
                    "roles": [],
                    "org_ctx": {"tenant_id": SYNTHETIC_TENANT},
                },
                "arguments": {"business_key": "explicit_input_key"},
            },
            checkpoint,
        )
    )
    order = [
        events.index(name)
        for name in (
            "reserve",
            "attach",
            "start",
            "launch",
            "record_acquired",
            "native_status",
            "subject_dom",
            "authorize_resource",
        )
    ]
    assert order == sorted(order) and len(set(order)) == len(order)
    for index, event in enumerate(events):
        if event in {"native_status", "subject_dom"}:
            assert events[index - 1] == events[index + 1] == "auth"
    assert result.registry is factory and result.site is source.site
    assert result.project_output is source.projector
    assert result.observer._pages[result.session.session_ref].page is not None
    assert result.confirmed_key.value_ref.name == "business_key"
    assert factory._states[current.run_id].key == "explicit_input_key"
