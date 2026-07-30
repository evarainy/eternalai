"""PostgreSQL-backed encrypted OA credential and principal-role storage."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ports.auth import CredentialStoreError, CredentialStorePort, OASessionCredential

_CIPHER_VERSION = "aes256gcm-v1"
_AES_256_KEY_BYTES = 32
_GCM_NONCE_BYTES = 12


class PostgreSQLCredentialStore:
    """Encrypt OA session material before it reaches PostgreSQL."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        encryption_key: bytes,
    ) -> None:
        if not isinstance(encryption_key, bytes) or len(encryption_key) != _AES_256_KEY_BYTES:
            raise ValueError("credential encryption key must contain exactly 32 bytes")
        self._session_factory = session_factory
        self._cipher = AESGCM(encryption_key)

    async def store(
        self,
        ai_user_id: str,
        credential: OASessionCredential,
    ) -> None:
        if not ai_user_id:
            raise ValueError("ai_user_id must not be blank")
        nonce = os.urandom(_GCM_NONCE_BYTES)
        payload = json.dumps(
            {
                "oa_user_id": credential.oa_user_id.get_secret_value(),
                "cookies": {
                    name: value.get_secret_value()
                    for name, value in sorted(credential.cookies.items())
                },
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        associated_data = _associated_data(ai_user_id)
        encrypted_payload = self._cipher.encrypt(nonce, payload, associated_data)
        async with self._session_factory() as session:
            await session.execute(
                text(
                    "INSERT INTO oa_session_credentials"
                    " (ai_user_id, cipher_version, nonce, encrypted_payload,"
                    " expires_at, updated_at)"
                    " VALUES"
                    " (:ai_user_id, :cipher_version, :nonce, :encrypted_payload,"
                    " :expires_at, :updated_at)"
                    " ON CONFLICT (ai_user_id) DO UPDATE SET"
                    " cipher_version = EXCLUDED.cipher_version,"
                    " nonce = EXCLUDED.nonce,"
                    " encrypted_payload = EXCLUDED.encrypted_payload,"
                    " expires_at = EXCLUDED.expires_at,"
                    " updated_at = EXCLUDED.updated_at"
                ),
                {
                    "ai_user_id": ai_user_id,
                    "cipher_version": _CIPHER_VERSION,
                    "nonce": nonce,
                    "encrypted_payload": encrypted_payload,
                    "expires_at": credential.expires_at,
                    "updated_at": datetime.now(UTC),
                },
            )
            await session.commit()

    async def load(self, ai_user_id: str) -> OASessionCredential | None:
        """Decrypt one OA Session row or fail with a context-free safe error."""

        if not ai_user_id:
            raise CredentialStoreError("OA session credential cannot be loaded")

        row: RowMapping | None = None
        query_failed = False
        try:
            async with self._session_factory() as session:
                row = (
                    await session.execute(
                        text(
                            "SELECT cipher_version, nonce, encrypted_payload, expires_at"
                            " FROM oa_session_credentials"
                            " WHERE ai_user_id = :ai_user_id"
                        ),
                        {"ai_user_id": ai_user_id},
                    )
                ).mappings().one_or_none()
        except Exception:
            query_failed = True

        if query_failed:
            raise CredentialStoreError("OA session credential cannot be loaded")
        if row is None:
            return None

        credential: OASessionCredential | None = None
        decode_failed = False
        try:
            credential = _decode_credential_row(
                cipher=self._cipher,
                ai_user_id=ai_user_id,
                row=row,
            )
        except Exception:
            decode_failed = True

        if decode_failed or credential is None:
            raise CredentialStoreError("OA session credential cannot be loaded")
        return credential


class PostgreSQLPrincipalRoleReader:
    """Read locally assigned roles; an absent mapping is intentionally empty."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def list_roles(self, ai_user_id: str) -> tuple[str, ...]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT role FROM principal_roles"
                        " WHERE ai_user_id = :ai_user_id"
                        " ORDER BY role ASC"
                    ),
                    {"ai_user_id": ai_user_id},
                )
            ).fetchall()
        return tuple(str(row.role) for row in rows)


def credential_associated_data(ai_user_id: str) -> bytes:
    """Expose deterministic AAD construction for black-box persistence tests."""

    return _associated_data(ai_user_id)


def _associated_data(ai_user_id: str) -> bytes:
    return f"{_CIPHER_VERSION}\x00{ai_user_id}".encode("utf-8")


def _decode_credential_row(
    *,
    cipher: AESGCM,
    ai_user_id: str,
    row: RowMapping,
) -> OASessionCredential:
    if row.get("cipher_version") != _CIPHER_VERSION:
        raise ValueError

    nonce_value = row.get("nonce")
    encrypted_value = row.get("encrypted_payload")
    if not isinstance(nonce_value, (bytes, bytearray, memoryview)):
        raise TypeError
    if not isinstance(encrypted_value, (bytes, bytearray, memoryview)):
        raise TypeError
    nonce = bytes(nonce_value)
    encrypted_payload = bytes(encrypted_value)
    if len(nonce) != _GCM_NONCE_BYTES or len(encrypted_payload) < 16:
        raise ValueError

    expires_at = row.get("expires_at")
    if (
        not isinstance(expires_at, datetime)
        or expires_at.tzinfo is None
        or expires_at.utcoffset() is None
    ):
        raise TypeError

    plaintext = cipher.decrypt(
        nonce,
        encrypted_payload,
        _associated_data(ai_user_id),
    )
    decoded: object = json.loads(
        plaintext.decode("utf-8"),
        object_pairs_hook=_object_without_duplicate_keys,
    )
    if not isinstance(decoded, dict) or set(decoded) != {"oa_user_id", "cookies"}:
        raise ValueError

    oa_user_id = decoded["oa_user_id"]
    raw_cookies = decoded["cookies"]
    if not isinstance(oa_user_id, str) or not oa_user_id:
        raise TypeError
    if not isinstance(raw_cookies, dict):
        raise TypeError

    cookies: dict[str, SecretStr] = {}
    for name, value in raw_cookies.items():
        if not isinstance(name, str) or not name or not isinstance(value, str):
            raise TypeError
        cookies[name] = SecretStr(value)

    return OASessionCredential(
        oa_user_id=SecretStr(oa_user_id),
        cookies=cookies,
        expires_at=expires_at,
    )


def _object_without_duplicate_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


if TYPE_CHECKING:

    def _credential_store_protocol_check(
        store: PostgreSQLCredentialStore,
    ) -> CredentialStorePort:
        return store


__all__ = (
    "PostgreSQLCredentialStore",
    "PostgreSQLPrincipalRoleReader",
    "credential_associated_data",
)
