from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from app.infra.auth.background import OAPasswordCredentialAcquirer
from app.infra.organization_directory.source_binding import BoundOrganizationDirectorySourceFactory
from app.ports.auth import OASessionCredential
from app.ports.credential_binding import PasswordBindingCredential
from app.ports.organization_directory_sync import DirectorySourceError
from tests.api.test_work_object_dispatch import dispatch_db as dispatch_db
from tests.test_credential_polling import CANDIDATE, PRINCIPAL, FakeAcquirer, FakeBindingStore


@asynccontextmanager
async def transport(credential):
    assert isinstance(credential, OASessionCredential)
    yield object()


def factory(store, acquirer, *, transport_factory=transport, user=CANDIDATE.ai_user_id):
    credential = OASessionCredential(
        oa_user_id=SecretStr("synthetic-oa-user"),
        cookies={},
        expires_at=datetime(2099, 1, 1, tzinfo=UTC),
    )
    credentials = AsyncMock()
    credentials.load.return_value = credential
    result = BoundOrganizationDirectorySourceFactory(
        ai_user_id=user,
        polling_store=store,
        acquirer=acquirer,
        credential_store=credentials,
        transport_factory=transport_factory,
        tenant_id="default",
    )
    return result, credentials


@pytest.mark.parametrize(
    "failure,expected,terminal",
    [
        ("credentials_rejected", "source_authentication_failed", "invalid"),
        ("identity_mismatch", "source_authentication_failed", "invalid"),
        ("captcha_required", "source_authentication_failed", "captcha_required"),
        ("network_unreachable", "source_unavailable", None),
        ("timeout", "source_timeout", None),
        ("local_failure", "storage_unavailable", None),
    ],
)
def test_binding_selection_never_falls_back_or_reactivates(failure, expected, terminal):
    async def exercise():
        store, acquirer = FakeBindingStore(), FakeAcquirer(failure)
        bound, credentials = factory(store, acquirer)
        with pytest.raises(DirectorySourceError) as error:
            async with bound():
                pytest.fail("unusable identity opened a source")
        assert error.value.code == expected
        assert store.terminal == ([] if terminal is None else [terminal])
        assert credentials.load.await_count == 0
        assert store.successes == store.counted_failures == store.non_counted_failures == 0
        if terminal:
            with pytest.raises(DirectorySourceError) as repeated:
                async with bound():
                    pytest.fail("terminal binding must remain unavailable")
            assert repeated.value.code == "source_binding_unavailable"
            assert acquirer.calls == 1

    asyncio.run(exercise())


def test_actual_acquirer_keeps_revoked_session_closed_and_checks_identity():
    async def exercise():
        store = FakeBindingStore()
        store.load_password_for_poll = AsyncMock(
            return_value=PasswordBindingCredential(
                login_id=SecretStr("synthetic-login"),
                password=SecretStr("synthetic-password"),
            )
        )
        authentication = AsyncMock()
        authentication.authenticate.return_value = PRINCIPAL
        session = AsyncMock()
        session.post_form.return_value = {"loginSetting": {"hasValidateCode": False}}
        acquirer = OAPasswordCredentialAcquirer(
            session_factory=lambda: session,
            authentication=authentication,
            binding_store=store,
            tenant_id="default",
        )
        bound, credentials = factory(store, acquirer)
        async with bound() as source:
            assert source is not None
        assert authentication.authenticate.await_args.kwargs == {
            "reactivate_revoked_session": False,
            "expected_subject": (CANDIDATE.tenant_id, CANDIDATE.ai_user_id),
        }
        credentials.load.assert_awaited_once_with(
            CANDIDATE.ai_user_id, "oa", tenant_id=CANDIDATE.tenant_id
        )
        assert store.successes == store.counted_failures == 0
        authentication.authenticate.return_value = PRINCIPAL.model_copy(
            update={
                "org_ctx": PRINCIPAL.org_ctx.model_copy(update={"tenant_id": "synthetic-other"}),
            }
        )
        with pytest.raises(DirectorySourceError) as error:
            async with bound():
                pytest.fail("nondefault tenant opened source")
        assert error.value.code == "source_authentication_failed"
        assert store.terminal == ["invalid"]
        assert credentials.load.await_count == 1

    asyncio.run(exercise())


