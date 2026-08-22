"""PostgreSQL-backed encrypted OA credential and principal-role storage."""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING, AsyncIterator

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ports.auth import CredentialStoreError, CredentialStorePort, OASessionCredential
from app.ports.credential_binding import (
    CredentialBindingStorePort,
    CredentialBindingView,
    CredentialPollCandidate,
    CredentialPollingStorePort,
    CredentialTargetSystem,
    CredentialTerminalFailure,
    PasswordBindingCredential,
    PasswordBindingReaderPort,
)

_CIPHER_VERSION = "aes256gcm-v1"
_AES_256_KEY_BYTES = 32
_GCM_NONCE_BYTES = 12
_PASSWORD_CIPHER_VERSION = "aes256gcm-password-v1"
_SUPPORTED_TARGET_SYSTEMS = frozenset({"oa", "u8", "hikvision_ivms"})


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
        target_system: str,
        credential: OASessionCredential,
    ) -> None:
        if not ai_user_id:
            raise ValueError("ai_user_id must not be blank")
        if target_system != "oa":
            raise ValueError("OA session credentials require the OA target system")
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
                    " (ai_user_id, target_system, cipher_version, nonce,"
                    " encrypted_payload,"
                    " expires_at, updated_at)"
                    " VALUES"
                    " (:ai_user_id, :target_system, :cipher_version, :nonce,"
                    " :encrypted_payload,"
                    " :expires_at, :updated_at)"
                    " ON CONFLICT (ai_user_id, target_system) DO UPDATE SET"
                    " cipher_version = EXCLUDED.cipher_version,"
                    " nonce = EXCLUDED.nonce,"
                    " encrypted_payload = EXCLUDED.encrypted_payload,"
                    " expires_at = EXCLUDED.expires_at,"
                    " revoked_at = NULL,"
                    " updated_at = EXCLUDED.updated_at"
                ),
                {
                    "ai_user_id": ai_user_id,
                    "target_system": target_system,
                    "cipher_version": _CIPHER_VERSION,
                    "nonce": nonce,
                    "encrypted_payload": encrypted_payload,
                    "expires_at": credential.expires_at,
                    "updated_at": datetime.now(UTC),
                },
            )
            await session.commit()

    async def load(
        self,
        ai_user_id: str,
        target_system: str,
    ) -> OASessionCredential | None:
        """Decrypt one OA Session row or fail with a context-free safe error."""

        if not ai_user_id or target_system != "oa":
            raise CredentialStoreError("OA session credential cannot be loaded")

        credential: OASessionCredential | None = None
        load_failed = False
        try:
            async with self._session_factory() as session:
                row: RowMapping | None = (
                    await session.execute(
                        text(
                            "SELECT cipher_version, nonce, encrypted_payload, expires_at,"
                            " revoked_at"
                            " FROM oa_session_credentials"
                            " WHERE ai_user_id = :ai_user_id"
                            " AND target_system = :target_system"
                        ),
                        {
                            "ai_user_id": ai_user_id,
                            "target_system": target_system,
                        },
                    )
                ).mappings().one_or_none()
                if row is None:
                    return None
                if row.get("revoked_at") is not None:
                    load_failed = True
                else:
                    credential = _decode_credential_row(
                        cipher=self._cipher,
                        ai_user_id=ai_user_id,
                        row=row,
                    )
        except Exception:
            load_failed = True

        if load_failed or credential is None:
            raise CredentialStoreError("OA session credential cannot be loaded")
        return credential

    async def bind_password(
        self,
        ai_user_id: str,
        target_system: CredentialTargetSystem,
        credential: PasswordBindingCredential,
    ) -> CredentialBindingView:
        _validate_binding_key(ai_user_id, target_system)
        nonce = os.urandom(_GCM_NONCE_BYTES)
        payload = json.dumps(
            {
                "login_id": credential.login_id.get_secret_value(),
                "password": credential.password.get_secret_value(),
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        encrypted_payload = self._cipher.encrypt(
            nonce,
            payload,
            _password_associated_data(ai_user_id, target_system),
        )
        now = datetime.now(UTC)
        async with self._session_factory() as session:
            await session.execute(
                text(
                    "INSERT INTO oa_session_credentials"
                    " (ai_user_id, target_system, password_cipher_version,"
                    " password_nonce, encrypted_password_payload, poll_status,"
                    " poll_failure_count, updated_at) VALUES"
                    " (:ai_user_id, :target_system, :password_cipher_version,"
                    " :password_nonce, :encrypted_password_payload, 'active', 0,"
                    " :updated_at)"
                    " ON CONFLICT (ai_user_id, target_system) DO UPDATE SET"
                    " password_cipher_version = :password_cipher_version,"
                    " password_nonce = :password_nonce,"
                    " encrypted_password_payload = :encrypted_password_payload,"
                    " poll_status = 'active', poll_failure_count = 0,"
                    " revoked_at = NULL, updated_at = :updated_at"
                ),
                {
                    "ai_user_id": ai_user_id,
                    "target_system": target_system,
                    "password_cipher_version": _PASSWORD_CIPHER_VERSION,
                    "password_nonce": nonce,
                    "encrypted_password_payload": encrypted_payload,
                    "updated_at": now,
                },
            )
            await session.commit()
        return CredentialBindingView(
            target_system=target_system,
            poll_status="active",
            poll_failure_count=0,
            updated_at=now,
            bound=True,
        )

    async def get_password_binding(
        self,
        ai_user_id: str,
        target_system: CredentialTargetSystem,
    ) -> CredentialBindingView:
        _validate_binding_key(ai_user_id, target_system)
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT poll_status, poll_failure_count, updated_at,"
                        " encrypted_password_payload"
                        " FROM oa_session_credentials"
                        " WHERE ai_user_id = :ai_user_id"
                        " AND target_system = :target_system"
                    ),
                    {"ai_user_id": ai_user_id, "target_system": target_system},
                )
            ).mappings().one_or_none()
        if row is None:
            return CredentialBindingView(
                target_system=target_system,
                poll_status="unbound",
                poll_failure_count=0,
                updated_at=None,
                bound=False,
            )
        return _binding_view(row, target_system)

    async def unbind_password(
        self,
        ai_user_id: str,
        target_system: CredentialTargetSystem,
    ) -> CredentialBindingView:
        _validate_binding_key(ai_user_id, target_system)
        now = datetime.now(UTC)
        async with self._session_factory() as session:
            await session.execute(
                text(
                    "UPDATE oa_session_credentials SET"
                    " password_cipher_version = NULL, password_nonce = NULL,"
                    " encrypted_password_payload = NULL, poll_status = 'unbound',"
                    " poll_failure_count = 0, revoked_at = :revoked_at,"
                    " updated_at = :updated_at"
                    " WHERE ai_user_id = :ai_user_id"
                    " AND target_system = :target_system"
                ),
                {
                    "ai_user_id": ai_user_id,
                    "target_system": target_system,
                    "revoked_at": now,
                    "updated_at": now,
                },
            )
            await session.commit()
        return CredentialBindingView(
            target_system=target_system,
            poll_status="unbound",
            poll_failure_count=0,
            updated_at=now,
            bound=False,
        )

    async def list_poll_candidates(self) -> list[CredentialPollCandidate]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT ai_user_id, target_system, poll_failure_count,"
                        " updated_at FROM oa_session_credentials"
                        " WHERE encrypted_password_payload IS NOT NULL"
                        " AND target_system = 'oa'"
                        " AND revoked_at IS NULL"
                        " AND poll_status IN ('active', 'retrying')"
                        " ORDER BY updated_at ASC, ai_user_id ASC, target_system ASC"
                    )
                )
            ).mappings().all()
        return [
            CredentialPollCandidate.model_validate(dict(row), strict=True)
            for row in rows
        ]

    async def refresh_poll_candidate(
        self,
        ai_user_id: str,
        target_system: CredentialTargetSystem,
    ) -> CredentialPollCandidate | None:
        _validate_binding_key(ai_user_id, target_system)
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT ai_user_id, target_system, poll_failure_count,"
                        " updated_at FROM oa_session_credentials"
                        " WHERE ai_user_id = :ai_user_id"
                        " AND target_system = :target_system"
                        " AND encrypted_password_payload IS NOT NULL"
                        " AND revoked_at IS NULL"
                        " AND poll_status IN ('active', 'retrying')"
                    ),
                    {"ai_user_id": ai_user_id, "target_system": target_system},
                )
            ).mappings().one_or_none()
        if row is None:
            return None
        return CredentialPollCandidate.model_validate(dict(row), strict=True)

    @asynccontextmanager
    async def poll_lock(
        self,
        ai_user_id: str,
        target_system: CredentialTargetSystem,
    ) -> AsyncIterator[bool]:
        _validate_binding_key(ai_user_id, target_system)
        lock_key = _advisory_lock_key(ai_user_id, target_system)
        async with self._session_factory() as session:
            acquired = bool(
                (
                    await session.execute(
                        text("SELECT pg_try_advisory_lock(:lock_key)"),
                        {"lock_key": lock_key},
                    )
                ).scalar_one()
            )
            try:
                yield acquired
            finally:
                if acquired:
                    await session.execute(
                        text("SELECT pg_advisory_unlock(:lock_key)"),
                        {"lock_key": lock_key},
                    )

    async def load_password_for_poll(
        self,
        ai_user_id: str,
        target_system: CredentialTargetSystem,
    ) -> PasswordBindingCredential:
        _validate_binding_key(ai_user_id, target_system)
        try:
            async with self._session_factory() as session:
                row = (
                    await session.execute(
                        text(
                            "SELECT password_cipher_version, password_nonce,"
                            " encrypted_password_payload"
                            " FROM oa_session_credentials"
                            " WHERE ai_user_id = :ai_user_id"
                            " AND target_system = :target_system"
                            " AND revoked_at IS NULL"
                            " AND poll_status IN ('active', 'retrying')"
                        ),
                        {
                            "ai_user_id": ai_user_id,
                            "target_system": target_system,
                        },
                    )
                ).mappings().one_or_none()
            if row is None:
                raise ValueError
            return _decode_password_row(
                cipher=self._cipher,
                ai_user_id=ai_user_id,
                target_system=target_system,
                row=row,
            )
        except Exception:
            raise CredentialStoreError("password binding cannot be loaded") from None

    async def mark_poll_succeeded(
        self,
        ai_user_id: str,
        target_system: CredentialTargetSystem,
    ) -> None:
        await self._update_poll_state(
            ai_user_id,
            target_system,
            status="active",
            terminal=False,
            increment_non_authentication_failure=False,
        )

    async def mark_non_authentication_failure(
        self,
        ai_user_id: str,
        target_system: CredentialTargetSystem,
    ) -> None:
        """Count only transport, 5xx, timeout, or invalid-response failures."""

        await self._update_poll_state(
            ai_user_id,
            target_system,
            status="retrying",
            terminal=False,
            increment_non_authentication_failure=True,
        )

    async def mark_non_counted_failure(
        self,
        ai_user_id: str,
        target_system: CredentialTargetSystem,
    ) -> None:
        """Delay an unknown/local failure without consuming the external counter."""

        await self._update_poll_state(
            ai_user_id,
            target_system,
            status="retrying",
            terminal=False,
            increment_non_authentication_failure=False,
            preserve_failure_count=True,
        )

    async def mark_terminal_authentication_failure(
        self,
        ai_user_id: str,
        target_system: CredentialTargetSystem,
        failure: CredentialTerminalFailure,
    ) -> None:
        """Stop after one denial; keep the non-authentication failure count at zero."""

        await self._update_poll_state(
            ai_user_id,
            target_system,
            status=failure,
            terminal=True,
            increment_non_authentication_failure=False,
        )

    async def _update_poll_state(
        self,
        ai_user_id: str,
        target_system: CredentialTargetSystem,
        *,
        status: str,
        terminal: bool,
        increment_non_authentication_failure: bool,
        preserve_failure_count: bool = False,
    ) -> None:
        _validate_binding_key(ai_user_id, target_system)
        now = datetime.now(UTC)
        if increment_non_authentication_failure:
            failure_expression = "poll_failure_count + 1"
        elif preserve_failure_count:
            failure_expression = "poll_failure_count"
        else:
            failure_expression = "0"
        revoked_expression = ":updated_at" if terminal else "revoked_at"
        async with self._session_factory() as session:
            await session.execute(
                text(
                    "UPDATE oa_session_credentials SET poll_status = :poll_status,"
                    f" poll_failure_count = {failure_expression},"
                    f" revoked_at = {revoked_expression}, updated_at = :updated_at"
                    " WHERE ai_user_id = :ai_user_id"
                    " AND target_system = :target_system"
                    " AND encrypted_password_payload IS NOT NULL"
                    " AND revoked_at IS NULL"
                    " AND poll_status IN ('active', 'retrying')"
                ),
                {
                    "ai_user_id": ai_user_id,
                    "target_system": target_system,
                    "poll_status": status,
                    "updated_at": now,
                },
            )
            await session.commit()


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


