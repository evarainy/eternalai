from __future__ import annotations

import asyncio
from unittest.mock import Mock

import pytest

from app.browser_skill.models import DecisionSource, ProfileRef
from app.infra.browser.browserless_wire import BrowserProviderError
from app.infra.browser.enterprise_provider import EnterpriseProvider
from app.ports.browser import BrowserProvider
from tests.browser_skill.factories import binding
from tests.infra.browser.test_enterprise_manifest import accepted_view, registration


class Authority:
    def __init__(self, manifest):
        self.manifest = manifest
        self.view = accepted_view(manifest)
        self.binding = binding()
        self.cleanup_binding = self.binding
        self.registration = manifest.registered_sources[0]
        self.calls = []

    async def deployment(self, manifest):
        self.calls.append("deployment")
        return self.view

    async def current(self, binding):
        self.calls.append("current")
        return self.binding

    async def source(self, binding):
        self.calls.append("source")
        return self.registration

    async def cleanup(self, original_binding):
        self.calls.append("cleanup")
        return self.cleanup_binding


def fixture(**updates):
    manifest = registration()
    authority = Authority(manifest)
    credential = Mock(side_effect=AssertionError("no_credential_without_verified_codec"))
    values = dict(
        enabled=True, manifest=manifest, authority=authority,
        credential=credential, clock=lambda: 100.0,
    )
    values.update(updates)
    provider = EnterpriseProvider(**values)
    return provider, authority, credential


def test_provider_satisfies_neutral_protocol_without_claiming_real_capabilities() -> None:
    async def run():
        provider, _, credential = fixture()
        port: BrowserProvider = provider
        caps = await port.capabilities()
        assert caps.model_dump() == dict(
            transport="playwright", cookies=False, local_storage=False, indexed_db=False,
            immutable_capture=False, confirmed_termination=False,
        )
        credential.assert_not_called()
    asyncio.run(run())


def test_default_off_touches_no_authority_credentials_or_client() -> None:
    async def run():
        provider, authority, credential = fixture(enabled=False)
        with pytest.raises(BrowserProviderError) as caught:
            await provider.acquire(authority.binding)
        assert caught.value.reason == "disabled"
        assert authority.calls == [] and provider.occupied == 0
        credential.assert_not_called()
    asyncio.run(run())


def test_reserve_is_local_and_release_is_idempotent_without_remote_success_claim() -> None:
    async def run():
        provider, authority, credential = fixture()
        session = await provider.acquire(authority.binding)
        assert provider.occupied == 1 and session.binding == authority.binding
        first = await provider.release(session)
        assert first.status == "released" and provider.occupied == 0
        assert await provider.terminate(session) == first
        credential.assert_not_called()
    asyncio.run(run())


@pytest.mark.parametrize("profile", [False, True])
def test_unknown_start_and_profile_never_send_or_consume_capacity_permanently(profile) -> None:
    async def run():
        provider, authority, credential = fixture()
        session = await provider.acquire(authority.binding)
        ref = ProfileRef(
            generation_ref="unregistered_generation", binding=authority.binding,
            profile_revision=1, subject_digest="a" * 64,
        ) if profile else None
        with pytest.raises(BrowserProviderError) as caught:
            await provider.restore(session, ref)
        assert caught.value.failure.code == "unsupported"
        assert caught.value.failure.dispatch_state == "not_sent"
        assert provider.occupied == 1
        assert (await provider.release(session)).status == "released"
        credential.assert_not_called()
    asyncio.run(run())


@pytest.mark.parametrize("mutation", ["owner", "authorization_revision", "lease_epoch", "source"])
def test_current_authority_mutation_blocks_operations_without_freeing_capacity(mutation) -> None:
    async def run():
        provider, authority, credential = fixture()
        session = await provider.acquire(authority.binding)
        if mutation == "source":
            authority.registration = authority.registration.model_copy()
        elif mutation == "owner":
            owner = authority.binding.owner.model_copy(update={"user_id": "other"})
            authority.binding = authority.binding.model_copy(update={"owner": owner})
        else:
            authority.binding = authority.binding.model_copy(update={mutation: 99})
        operations = (provider.capture, provider.resolve_live)
        for operation in operations:
            with pytest.raises(BrowserProviderError) as caught:
                await operation(session)
            assert caught.value.failure.code == ("denied" if mutation == "source" else "stale")
        assert provider.occupied == 1
        credential.assert_not_called()
    asyncio.run(run())


def test_capacity_exhaustion_returns_overloaded_and_forged_handle_cannot_release() -> None:
    async def run():
        provider, authority, credential = fixture()
        session = await provider.acquire(authority.binding)
        with pytest.raises(BrowserProviderError) as caught:
            await provider.acquire(authority.binding)
        assert caught.value.failure.code == "overloaded"
        forged = session.model_copy(update={"session_ref": "unknown"})
        with pytest.raises(BrowserProviderError) as caught:
            await provider.release(forged)
        assert caught.value.failure.code == "denied" and provider.occupied == 1
        credential.assert_not_called()
    asyncio.run(run())


