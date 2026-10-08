from __future__ import annotations

import asyncio
import json

import pytest

from app.browser_skill.models import DecisionSource
from app.infra.browser.browserless_provider import BrowserlessProvider
from app.infra.browser.browserless_wire import BrowserProviderError
from tests.infra.browser.provider_factories import fixture, prove


@pytest.mark.parametrize("mutation", ["none", "fresh", "subject", "source", "lease"])
def test_business_authority_requires_verified_start_and_current_subject(mutation) -> None:
    async def run():
        f = fixture()
        session = await f.provider.acquire(f.authority.binding)
        await f.provider.restore(session, None)
        source = DecisionSource(
            source_id=f.authority.registration.source_id,
            fixture_digest=f.authority.registration.fixture_digest,
            origin=f.authority.registration.origins[0],
        )
        if mutation != "fresh":
            profile = await f.provider.capture(session)
            await f.provider.terminate(session)
            session = await f.provider.acquire(f.authority.binding)
            await f.provider.restore(session, profile)
        if mutation == "subject":
            f.authority.subject_digest = "f" * 64
        elif mutation == "source":
            source = source.model_copy(update={"fixture_digest": "f" * 64})
        elif mutation == "lease":
            f.authority.binding = f.authority.binding.model_copy(update={"lease_epoch": 99})
        if mutation == "none":
            live = await f.provider.assert_business_authority(session, source)
            assert live.session == session
        else:
            with pytest.raises(BrowserProviderError) as caught:
                await f.provider.assert_business_authority(session, source)
            assert caught.value.failure.code in {"denied", "subject_mismatch", "stale"}
        assert f.provider.occupied == 1

    asyncio.run(run())


def test_disabled_provider_never_evaluates_credentials_or_opens_clients() -> None:
    async def run():
        def forbidden():
            raise AssertionError("disabled_provider_touched_factory")

        provider = BrowserlessProvider(credential=forbidden)
        caps = await provider.capabilities()
        assert not any(
            (
                caps.cookies,
                caps.local_storage,
                caps.indexed_db,
                caps.immutable_capture,
                caps.confirmed_termination,
            )
        )
        with pytest.raises(BrowserProviderError) as error:
            await provider.acquire(fixture().authority.binding)
        assert error.value.reason == "disabled" and provider.occupied == 0

    asyncio.run(run())


@pytest.mark.parametrize("transport", ["cdp", "playwright"])
def test_reservation_fresh_launch_exact_transport_and_capture_restore(transport) -> None:
    async def run():
        f = fixture(transport)
        session = await f.provider.acquire(f.authority.binding)
        assert f.provider.occupied == 1 and f.http.calls == [] and f.connector.calls == []
        result = await f.provider.restore(session, None)
        assert result.status == "fresh" and result.subject_digest is None
        assert f.connector.calls[0][1] == transport
        endpoint = f.connector.calls[0][0]
        expected_path = (
            "/chromium/playwright?" if transport == "playwright" else "/session/connect/"
        )
        assert expected_path in endpoint
        first = await f.provider.capture(session)
        second = await f.provider.capture(session)
        assert first.generation_ref != second.generation_ref
        assert (first.profile_revision, second.profile_revision) == (1, 2)
        assert all("/profile/refresh" not in call[1] for call in f.http.calls)
        uploads = [json.loads(call[2]) for call in f.http.calls if "/profile/upload?" in call[1]]
        assert len(uploads) == 2
        assert uploads[0]["state"]["origins"][0]["indexedDBs"][0]["objectStores"][0]["entries"] == [
            {"key": 1, "value": {"label": "fixture"}}
        ]
        assert (await f.provider.terminate(session)).status == "terminated"
        restored = await f.provider.acquire(f.authority.binding)
        verified = await f.provider.restore(restored, first)
        assert verified.status == "subject_verified"
        assert verified.subject_digest == first.subject_digest
        assert (await f.provider.resolve_live(restored)).session == restored

    asyncio.run(run())


def test_unverified_capabilities_do_not_become_true_and_capture_is_unsupported() -> None:
    async def run():
        f = fixture(evidence=False)
        caps = await f.provider.capabilities()
        assert not caps.cookies and not caps.indexed_db and not caps.confirmed_termination
        session = await f.provider.acquire(f.authority.binding)
        await f.provider.restore(session, None)
        with pytest.raises(BrowserProviderError) as error:
            await f.provider.capture(session)
        assert error.value.failure.code == "unsupported"
        assert len(f.http.calls) == 1

    asyncio.run(run())