def _password_associated_data(
    ai_user_id: str,
    target_system: CredentialTargetSystem,
) -> bytes:
    return (
        f"{_PASSWORD_CIPHER_VERSION}\x00{ai_user_id}\x00{target_system}".encode("utf-8")
    )


def _validate_binding_key(
    ai_user_id: str,
    target_system: CredentialTargetSystem,
) -> None:
    if not ai_user_id or target_system not in _SUPPORTED_TARGET_SYSTEMS:
        raise ValueError("credential binding key is invalid")


def _advisory_lock_key(
    ai_user_id: str,
    target_system: CredentialTargetSystem,
) -> int:
    digest = sha256(f"credential-poll\x00{ai_user_id}\x00{target_system}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _binding_view(
    row: RowMapping,
    target_system: CredentialTargetSystem,
) -> CredentialBindingView:
    return CredentialBindingView.model_validate(
        {
            "target_system": target_system,
            "poll_status": row.get("poll_status"),
            "poll_failure_count": row.get("poll_failure_count"),
            "updated_at": row.get("updated_at"),
            "bound": row.get("encrypted_password_payload") is not None,
        },
        strict=True,
    )


def _decode_password_row(
    *,
    cipher: AESGCM,
    ai_user_id: str,
    target_system: CredentialTargetSystem,
    row: RowMapping,
) -> PasswordBindingCredential:
    if row.get("password_cipher_version") != _PASSWORD_CIPHER_VERSION:
        raise ValueError
    nonce_value = row.get("password_nonce")
    encrypted_value = row.get("encrypted_password_payload")
    if not isinstance(nonce_value, (bytes, bytearray, memoryview)):
        raise TypeError
    if not isinstance(encrypted_value, (bytes, bytearray, memoryview)):
        raise TypeError
    nonce = bytes(nonce_value)
    encrypted_payload = bytes(encrypted_value)
    if len(nonce) != _GCM_NONCE_BYTES or len(encrypted_payload) < 16:
        raise ValueError
    plaintext = cipher.decrypt(
        nonce,
        encrypted_payload,
        _password_associated_data(ai_user_id, target_system),
    )
    decoded: object = json.loads(
        plaintext.decode("utf-8"),
        object_pairs_hook=_object_without_duplicate_keys,
    )
    if not isinstance(decoded, dict) or set(decoded) != {"login_id", "password"}:
        raise ValueError
    login_id = decoded["login_id"]
    password = decoded["password"]
    if not isinstance(login_id, str) or not login_id:
        raise TypeError
    if not isinstance(password, str) or not password:
        raise TypeError
    return PasswordBindingCredential(
        login_id=SecretStr(login_id),
        password=SecretStr(password),
    )


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

    def _credential_binding_store_protocol_check(
        store: PostgreSQLCredentialStore,
    ) -> CredentialBindingStorePort:
        return store

    def _credential_polling_store_protocol_check(
        store: PostgreSQLCredentialStore,
    ) -> CredentialPollingStorePort:
        return store

    def _password_binding_reader_protocol_check(
        store: PostgreSQLCredentialStore,
    ) -> PasswordBindingReaderPort:
        return store


__all__ = (
    "PostgreSQLCredentialStore",
    "PostgreSQLPrincipalRoleReader",
    "credential_associated_data",
)