@pytest.mark.parametrize("state", ["acquiring", "active", "quarantined"])
def test_nonreservation_state_without_real_proof_always_quarantines(state) -> None:
    async def run():
        provider, authority, credential = fixture()
        session = await provider.acquire(authority.binding)
        # Fault injection: these states are unreachable with the closed codec set.
        # A future partial launch must never fall through to reservation release.
        provider._ledger.get(session).state = state
        assert (await provider.release(session)).status == "quarantined"
        assert (await provider.terminate(session)).status == "quarantined"
        assert provider.occupied == 1
        credential.assert_not_called()
    asyncio.run(run())


def test_no_capture_or_business_authority_is_manufactured_for_reserved_session() -> None:
    async def run():
        provider, authority, credential = fixture()
        session = await provider.acquire(authority.binding)
        with pytest.raises(BrowserProviderError) as caught:
            await provider.capture(session)
        assert caught.value.failure.code == "unsupported"
        source = authority.registration
        decision_source = DecisionSource(
            source_id=source.source_id, fixture_digest=source.fixture_digest,
            origin=source.origins[0],
        )
        with pytest.raises(BrowserProviderError) as caught:
            await provider.assert_business_authority(session, decision_source)
        assert caught.value.failure.code == "denied" and caught.value.reason == "subject"
        assert provider.occupied == 1
        credential.assert_not_called()
    asyncio.run(run())


def test_authority_failure_is_sanitized_and_does_not_allocate() -> None:
    async def run():
        provider, authority, credential = fixture()
        async def broken(manifest):
            raise RuntimeError("private_authority_detail")
        authority.deployment = broken
        with pytest.raises(BrowserProviderError) as caught:
            await provider.acquire(authority.binding)
        assert caught.value.failure.code == "denied" and provider.occupied == 0
        assert "private_authority_detail" not in str(caught.value)
        credential.assert_not_called()
    asyncio.run(run())


def test_revoked_business_authority_can_cleanup_only_never_sent_reservation() -> None:
    async def run():
        provider, authority, credential = fixture()
        session = await provider.acquire(authority.binding)
        authority.binding = authority.binding.model_copy(update={"authorization_revision": 99})
        authority.view = accepted_view(authority.manifest, expires_at=50.0)
        outcome = await provider.release(session)
        assert outcome.status == "released" and provider.occupied == 0
        assert authority.calls[-1] == "cleanup"
        credential.assert_not_called()
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["wrong_claim", "denied", "timeout", "cancelled"])
def test_cleanup_authority_failure_never_releases_or_exposes_private_error(failure) -> None:
    async def run():
        provider, authority, credential = fixture()
        session = await provider.acquire(authority.binding)
        if failure == "wrong_claim":
            authority.cleanup_binding = authority.binding.model_copy(update={"lease_epoch": 99})
        else:
            async def broken(original_binding):
                if failure == "timeout":
                    raise TimeoutError("private_cleanup_detail")
                if failure == "cancelled":
                    raise asyncio.CancelledError("private_cleanup_detail")
                raise RuntimeError("private_cleanup_detail")
            authority.cleanup = broken
        expected = asyncio.CancelledError if failure == "cancelled" else BrowserProviderError
        with pytest.raises(expected) as caught:
            await provider.release(session)
        if failure != "cancelled":
            assert caught.value.failure.code == ("timeout" if failure == "timeout" else "denied")
        assert "private_cleanup_detail" not in str(caught.value)
        assert provider.occupied == 1
        credential.assert_not_called()
    asyncio.run(run())


def test_possibly_sent_record_cannot_use_local_cleanup_even_if_state_is_reserved() -> None:
    async def run():
        provider, authority, credential = fixture()
        session = await provider.acquire(authority.binding)
        provider._sessions[session.session_ref].dispatch_attempted = True
        assert (await provider.release(session)).status == "quarantined"
        assert provider.occupied == 1 and "cleanup" not in authority.calls
        credential.assert_not_called()
    asyncio.run(run())


def test_cleanup_has_real_async_deadline_and_retains_reservation() -> None:
    async def run():
        manifest = registration(timeout_seconds=0.01)
        authority = Authority(manifest)
        credential = Mock()
        provider = EnterpriseProvider(
            enabled=True, manifest=manifest, authority=authority,
            credential=credential, clock=lambda: 100.0,
        )
        session = await provider.acquire(authority.binding)
        entered = asyncio.Event()
        stopped = asyncio.Event()

        async def blocked(original_binding):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        authority.cleanup = blocked
        with pytest.raises(BrowserProviderError) as caught:
            await provider.release(session)
        assert caught.value.failure.code == "timeout"
        assert entered.is_set() and stopped.is_set() and provider.occupied == 1
        credential.assert_not_called()
    asyncio.run(run())


def test_other_owner_cannot_reach_cleanup_callback_with_existing_session_id() -> None:
    async def run():
        provider, authority, credential = fixture()
        session = await provider.acquire(authority.binding)
        owner = authority.binding.owner.model_copy(update={"user_id": "other"})
        wrong_binding = authority.binding.model_copy(update={"owner": owner})
        forged = session.model_copy(update={"binding": wrong_binding})
        with pytest.raises(BrowserProviderError) as caught:
            await provider.release(forged)
        assert caught.value.failure.code == "denied"
        assert "cleanup" not in authority.calls and provider.occupied == 1
        credential.assert_not_called()
    asyncio.run(run())
