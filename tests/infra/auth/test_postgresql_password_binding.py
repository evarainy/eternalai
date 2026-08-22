"""PostgreSQL password binding, terminal state, and advisory-lock proofs."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import text

from app.credential_polling import CredentialPollingPolicy, CredentialPollingService
from app.infra.auth.background import OAPasswordCredentialAcquirer
from app.infra.auth.postgresql import PostgreSQLCredentialStore
from app.infra.identity.postgresql import PostgreSQLOAIdentityMapping
from app.ports.auth import (
    CredentialStoreError,
    LoginCredential,
    OASessionCredential,
    Principal,
    PrincipalOrgContext,
)
from app.ports.credential_binding import (
    CredentialAcquisitionError,
    CredentialPollCandidate,
    PasswordBindingCredential,
)

DATABASE_URL = os.environ.get("DATABASE_URL")

def _require_db() -> str:
    if not DATABASE_URL:
        raise AssertionError("DATABASE_URL must be set by the test runner environment")
    return DATABASE_URL


def _run(coroutine: Coroutine[Any, Any, None]) -> None:
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(coroutine)


def _password() -> PasswordBindingCredential:
    return PasswordBindingCredential(
        login_id=SecretStr("synthetic-login-" + uuid4().hex),
        password=SecretStr("synthetic-password-" + uuid4().hex),
    )


def _session_credential() -> OASessionCredential:
    return OASessionCredential(
        oa_user_id=SecretStr("synthetic-oa-user-" + uuid4().hex),
        cookies={"synthetic_name": SecretStr("synthetic-cookie-" + uuid4().hex)},
        expires_at=datetime.now(UTC) + timedelta(hours=2),
    )


def _ai_user_id() -> str:
    return f"usr_v1_{uuid4().hex}{uuid4().hex[:11]}"


class UserScopedCredentialStore(PostgreSQLCredentialStore):
    def __init__(self, *, ai_user_id: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._test_ai_user_id = ai_user_id

    async def list_poll_candidates(self) -> list[CredentialPollCandidate]:
        return [
            candidate
            for candidate in await super().list_poll_candidates()
            if candidate.ai_user_id == self._test_ai_user_id
        ]


def test_unbind_password_preserves_active_oa_session() -> None:
    from app.db.session import make_async_engine, make_async_session_factory

    async def exercise() -> None:
        engine = make_async_engine(_require_db())
        factory = make_async_session_factory(engine)
        ai_user_id = _ai_user_id()
        store = PostgreSQLCredentialStore(
            session_factory=factory,
            encryption_key=bytes(range(32)),
        )
        mapping = PostgreSQLOAIdentityMapping(session_factory=factory)
        session_credential = _session_credential()
        try:
            await store.store(ai_user_id, "oa", session_credential)
            await store.bind_password(ai_user_id, "oa", _password())

            view = await store.unbind_password(ai_user_id, "oa")
            binding = await mapping.get_mapping(ai_user_id, "oa")
            loaded = await store.load(ai_user_id, "oa")

            assert view.bound is False
            assert view.poll_status == "unbound"
            assert binding is not None
            assert binding.bind_status == "active"
            assert loaded is not None
            assert loaded.oa_user_id == session_credential.oa_user_id
        finally:
            async with factory() as session:
                await session.execute(
                    text(
                        "DELETE FROM oa_session_credentials"
                        " WHERE ai_user_id = :ai_user_id"
                    ),
                    {"ai_user_id": ai_user_id},
                )
                await session.commit()
            await engine.dispose()

    _run(exercise())


def test_bind_password_does_not_clear_administrator_revocation() -> None:
    from app.db.session import make_async_engine, make_async_session_factory

    async def exercise() -> None:
        engine = make_async_engine(_require_db())
        factory = make_async_session_factory(engine)
        ai_user_id = _ai_user_id()
        store = PostgreSQLCredentialStore(
            session_factory=factory,
            encryption_key=bytes(range(32)),
        )
        mapping = PostgreSQLOAIdentityMapping(session_factory=factory)
        try:
            await store.store(ai_user_id, "oa", _session_credential())
            revoked = await mapping.revoke_mapping(f"oa-session-v1:{ai_user_id}")
            assert revoked is not None
            assert revoked.mapping.bind_status == "revoked"

            await store.bind_password(ai_user_id, "oa", _password())

            binding = await mapping.get_mapping(ai_user_id, "oa")
            assert binding is not None
            assert binding.bind_status == "revoked"
            with pytest.raises(CredentialStoreError):
                await store.load(ai_user_id, "oa")
        finally:
            async with factory() as session:
                await session.execute(
                    text(
                        "DELETE FROM oa_session_credentials"
                        " WHERE ai_user_id = :ai_user_id"
                    ),
                    {"ai_user_id": ai_user_id},
                )
                await session.commit()
            await engine.dispose()

    _run(exercise())


def test_unbind_without_password_is_a_session_preserving_no_op() -> None:
    from app.db.session import make_async_engine, make_async_session_factory

    async def exercise() -> None:
        engine = make_async_engine(_require_db())
        factory = make_async_session_factory(engine)
        ai_user_id = _ai_user_id()
        store = PostgreSQLCredentialStore(
            session_factory=factory,
            encryption_key=bytes(range(32)),
        )
        mapping = PostgreSQLOAIdentityMapping(session_factory=factory)
        session_credential = _session_credential()
        try:
            await store.store(ai_user_id, "oa", session_credential)

            view = await store.unbind_password(ai_user_id, "oa")
            binding = await mapping.get_mapping(ai_user_id, "oa")
            loaded = await store.load(ai_user_id, "oa")

            assert view.bound is False
            assert binding is not None
            assert binding.bind_status == "active"
            assert loaded is not None
            assert loaded.oa_user_id == session_credential.oa_user_id
        finally:
            async with factory() as session:
                await session.execute(
                    text(
                        "DELETE FROM oa_session_credentials"
                        " WHERE ai_user_id = :ai_user_id"
                    ),
                    {"ai_user_id": ai_user_id},
                )
                await session.commit()
            await engine.dispose()

    _run(exercise())


def test_password_only_row_is_encrypted_and_supports_independent_systems() -> None:
    from app.db.session import make_async_engine, make_async_session_factory

    async def exercise() -> None:
        engine = make_async_engine(_require_db())
        factory = make_async_session_factory(engine)
        ai_user_id = f"usr_v1_{uuid4().hex}"
        store = UserScopedCredentialStore(
            ai_user_id=ai_user_id,
            session_factory=factory,
            encryption_key=bytes(range(32)),
        )
        oa_password = _password()
        u8_password = _password()
        try:
            await store.bind_password(ai_user_id, "oa", oa_password)
            await store.bind_password(ai_user_id, "u8", u8_password)
            loaded_oa = await store.load_password_for_poll(ai_user_id, "oa")
            loaded_u8 = await store.load_password_for_poll(ai_user_id, "u8")
            assert loaded_oa.login_id.get_secret_value() == (
                oa_password.login_id.get_secret_value()
            )
            assert loaded_u8.password.get_secret_value() == (
                u8_password.password.get_secret_value()
            )
            candidates = [
                candidate
                for candidate in await store.list_poll_candidates()
                if candidate.ai_user_id == ai_user_id
            ]
            assert [candidate.target_system for candidate in candidates] == ["oa"]

            async with factory() as session:
                rows = (
                    await session.execute(
                        text(
                            "SELECT target_system, cipher_version, nonce,"
                            " encrypted_payload, expires_at, encrypted_password_payload"
                            " FROM oa_session_credentials"
                            " WHERE ai_user_id = :ai_user_id"
                            " ORDER BY target_system"
                        ),
                        {"ai_user_id": ai_user_id},
                    )
                ).mappings().all()
            assert [row["target_system"] for row in rows] == ["oa", "u8"]
            for row in rows:
                assert all(
                    row[name] is None
                    for name in ("cipher_version", "nonce", "encrypted_payload", "expires_at")
                )
                encrypted = bytes(row["encrypted_password_payload"])
                assert oa_password.password.get_secret_value().encode() not in encrypted
                assert u8_password.password.get_secret_value().encode() not in encrypted

            await store.mark_terminal_authentication_failure(ai_user_id, "oa", "invalid")
            oa_view = await store.get_password_binding(ai_user_id, "oa")
            u8_view = await store.get_password_binding(ai_user_id, "u8")
            assert oa_view.poll_status == "invalid"
            assert oa_view.poll_failure_count == 0
            assert u8_view.poll_status == "active"
            assert u8_view.poll_failure_count == 0
            assert (await store.load_password_for_poll(ai_user_id, "u8")).password == (
                u8_password.password
            )
        finally:
            async with factory() as session:
                await session.execute(
                    text(
                        "DELETE FROM oa_session_credentials"
                        " WHERE ai_user_id = :ai_user_id"
                    ),
                    {"ai_user_id": ai_user_id},
                )
                await session.commit()
            await engine.dispose()

    _run(exercise())


class RejectingAcquirer:
    async def acquire(self, candidate: CredentialPollCandidate) -> Principal:
        del candidate
        raise CredentialAcquisitionError("credentials_rejected")


class SuccessfulWorkObjects:
    async def sync_for_background(self, principal: Principal) -> object:
        del principal
        return object()


def test_password_rejection_persists_invalid_until_rebind_restores_polling() -> None:
    from app.db.session import make_async_engine, make_async_session_factory

    async def exercise() -> None:
        engine = make_async_engine(_require_db())
        factory = make_async_session_factory(engine)
        ai_user_id = f"usr_v1_{uuid4().hex}"
        store = UserScopedCredentialStore(
            ai_user_id=ai_user_id,
            session_factory=factory,
            encryption_key=bytes(range(32)),
        )
        now = datetime.now(UTC) + timedelta(minutes=11)
        try:
            await store.bind_password(ai_user_id, "oa", _password())
            service = CredentialPollingService(
                binding_store=store,
                acquirer=RejectingAcquirer(),
                work_objects=SuccessfulWorkObjects(),
                policy=_policy(),
                clock=lambda: now,
            )
            assert await service.run_due() == 1
            view = await store.get_password_binding(ai_user_id, "oa")
            assert view.poll_status == "invalid"
            assert view.poll_failure_count == 0
            assert await store.refresh_poll_candidate(ai_user_id, "oa") is None
            assert not any(
                candidate.ai_user_id == ai_user_id
                for candidate in await store.list_poll_candidates()
            )

            await store.mark_non_authentication_failure(ai_user_id, "oa")
            unchanged = await store.get_password_binding(ai_user_id, "oa")
            assert unchanged.poll_status == "invalid"
            assert unchanged.poll_failure_count == 0
            async with factory() as session:
                revoked_at = (
                    await session.execute(
                        text(
                            "SELECT revoked_at FROM oa_session_credentials"
                            " WHERE ai_user_id = :ai_user_id AND target_system = 'oa'"
                        ),
                        {"ai_user_id": ai_user_id},
                    )
                ).scalar_one()
            assert revoked_at is not None

            rebound = await store.bind_password(ai_user_id, "oa", _password())
            current = await store.get_password_binding(ai_user_id, "oa")
            refreshed = await store.refresh_poll_candidate(ai_user_id, "oa")
            assert rebound.bound is True
            assert rebound.poll_status == "active"
            assert current.bound is True
            assert current.poll_status == "active"
            assert refreshed is not None
            assert refreshed.ai_user_id == ai_user_id
            assert refreshed.target_system == "oa"
            assert any(
                candidate.ai_user_id == ai_user_id
                and candidate.target_system == "oa"
                for candidate in await store.list_poll_candidates()
            )
            async with factory() as session:
                reactivated_at = (
                    await session.execute(
                        text(
                            "SELECT revoked_at FROM oa_session_credentials"
                            " WHERE ai_user_id = :ai_user_id AND target_system = 'oa'"
                        ),
                        {"ai_user_id": ai_user_id},
                    )
                ).scalar_one()
            assert reactivated_at is None
        finally:
            async with factory() as session:
                await session.execute(
                    text(
                        "DELETE FROM oa_session_credentials"
                        " WHERE ai_user_id = :ai_user_id"
                    ),
                    {"ai_user_id": ai_user_id},
                )
                await session.commit()
            await engine.dispose()

    _run(exercise())


class CaptchaAcquirer:
    async def acquire(self, candidate: CredentialPollCandidate) -> Principal:
        del candidate
        raise CredentialAcquisitionError("captcha_required")


def test_captcha_terminal_state_is_persistent_and_stale_update_cannot_revive_it() -> None:
    from app.db.session import make_async_engine, make_async_session_factory

    async def exercise() -> None:
        engine = make_async_engine(_require_db())
        factory = make_async_session_factory(engine)
        ai_user_id = _ai_user_id()
        store = UserScopedCredentialStore(
            ai_user_id=ai_user_id,
            session_factory=factory,
            encryption_key=bytes(range(32)),
        )
        mapping = PostgreSQLOAIdentityMapping(session_factory=factory)
        session_credential = _session_credential()
        now = datetime.now(UTC) + timedelta(minutes=11)
        try:
            await store.store(ai_user_id, "oa", session_credential)
            await store.bind_password(ai_user_id, "oa", _password())
            service = CredentialPollingService(
                binding_store=store,
                acquirer=CaptchaAcquirer(),
                work_objects=SuccessfulWorkObjects(),
                policy=_policy(),
                clock=lambda: now,
            )
            assert await service.run_due() == 1
            view = await store.get_password_binding(ai_user_id, "oa")
            assert view.poll_status == "captcha_required"
            assert view.poll_failure_count == 0
            assert await store.refresh_poll_candidate(ai_user_id, "oa") is None
            binding = await mapping.get_mapping(ai_user_id, "oa")
            loaded = await store.load(ai_user_id, "oa")
            assert binding is not None
            assert binding.bind_status == "active"
            assert loaded is not None
            assert loaded.oa_user_id == session_credential.oa_user_id

            await store.mark_non_authentication_failure(ai_user_id, "oa")
            unchanged = await store.get_password_binding(ai_user_id, "oa")
            assert unchanged.poll_status == "captcha_required"
            assert unchanged.poll_failure_count == 0
        finally:
            async with factory() as session:
                await session.execute(
                    text(
                        "DELETE FROM oa_session_credentials"
                        " WHERE ai_user_id = :ai_user_id"
                    ),
                    {"ai_user_id": ai_user_id},
                )
                await session.commit()
            await engine.dispose()

    _run(exercise())


class BlockingAcquirer:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.active = 0
        self.maximum_active = 0
        self.calls = 0

    async def acquire(self, candidate: CredentialPollCandidate) -> Principal:
        self.calls += 1
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        self.started.set()
        await self.release.wait()
        self.active -= 1
        return Principal(
            ai_user_id=candidate.ai_user_id,
            display_name="Synthetic User",
            roles=(),
            org_ctx=PrincipalOrgContext(),
        )


def test_advisory_lock_prevents_multi_instance_concurrent_authentication() -> None:
    from app.db.session import make_async_engine, make_async_session_factory

    async def exercise() -> None:
        engine = make_async_engine(_require_db())
        factory = make_async_session_factory(engine)
        ai_user_id = f"usr_v1_{uuid4().hex}"
        store_a = UserScopedCredentialStore(
            ai_user_id=ai_user_id,
            session_factory=factory,
            encryption_key=bytes(range(32)),
        )
        store_b = UserScopedCredentialStore(
            ai_user_id=ai_user_id,
            session_factory=factory,
            encryption_key=bytes(range(32)),
        )
        acquirer = BlockingAcquirer()
        now = datetime.now(UTC)
        try:
            await store_a.bind_password(ai_user_id, "oa", _password())
            async with factory() as session:
                await session.execute(
                    text(
                        "UPDATE oa_session_credentials"
                        " SET updated_at = :updated_at"
                        " WHERE ai_user_id = :ai_user_id AND target_system = 'oa'"
                    ),
                    {
                        "ai_user_id": ai_user_id,
                        "updated_at": now - timedelta(minutes=11),
                    },
                )
                await session.commit()
            stale_candidate = next(
                candidate
                for candidate in await store_a.list_poll_candidates()
                if candidate.ai_user_id == ai_user_id
                and candidate.target_system == "oa"
            )
            service_a = CredentialPollingService(
                binding_store=store_a,
                acquirer=acquirer,
                work_objects=SuccessfulWorkObjects(),
                policy=_policy(),
                clock=lambda: now,
            )
            service_b = CredentialPollingService(
                binding_store=store_b,
                acquirer=acquirer,
                work_objects=SuccessfulWorkObjects(),
                policy=_policy(),
                clock=lambda: now,
            )
            first = asyncio.create_task(service_a.run_due())
            await acquirer.started.wait()
            second = asyncio.create_task(service_b.run_due())
            await second
            acquirer.release.set()
            await first
            assert acquirer.calls == 1
            assert acquirer.maximum_active == 1

            class StaleListingStore(PostgreSQLCredentialStore):
                async def list_poll_candidates(self) -> list[CredentialPollCandidate]:
                    return [stale_candidate]

            stale_store = StaleListingStore(
                session_factory=factory,
                encryption_key=bytes(range(32)),
            )
            stale_service = CredentialPollingService(
                binding_store=stale_store,
                acquirer=acquirer,
                work_objects=SuccessfulWorkObjects(),
                policy=_policy(),
                clock=lambda: now,
            )
            assert await stale_service.run_due() == 1
            assert acquirer.calls == 1
        finally:
            async with factory() as session:
                await session.execute(
                    text(
                        "DELETE FROM oa_session_credentials"
                        " WHERE ai_user_id = :ai_user_id"
                    ),
                    {"ai_user_id": ai_user_id},
                )
                await session.commit()
            await engine.dispose()

    _run(exercise())


class CaptchaNotRequiredSession:
    async def get_json(
        self,
        path: str,
        params: dict[str, str],
    ) -> dict[str, Any]:
        del path, params
        raise AssertionError("captcha preflight must use post_form")

    async def post_form(
        self,
        path: str,
        fields: dict[str, str],
    ) -> dict[str, Any]:
        assert path == "/api/hrm/login/getLoginForm"
        assert fields == {}
        return {"loginSetting": {"hasValidateCode": False}}

    def cookies(self) -> dict[str, str]:
        return {}


class RevocationRaceAuthentication:
    def __init__(
        self,
        *,
        store: PostgreSQLCredentialStore,
        ai_user_id: str,
    ) -> None:
        self._store = store
        self._ai_user_id = ai_user_id
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0
        self.reactivation_flags: list[bool] = []
        self.store_completed = False

    async def authenticate(
        self,
        credential: LoginCredential,
        *,
        reactivate_revoked_session: bool = True,
    ) -> Principal:
        del credential
        self.calls += 1
        self.reactivation_flags.append(reactivate_revoked_session)
        self.started.set()
        await self.release.wait()
        await self._store.store(
            self._ai_user_id,
            "oa",
            _session_credential(),
            reactivate_revoked_session=reactivate_revoked_session,
        )
        self.store_completed = True
        return Principal(
            ai_user_id=self._ai_user_id,
            display_name="Synthetic User",
            roles=(),
            org_ctx=PrincipalOrgContext(),
        )


def test_revocation_committed_during_poll_is_not_reactivated() -> None:
    from app.db.session import make_async_engine, make_async_session_factory

    async def exercise() -> None:
        engine = make_async_engine(_require_db())
        factory = make_async_session_factory(engine)
        ai_user_id = _ai_user_id()
        store = UserScopedCredentialStore(
            ai_user_id=ai_user_id,
            session_factory=factory,
            encryption_key=bytes(range(32)),
        )
        mapping = PostgreSQLOAIdentityMapping(session_factory=factory)
        authentication = RevocationRaceAuthentication(
            store=store,
            ai_user_id=ai_user_id,
        )
        now = datetime.now(UTC)
        polling_task: asyncio.Task[int] | None = None
        try:
            await store.store(ai_user_id, "oa", _session_credential())
            await store.bind_password(ai_user_id, "oa", _password())
            async with factory() as session:
                await session.execute(
                    text(
                        "UPDATE oa_session_credentials"
                        " SET updated_at = :updated_at"
                        " WHERE ai_user_id = :ai_user_id AND target_system = 'oa'"
                    ),
                    {
                        "ai_user_id": ai_user_id,
                        "updated_at": now - timedelta(minutes=11),
                    },
                )
                await session.commit()
            service = CredentialPollingService(
                binding_store=store,
                acquirer=OAPasswordCredentialAcquirer(
                    session_factory=CaptchaNotRequiredSession,
                    authentication=authentication,
                    binding_store=store,
                ),
                work_objects=SuccessfulWorkObjects(),
                policy=_policy(),
                clock=lambda: now,
            )

            polling_task = asyncio.create_task(service.run_due())
            await authentication.started.wait()
            revoked = await mapping.revoke_mapping(f"oa-session-v1:{ai_user_id}")
            assert revoked is not None
            assert revoked.mapping.bind_status == "revoked"
            authentication.release.set()

            assert await polling_task == 1
            assert authentication.calls == 1
            assert authentication.reactivation_flags == [False]
            assert authentication.store_completed is True
            binding = await mapping.get_mapping(ai_user_id, "oa")
            assert binding is not None
            assert binding.bind_status == "revoked"
            assert await store.refresh_poll_candidate(ai_user_id, "oa") is None
            assert not any(
                candidate.ai_user_id == ai_user_id
                for candidate in await store.list_poll_candidates()
            )
        finally:
            authentication.release.set()
            if polling_task is not None and not polling_task.done():
                await polling_task
            async with factory() as session:
                await session.execute(
                    text(
                        "DELETE FROM oa_session_credentials"
                        " WHERE ai_user_id = :ai_user_id"
                    ),
                    {"ai_user_id": ai_user_id},
                )
                await session.commit()
            await engine.dispose()

    _run(exercise())


def _policy() -> CredentialPollingPolicy:
    return CredentialPollingPolicy(
        interval_seconds=600,
        maximum_backoff_seconds=3600,
        work_start_hour=0,
        work_end_hour=24,
        timezone_name="UTC",
        global_concurrency=4,
        scheduler_tick_seconds=60,
    )