def test_missing_factory_and_busy_binding_do_not_login():
    async def exercise():
        store, acquirer = FakeBindingStore(), FakeAcquirer()
        for user, transport_factory in ((None, transport), (CANDIDATE.ai_user_id, None)):
            bound, credentials = factory(
                store, acquirer, user=user, transport_factory=transport_factory
            )
            with pytest.raises(DirectorySourceError) as error:
                async with bound():
                    pytest.fail("unconfigured source opened")
            assert error.value.code == "source_unconfigured"
            assert credentials.load.await_count == 0

        @asynccontextmanager
        async def busy(user, target, *, tenant_id):
            yield False

        store.poll_lock = busy
        bound, credentials = factory(store, acquirer)
        with pytest.raises(DirectorySourceError) as error:
            async with bound():
                pytest.fail("busy binding opened")
        assert error.value.code == "source_binding_unavailable"
        assert acquirer.calls == credentials.load.await_count == store.refreshes == 0

    asyncio.run(exercise())


def test_directory_source_lock_delays_only_the_same_users_real_polling_path(dispatch_db):
    from app.credential_polling import CredentialPollingPolicy, CredentialPollingService
    from app.infra.auth.postgresql import PostgreSQLCredentialStore
    from tests.api.test_work_object_dispatch import run
    from tests.test_credential_polling import NOW

    db = dispatch_db

    async def exercise():
        actual_store = PostgreSQLCredentialStore(
            session_factory=db.factory, encryption_key=b"s" * 32
        )
        other = CANDIDATE.model_copy(update={"ai_user_id": "usr_v1_synthetic_other"})
        candidates = {item.ai_user_id: item for item in (CANDIDATE, other)}
        store = FakeBindingStore()
        store.poll_lock = actual_store.poll_lock

        async def listed(*, tenant_id):
            return list(candidates.values())

        async def refresh(user, target, *, tenant_id):
            assert target == "oa"
            return candidates.get(user)

        async def succeeded(user, target, *, tenant_id):
            store.successes += 1
            candidates.pop(user)

        store.list_poll_candidates = listed
        store.refresh_poll_candidate = refresh
        store.mark_poll_succeeded = succeeded
        acquired, synced = [], []

        class Acquirer:
            async def acquire(self, candidate):
                acquired.append(candidate.ai_user_id)
                return PRINCIPAL.model_copy(update={"ai_user_id": candidate.ai_user_id})

        class WorkObjects:
            async def sync_for_background(self, principal):
                synced.append(principal.ai_user_id)

        bound, _ = factory(store, Acquirer())
        polling = CredentialPollingService(
            binding_store=store,
            acquirer=Acquirer(),
            work_objects=WorkObjects(),
            policy=CredentialPollingPolicy(
                interval_seconds=600,
                maximum_backoff_seconds=3600,
                work_start_hour=8,
                work_end_hour=18,
                timezone_name="Asia/Shanghai",
                global_concurrency=2,
                scheduler_tick_seconds=60,
            ),
            clock=lambda: NOW,
            tenant_id="default",
        )
        async with bound():
            assert await polling.run_due() == 2
            assert acquired == [CANDIDATE.ai_user_id, other.ai_user_id]
            assert synced == [other.ai_user_id]
            assert store.successes == 1
            assert store.counted_failures == store.non_counted_failures == 0
        assert await polling.run_due() == 1
        assert acquired == [CANDIDATE.ai_user_id, other.ai_user_id, CANDIDATE.ai_user_id]
        assert synced == [other.ai_user_id, CANDIDATE.ai_user_id]
        assert store.successes == 2
        assert store.counted_failures == store.non_counted_failures == 0

    run(exercise())
