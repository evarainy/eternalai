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
from app.ports.auth import OASessionCredential

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
