"""Process-local fencing wiring with simulated transport; no browser/DB success proof."""

import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.browser_skill import executor as executor_module
from app.browser_skill import verifier as verifier_module
from app.browser_skill.executor import BrowserExecutor
from app.browser_skill.models import (
    ActionCommand,
    BrowserOperationError,
    BrowserOwner,
    DecisionCallContext,
    DecisionRequest,
    ModelManifest,
)
from app.browser_skill.publication_contracts import (
    BrowserPublicationManifest,
    canonical_json,
    publication_digest,
)
from app.browser_skill.site_rules import FrozenSiteAdapter
from app.browser_skill.verifier import authorize_current, failure
from app.infra.browser import local_read_execution as local_execution_module
from app.infra.browser.fixed_synthetic_seed import (
    SYNTHETIC_TENANT,
    SYNTHETIC_USER,
    build_fixed_synthetic_query_source,
)
from app.infra.browser.local_read_execution import LocalBrowserReadExecutionFactory, _FencedDecision
from app.infra.browser.local_resource_lifecycle import (
    LocalBrowserReadLifecycle,
    LocalBrowserResources,
    LocalChromiumDeployment,
    LocalPlaywrightDriver,
    RegisteredLocalSite,
    _Resource,
    local_subject_digest,
    registered_local_digest,
    registered_subject_digest,
)
from app.infra.browser.playwright_actions import LiveBrowser
from app.infra.browser.playwright_web_adapter import PlaywrightWebAdapter, RegisteredExecution
from app.infra.browser.read_execution import VerifiedBrowserReadExecution
from app.infra.persistence.browser.crypto import BrowserClaimProofContext
from app.infra.persistence.browser.payload_crypto import BrowserPayloadCipher
from app.ports.browser import DecisionProvider, WebAdapter
from app.ports.browser_profile_store import BrowserProfileStorePort
from app.ports.browser_read_execution import BrowserReadExecutionError, BrowserWorkerCheckpoint
from app.ports.browser_store import (
    BrowserCleanupAuthorityPort,
    BrowserLeaseClaim,
    BrowserLeaseError,
    BrowserLeaseStorePort,
    BrowserProviderExpectation,
)
from app.ports.credential_vault import (
    BrowserBindingFact,
    BrowserBindingReaderPort,
    BrowserCurrentAuthPort,
)
from tests.browser_skill.factories import projection
from tests.browser_skill.test_runtime import persisted_run