def test_cross_owner_stale_binding_and_unregistered_source_fail_before_send() -> None:
    async def run():
        f = fixture()
        session = await f.provider.acquire(f.authority.binding)
        other = session.model_copy(
            update={
                "binding": session.binding.model_copy(
                    update={"lease_epoch": session.binding.lease_epoch + 1},
                )
            }
        )
        with pytest.raises(BrowserProviderError, match="binding"):
            await f.provider.restore(other, None)
        f.authority.registration = f.authority.registration.model_copy(
            update={"fixture_digest": "e" * 64},
        )
        with pytest.raises(BrowserProviderError, match="source"):
            await f.provider.restore(session, None)
        assert f.http.calls == [] and f.connector.calls == []

    asyncio.run(run())


def test_subject_mismatch_after_profile_launch_quarantines_and_retains_capacity() -> None:
    async def run():
        f = fixture()
        session = await f.provider.acquire(f.authority.binding)
        await f.provider.restore(session, None)
        profile = await f.provider.capture(session)
        await f.provider.terminate(session)
        f.authority.subject_digest = "e" * 64
        restored = await f.provider.acquire(f.authority.binding)
        with pytest.raises(BrowserProviderError) as error:
            await f.provider.restore(restored, profile)
        assert error.value.failure.code == "subject_mismatch"
        assert error.value.failure.cleanup_required
        assert f.provider.occupied == 1
        with pytest.raises(BrowserProviderError, match="quarantined"):
            await f.provider.resolve_live(restored)

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["timeout", "transport", "cancelled"])
def test_connection_failure_is_not_retried_or_released(kind) -> None:
    async def run():
        f = fixture("playwright", capacity=1)
        session = await f.provider.acquire(f.authority.binding)
        f.connector.failure = {
            "timeout": TimeoutError(),
            "transport": RuntimeError(),
            "cancelled": asyncio.CancelledError(),
        }[kind]
        exception = asyncio.CancelledError if kind == "cancelled" else BrowserProviderError
        with pytest.raises(exception):
            await f.provider.restore(session, None)
        assert len(f.connector.calls) == 1 and f.provider.occupied == 1
        with pytest.raises(BrowserProviderError, match="capacity"):
            await f.provider.acquire(f.authority.binding)

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["no_proof", "wrong_proof", "close_failure", "delete_failure"])
def test_cleanup_failure_quarantines_without_returning_capacity(failure) -> None:
    async def wrong_proof(request):
        return (await prove(request)).model_copy(update={"challenge": "different"})

    async def run():
        f = fixture(prover=None if failure == "no_proof" else wrong_proof)
        session = await f.provider.acquire(f.authority.binding)
        await f.provider.restore(session, None)
        if failure == "close_failure":
            f.connector.live.browser.failure = RuntimeError()
        if failure == "delete_failure":
            f.http.status = 503
        outcome = await f.provider.release(session)
        assert outcome.status == "quarantined" and f.provider.occupied == 1

    asyncio.run(run())


def test_cleanup_can_run_after_revocation_and_is_idempotent() -> None:
    async def run():
        f = fixture(capacity=1)
        session = await f.provider.acquire(f.authority.binding)
        await f.provider.restore(session, None)
        f.authority.binding = f.authority.binding.model_copy(update={"authorization_revision": 99})
        first = await f.provider.terminate(session)
        second = await f.provider.terminate(session)
        assert first == second and first.status == "terminated" and f.provider.occupied == 0
        assert len([call for call in f.http.calls if call[0] == "DELETE"]) == 1

    asyncio.run(run())


def test_never_launched_reservation_is_released_without_network() -> None:
    async def run():
        f = fixture()
        session = await f.provider.acquire(f.authority.binding)
        assert (await f.provider.release(session)).status == "released"
        assert f.provider.occupied == 0 and f.http.calls == []

    asyncio.run(run())


def test_profile_diagnostics_failure_retains_orphan_and_does_not_retry() -> None:
    async def run():
        f = fixture()
        session = await f.provider.acquire(f.authority.binding)
        await f.provider.restore(session, None)
        f.http.corrupt = lambda result: result["diagnostics"].update(truncatedIdbEntries=1)
        with pytest.raises(BrowserProviderError, match="profile_loss"):
            await f.provider.capture(session)
        assert len(f.provider.orphan_generations) == 1
        assert len([call for call in f.http.calls if "/profile/upload?" in call[1]]) == 1
        assert f.provider.occupied == 1

    asyncio.run(run())


def test_late_termination_proof_reconciles_quarantine_without_repeating_delete() -> None:
    proofs = []

    async def delayed(request):
        proofs.append(request.challenge)
        return None if len(proofs) == 1 else await prove(request)

    async def run():
        f = fixture(prover=delayed, capacity=1)
        session = await f.provider.acquire(f.authority.binding)
        await f.provider.restore(session, None)
        assert (await f.provider.terminate(session)).status == "quarantined"
        assert f.provider.occupied == 1
        assert (await f.provider.terminate(session)).status == "terminated"
        assert f.provider.occupied == 0 and proofs[0] != proofs[1]
        assert len([call for call in f.http.calls if call[0] == "DELETE"]) == 1

    asyncio.run(run())


