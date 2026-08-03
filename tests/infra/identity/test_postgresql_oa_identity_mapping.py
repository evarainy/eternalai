"""Real-PostgreSQL proof for the metadata-only OA identity projection."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import text

from app.infra.adapters.oa.adapter import OAReadAdapter
from app.infra.adapters.oa.provider import LiveOAReadProvider
from app.infra.auth.postgresql import PostgreSQLCredentialStore
from app.infra.auth.secret_provider import CredentialStoreSecretProvider
from app.infra.gateway.capability_gateway import CapabilityGateway
from app.infra.identity.postgresql import PostgreSQLOAIdentityMapping
from app.infra.policy.minimal_policy_guard import MinimalPolicyGuard
from app.ports.auth import OASessionCredential
from app.ports.capability_gateway import RequestOrgContext
from app.ports.capability_registry import CapabilitySpec

DATABASE_URL = os.environ.get("DATABASE_URL")
NOW = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
SOURCE = Path("app/infra/identity/postgresql.py")
CONTRACT_PACK = (
    Path(__file__).resolve().parents[2]
    / "contract_packs"
    / "oa"
    / "ecology9-pending-workflows-v1"
)
SYSTEM_MESSAGE_CONTRACT_PACK = (
    Path(__file__).resolve().parents[2]
    / "contract_packs"
    / "oa"
    / "ecology9-system-messages-v1"
)

if hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # type: ignore[attr-defined]


def _require_db() -> str:
    if not DATABASE_URL:
        raise AssertionError("DATABASE_URL must be set by the test runner environment")
    return DATABASE_URL


def _ai_user_id() -> str:
    return f"usr_v1_{uuid4().hex}{uuid4().hex[:11]}"


def _request_context(**overrides: str) -> RequestOrgContext:
    return RequestOrgContext(request_id="oa-identity-test", **overrides)


def test_active_projection_uses_only_metadata_and_returns_namespaced_reference() -> None:
    from app.db.session import make_async_engine, make_async_session_factory

    database_url = _require_db()
    ai_user_id = _ai_user_id()
    sensitive_marker = "synthetic-" + uuid4().hex
    expires_at = NOW + timedelta(minutes=5)

    async def exercise() -> None:
        engine = make_async_engine(database_url)
        factory = make_async_session_factory(engine)
        try:
            async with factory() as session:
                await session.execute(
                    text(
                        "INSERT INTO oa_session_credentials"
                        " (ai_user_id, cipher_version, nonce, encrypted_payload,"
                        " expires_at, updated_at)"
                        " VALUES"
                        " (:ai_user_id, :cipher_version, :nonce, :encrypted_payload,"
                        " :expires_at, :updated_at)"
                    ),
                    {
                        "ai_user_id": ai_user_id,
                        "cipher_version": "intentionally-unsupported",
                        "nonce": sensitive_marker.encode(),
                        "encrypted_payload": sensitive_marker.encode(),
                        "expires_at": expires_at,
                        "updated_at": NOW,
                    },
                )
                await session.commit()

            mapping = PostgreSQLOAIdentityMapping(
                session_factory=factory,
                now=lambda: NOW,
            )
            resolved = await mapping.resolve_execution_identity(
                ai_user_id=ai_user_id,
                target_system="oa",
                execution_identity="user_delegated",
                request_context=_request_context(),
            )
            fetched = await mapping.get_mapping(ai_user_id, "oa")
            listed = await mapping.list_mappings(ai_user_id)

            assert resolved.model_dump() == {
                "bind_status": "active",
                "binding_id": f"oa-session-v1:{ai_user_id}",
                "target_system": "oa",
                "execution_identity": "user_delegated",
                "binding_scope": None,
                "account_set_id": None,
                "device_domain_id": None,
                "reason_code": None,
            }
            assert fetched == resolved
            assert listed == [resolved]
            rendered = repr(mapping) + repr(resolved) + resolved.model_dump_json()
            assert sensitive_marker not in rendered
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

    asyncio.run(exercise())


def test_missing_projection_is_unbound_and_absent_from_management_reads() -> None:
    from app.db.session import make_async_engine, make_async_session_factory

    database_url = _require_db()
    ai_user_id = _ai_user_id()

    async def exercise() -> None:
        engine = make_async_engine(database_url)
        factory = make_async_session_factory(engine)
        try:
            mapping = PostgreSQLOAIdentityMapping(
                session_factory=factory,
                now=lambda: NOW,
            )
            resolved = await mapping.resolve_execution_identity(
                ai_user_id=ai_user_id,
                target_system="oa",
                execution_identity="user_delegated",
                request_context=_request_context(),
            )

            assert resolved.bind_status == "unbound"
            assert resolved.binding_id is None
            assert resolved.reason_code == "identity_unbound"
            assert await mapping.get_mapping(ai_user_id, "oa") is None
            assert await mapping.list_mappings(ai_user_id, "oa") == []
        finally:
            await engine.dispose()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "expires_at",
    (
        NOW,
        NOW - timedelta(microseconds=1),
    ),
)
def test_expired_projection_never_emits_a_binding_reference(
    expires_at: datetime,
) -> None:
    from app.db.session import make_async_engine, make_async_session_factory

    database_url = _require_db()
    ai_user_id = _ai_user_id()

    async def exercise() -> None:
        engine = make_async_engine(database_url)
        factory = make_async_session_factory(engine)
        try:
            async with factory() as session:
                await session.execute(
                    text(
                        "INSERT INTO oa_session_credentials"
                        " (ai_user_id, cipher_version, nonce, encrypted_payload,"
                        " expires_at, updated_at)"
                        " VALUES"
                        " (:ai_user_id, 'unused', :nonce, :encrypted_payload,"
                        " :expires_at, :updated_at)"
                    ),
                    {
                        "ai_user_id": ai_user_id,
                        "nonce": b"unused",
                        "encrypted_payload": b"unused",
                        "expires_at": expires_at,
                        "updated_at": NOW,
                    },
                )
                await session.commit()

            mapping = PostgreSQLOAIdentityMapping(
                session_factory=factory,
                now=lambda: NOW,
            )
            resolved = await mapping.resolve_execution_identity(
                ai_user_id=ai_user_id,
                target_system="oa",
                execution_identity="user_delegated",
                request_context=_request_context(),
            )

            assert resolved.model_dump() == {
                "bind_status": "expired",
                "binding_id": None,
                "target_system": "oa",
                "execution_identity": "user_delegated",
                "binding_scope": None,
                "account_set_id": None,
                "device_domain_id": None,
                "reason_code": "identity_expired",
            }
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

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "expires_at",
    (
        NOW + timedelta(minutes=5),
        NOW,
        NOW - timedelta(microseconds=1),
    ),
)
def test_revoked_projection_precedes_expiry_and_preserves_binding_reference(
    expires_at: datetime,
) -> None:
    from app.db.session import make_async_engine, make_async_session_factory

    database_url = _require_db()
    ai_user_id = _ai_user_id()
    binding_id = f"oa-session-v1:{ai_user_id}"

    async def exercise() -> None:
        engine = make_async_engine(database_url)
        factory = make_async_session_factory(engine)
        try:
            async with factory() as session:
                await session.execute(
                    text(
                        "INSERT INTO oa_session_credentials"
                        " (ai_user_id, cipher_version, nonce, encrypted_payload,"
                        " expires_at, revoked_at, updated_at)"
                        " VALUES"
                        " (:ai_user_id, 'unused', :nonce, :encrypted_payload,"
                        " :expires_at, :revoked_at, :updated_at)"
                    ),
                    {
                        "ai_user_id": ai_user_id,
                        "nonce": b"unused",
                        "encrypted_payload": b"unused",
                        "expires_at": expires_at,
                        "revoked_at": NOW - timedelta(seconds=1),
                        "updated_at": NOW,
                    },
                )
                await session.commit()

            mapping = PostgreSQLOAIdentityMapping(
                session_factory=factory,
                now=lambda: NOW,
            )
            resolved = await mapping.resolve_execution_identity(
                ai_user_id=ai_user_id,
                target_system="oa",
                execution_identity="user_delegated",
                request_context=_request_context(),
            )

            assert resolved.bind_status == "revoked"
            assert resolved.binding_id == binding_id
            assert resolved.reason_code == "identity_revoked"
            assert await mapping.get_mapping(ai_user_id, "oa") == resolved
            assert await mapping.list_mappings(ai_user_id, "oa") == [resolved]
        finally:
            async with factory() as session:
                await session.execute(
                    text("DELETE FROM oa_session_credentials WHERE ai_user_id = :ai_user_id"),
                    {"ai_user_id": ai_user_id},
                )
                await session.commit()
            await engine.dispose()

    asyncio.run(exercise())


def test_revoke_and_reset_are_idempotent_and_preserve_credential_timestamps() -> None:
    from app.db.session import make_async_engine, make_async_session_factory

    database_url = _require_db()
    ai_user_id = _ai_user_id()
    binding_id = f"oa-session-v1:{ai_user_id}"
    expires_at = NOW + timedelta(minutes=5)
    clock_values = iter((NOW,))

    async def exercise() -> None:
        engine = make_async_engine(database_url)
        factory = make_async_session_factory(engine)
        try:
            async with factory() as session:
                await session.execute(
                    text(
                        "INSERT INTO oa_session_credentials"
                        " (ai_user_id, cipher_version, nonce, encrypted_payload,"
                        " expires_at, revoked_at, updated_at)"
                        " VALUES"
                        " (:ai_user_id, 'unused', :nonce, :encrypted_payload,"
                        " :expires_at, NULL, :updated_at)"
                    ),
                    {
                        "ai_user_id": ai_user_id,
                        "nonce": b"unused",
                        "encrypted_payload": b"unused",
                        "expires_at": expires_at,
                        "updated_at": NOW,
                    },
                )
                await session.commit()

            mapping = PostgreSQLOAIdentityMapping(
                session_factory=factory,
                now=lambda: next(clock_values),
            )
            first = await mapping.revoke_mapping(binding_id)
            repeated_revoke = await mapping.revoke_mapping(binding_id)
            reset = await mapping.reset_mapping(binding_id)

            assert first is not None
            assert first.previous_bind_status == "active"
            assert first.changed is True
            assert first.mapping.bind_status == "revoked"
            assert first.mapping.binding_id == binding_id
            assert repeated_revoke is not None
            assert repeated_revoke.previous_bind_status == "revoked"
            assert repeated_revoke.changed is False
            assert repeated_revoke.mapping == first.mapping
            assert reset is not None
            assert reset.previous_bind_status == "revoked"
            assert reset.changed is False
            assert reset.mapping == first.mapping

            async with factory() as session:
                query_result = await session.execute(
                    text(
                        "SELECT expires_at, revoked_at"
                        " FROM oa_session_credentials"
                        " WHERE ai_user_id = :ai_user_id"
                    ),
                    {"ai_user_id": ai_user_id},
                )
                row = query_result.mappings().one()
            assert row["expires_at"] == expires_at
            assert row["revoked_at"] == NOW
        finally:
            async with factory() as session:
                await session.execute(
                    text("DELETE FROM oa_session_credentials WHERE ai_user_id = :ai_user_id"),
                    {"ai_user_id": ai_user_id},
                )
                await session.commit()
            await engine.dispose()

    asyncio.run(exercise())


def test_revoke_reports_expired_previous_status_without_changing_expires_at() -> None:
    from app.db.session import make_async_engine, make_async_session_factory

    database_url = _require_db()
    ai_user_id = _ai_user_id()
    binding_id = f"oa-session-v1:{ai_user_id}"

    async def exercise() -> None:
        engine = make_async_engine(database_url)
        factory = make_async_session_factory(engine)
        try:
            async with factory() as session:
                await session.execute(
                    text(
                        "INSERT INTO oa_session_credentials"
                        " (ai_user_id, cipher_version, nonce, encrypted_payload,"
                        " expires_at, revoked_at, updated_at)"
                        " VALUES"
                        " (:ai_user_id, 'unused', :nonce, :encrypted_payload,"
                        " :expires_at, NULL, :updated_at)"
                    ),
                    {
                        "ai_user_id": ai_user_id,
                        "nonce": b"unused",
                        "encrypted_payload": b"unused",
                        "expires_at": NOW,
                        "updated_at": NOW,
                    },
                )
                await session.commit()

            mapping = PostgreSQLOAIdentityMapping(
                session_factory=factory,
                now=lambda: NOW,
            )
            result = await mapping.revoke_mapping(binding_id)

            assert result is not None
            assert result.previous_bind_status == "expired"
            assert result.changed is True
            assert result.mapping.bind_status == "revoked"
            assert result.mapping.binding_id == binding_id
            async with factory() as session:
                stored_expires_at = await session.scalar(
                    text(
                        "SELECT expires_at FROM oa_session_credentials"
                        " WHERE ai_user_id = :ai_user_id"
                    ),
                    {"ai_user_id": ai_user_id},
                )
            assert stored_expires_at == NOW
        finally:
            async with factory() as session:
                await session.execute(
                    text("DELETE FROM oa_session_credentials WHERE ai_user_id = :ai_user_id"),
                    {"ai_user_id": ai_user_id},
                )
                await session.commit()
            await engine.dispose()

    asyncio.run(exercise())


def test_committed_revocation_blocks_new_and_stale_prechecked_requests() -> None:
    from app.db.session import make_async_engine, make_async_session_factory

    database_url = _require_db()
    ai_user_id = _ai_user_id()
    binding_id = f"oa-session-v1:{ai_user_id}"
    capability = CapabilitySpec(
        capability_id="oa.list_pending_workflows",
        name="OA pending workflows",
        type="query",
        input_schema_digest="input-digest",
        output_schema_digest="output-digest",
        risk_level="low",
        owner="phase2",
        version="1.0.0",
        status="active",
        short_description="OA pending workflows",
        target_system="oa",
        execution_identity="user_delegated",
        binding_required=True,
    )

    class Registry:
        async def get(self, capability_id: str) -> CapabilitySpec | None:
            assert capability_id == capability.capability_id
            return capability

    class CountingCredentialStore(PostgreSQLCredentialStore):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self.load_calls = 0

        async def load(self, stored_ai_user_id: str) -> OASessionCredential | None:
            self.load_calls += 1
            return await super().load(stored_ai_user_id)

    class CountingSecretProvider(CredentialStoreSecretProvider):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self.resolve_calls = 0

        async def resolve_oa_session(
            self,
            credential_ref: str,
        ) -> OASessionCredential:
            self.resolve_calls += 1
            return await super().resolve_oa_session(credential_ref)

    class CountingAdapter(OAReadAdapter):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.execute_calls = 0

        async def execute(
            self,
            capability_id: str,
            arguments: dict[str, Any],
            execution_context: dict[str, Any],
        ) -> Any:
            self.execute_calls += 1
            return await super().execute(
                capability_id,
                arguments,
                execution_context,
            )

    async def exercise() -> None:
        engine = make_async_engine(database_url)
        factory = make_async_session_factory(engine)
        http_open_calls = 0

        def forbidden_opener_factory() -> Any:
            nonlocal http_open_calls
            http_open_calls += 1
            raise AssertionError("revoked request must not reach Live HTTP")

        try:
            credential_store = CountingCredentialStore(
                session_factory=factory,
                encryption_key=bytes(range(32)),
            )
            await credential_store.store(
                ai_user_id,
                OASessionCredential(
                    oa_user_id=SecretStr("synthetic-" + uuid4().hex),
                    cookies={
                        "synthetic_name": SecretStr(
                            "synthetic-" + uuid4().hex
                        )
                    },
                    expires_at=NOW + timedelta(minutes=5),
                ),
            )
            secret_provider = CountingSecretProvider(
                credential_store=credential_store,
                now=lambda: NOW,
            )
            live_provider = LiveOAReadProvider(
                base_url="https://oa.synthetic.invalid",
                pending_workflows_endpoint_path="/api/pending",
                system_messages_endpoint_path="/api/messages",
                timeout_seconds=2.0,
                pending_workflows_contract_pack_dir=CONTRACT_PACK,
                system_messages_contract_pack_dir=(
                    SYSTEM_MESSAGE_CONTRACT_PACK
                ),
                opener_factory=forbidden_opener_factory,
                clock=lambda: NOW,
            )
            adapter = CountingAdapter(
                live_provider,
                secret_provider=secret_provider,
            )
            identity_mapping = PostgreSQLOAIdentityMapping(
                session_factory=factory,
                now=lambda: NOW,
            )
            active = await identity_mapping.resolve_execution_identity(
                ai_user_id,
                "oa",
                "user_delegated",
                _request_context(),
            )
            assert active.bind_status == "active"
            assert active.binding_id == binding_id

            revoked = await identity_mapping.revoke_mapping(binding_id)
            assert revoked is not None
            assert revoked.previous_bind_status == "active"
            assert revoked.changed is True
            assert revoked.mapping.bind_status == "revoked"
            assert revoked.mapping.binding_id == binding_id

            gateway = CapabilityGateway(
                capability_registry=cast(Any, Registry()),
                identity_mapping=identity_mapping,
                policy_guard=MinimalPolicyGuard(),
                adapters={"oa": adapter},
            )
            result = await gateway.execute_capability(
                "task-revoked-001",
                "session-revoked-001",
                ai_user_id,
                capability.capability_id,
                {},
                RequestOrgContext(request_id="trace-revoked-001"),
            )

            assert result.status == "binding_required"
            assert result.error_code == "identity_revoked"
            assert result.data is None
            assert adapter.execute_calls == 0
            assert secret_provider.resolve_calls == 0
            assert credential_store.load_calls == 0
            assert http_open_calls == 0

            stale_precheck_result = await adapter.execute(
                capability.capability_id,
                {},
                {"credential_ref": binding_id},
            )

            assert stale_precheck_result.status == "error"
            assert stale_precheck_result.error_code == "adapter_error"
            assert stale_precheck_result.data is None
            assert adapter.execute_calls == 1
            assert secret_provider.resolve_calls == 1
            assert credential_store.load_calls == 1
            assert http_open_calls == 0
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

    asyncio.run(exercise())


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("target_system", "execution_identity", "context"),
    (
        ("u8", "user_delegated", _request_context()),
        ("oa", "system_scope", _request_context()),
        ("oa", "admin_approved_proxy", _request_context()),
        ("oa", "user_delegated", _request_context(resource_scope="unsupported")),
        ("oa", "user_delegated", _request_context(account_set_id="unsupported")),
        ("oa", "user_delegated", _request_context(device_domain_id="unsupported")),
    ),
)
async def test_unsupported_identity_shape_fails_closed_without_database_access(
    target_system: Any,
    execution_identity: Any,
    context: RequestOrgContext,
) -> None:
    def fail_if_called() -> Any:
        raise AssertionError("unsupported identity must not query credential storage")

    mapping = PostgreSQLOAIdentityMapping(
        session_factory=cast(Any, fail_if_called),
        now=lambda: NOW,
    )

    resolved = await mapping.resolve_execution_identity(
        ai_user_id=_ai_user_id(),
        target_system=target_system,
        execution_identity=execution_identity,
        request_context=context,
    )

    assert resolved.bind_status == "unbound"
    assert resolved.binding_id is None
    assert resolved.reason_code == "identity_unbound"


@pytest.mark.anyio
async def test_database_error_is_verification_failed_without_exception_or_log_leak(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sensitive_marker = "synthetic-" + uuid4().hex

    def failing_factory() -> Any:
        raise RuntimeError(sensitive_marker)

    mapping = PostgreSQLOAIdentityMapping(
        session_factory=cast(Any, failing_factory),
        now=lambda: NOW,
    )
    caplog.set_level(logging.DEBUG)

    resolved = await mapping.resolve_execution_identity(
        ai_user_id=_ai_user_id(),
        target_system="oa",
        execution_identity="user_delegated",
        request_context=_request_context(),
    )

    assert resolved.bind_status == "verification_failed"
    assert resolved.binding_id is None
    assert sensitive_marker not in (repr(mapping) + repr(resolved) + caplog.text)


@pytest.mark.anyio
async def test_management_filters_never_broaden_to_unscoped_oa_binding() -> None:
    def fail_if_called() -> Any:
        raise AssertionError("unsupported filters must not query credential storage")

    mapping = PostgreSQLOAIdentityMapping(
        session_factory=cast(Any, fail_if_called),
        now=lambda: NOW,
    )
    ai_user_id = _ai_user_id()

    assert await mapping.get_mapping(
        ai_user_id,
        "oa",
        binding_scope="unsupported",
    ) is None
    assert await mapping.list_mappings(
        ai_user_id,
        "oa",
        account_set_id="unsupported",
    ) == []
    assert await mapping.list_mappings(ai_user_id, "u8") == []


def test_user_a_cannot_resolve_user_b_oa_credential_or_reach_live_http() -> None:
    from app.db.session import make_async_engine, make_async_session_factory

    database_url = _require_db()
    user_a_id = _ai_user_id()
    user_b_id = _ai_user_id()
    capability = CapabilitySpec(
        capability_id="oa.list_pending_workflows",
        name="OA pending workflows",
        type="query",
        input_schema_digest="input-digest",
        output_schema_digest="output-digest",
        risk_level="low",
        owner="phase2",
        version="1.0.0",
        status="active",
        short_description="OA pending workflows",
        target_system="oa",
        execution_identity="user_delegated",
        binding_required=True,
    )

    class Registry:
        async def get(self, capability_id: str) -> CapabilitySpec | None:
            assert capability_id == capability.capability_id
            return capability

    class CountingSecretProvider(CredentialStoreSecretProvider):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self.resolve_calls = 0

        async def resolve_oa_session(
            self,
            credential_ref: str,
        ) -> OASessionCredential:
            self.resolve_calls += 1
            return await super().resolve_oa_session(credential_ref)

    class CountingAdapter(OAReadAdapter):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.execute_calls = 0

        async def execute(
            self,
            capability_id: str,
            arguments: dict[str, Any],
            execution_context: dict[str, Any],
        ) -> Any:
            self.execute_calls += 1
            return await super().execute(
                capability_id,
                arguments,
                execution_context,
            )

    async def exercise() -> None:
        engine = make_async_engine(database_url)
        factory = make_async_session_factory(engine)
        http_open_calls = 0

        def forbidden_opener_factory() -> Any:
            nonlocal http_open_calls
            http_open_calls += 1
            raise AssertionError("cross-user request must not reach Live HTTP")

        try:
            credential_store = PostgreSQLCredentialStore(
                session_factory=factory,
                encryption_key=bytes(range(32)),
            )
            await credential_store.store(
                user_b_id,
                OASessionCredential(
                    oa_user_id=SecretStr("synthetic-" + uuid4().hex),
                    cookies={
                        "synthetic_name": SecretStr(
                            "synthetic-" + uuid4().hex
                        )
                    },
                    expires_at=NOW + timedelta(minutes=5),
                ),
            )
            secret_provider = CountingSecretProvider(
                credential_store=credential_store,
                now=lambda: NOW,
            )
            live_provider = LiveOAReadProvider(
                base_url="https://oa.synthetic.invalid",
                pending_workflows_endpoint_path="/api/pending",
                system_messages_endpoint_path="/api/messages",
                timeout_seconds=2.0,
                pending_workflows_contract_pack_dir=CONTRACT_PACK,
                system_messages_contract_pack_dir=(
                    SYSTEM_MESSAGE_CONTRACT_PACK
                ),
                opener_factory=forbidden_opener_factory,
                clock=lambda: NOW,
            )
            adapter = CountingAdapter(
                live_provider,
                secret_provider=secret_provider,
            )
            identity_mapping = PostgreSQLOAIdentityMapping(
                session_factory=factory,
                now=lambda: NOW,
            )
            user_b_mapping = await identity_mapping.resolve_execution_identity(
                user_b_id,
                "oa",
                "user_delegated",
                _request_context(),
            )
            assert user_b_mapping.bind_status == "active"
            assert user_b_mapping.binding_id == f"oa-session-v1:{user_b_id}"
            gateway = CapabilityGateway(
                capability_registry=cast(Any, Registry()),
                identity_mapping=identity_mapping,
                policy_guard=MinimalPolicyGuard(),
                adapters={"oa": adapter},
            )

            result = await gateway.execute_capability(
                "task-cross-user-001",
                "session-cross-user-001",
                user_a_id,
                capability.capability_id,
                {},
                RequestOrgContext(request_id="trace-cross-user-001"),
            )

            assert result.status == "binding_required"
            assert result.error_code == "identity_unbound"
            assert result.data is None
            assert adapter.execute_calls == 0
            assert secret_provider.resolve_calls == 0
            assert http_open_calls == 0
        finally:
            async with factory() as session:
                await session.execute(
                    text(
                        "DELETE FROM oa_session_credentials"
                        " WHERE ai_user_id = :ai_user_id"
                    ),
                    {"ai_user_id": user_b_id},
                )
                await session.commit()
            await engine.dispose()

    asyncio.run(exercise())


def test_identity_mapping_source_cannot_select_or_decrypt_credential_material() -> None:
    source = SOURCE.read_text(encoding="utf-8").lower()

    assert "encrypted_payload" not in source
    assert "cipher_version" not in source
    assert "aesgcm" not in source
    assert "secretstr" not in source
