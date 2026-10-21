"""Real PostgreSQL fencing evidence; execute only in the attested task namespace."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import text

from app.db.session import make_async_engine, make_async_session_factory
from app.infra.auth.postgresql import PostgreSQLCredentialStore
from app.infra.browser.local_resource_lifecycle import registered_subject_digest
from app.ports.auth import (
    AuthenticatedSessionContext,
    CredentialStoreError,
    OASessionCredential,
    Principal,
    PrincipalOrgContext,
    StaleCredentialWrite,
    authenticated_session,
)
from app.ports.credential_binding import PasswordBindingCredential
from app.ports.credential_vault import BrowserAuthorizationError, BrowserBindingFact


def password():
    return PasswordBindingCredential(
        login_id=SecretStr(uuid4().hex), password=SecretStr(uuid4().hex)
    )


def credential():
    return OASessionCredential(
        oa_user_id=SecretStr(uuid4().hex),
        cookies={},
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


@asynccontextmanager
async def isolated(url):
    engine = make_async_engine(url)
    factory = make_async_session_factory(engine)
    store = PostgreSQLCredentialStore(session_factory=factory, encryption_key=bytes(range(32)))
    tenant, user = "synthetic-brdb01", "usr_v1_" + uuid4().hex
    try:
        yield engine, factory, store, tenant, user
    finally:
        try:
            async with factory() as session:
                await session.execute(
                    text(
                        "DELETE FROM oa_session_credentials WHERE tenant_id=:tenant AND"
                        " ai_user_id=:user AND target_system='oa'"
                    ),
                    {"tenant": tenant, "user": user},
                )
                await session.commit()
        finally:
            await engine.dispose()


async def claim(store, tenant, user):
    return await store.claim_write(await store.snapshot(user, "oa", tenant_id=tenant))


async def state(factory, tenant, user):
    async with factory() as session:
        return dict(
            (
                await session.execute(
                    text(
                        "SELECT binding_id,binding_revision,binding_state,"
                        "credential_write_revision,refresh_epoch,"
                        "refresh_operation_id,refresh_deadline,poll_status,"
                        "poll_failure_count,updated_at,revoked_at,"
                        "cipher_version,nonce,encrypted_payload,"
                        "password_cipher_version,password_nonce,encrypted_password_payload"
                        " FROM oa_session_credentials WHERE tenant_id=:tenant AND ai_user_id=:user"
                    ),
                    {"tenant": tenant, "user": user},
                )
            )
            .mappings()
            .one()
        )


def test_expected_absent_login_cannot_cross_unbind_tombstone(migrated_database_url):
    async def exercise():
        async with isolated(migrated_database_url) as (_, factory, store, tenant, user):
            before = await store.snapshot(user, "oa", tenant_id=tenant)
            assert before.absent
            await store.unbind_password(user, "oa", tenant_id=tenant)
            tombstone = await state(factory, tenant, user)
            with pytest.raises(StaleCredentialWrite):
                await store.claim_write(before)
            assert await state(factory, tenant, user) == tombstone
            assert tombstone["binding_state"] == "unbound"

    asyncio.run(exercise())


@pytest.mark.parametrize("change", ["unbind", "rebind"])
@pytest.mark.parametrize(
    "writer", ["session", "bind", "success", "counted", "local", "invalid", "captcha"]
)
def test_every_late_completion_is_fenced_after_binding_change(
    migrated_database_url, change, writer
):
    async def exercise():
        async with isolated(migrated_database_url) as (_, factory, store, tenant, user):
            await store.bind_password(
                user,
                "oa",
                password(),
                tenant_id=tenant,
                expected_write=await claim(store, tenant, user),
            )
            old = await claim(store, tenant, user)
            if change == "unbind":
                await store.unbind_password(user, "oa", tenant_id=tenant)
            else:
                await store.bind_password(
                    user,
                    "oa",
                    password(),
                    tenant_id=tenant,
                    expected_write=await claim(store, tenant, user),
                )
            current = await state(factory, tenant, user)
            with pytest.raises(StaleCredentialWrite):
                if writer == "session":
                    await store.store(
                        user,
                        "oa",
                        credential(),
                        tenant_id=tenant,
                        expected_write=old,
                        reactivate_revoked_session=False,
                    )
                elif writer == "bind":
                    await store.bind_password(
                        user, "oa", password(), tenant_id=tenant, expected_write=old
                    )
                elif writer == "success":
                    await store.mark_poll_succeeded(
                        user, "oa", tenant_id=tenant, expected_write=old
                    )
                elif writer == "counted":
                    await store.mark_non_authentication_failure(
                        user, "oa", tenant_id=tenant, expected_write=old
                    )
                elif writer == "local":
                    await store.mark_non_counted_failure(
                        user, "oa", tenant_id=tenant, expected_write=old
                    )
                else:
                    await store.mark_terminal_authentication_failure(
                        user,
                        "oa",
                        "invalid" if writer == "invalid" else "captcha_required",
                        tenant_id=tenant,
                        expected_write=old,
                    )
            assert await state(factory, tenant, user) == current
            assert current["binding_id"] == old.snapshot.binding_id
            assert current["binding_revision"] > old.snapshot.binding_revision

    asyncio.run(exercise())


def test_session_receipt_is_required_for_following_poll_metadata(migrated_database_url):
    async def exercise():
        async with isolated(migrated_database_url) as (_, factory, store, tenant, user):
            await store.bind_password(
                user,
                "oa",
                password(),
                tenant_id=tenant,
                expected_write=await claim(store, tenant, user),
            )
            old = await claim(store, tenant, user)
            receipt = await store.store(
                user,
                "oa",
                credential(),
                tenant_id=tenant,
                expected_write=old,
                reactivate_revoked_session=False,
            )
            assert receipt.snapshot.binding_revision == old.snapshot.binding_revision
            assert (
                receipt.snapshot.credential_write_revision
                == old.snapshot.credential_write_revision + 1
            )
            with pytest.raises(StaleCredentialWrite):
                await store.mark_poll_succeeded(user, "oa", tenant_id=tenant, expected_write=old)
            await store.mark_poll_succeeded(user, "oa", tenant_id=tenant, expected_write=receipt)
            current = await state(factory, tenant, user)
            assert current["poll_status"] == "active"
            assert (
                current["credential_write_revision"]
                == receipt.snapshot.credential_write_revision + 1
            )

    asyncio.run(exercise())


def test_database_deadline_and_new_epoch_fence_old_operations(migrated_database_url):
    async def exercise():
        async with isolated(migrated_database_url) as (_, factory, store, tenant, user):
            stamp = await claim(store, tenant, user)
            async with factory() as session:
                deadline = (
                    await session.execute(
                        text(
                            "UPDATE oa_session_credentials SET"
                            " refresh_deadline=clock_timestamp()-interval '1 second'"
                            " WHERE tenant_id=:tenant AND ai_user_id=:user RETURNING"
                            " refresh_deadline"
                        ),
                        {"tenant": tenant, "user": user},
                    )
                ).scalar_one()
                await session.commit()
            exact_expired = replace(stamp, deadline=deadline)
            with pytest.raises(StaleCredentialWrite):
                await store.store(
                    user, "oa", credential(), tenant_id=tenant, expected_write=exact_expired
                )
            new = await claim(store, tenant, user)
            assert new.snapshot.refresh_epoch == stamp.snapshot.refresh_epoch + 1
            with pytest.raises(StaleCredentialWrite):
                await store.store(user, "oa", credential(), tenant_id=tenant, expected_write=stamp)
            await store.store(user, "oa", credential(), tenant_id=tenant, expected_write=new)

    asyncio.run(exercise())


def test_advisory_connection_loss_requires_new_epoch_before_write(migrated_database_url):
    async def exercise():
        async with isolated(migrated_database_url) as (_, _, store, tenant, user):
            async with store.poll_lock(user, "oa", tenant_id=tenant) as locked:
                assert locked
                old = await claim(store, tenant, user)
                active = store._coordinator.get()
                assert active is not None
                await active[1].invalidate()
            async with store.poll_lock(user, "oa", tenant_id=tenant) as locked:
                assert locked
                new = await claim(store, tenant, user)
                assert new.snapshot.refresh_epoch > old.snapshot.refresh_epoch
                with pytest.raises(StaleCredentialWrite):
                    await store.store(
                        user, "oa", credential(), tenant_id=tenant, expected_write=old
                    )
                await store.store(user, "oa", credential(), tenant_id=tenant, expected_write=new)

    asyncio.run(exercise())


def test_active_browser_refresh_without_resource_subject_proof_is_closed(migrated_database_url):
    async def exercise():
        async with isolated(migrated_database_url) as (_, factory, store, tenant, user):
            await store.bind_password(
                user,
                "oa",
                password(),
                tenant_id=tenant,
                expected_write=await claim(store, tenant, user),
            )
            async with factory() as session:
                await session.execute(
                    text(
                        "UPDATE oa_session_credentials SET binding_state='active',"
                        "binding_subject_digest=:digest,"
                        "binding_subject_verified_at=clock_timestamp() WHERE"
                        " tenant_id=:tenant AND ai_user_id=:user"
                    ),
                    {"tenant": tenant, "user": user, "digest": bytes(range(32))},
                )
                await session.commit()
            stamp = await claim(store, tenant, user)
            before = await state(factory, tenant, user)
            with pytest.raises(CredentialStoreError, match="browser_subject_verifier_unavailable"):
                await store.store(
                    user,
                    "oa",
                    credential(),
                    tenant_id=tenant,
                    expected_write=stamp,
                    reactivate_revoked_session=False,
                )
            assert await state(factory, tenant, user) == before

    asyncio.run(exercise())


def test_write_revision_overflow_is_rejected_without_wrapping(migrated_database_url):
    async def exercise():
        async with isolated(migrated_database_url) as (_, factory, store, tenant, user):
            await claim(store, tenant, user)
            async with factory() as session:
                await session.execute(
                    text(
                        "UPDATE oa_session_credentials SET"
                        " credential_write_revision=9007199254740991"
                        " WHERE tenant_id=:tenant AND ai_user_id=:user"
                    ),
                    {"tenant": tenant, "user": user},
                )
                await session.commit()
            before = await state(factory, tenant, user)
            with pytest.raises(StaleCredentialWrite, match="credential_revision_exhausted"):
                await claim(store, tenant, user)
            assert await state(factory, tenant, user) == before

    asyncio.run(exercise())


def test_identity_revocation_is_idempotent_and_fences_existing_writer(migrated_database_url):
    from app.infra.identity.postgresql import PostgreSQLOAIdentityMapping

    async def exercise():
        async with isolated(migrated_database_url) as (_, factory, store, tenant, _):
            user = "usr_v1_" + uuid4().hex + uuid4().hex[:11]
            try:
                await store.store(
                    user,
                    "oa",
                    credential(),
                    tenant_id=tenant,
                    expected_write=await claim(store, tenant, user),
                )
                old = await claim(store, tenant, user)
                mapping = PostgreSQLOAIdentityMapping(session_factory=factory)
                changed = await mapping.revoke_mapping("oa-session-v1:" + user, tenant_id=tenant)
                assert changed is not None and changed.changed
                first = await state(factory, tenant, user)
                assert first["binding_revision"] == old.snapshot.binding_revision + 1
                assert first["refresh_epoch"] == old.snapshot.refresh_epoch + 1
                assert first["binding_state"] == "revoked"
                repeated = await mapping.revoke_mapping("oa-session-v1:" + user, tenant_id=tenant)
                assert repeated is not None and not repeated.changed
                assert await state(factory, tenant, user) == first
                with pytest.raises(StaleCredentialWrite):
                    await store.store(
                        user,
                        "oa",
                        credential(),
                        tenant_id=tenant,
                        expected_write=old,
                        reactivate_revoked_session=False,
                    )
            finally:
                async with factory() as session:
                    await session.execute(
                        text(
                            "DELETE FROM oa_session_credentials WHERE tenant_id=:tenant AND"
                            " ai_user_id=:user AND target_system='oa'"
                        ),
                        {"tenant": tenant, "user": user},
                    )
                    await session.commit()

    asyncio.run(exercise())


def test_lost_coordinator_cannot_use_pool_before_another_worker_claims(migrated_database_url):
    async def exercise():
        async with isolated(migrated_database_url) as (_, factory, store, tenant, user):
            async with store.poll_lock(user, "oa", tenant_id=tenant) as locked:
                assert locked
                old = await claim(store, tenant, user)
                active = store._coordinator.get()
                assert active is not None
                assert not active[1].in_transaction()
                await active[1].invalidate()
                before = await state(factory, tenant, user)
                with pytest.raises(StaleCredentialWrite, match="credential coordinator lost"):
                    await store.store(
                        user, "oa", credential(), tenant_id=tenant, expected_write=old
                    )
                with pytest.raises(StaleCredentialWrite, match="credential coordinator lost"):
                    await store.mark_non_counted_failure(
                        user, "oa", tenant_id=tenant, expected_write=old
                    )
                assert await state(factory, tenant, user) == before

    asyncio.run(exercise())


def test_anonymous_writer_guard_blocks_downgrade_marker_without_creating_owner_row(
    migrated_database_url,
):
    async def exercise():
        async with isolated(migrated_database_url) as (engine, factory, store, tenant, user):
            async with store.writer_guard():
                absent = await store.snapshot(user, "oa", tenant_id=tenant)
                assert absent.absent
                async with engine.connect() as other:
                    allowed = (
                        await other.execute(text("SELECT pg_try_advisory_xact_lock(746420210000)"))
                    ).scalar_one()
                    assert allowed is False
                async with factory() as session:
                    count = (
                        await session.execute(
                            text(
                                "SELECT count(*) FROM oa_session_credentials"
                                " WHERE tenant_id=:tenant AND ai_user_id=:user"
                            ),
                            {"tenant": tenant, "user": user},
                        )
                    ).scalar_one()
                    assert count == 0
            async with engine.connect() as other:
                allowed = (
                    await other.execute(text("SELECT pg_try_advisory_xact_lock(746420210000)"))
                ).scalar_one()
                assert allowed is True

    asyncio.run(exercise())


def test_child_tasks_do_not_inherit_authority_to_use_parent_coordinator(migrated_database_url):
    async def exercise():
        async with isolated(migrated_database_url) as (_, _, store, tenant, user):
            async with store.poll_lock(user, "oa", tenant_id=tenant) as locked:
                assert locked
                stamp = await claim(store, tenant, user)

                async def direct_write():
                    with pytest.raises(StaleCredentialWrite, match="task mismatch"):
                        await store.store(
                            user, "oa", credential(), tenant_id=tenant, expected_write=stamp
                        )

                async def same_slot():
                    async with store.poll_lock(user, "oa", tenant_id=tenant) as acquired:
                        assert acquired is False

                async def other_slot():
                    async with store.poll_lock(user + "other", "oa", tenant_id=tenant) as acquired:
                        assert acquired is True

                await asyncio.gather(direct_write(), same_slot(), other_slot())

    asyncio.run(exercise())


@asynccontextmanager
async def activation_case(url):
    async with isolated(url) as (_, factory, store, tenant, user):
        principal = Principal(ai_user_id=user, display_name="Synthetic activation",
                              roles=("synthetic-role",), org_ctx=PrincipalOrgContext(
                                  tenant_id=tenant, directory_user_id="synthetic-oa-subject",
                              ))
        session_credential = OASessionCredential(
            oa_user_id=SecretStr("synthetic-oa-subject"),
            cookies={"synthetic-session": SecretStr("synthetic-cookie")},
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        captured = AuthenticatedSessionContext(
            principal, uuid4().bytes + uuid4().bytes, datetime.now(UTC) + timedelta(minutes=10),
        )
        async with factory() as session:
            await session.execute(text(
                "INSERT INTO principal_roles (tenant_id,ai_user_id,role)"
                " VALUES (:tenant,:user,:role)",
            ), {"tenant": tenant, "user": user, "role": "synthetic-role"})
            await session.commit()
        try:
            await store.store(user, "oa", credential(), tenant_id=tenant,
                              expected_write=await claim(store, tenant, user))
            submitted = password()
            await store.bind_password(user, "oa", submitted, tenant_id=tenant,
                                      expected_write=await claim(store, tenant, user))
            snapshot = await store.snapshot(user, "oa", tenant_id=tenant)
            selected = await store.browser_activation_snapshot(
                principal, snapshot.binding_id, snapshot.binding_revision,
            )
            assert selected == snapshot
            stamp = await store.claim_write(selected)
            token = authenticated_session.set(captured)
            try:
                yield factory, store, principal, captured, submitted, stamp, session_credential
            finally:
                authenticated_session.reset(token)
        finally:
            async with factory() as session:
                await session.execute(text(
                    "DELETE FROM principal_roles WHERE tenant_id=:tenant AND ai_user_id=:user",
                ), {"tenant": tenant, "user": user})
                await session.execute(text(
                    "DELETE FROM auth_session_revocations WHERE token_fingerprint=:fp",
                ), {"fp": captured.fingerprint})
                await session.commit()


def test_first_browser_activation_is_one_revisioned_password_subject_update(migrated_database_url):
    async def exercise():
        async with activation_case(migrated_database_url) as case:
            factory, store, principal, captured, submitted, stamp, session_credential = case
            view = await store.activate_browser_binding(
                submitted, verified=principal, session_credential=session_credential,
                captured_session=captured, expected_write=stamp,
            )
            assert view.binding_id == stamp.snapshot.binding_id
            assert view.binding_revision == stamp.snapshot.binding_revision + 1
            row = await state(factory, principal.org_ctx.tenant_id, principal.ai_user_id)
            assert row["binding_state"] == "active"
            assert row["credential_write_revision"] == stamp.snapshot.credential_write_revision + 1
            assert row["refresh_operation_id"] is None and row["refresh_deadline"] is None
            assert row["cipher_version"] is not None and row["encrypted_payload"] is not None
            digest = registered_subject_digest("oa", "synthetic-oa-subject")
            fact = BrowserBindingFact(principal.org_ctx.tenant_id, principal.ai_user_id, "oa",
                                      view.binding_id, view.binding_revision, digest)
            loaded = await store.load_for_browser(fact, "browser_login")
            assert isinstance(loaded, PasswordBindingCredential)
            assert loaded.password == submitted.password
            assert (submitted.password.get_secret_value().encode()
                    not in row["encrypted_password_payload"])
            assert (session_credential.oa_user_id.get_secret_value().encode()
                    not in row["encrypted_payload"])
            assert (session_credential.cookies["synthetic-session"].get_secret_value().encode()
                    not in row["encrypted_payload"])
            restored = await store.load_for_browser(fact, "browser_restore")
            assert isinstance(restored, OASessionCredential)
            assert restored == session_credential
            foreign = replace(fact, subject_digest=registered_subject_digest("oa", "other-subject"))
            with pytest.raises(BrowserAuthorizationError) as denied:
                await store.load_for_browser(foreign, "browser_restore")
            assert denied.value.code == "browser_binding_stale"
            with pytest.raises(StaleCredentialWrite):
                await store.activate_browser_binding(
                    submitted, verified=principal, session_credential=session_credential,
                    captured_session=captured, expected_write=stamp,
                )

    asyncio.run(exercise())


@pytest.mark.parametrize("fault", [
    "unbind", "rebind", "revoked", "session_revoked", "role_removed", "timeout",
])
def test_browser_activation_fences_intervening_changes_without_password_update(
    migrated_database_url, fault,
):
    async def exercise():
        async with activation_case(migrated_database_url) as case:
            factory, store, principal, captured, submitted, stamp, session_credential = case
            tenant, user = principal.org_ctx.tenant_id, principal.ai_user_id
            if fault == "unbind":
                await store.unbind_password(user, "oa", tenant_id=tenant)
            elif fault == "rebind":
                await store.bind_password(user, "oa", password(), tenant_id=tenant,
                                          expected_write=await claim(store, tenant, user))
            else:
                async with factory() as session:
                    if fault == "session_revoked":
                        await session.execute(text(
                            "INSERT INTO auth_session_revocations (token_fingerprint,expires_at)"
                            " VALUES (:fp,:expires)",
                        ), {"fp": captured.fingerprint, "expires": captured.expires_at})
                    elif fault == "role_removed":
                        await session.execute(text(
                            "DELETE FROM principal_roles"
                            " WHERE tenant_id=:tenant AND ai_user_id=:user",
                        ), {"tenant": tenant, "user": user})
                    elif fault == "revoked":
                        await session.execute(text(
                            "UPDATE oa_session_credentials SET revoked_at=clock_timestamp(),"
                            "binding_state='revoked' WHERE tenant_id=:tenant AND ai_user_id=:user",
                        ), {"tenant": tenant, "user": user})
                    else:
                        await session.execute(text(
                            "UPDATE oa_session_credentials SET refresh_deadline=clock_timestamp()"
                            " - interval '1 second' WHERE tenant_id=:tenant AND ai_user_id=:user",
                        ), {"tenant": tenant, "user": user})
                    await session.commit()
            before = await state(factory, tenant, user)
            with pytest.raises(CredentialStoreError):
                await store.activate_browser_binding(
                    submitted, verified=principal, session_credential=session_credential,
                    captured_session=captured, expected_write=stamp,
                )
            assert await state(factory, tenant, user) == before

    asyncio.run(exercise())


def test_active_browser_binding_is_excluded_at_all_poll_read_boundaries(migrated_database_url):
    async def exercise():
        async with activation_case(migrated_database_url) as case:
            _, store, principal, captured, submitted, stamp, session_credential = case
            tenant, user = principal.org_ctx.tenant_id, principal.ai_user_id
            before = await store.list_poll_candidates(tenant_id=tenant)
            assert len(before) == 1 and before[0].ai_user_id == user
            candidate = await store.refresh_poll_candidate(user, "oa", tenant_id=tenant)
            assert candidate is not None
            loaded = await store.load_password_for_poll(
                user, "oa", tenant_id=tenant, expected_write=stamp,
            )
            assert loaded.password == submitted.password
            await store.activate_browser_binding(
                submitted, verified=principal, session_credential=session_credential,
                captured_session=captured, expected_write=stamp,
            )
            assert await store.list_poll_candidates(tenant_id=tenant) == []
            assert await store.refresh_poll_candidate(user, "oa", tenant_id=tenant) is None
            # Even a freshly claimed writer cannot use the polling password reader.
            fresh = await claim(store, tenant, user)
            with pytest.raises(CredentialStoreError, match=r"^password binding cannot be loaded$"):
                await store.load_password_for_poll(
                    user, "oa", tenant_id=tenant, expected_write=fresh,
                )
    asyncio.run(exercise())
