"""Process-local fencing wiring with simulated transport; no browser/DB success proof."""

import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest

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
from app.browser_skill.verifier import authorize_current, failure
from app.infra.browser import local_read_execution as local_execution_module
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
from app.infra.browser.read_execution import VerifiedBrowserReadExecution
from app.infra.persistence.browser.crypto import BrowserClaimProofContext
from app.ports.browser import DecisionProvider, WebAdapter
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

    async def launch(claim, session, given_source, *, guard, subject_guard):
        events.append("launch")
        assert current.lease_epoch == claim.lease_epoch and given_source is source

        async def native_status(operation):
            assert operation == "status"
            events.append("native_status")

        async def dom_subject(*args):
            events.append("subject_dom")
            await boundary("subject_dom")
            return list(facts.subject)

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
        events=events, state=factory._states[current.run_id],
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


@pytest.mark.parametrize("change", ["auth", "binding", "lease", "publication", "cancel"])
def test_authorization_rechecks_changes_across_subject_await(
    change: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _open_with_simulated_transport(Path.cwd(), monkeypatch)
    continued: list[str] = []

    async def scenario():
        session, context = transport.execution.session, transport.context
        spec = await transport.execution.resolver(session, context, session.binding)
        await authorize_current(session, context, spec)
        entered, resume = asyncio.Event(), asyncio.Event()

        async def pause(name):
            if name == "subject_dom":
                entered.set()
                await resume.wait()

        async def authorize_then_continue():
            await authorize_current(session, context, spec)
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