def test_binding_changes_during_upload_leave_orphan_and_no_profile_success() -> None:
    async def run():
        f = fixture()
        session = await f.provider.acquire(f.authority.binding)
        await f.provider.restore(session, None)

        def revoke(result):
            f.authority.binding = f.authority.binding.model_copy(update={"lease_epoch": 999})

        f.http.corrupt = revoke
        with pytest.raises(BrowserProviderError, match="stale"):
            await f.provider.capture(session)
        assert len(f.provider.orphan_generations) == 1
        assert len([call for call in f.http.calls if "/profile/upload?" in call[1]]) == 1
        assert f.provider.occupied == 1

    asyncio.run(run())


def test_authority_failure_is_sanitized_before_any_provider_dispatch() -> None:
    async def run():
        f = fixture()
        marker = f.http.credential

        async def failed(expected):
            raise RuntimeError(marker)

        f.authority.current = failed
        with pytest.raises(BrowserProviderError) as error:
            await f.provider.acquire(f.authority.binding)
        assert marker not in str(error.value)
        assert error.value.failure.code == "denied"
        assert f.http.calls == [] and f.provider.occupied == 0

    asyncio.run(run())


@pytest.mark.parametrize("transport", ["cdp", "playwright"])
@pytest.mark.parametrize("change", ["authorization_revision", "lease_epoch", "owner"])
def test_restore_rechecks_binding_after_suspended_source_before_any_send(transport, change) -> None:
    async def run():
        f = fixture(transport)
        session = await f.provider.acquire(f.authority.binding)
        entered, resume = asyncio.Event(), asyncio.Event()

        async def pending_source(expected):
            entered.set()
            await resume.wait()
            return f.authority.registration

        f.authority.source = pending_source
        restoring = asyncio.create_task(f.provider.restore(session, None))
        await entered.wait()
        updated = (
            f.authority.binding.owner.model_copy(update={"user_id": "another_user"})
            if change == "owner" else 999
        )
        f.authority.binding = f.authority.binding.model_copy(update={change: updated})
        resume.set()
        with pytest.raises(BrowserProviderError) as caught:
            await restoring
        assert caught.value.failure.code == "stale"
        assert caught.value.failure.dispatch_state == "not_sent"
        assert f.http.calls == [] and f.connector.calls == []
        assert f.provider.occupied == 1
        assert (await f.provider.release(session)).status == "released"
        assert f.authority.cleanup_calls == [session.binding]

    asyncio.run(run())


def test_create_session_response_revocation_prevents_cdp_connect_and_retains_capacity() -> None:
    async def run():
        f = fixture()
        session = await f.provider.acquire(f.authority.binding)

        def revoke(result):
            f.authority.binding = f.authority.binding.model_copy(update={"lease_epoch": 999})

        f.http.corrupt = revoke
        with pytest.raises(BrowserProviderError) as caught:
            await f.provider.restore(session, None)
        assert caught.value.failure.code == "stale"
        assert caught.value.failure.dispatch_state == "possibly_sent"
        assert caught.value.failure.cleanup_required
        assert len(f.http.calls) == 1 and len(f.connector.calls) == 0
        assert f.provider.occupied == 1

    asyncio.run(run())


@pytest.mark.parametrize("state", ["reserved", "active", "terminated"])
@pytest.mark.parametrize("failure", ["wrong_claim", "denied", "timeout", "cancelled"])
def test_cleanup_requires_independent_authority_before_close_delete_or_local_release(
    state, failure,
) -> None:
    async def run():
        f = fixture()
        session = await f.provider.acquire(f.authority.binding)
        if state != "reserved":
            await f.provider.restore(session, None)
        if state == "terminated":
            await f.provider.terminate(session)
        before_calls = list(f.http.calls)
        before_closed = f.connector.live.browser.closed if f.connector.live else 0
        before_occupied = f.provider.occupied

        async def rejected(original):
            if failure == "wrong_claim":
                return original.model_copy(update={"authorization_revision": 999})
            if failure == "timeout":
                raise TimeoutError("private_cleanup_detail")
            if failure == "cancelled":
                raise asyncio.CancelledError("private_cleanup_detail")
            raise RuntimeError("private_cleanup_detail")

        f.authority.cleanup = rejected
        expected = asyncio.CancelledError if failure == "cancelled" else BrowserProviderError
        with pytest.raises(expected) as caught:
            await f.provider.release(session)
        if failure != "cancelled":
            assert caught.value.failure.code == ("timeout" if failure == "timeout" else "denied")
            assert caught.value.failure.dispatch_state == "not_sent"
        assert "private_cleanup_detail" not in str(caught.value)
        assert f.http.calls == before_calls and f.provider.occupied == before_occupied
        assert (f.connector.live.browser.closed if f.connector.live else 0) == before_closed

    asyncio.run(run())


