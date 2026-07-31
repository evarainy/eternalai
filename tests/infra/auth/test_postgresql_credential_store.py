"""Real PostgreSQL proof for encrypted OA credential persistence."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr
from sqlalchemy import text

from app.infra.auth.postgresql import (
    PostgreSQLCredentialStore,
    PostgreSQLPrincipalRoleReader,
    credential_associated_data,
)
from app.ports.auth import CredentialStoreError, OASessionCredential

DATABASE_URL = os.environ.get("DATABASE_URL")

if hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # type: ignore[attr-defined]


def _require_db() -> str:
    if not DATABASE_URL:
        raise AssertionError("DATABASE_URL must be set by the test runner environment")
    return DATABASE_URL


@pytest.mark.parametrize("key", [b"", bytes(range(31)), bytes(range(33))])
def test_credential_store_rejects_non_aes256_keys(key: bytes) -> None:
    with pytest.raises(ValueError, match="exactly 32 bytes"):
        PostgreSQLCredentialStore(
            session_factory=cast(Any, object()),
            encryption_key=key,
        )


def test_oa_credential_is_ciphertext_with_ttl() -> None:
    from app.db.session import make_async_engine, make_async_session_factory

    database_url = _require_db()
    ai_user_id = f"usr_v1_{uuid4().hex}"
    oa_user_id = f"oa-{uuid4().hex}"
    cookie_value = f"cookie-{uuid4().hex}"
    key = bytes(range(32))
    expires_at = datetime.now(UTC) + timedelta(hours=2)

    async def exercise() -> None:
        engine = make_async_engine(database_url)
        factory = make_async_session_factory(engine)
        try:
            store = PostgreSQLCredentialStore(
                session_factory=factory,
                encryption_key=key,
            )
            await store.store(
                ai_user_id,
                OASessionCredential(
                    oa_user_id=SecretStr(oa_user_id),
                    cookies={"loginuuids": SecretStr(cookie_value)},
                    expires_at=expires_at,
                ),
            )
            async with factory() as session:
                row = (
                    await session.execute(
                        text(
                            "SELECT cipher_version, nonce, encrypted_payload, expires_at"
                            " FROM oa_session_credentials"
                            " WHERE ai_user_id = :ai_user_id"
                        ),
                        {"ai_user_id": ai_user_id},
                    )
                ).one()
                await session.execute(
                    text(
                        "DELETE FROM oa_session_credentials"
                        " WHERE ai_user_id = :ai_user_id"
                    ),
                    {"ai_user_id": ai_user_id},
                )
                await session.commit()

            encrypted_payload = bytes(row.encrypted_payload)
            assert oa_user_id.encode("utf-8") not in encrypted_payload
            assert cookie_value.encode("utf-8") not in encrypted_payload
            plaintext = AESGCM(key).decrypt(
                bytes(row.nonce),
                encrypted_payload,
                credential_associated_data(ai_user_id),
            )
            decoded = json.loads(plaintext)
            assert hashlib.sha256(decoded["oa_user_id"].encode()).digest() == hashlib.sha256(
                oa_user_id.encode()
            ).digest()
            assert hashlib.sha256(
                decoded["cookies"]["loginuuids"].encode()
            ).digest() == hashlib.sha256(cookie_value.encode()).digest()
            assert row.cipher_version == "aes256gcm-v1"
            assert row.expires_at == expires_at
        finally:
            await engine.dispose()

    asyncio.run(exercise())


def test_principal_roles_are_local_sorted_and_absent_is_empty() -> None:
    from app.db.session import make_async_engine, make_async_session_factory

    database_url = _require_db()
    ai_user_id = f"usr_v1_{uuid4().hex}"

    async def exercise() -> None:
        engine = make_async_engine(database_url)
        factory = make_async_session_factory(engine)
        try:
            reader = PostgreSQLPrincipalRoleReader(session_factory=factory)
            assert await reader.list_roles(ai_user_id) == ()
            async with factory() as session:
                await session.execute(
                    text(
                        "INSERT INTO principal_roles (ai_user_id, role)"
                        " VALUES (:ai_user_id, 'viewer'), (:ai_user_id, 'admin')"
                    ),
                    {"ai_user_id": ai_user_id},
                )
                await session.commit()
            assert await reader.list_roles(ai_user_id) == ("admin", "viewer")
            async with factory() as session:
                await session.execute(
                    text("DELETE FROM principal_roles WHERE ai_user_id = :ai_user_id"),
                    {"ai_user_id": ai_user_id},
                )
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(exercise())


def test_oa_credential_round_trips_through_authenticated_load() -> None:
    from app.db.session import make_async_engine, make_async_session_factory

    database_url = _require_db()
    ai_user_id = f"usr_v1_{uuid4().hex}{uuid4().hex[:11]}"
    oa_user_id = f"synthetic-{uuid4().hex}"
    cookie_value = f"synthetic-{uuid4().hex}"
    key = bytes(range(32))
    expires_at = datetime.now(UTC) + timedelta(hours=2)

    async def exercise() -> None:
        engine = make_async_engine(database_url)
        factory = make_async_session_factory(engine)
        try:
            store = PostgreSQLCredentialStore(
                session_factory=factory,
                encryption_key=key,
            )
            await store.store(
                ai_user_id,
                OASessionCredential(
                    oa_user_id=SecretStr(oa_user_id),
                    cookies={"synthetic_name": SecretStr(cookie_value)},
                    expires_at=expires_at,
                ),
            )

            loaded = await store.load(ai_user_id)

            assert loaded is not None
            assert hashlib.sha256(
                loaded.oa_user_id.get_secret_value().encode()
            ).digest() == hashlib.sha256(oa_user_id.encode()).digest()
            assert hashlib.sha256(
                loaded.cookies["synthetic_name"].get_secret_value().encode()
            ).digest() == hashlib.sha256(cookie_value.encode()).digest()
            assert loaded.expires_at == expires_at
            rendered = repr(loaded) + loaded.model_dump_json()
            assert oa_user_id not in rendered
            assert cookie_value not in rendered
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


def test_successful_credential_upsert_clears_revocation() -> None:
    from app.db.session import make_async_engine, make_async_session_factory

    database_url = _require_db()
    ai_user_id = f"usr_v1_{uuid4().hex}{uuid4().hex[:11]}"
    key = bytes(range(32))
    initial_expires_at = datetime.now(UTC) + timedelta(hours=1)
    reauthenticated_expires_at = datetime.now(UTC) + timedelta(hours=2)

    async def exercise() -> None:
        engine = make_async_engine(database_url)
        factory = make_async_session_factory(engine)
        try:
            store = PostgreSQLCredentialStore(
                session_factory=factory,
                encryption_key=key,
            )
            await store.store(
                ai_user_id,
                OASessionCredential(
                    oa_user_id=SecretStr(f"synthetic-{uuid4().hex}"),
                    cookies={"synthetic_name": SecretStr(f"synthetic-{uuid4().hex}")},
                    expires_at=initial_expires_at,
                ),
            )
            revoked_at = datetime.now(UTC)
            async with factory() as session:
                await session.execute(
                    text(
                        "UPDATE oa_session_credentials"
                        " SET revoked_at = :revoked_at"
                        " WHERE ai_user_id = :ai_user_id"
                    ),
                    {
                        "ai_user_id": ai_user_id,
                        "revoked_at": revoked_at,
                    },
                )
                await session.commit()

            await store.store(
                ai_user_id,
                OASessionCredential(
                    oa_user_id=SecretStr(f"synthetic-{uuid4().hex}"),
                    cookies={"synthetic_name": SecretStr(f"synthetic-{uuid4().hex}")},
                    expires_at=reauthenticated_expires_at,
                ),
            )

            async with factory() as session:
                row = (
                    await session.execute(
                        text(
                            "SELECT expires_at, revoked_at"
                            " FROM oa_session_credentials"
                            " WHERE ai_user_id = :ai_user_id"
                        ),
                        {"ai_user_id": ai_user_id},
                    )
                ).one()

            assert row.revoked_at is None
            assert row.expires_at == reauthenticated_expires_at
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


def test_revoked_credential_is_rejected_before_decryption() -> None:
    from app.db.session import make_async_engine, make_async_session_factory

    database_url = _require_db()
    ai_user_id = f"usr_v1_{uuid4().hex}{uuid4().hex[:11]}"
    oa_user_id = f"synthetic-{uuid4().hex}"
    cookie_value = f"synthetic-{uuid4().hex}"

    class DecryptMustNotRun:
        def __init__(self) -> None:
            self.call_count = 0

        def decrypt(self, *_args: object, **_kwargs: object) -> bytes:
            self.call_count += 1
            raise AssertionError("revoked credentials must not be decrypted")

    async def exercise() -> None:
        engine = make_async_engine(database_url)
        factory = make_async_session_factory(engine)
        try:
            store = PostgreSQLCredentialStore(
                session_factory=factory,
                encryption_key=bytes(range(32)),
            )
            await store.store(
                ai_user_id,
                OASessionCredential(
                    oa_user_id=SecretStr(oa_user_id),
                    cookies={"synthetic_name": SecretStr(cookie_value)},
                    expires_at=datetime.now(UTC) + timedelta(hours=2),
                ),
            )
            async with factory() as session:
                await session.execute(
                    text(
                        "UPDATE oa_session_credentials"
                        " SET revoked_at = :revoked_at"
                        " WHERE ai_user_id = :ai_user_id"
                    ),
                    {
                        "ai_user_id": ai_user_id,
                        "revoked_at": datetime.now(UTC),
                    },
                )
                await session.commit()

            decrypt_guard = DecryptMustNotRun()
            setattr(store, "_cipher", decrypt_guard)

            with pytest.raises(CredentialStoreError) as exc_info:
                await store.load(ai_user_id)

            assert decrypt_guard.call_count == 0
            rendered = repr(exc_info.value) + str(exc_info.value)
            assert oa_user_id not in rendered
            assert cookie_value not in rendered
            assert ai_user_id not in rendered
            assert exc_info.value.__context__ is None
            assert exc_info.value.__cause__ is None
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


def test_oa_credential_load_returns_none_for_missing_row() -> None:
    from app.db.session import make_async_engine, make_async_session_factory

    database_url = _require_db()
    ai_user_id = f"usr_v1_{uuid4().hex}{uuid4().hex[:11]}"

    async def exercise() -> None:
        engine = make_async_engine(database_url)
        factory = make_async_session_factory(engine)
        try:
            store = PostgreSQLCredentialStore(
                session_factory=factory,
                encryption_key=bytes(range(32)),
            )
            assert await store.load(ai_user_id) is None
        finally:
            await engine.dispose()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "corruption",
    (
        "cipher_version",
        "nonce",
        "aad",
        "authentication_tag",
        "json_extra_field",
        "json_wrong_type",
        "json_duplicate_key",
    ),
)
def test_oa_credential_load_rejects_corrupted_rows_without_sensitive_context(
    corruption: str,
) -> None:
    from app.db.session import make_async_engine, make_async_session_factory

    database_url = _require_db()
    ai_user_id = f"usr_v1_{uuid4().hex}{uuid4().hex[:11]}"
    oa_user_id = f"synthetic-{uuid4().hex}"
    cookie_value = f"synthetic-{uuid4().hex}"
    key = bytes(range(32))
    nonce = os.urandom(12)
    expires_at = datetime.now(UTC) + timedelta(hours=2)
    payload: bytes
    if corruption == "json_extra_field":
        payload = json.dumps(
            {
                "oa_user_id": oa_user_id,
                "cookies": {"synthetic_name": cookie_value},
                "unexpected": "value",
            },
            separators=(",", ":"),
        ).encode()
    elif corruption == "json_wrong_type":
        payload = json.dumps(
            {
                "oa_user_id": oa_user_id,
                "cookies": ["not", "an", "object"],
            },
            separators=(",", ":"),
        ).encode()
    elif corruption == "json_duplicate_key":
        payload = (
            "{"
            f'"oa_user_id":{json.dumps(oa_user_id)},'
            f'"oa_user_id":{json.dumps(oa_user_id)},'
            f'"cookies":{{"synthetic_name":{json.dumps(cookie_value)}}}'
            "}"
        ).encode()
    else:
        payload = json.dumps(
            {
                "oa_user_id": oa_user_id,
                "cookies": {"synthetic_name": cookie_value},
            },
            separators=(",", ":"),
        ).encode()

    aad_ai_user_id = (
        f"usr_v1_{uuid4().hex}{uuid4().hex[:11]}"
        if corruption == "aad"
        else ai_user_id
    )
    encrypted_payload = AESGCM(key).encrypt(
        nonce,
        payload,
        credential_associated_data(aad_ai_user_id),
    )
    stored_nonce = b"short" if corruption == "nonce" else nonce
    stored_cipher_version = (
        "unsupported-v2" if corruption == "cipher_version" else "aes256gcm-v1"
    )
    if corruption == "authentication_tag":
        encrypted_payload = encrypted_payload[:-1] + bytes([encrypted_payload[-1] ^ 1])

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
                        "cipher_version": stored_cipher_version,
                        "nonce": stored_nonce,
                        "encrypted_payload": encrypted_payload,
                        "expires_at": expires_at,
                        "updated_at": datetime.now(UTC),
                    },
                )
                await session.commit()

            store = PostgreSQLCredentialStore(
                session_factory=factory,
                encryption_key=key,
            )
            with pytest.raises(CredentialStoreError) as exc_info:
                await store.load(ai_user_id)

            rendered = repr(exc_info.value) + str(exc_info.value)
            assert oa_user_id not in rendered
            assert cookie_value not in rendered
            assert ai_user_id not in rendered
            assert exc_info.value.__context__ is None
            assert exc_info.value.__cause__ is None
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