def factory_at(path: Path) -> LocalBrowserReadExecutionFactory:
    source = build_fixed_synthetic_query_source(
        ModelManifest(
            request_model="synthetic_model",
            deployment_model="synthetic_deployment",
            manifest_digest="a" * 64,
        )
    )
    deployment = LocalChromiumDeployment(
        "local",
        b"m" * 32,
        path / "node",
        path / "package",
        path / "browsers",
        b"h" * 32,
        b"n" * 32,
        b"c" * 32,
        enabled=True,
    )
    resources = LocalBrowserResources(
        deployment,
        BrowserClaimProofContext(b"p" * 32),
        Mock(spec=BrowserCleanupAuthorityPort),
        LocalPlaywrightDriver(deployment),
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


def registered_site(decision_manifest: ModelManifest | None = None) -> RegisteredLocalSite:
    """Fictitious trusted registration, never an actual OA rule or credential."""
    base = build_fixed_synthetic_query_source(decision_manifest or ModelManifest(
        request_model="synthetic_model", deployment_model="synthetic_deployment",
        manifest_digest="a" * 64,
    ))
    entry_url = base.manifest.site.source.origin + "/"
    data = base.manifest.model_dump(mode="json")
    data["source_kind"] = "registered_site"
    data["site"]["source"]["fixture_digest"] = registered_local_digest(
        entry_url=entry_url, region=base.region, rules=base.rules,
        projector=base.projector, subject_selector="[data-subject]",
    )
    data["site"]["steps"][0]["effect"]["evidence_digest"] = (
        data["site"]["source"]["fixture_digest"]
    )
    data["site"]["steps"][0]["effect"]["proof_ref"] = "fixture_registered_read_v1"
    data["site"]["digest"] = publication_digest(
        "browser_site.publication.v1", data["site"],
    )
    data["skill"]["site_digest"] = data["site"]["digest"]
    data["skill"]["digest"] = publication_digest(
        "browser_skill.v1", data["skill"],
    )
    data["digest"] = publication_digest("browser_publication.durable.v1", data)
    manifest = BrowserPublicationManifest.model_validate_json(canonical_json(data))
    rules = replace(
        base.rules, site_digest=manifest.site.digest, skill_digest=manifest.skill.digest,
    )
    return RegisteredLocalSite(
        manifest, entry_url, base.region, rules,
        FrozenSiteAdapter((manifest.site_plan(),)), base.projector,
        "[data-subject]",
    )


def test_registered_site_is_distinct_from_fixed_synthetic_source() -> None:
    site = registered_site()
    assert site.manifest.source_kind == "registered_site"
    assert site.registration_digest() == site.manifest.site.source.fixture_digest
    assert registered_subject_digest("oa", "account_a") != registered_subject_digest(
        "oa", "account_b",
    )
    with pytest.raises(ValueError, match="registration_invalid"):
        replace(site, entry_url=site.manifest.site.source.origin + "/other")


@pytest.mark.parametrize("arguments,invocation", [
    ({"business_key": 5}, None),
    ({"business_key": "\ud800"}, None),
    ({"business_key": "key", "unapproved": "extra"}, None),
    ({"business_key": "key"}, {
        "caller": "authenticated_session_v1", "entrypoint": "runtime_chat",
        "operator_user_id": "another_user", "represented_user_id": "reader",
    }),
])
def test_registered_input_denied_before_worker_driver(
    tmp_path: Path, arguments: dict[str, object], invocation: dict[str, str] | None,
) -> None:
    source = registered_site()
    deployment = LocalChromiumDeployment(
        "local", b"m" * 32, tmp_path / "node", tmp_path / "package",
        tmp_path / "browsers", b"h" * 32, b"n" * 32, b"c" * 32, enabled=True,
    )
    resources = LocalBrowserResources(
        deployment, BrowserClaimProofContext(b"p" * 32),
        Mock(spec=BrowserCleanupAuthorityPort), LocalPlaywrightDriver(deployment),
    )
    resources.ensure_started = AsyncMock(side_effect=AssertionError("driver started"))
    factory = LocalBrowserReadExecutionFactory(
        source, resources,
        BrowserBindingFact("tenant", "reader", "oa", "binding", 1,
                           registered_subject_digest("oa", "account_a")),
        Mock(spec=DecisionProvider), session_factory=async_sessionmaker(),
    )
    factory.install_authority(
        leases=Mock(spec=BrowserLeaseStorePort),
        current_auth=Mock(spec=BrowserCurrentAuthPort),
        binding_reader=Mock(spec=BrowserBindingReaderPort),
    )
    factory.install_profiles(
        Mock(spec=BrowserProfileStorePort),
        BrowserPayloadCipher({"profile": b"k" * 32}, active_key_id="profile"),
    )
    original = persisted_run(phase="acquiring")
    run = replace(original,
                  admission=replace(original.admission,
                                    publication_digest=bytes.fromhex(source.manifest.digest)),
                  provider_key=None, lease_epoch=None, provider_manifest_digest=None)
    private_input: dict[str, object] = {
        "schema_version": "browser.request.input.v2", "channel": "web",
        "capability_id": source.manifest.capability.capability_id,
        "principal": {"ai_user_id": "reader", "display_name": "Fictitious reader",
                      "roles": [], "org_ctx": {"tenant_id": "tenant"}},
        "arguments": arguments,
    }
    if invocation is not None:
        private_input["invocation"] = invocation
    with pytest.raises(BrowserReadExecutionError) as denied:
        asyncio.run(factory.open(run, source.manifest, private_input,
                                 Mock(spec=BrowserWorkerCheckpoint)))
    assert denied.value.code == "denied"
    resources.ensure_started.assert_not_awaited()


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
@pytest.mark.parametrize("invalid_field", [
    "schema_version", "capability_id", "channel", "principal", "owner", "arguments",
])
def test_invalid_input_or_recovered_lease_never_starts_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recovered: bool,
    invalid_field: str,
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
    driver_start = AsyncMock(side_effect=AssertionError("invalid input started worker driver"))
    monkeypatch.setattr(factory.resources.driver, "ensure_started", driver_start)
    private_input = {
        "schema_version": "browser.request.input.v2",
        "capability_id": factory.source.manifest.capability.capability_id,
        "channel": "web",
        "principal": {
            "ai_user_id": SYNTHETIC_USER,
            "display_name": "Synthetic user",
            "roles": [],
            "org_ctx": {"tenant_id": SYNTHETIC_TENANT},
        },
        "arguments": {"business_key": "explicit_input_key"},
    }
    if invalid_field == "schema_version":
        private_input["schema_version"] = "browser.request.input.v1"
    elif invalid_field == "capability_id":
        private_input["capability_id"] = "another_capability"
    elif invalid_field == "channel":
        private_input["channel"] = ["web"]
    elif invalid_field == "principal":
        private_input["principal"] = {"ai_user_id": SYNTHETIC_USER}
    elif invalid_field == "owner":
        private_input["principal"] = {
            **private_input["principal"], "ai_user_id": "another_user",
        }
    else:
        private_input["arguments"] = {"business_key": "invalid key"}
    with pytest.raises(BrowserReadExecutionError) as denied:
        asyncio.run(
            factory.open(
                run,
                factory.source.manifest,
                private_input,
                checkpoint,
            )
        )
    assert denied.value.code == "denied"
    driver_start.assert_not_awaited()
    leases.reserve.assert_not_awaited()
    auth.check_current.assert_not_awaited()
    checkpoint.refresh.assert_not_awaited()
    assert factory.resources._resources == {}
    assert factory.state_for_cleanup(run) is None


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


def _open_with_simulated_transport(
    path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> SimpleNamespace:
    """Wiring evidence only: native status/DOM and store transport are synthetic."""
    factory = factory_at(path)
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
    calls: list[str] = []
    facts = SimpleNamespace(
        run=current, authorized=True, binding=factory.binding, published=True, hook=None,
        subject=[SYNTHETIC_TENANT, SYNTHETIC_USER, "system_message_collection"],
    )
    leases, auth, bindings, checkpoint = Mock(), Mock(), Mock(), Mock()

    async def start_driver():
        if resources.driver.ready:
            events.append("driver_reuse")
            return
        resources.driver._driver = Mock()
        resources.driver._state = "ready"
        events.append("driver_start")

    monkeypatch.setattr(resources.driver, "ensure_started", start_driver)

    async def boundary(name):
        calls.append(name)
        if facts.hook is not None:
            await facts.hook(name)

    async def refresh(**kwargs):
        await boundary("refresh")
        return facts.run

    async def check_auth(fact):
        await boundary("auth")
        if not facts.authorized:
            raise failure("denied")
        assert fact.authorization_run_id == current.run_id
        events.append("auth")

    async def check_binding(binding):
        await boundary("binding")
        assert binding == factory.binding
        if binding != facts.binding:
            raise failure("stale")

    async def reserve(auth_fact, binding, provider_key, **kwargs):
        events.append("reserve")
        return BrowserLeaseClaim(
            auth_fact, binding, 1, 1, "holder", "operation", provider_key, original.worker_deadline
        )

    async def attach(**kwargs):
        nonlocal current
        events.append("attach")
        current = replace(current, **kwargs)
        facts.run = current
        return current

    async def start(claim):
        events.append("start")
        return claim

    async def renew(claim, **kwargs):
        await boundary("lease_renew")
        return replace(claim, lease_revision=claim.lease_revision + 1)

    async def launch(claim, session, given_source, *, guard, subject_guard, storage_state):
        events.append("launch")
        assert current.lease_epoch == claim.lease_epoch and given_source is source
        assert storage_state is None

        async def native_status(operation):
            assert operation in {"status", "stop"}
            events.append("native_status" if operation == "status" else "native_stop")
            if operation == "stop":
                native.exited = True

        async def dom_subject(*args):
            events.append("subject_dom")
            await boundary("subject_dom")
            return list(facts.subject)

        page = Mock(url=source.manifest.site.source.origin + "/")
        page.is_closed.return_value = False
        page.evaluate = AsyncMock(side_effect=dom_subject)
        native = Mock(exited=False)
        native.call = AsyncMock(side_effect=native_status)
        native.dispose_helper = AsyncMock(return_value=True)
        browser = Mock()
        browser.is_connected.return_value = True
        browser.close = AsyncMock()
        context = Mock(pages=[page])
        context.close = AsyncMock()
        live = LiveBrowser(session, browser, context)
        item = _Resource(
            claim, session, native, b"synthetic_resource", live, page, source, subject_guard,
            connection=browser, context=context,
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

    async def assert_current(owner, manifest):
        assert owner == facts.run.owner and manifest == source.manifest
        await boundary("publication")
        if not facts.published:
            raise failure("denied")

    publications = Mock()
    publications.assert_current = assert_current
    bridge = VerifiedBrowserReadExecution(
        factory, Mock(), publications, Mock(), result_digest_key=b"r" * 32,
    )
    context = bridge._fenced_context(
        result.context, source.manifest, checkpoint, business_key="explicit_input_key",
    )
    return SimpleNamespace(
        factory=factory, execution=result, context=context, facts=facts, calls=calls,
        events=events, state=factory._states[current.run_id], bridge=bridge, checkpoint=checkpoint,
    )


def test_factory_order_and_real_subject_proof_hooks_with_simulated_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _open_with_simulated_transport(tmp_path, monkeypatch)
    factory, result, events = transport.factory, transport.execution, transport.events
    source, current = factory.source, transport.facts.run
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
    assert events.count("driver_start") == 1
    assert events.index("driver_start") < events.index("reserve")
    assert factory.state_for_cleanup(current) is transport.state
    assert transport.state.resource is factory.resources.resource(
        transport.state.resource.reference
    )


def test_cancel_cleanup_closes_only_run_context_and_keeps_worker_driver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _open_with_simulated_transport(tmp_path, monkeypatch)
    factory, state = transport.factory, transport.state
    item = state.resource
    assert item is not None and item.live is not None
    context, browser = item.live.context, item.live.browser
    cleanup_authority = Mock()
    cleanup_authority.check_recovery = AsyncMock()
    cleanup_authority.check_cleanup = AsyncMock()
    factory.resources.cleanup_authority = cleanup_authority
    leases = factory.authority()[0]
    leases.cleanup = AsyncMock(return_value=True)
    driver_stop = AsyncMock(side_effect=AssertionError("Run cleanup stopped worker driver"))
    monkeypatch.setattr(factory.resources.driver, "stop", driver_stop)
    cancelled = replace(transport.facts.run, cancel_requested=True)
    transport.facts.run = cancelled
    lifecycle = LocalBrowserReadLifecycle(factory, cleanup_authority)

    async def scenario():
        await lifecycle.stop(cancelled, transport.checkpoint)
        await factory.resources.ensure_started()

    asyncio.run(scenario())
    assert state.cancellation.is_set() and state.released
    assert state.cleanup_outcome == "terminated"
    assert lifecycle._cancel[cancelled.run_id][0] == cancelled
    assert factory.state_for_cleanup(cancelled) is state
    context.close.assert_awaited_once()
    browser.close.assert_awaited_once()
    driver_stop.assert_not_awaited()
    assert factory.resources.driver.ready
    assert transport.events.count("driver_start") == 1
    assert transport.events.count("driver_reuse") == 1
    assert leases.cleanup.await_count == 1


def test_failed_run_termination_retains_cleanup_state_and_driver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _open_with_simulated_transport(tmp_path, monkeypatch)
    factory, state = transport.factory, transport.state
    cleanup_authority = Mock()
    cleanup_authority.check_recovery = AsyncMock()
    cleanup_authority.check_cleanup = AsyncMock()
    leases = factory.authority()[0]
    leases.cleanup = AsyncMock(return_value=False)
    termination = AsyncMock(side_effect=BrowserReadExecutionError("unavailable"))
    monkeypatch.setattr(factory.resources, "terminate", termination)
    driver_stop = AsyncMock(side_effect=AssertionError("failed cleanup stopped worker driver"))
    monkeypatch.setattr(factory.resources.driver, "stop", driver_stop)
    lifecycle = LocalBrowserReadLifecycle(factory, cleanup_authority)

    outcome = asyncio.run(lifecycle.cleanup(transport.facts.run))
    assert outcome == "quarantined"
    assert state.cancellation.is_set() and not state.released
    assert factory.state_for_cleanup(transport.facts.run) is state
    termination.assert_awaited_once_with(state.resource)
    assert leases.cleanup.await_count == 1
    driver_stop.assert_not_awaited()
    assert factory.resources.driver.ready


def test_current_binding_fences_each_independent_call_without_subject_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _open_with_simulated_transport(Path.cwd(), monkeypatch)
    transport.events.clear()
    transport.calls.clear()

    async def scenario():
        revision = transport.state.claim.lease_revision
        first = await transport.context.current_binding(transport.execution.session)
        first_checks = set(transport.calls)
        first_revision = transport.state.claim.lease_revision
        transport.calls.clear()
        second = await transport.context.current_binding(transport.execution.session)
        assert first == second == transport.context.expected_binding
        required = {"refresh", "auth", "binding", "lease_renew", "publication"}
        assert required <= first_checks and required <= set(transport.calls)
        assert revision < first_revision < transport.state.claim.lease_revision

    asyncio.run(scenario())
    assert "subject_dom" not in transport.events
    assert "authorize_resource" not in transport.events


def test_current_binding_rejects_another_registered_execution_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _open_with_simulated_transport(Path.cwd(), monkeypatch)
    other_session = transport.execution.session.model_copy(update={"session_ref": "other_session"})
    other = replace(transport.state, session=other_session)
    transport.factory._states["other_execution"] = other
    assert transport.factory._state(other_session) is other
    transport.calls.clear()
    with pytest.raises(BrowserOperationError) as denied:
        asyncio.run(transport.context.current_binding(other_session))
    assert denied.value.failure.code == "denied"
    assert not {"auth", "binding", "lease_renew", "subject_dom"} & set(transport.calls)


@pytest.mark.parametrize("route", ["original", "operation"])
@pytest.mark.parametrize("change", ["auth", "binding", "lease", "publication", "cancel"])
def test_authorization_rechecks_changes_across_subject_await(
    change: str, route: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _open_with_simulated_transport(Path.cwd(), monkeypatch)
    continued: list[str] = []

    async def scenario():
        session, context = transport.execution.session, transport.context
        operation_authority = None
        if route == "operation":
            context = transport.bridge._fenced_context(
                transport.execution.context,
                transport.factory.source.manifest,
                transport.checkpoint,
                business_key="explicit_input_key",
                bind_publication_guard=transport.execution.bind_publication_guard,
            )
            operation_authority = transport.execution.bind_operation_authority(context)
        spec = await transport.execution.resolver(session, context, session.binding)
        async def authorize():
            if operation_authority is None:
                await authorize_current(session, context, spec)
            else:
                await operation_authority(session, context, spec)

        await authorize()
        entered, resume = asyncio.Event(), asyncio.Event()

        async def pause(name):
            if name == "subject_dom":
                entered.set()
                await resume.wait()

        async def authorize_then_continue():
            await authorize()
            continued.append("authorized")

        transport.facts.hook = pause
        operation = asyncio.create_task(authorize_then_continue())
        signal = asyncio.create_task(entered.wait())
        try:
            done, _ = await asyncio.wait((operation, signal), return_when=asyncio.FIRST_COMPLETED)
            assert signal in done and entered.is_set()
            if change == "auth":
                transport.facts.authorized = False
            elif change == "binding":
                transport.facts.binding = replace(transport.facts.binding, binding_revision=2)
            elif change == "lease":
                transport.facts.run = replace(transport.facts.run, lease_epoch=2)
            elif change == "publication":
                transport.facts.published = False
            else:
                transport.state.cancellation.set()
            resume.set()
            with pytest.raises(BrowserOperationError) as denied:
                await operation
            expected = {"auth": "denied", "binding": "stale", "lease": "stale",
                        "publication": "denied", "cancel": "cancelled"}
            assert denied.value.failure.code == expected[change]
            assert denied.value.failure.dispatch_state == "not_sent"
        finally:
            resume.set()
            signal.cancel()
            if not operation.done():
                operation.cancel()
            await asyncio.gather(operation, signal, return_exceptions=True)

    asyncio.run(scenario())
    assert continued == []


def test_operation_authority_is_bound_and_retains_nested_subject_fences(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _open_with_simulated_transport(Path.cwd(), monkeypatch)

    async def scenario():
        execution = transport.execution
        context = transport.bridge._fenced_context(
            execution.context,
            transport.factory.source.manifest,
            transport.checkpoint,
            business_key="explicit_input_key",
            bind_publication_guard=execution.bind_publication_guard,
        )
        authority = execution.bind_operation_authority(context)
        spec = await execution.resolver(execution.session, context, execution.session.binding)
        transport.state.authority_counts.clear()
        transport.calls.clear()
        live = await authority(execution.session, context, spec)
        assert live.session == execution.session
        assert execution.authority_diagnostic() == {
            # Two renewing outer fences plus the two real subject-DOM fences.
            "factory_fence_attempts": 4, "factory_fence_successes": 4,
            "factory_resource_authorization_attempts": 1,
            "factory_resource_authorization_successes": 1,
        }
        assert transport.calls.count("refresh") == 8
        transport.state.authority_counts.clear()
        with pytest.raises(BrowserOperationError) as cloned:
            await authority(execution.session, replace(context), spec)
        assert cloned.value.failure.code == "denied" and transport.state.authority_counts == {}
        wrong = spec.model_copy(
            update={"binding": spec.binding.model_copy(update={"lease_epoch": 2})}
        )
        with pytest.raises(BrowserOperationError) as rebound:
            await authority(execution.session, context, wrong)
        assert rebound.value.failure.code == "stale" and transport.state.authority_counts == {}
        with pytest.raises(BrowserOperationError) as second_registration:
            execution.bind_operation_authority(context)
        assert second_registration.value.failure.code == "denied"

    asyncio.run(scenario())


@pytest.mark.parametrize("route,fences,refreshes,resources", [
    ("original", 16, 32, 3), ("operation", 4, 8, 1),
])
def test_warm_authority_counts_include_real_subject_callback_fences(
    monkeypatch: pytest.MonkeyPatch, route: str, fences: int, refreshes: int, resources: int,
) -> None:
    transport = _open_with_simulated_transport(Path.cwd(), monkeypatch)

    async def scenario():
        execution = transport.execution
        context = transport.bridge._fenced_context(
            execution.context,
            transport.factory.source.manifest,
            transport.checkpoint,
            business_key="explicit_input_key",
            bind_publication_guard=execution.bind_publication_guard,
        )
        callback = execution.bind_operation_authority(context) if route == "operation" else None
        transport.state.resource.live.context.route = AsyncMock()
        adapter = PlaywrightWebAdapter(
            registry=execution.registry, observer=execution.observer,
            site=execution.site, rules=execution.rules,
            executions=(RegisteredExecution(execution.session, context, execution.confirmed_key,
                                            project_output=execution.project_output,
                                            operation_authority=callback),),
        )
        await adapter._authority(execution.session, context)  # Complete the cold route separately.
        transport.state.authority_counts.clear()
        transport.calls.clear()
        await adapter._authority(execution.session, context)
        assert transport.state.authority_counts == {
            "factory_fence_attempts": fences, "factory_fence_successes": fences,
            "factory_resource_authorization_attempts": resources,
            "factory_resource_authorization_successes": resources,
        }
        assert transport.calls.count("refresh") == refreshes
        assert transport.calls.count("subject_dom") == resources

    asyncio.run(scenario())


def test_direct_dispatch_barrier_rechecks_actual_subject_before_permit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _open_with_simulated_transport(Path.cwd(), monkeypatch)
    context, session = transport.context, transport.execution.session
    command = ActionCommand(
        skill_digest=context.skill.digest, step=context.skill.steps[0], binding=session.binding,
        target=projection().candidates[0].ref,
    )
    permits: list[str] = []

    async def scenario():
        async with context.dispatch_barrier(session, command, session.binding) as permit:
            permit.begin_send()
            permits.append("granted")
        transport.events.clear()
        transport.facts.subject[1] = "different_synthetic_user"
        with pytest.raises(BrowserLeaseError) as denied:
            async with context.dispatch_barrier(session, command, session.binding):
                permits.append("unexpected")
        assert denied.value.code == "browser_local_subject_mismatch"

    asyncio.run(scenario())
    assert permits == ["granted"]
    assert transport.events.count("authorize_resource") == 1
    assert transport.events.count("subject_dom") == 1


def test_context_deadline_is_not_extended_by_fences_and_expires_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [1000.0]
    controlled_time = SimpleNamespace(monotonic=lambda: clock[0])
    monkeypatch.setattr(local_execution_module, "time", controlled_time)
    monkeypatch.setattr(executor_module, "time", controlled_time)
    monkeypatch.setattr(verifier_module, "time", controlled_time)
    transport = _open_with_simulated_transport(Path.cwd(), monkeypatch)
    context, execution = transport.context, transport.execution
    expected_deadline = 1000.0 + transport.factory.ttl_seconds
    assert execution.context.deadline_monotonic == context.deadline_monotonic == expected_deadline
    web = Mock(spec=WebAdapter)
    verifier = Mock()
    executor = BrowserExecutor(
        web, execution.decision, verifier, execution.site, execution.resolver,
        execution.decision_contexts,
    )

    async def scenario():
        await context.current_binding(execution.session)
        assert context.deadline_monotonic == expected_deadline
        clock[0] = expected_deadline
        return await executor.run(execution.session, context)

    outcome = asyncio.run(scenario())
    assert context.deadline_monotonic == expected_deadline
    assert outcome.failure.code == "timeout" and outcome.failure.dispatch_state == "not_sent"
    assert outcome.verification is None and outcome.receipts == ()
    web.observe.assert_not_awaited()
    web.execute.assert_not_awaited()
    verifier.verify.assert_not_called()