def test_cleanup_authority_is_rechecked_after_close_before_delete() -> None:
    async def run():
        f = fixture()
        session = await f.provider.acquire(f.authority.binding)
        await f.provider.restore(session, None)

        async def revoke_during_close():
            f.authority.cleanup_binding = session.binding.model_copy(update={"lease_epoch": 999})

        f.connector.live.browser.close = revoke_during_close
        outcome = await f.provider.terminate(session)
        assert outcome.status == "quarantined" and f.provider.occupied == 1
        assert not any(call[0] == "DELETE" for call in f.http.calls)

    asyncio.run(run())


def test_cleanup_authority_is_rechecked_after_async_termination_proof() -> None:
    async def run():
        f = fixture()
        session = await f.provider.acquire(f.authority.binding)
        await f.provider.restore(session, None)

        async def revoke_during_proof(request):
            f.authority.cleanup_binding = session.binding.model_copy(update={"lease_epoch": 999})
            return await prove(request)

        f.provider._prover = revoke_during_proof
        outcome = await f.provider.terminate(session)
        assert outcome.status == "quarantined" and f.provider.occupied == 1
        assert len([call for call in f.http.calls if call[0] == "DELETE"]) == 1

    asyncio.run(run())


def test_source_revocation_blocks_subject_authority_and_capture_read() -> None:
    async def run():
        f = fixture()
        session = await f.provider.acquire(f.authority.binding)
        await f.provider.restore(session, None)
        entered, resume = asyncio.Event(), asyncio.Event()
        subject_calls = []
        storage_calls = []
        original_subject = f.authority.subject
        original_storage = f.connector.live.context.storage_state

        async def pending_source(expected):
            entered.set()
            await resume.wait()
            return f.authority.registration

        async def subject(*args):
            subject_calls.append(True)
            return await original_subject(*args)

        async def storage(**kwargs):
            storage_calls.append(True)
            return await original_storage(**kwargs)

        f.authority.source = pending_source
        f.authority.subject = subject
        f.connector.live.context.storage_state = storage
        capturing = asyncio.create_task(f.provider.capture(session))
        await entered.wait()
        f.authority.binding = session.binding.model_copy(update={"authorization_revision": 999})
        resume.set()
        with pytest.raises(BrowserProviderError) as caught:
            await capturing
        assert caught.value.failure.code == "stale"
        assert caught.value.failure.dispatch_state == "not_sent"
        assert subject_calls == [] and storage_calls == []
        assert len(f.http.calls) == 1 and f.provider.orphan_generations == ()

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["deadline", "cancel"])
def test_pending_cleanup_deadline_and_external_cancel_do_not_close_or_delete(failure) -> None:
    async def run():
        f = fixture()
        session = await f.provider.acquire(f.authority.binding)
        await f.provider.restore(session, None)
        f.provider._deployment = f.provider._deployment.model_copy(update={"timeout_seconds": 0.02})
        entered, stopped = asyncio.Event(), asyncio.Event()

        async def pending_cleanup(original):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        f.authority.cleanup = pending_cleanup
        cleaning = asyncio.create_task(f.provider.terminate(session))
        await entered.wait()
        if failure == "cancel":
            cleaning.cancel("private_cancel_detail")
            with pytest.raises(asyncio.CancelledError) as caught:
                await cleaning
            assert "private_cancel_detail" not in str(caught.value)
        else:
            with pytest.raises(BrowserProviderError) as caught:
                await cleaning
            assert caught.value.failure.code == "timeout"
            assert caught.value.failure.dispatch_state == "not_sent"
        assert stopped.is_set() and f.provider.occupied == 1
        assert f.connector.live.browser.closed == 0 and len(f.http.calls) == 1

    asyncio.run(run())


def test_cleanup_other_owner_claim_is_rejected_before_authority_callback() -> None:
    async def run():
        f = fixture()
        session = await f.provider.acquire(f.authority.binding)
        owner = session.binding.owner.model_copy(update={"user_id": "another_user"})
        claim = session.binding.model_copy(update={"owner": owner})
        forged = session.model_copy(update={"binding": claim})
        with pytest.raises(BrowserProviderError) as caught:
            await f.provider.terminate(forged)
        assert caught.value.failure.code == "denied"
        assert f.authority.cleanup_calls == [] and f.provider.occupied == 1
        assert len(f.http.calls) == 0

    asyncio.run(run())
