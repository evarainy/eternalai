"""PostgreSQL-backed encrypted OA credential and principal-role storage."""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING, AsyncIterator
from uuid import uuid4

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, async_sessionmaker

from app.ports.auth import (
    CredentialSnapshot,
    CredentialStoreError,
    CredentialStorePort,
    CredentialWriteStamp,
    OASessionCredential,
    StaleCredentialWrite,
)
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
from app.ports.credential_vault import BrowserAuthorizationError, BrowserBindingFact

_CIPHER_VERSION = "aes256gcm-v1"
_AES_256_KEY_BYTES = 32
_GCM_NONCE_BYTES = 12
_PASSWORD_CIPHER_VERSION = "aes256gcm-password-v1"
_SESSION_V2 = "aes256gcm-session-v2"
_PASSWORD_V2 = "aes256gcm-password-v2"
_BINDING_WRITER_GUARD = 746420210000
_STAMP_COLUMNS = (
    "tenant_id, ai_user_id, target_system, binding_id, "
    "binding_revision, credential_write_revision, refresh_epoch"
)
_KEY_WHERE = "tenant_id=:tenant_id AND ai_user_id=:ai_user_id AND target_system=:target_system"
_SNAPSHOT_WHERE = (
    _KEY_WHERE + " AND binding_id=:binding_id AND binding_revision=:binding_revision"
    " AND credential_write_revision=:credential_write_revision AND refresh_epoch=:refresh_epoch"
)
_WRITE_WHERE = (
    _SNAPSHOT_WHERE + " AND refresh_operation_id=:operation_id"
    " AND refresh_deadline=:deadline AND refresh_deadline > clock_timestamp()"
)
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
        self._ddl_guard: ContextVar[tuple[AsyncConnection, object] | None] = ContextVar(
            "credential_ddl_guard", default=None
        )
        self._coordinator: ContextVar[
            tuple[tuple[str, str, str], AsyncConnection, object] | None
        ] = ContextVar("credential_coordinator", default=None)

    async def check_binding(self, fact: BrowserBindingFact) -> None:
        async with self._session_factory() as session:
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT binding_revision,binding_state,binding_subject_digest,"
                            "revoked_at,"
                            "cipher_version,password_cipher_version FROM oa_session_credentials"
                            f" WHERE {_KEY_WHERE} AND binding_id=:binding_id"
                        ),
                        {
                            **_key(fact.tenant_id, fact.ai_user_id, fact.target_system),
                            "binding_id": fact.binding_id,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
        if (
            row is None
            or row["binding_revision"] != fact.binding_revision
            or row["binding_state"] != "active"
            or row["revoked_at"] is not None
            or row["binding_subject_digest"] != fact.subject_digest
        ):
            raise BrowserAuthorizationError("browser_binding_stale")
        if row["cipher_version"] != _SESSION_V2 and row["password_cipher_version"] != _PASSWORD_V2:
            raise BrowserAuthorizationError("browser_binding_reverification_required")

    async def load_for_browser(
        self,
        fact: BrowserBindingFact,
        purpose: str,
    ) -> PasswordBindingCredential | OASessionCredential:
        if purpose not in {"browser_login", "browser_restore"}:
            raise BrowserAuthorizationError("browser_credential_purpose_invalid")
        # Purpose-specific private path; never delegates to the unattended polling reader.
        async with self._session_factory() as session:
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT tenant_id,target_system,binding_id,cipher_version,nonce,"
                            "encrypted_payload,"
                            "expires_at,password_cipher_version,password_nonce,"
                            "encrypted_password_payload"
                            f" FROM oa_session_credentials WHERE {_KEY_WHERE} AND"
                            f" binding_id=:binding_id"
                            " AND binding_revision=:binding_revision AND"
                            " binding_subject_digest=:subject_digest"
                            " AND binding_state='active' AND revoked_at IS NULL"
                        ),
                        {
                            **_key(fact.tenant_id, fact.ai_user_id, fact.target_system),
                            "binding_id": fact.binding_id,
                            "binding_revision": fact.binding_revision,
                            "subject_digest": fact.subject_digest,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise BrowserAuthorizationError("browser_binding_stale")
            try:
                if purpose == "browser_login":
                    if row["password_cipher_version"] != _PASSWORD_V2:
                        raise ValueError
                    return _decode_password_row(
                        cipher=self._cipher,
                        ai_user_id=fact.ai_user_id,
                        target_system=fact.target_system,
                        row=row,
                    )
                if row["cipher_version"] != _SESSION_V2 or row["expires_at"] <= datetime.now(UTC):
                    raise ValueError
                return _decode_credential_row(
                    cipher=self._cipher, ai_user_id=fact.ai_user_id, row=row
                )
            except Exception:
                raise BrowserAuthorizationError("browser_credential_unavailable") from None

    async def snapshot(
        self,
        ai_user_id: str,
        target_system: str,
        *,
        tenant_id: str,
    ) -> CredentialSnapshot:
        _validate_binding_key(ai_user_id, target_system)
        async with self._session_factory() as session:
            row = (
                (
                    await session.execute(
                        text(
                            f"SELECT {_STAMP_COLUMNS} FROM oa_session_credentials WHERE"
                            f" {_KEY_WHERE}"
                        ),
                        _key(tenant_id, ai_user_id, target_system),
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return CredentialSnapshot(
                tenant_id, ai_user_id, target_system, uuid4().hex, 1, 0, 0, True
            )
        return _snapshot(row)

    async def claim_write(self, snapshot: CredentialSnapshot) -> CredentialWriteStamp:
        operation_id = uuid4().hex
        params = {**_snapshot_params(snapshot), "operation_id": operation_id}
        async with self._write_session(
            snapshot.tenant_id, snapshot.ai_user_id, snapshot.target_system
        ) as session:
            if snapshot.absent:
                query = (
                    "INSERT INTO oa_session_credentials (tenant_id,ai_user_id,"
                    "target_system,binding_id,"
                    "binding_state,binding_revision,credential_write_revision,refresh_epoch,"
                    "refresh_operation_id,refresh_deadline) VALUES"
                    " (:tenant_id,:ai_user_id,:target_system,:binding_id,'unverified',1,0,1,"
                    ":operation_id,clock_timestamp()+interval '120 seconds')"
                    " ON CONFLICT (tenant_id,ai_user_id,target_system) DO NOTHING"
                )
            else:
                query = (
                    "UPDATE oa_session_credentials SET refresh_epoch=refresh_epoch+1,"
                    "refresh_operation_id=:operation_id,"
                    "refresh_deadline=clock_timestamp()+interval '120 seconds'"
                    f" WHERE {_SNAPSHOT_WHERE}"
                )
            row = (
                (
                    await session.execute(
                        text(query + f" RETURNING {_STAMP_COLUMNS}, refresh_deadline"), params
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise StaleCredentialWrite("credential write fenced")
            await session.commit()
        return CredentialWriteStamp(_snapshot(row), operation_id, row["refresh_deadline"])

    async def store(
        self,
        ai_user_id: str,
        target_system: str,
        credential: OASessionCredential,
        *,
        tenant_id: str,
        reactivate_revoked_session: bool = True,
        expected_write: CredentialWriteStamp,
    ) -> CredentialWriteStamp:
        _assert_stamp_key(expected_write, tenant_id, ai_user_id, target_system)
        if target_system != "oa":
            raise ValueError("OA session credentials require the OA target system")
        nonce = os.urandom(_GCM_NONCE_BYTES)
        payload = json.dumps(
            {
                "oa_user_id": credential.oa_user_id.get_secret_value(),
                "cookies": {k: v.get_secret_value() for k, v in credential.cookies.items()},
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        encrypted_payload = self._cipher.encrypt(
            nonce, payload, _v2_aad(expected_write.snapshot, "session")
        )
        mutation = ""
        if reactivate_revoked_session:
            mutation = (
                ", revoked_at=NULL, binding_revision=binding_revision+1,"
                "binding_state='unverified',binding_subject_digest=NULL,"
                "binding_subject_verified_at=NULL"
            )
        async with self._write_session(tenant_id, ai_user_id, target_system) as session:
            current = (
                await session.execute(
                    text(
                        f"SELECT binding_state FROM oa_session_credentials WHERE"
                        f" {_WRITE_WHERE} FOR UPDATE"
                    ),
                    _stamp_params(expected_write),
                )
            ).scalar_one_or_none()
            if current is None:
                raise StaleCredentialWrite("credential write fenced")
            if current == "active" and not reactivate_revoked_session:
                raise CredentialStoreError("browser_subject_verifier_unavailable")
            row = (
                (
                    await session.execute(
                        text(
                            "UPDATE oa_session_credentials SET cipher_version=:version,"
                            "nonce=:nonce,"
                            "encrypted_payload=:payload,expires_at=:expires_at,"
                            "updated_at=clock_timestamp(),"
                            "credential_write_revision=credential_write_revision+1"
                            + mutation
                            + f" WHERE {_WRITE_WHERE} RETURNING {_STAMP_COLUMNS}"
                        ),
                        {
                            **_stamp_params(expected_write),
                            "version": _SESSION_V2,
                            "nonce": nonce,
                            "payload": encrypted_payload,
                            "expires_at": credential.expires_at,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise StaleCredentialWrite("credential write fenced")
            await session.commit()
        return CredentialWriteStamp(
            _snapshot(row), expected_write.operation_id, expected_write.deadline
        )

    async def load(
        self, ai_user_id: str, target_system: str, *, tenant_id: str
    ) -> OASessionCredential | None:
        """Decrypt one OA Session row or fail with a context-free safe error."""

        if not ai_user_id or target_system != "oa":
            raise CredentialStoreError("OA session credential cannot be loaded")

        credential: OASessionCredential | None = None
        load_failed = False
        try:
            async with self._session_factory() as session:
                row: RowMapping | None = (
                    (
                        await session.execute(
                            text(
                                "SELECT cipher_version, nonce, encrypted_payload, expires_at,"
                                " revoked_at, tenant_id, target_system, binding_id"
                                " FROM oa_session_credentials"
                                " WHERE tenant_id = :tenant_id AND ai_user_id = :ai_user_id"
                                " AND target_system = :target_system"
                            ),
                            {
                                "tenant_id": tenant_id,
                                "ai_user_id": ai_user_id,
                                "target_system": target_system,
                            },
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
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
        *,
        tenant_id: str,
        expected_write: CredentialWriteStamp,
    ) -> CredentialBindingView:
        _assert_stamp_key(expected_write, tenant_id, ai_user_id, target_system)
        nonce = os.urandom(_GCM_NONCE_BYTES)
        payload = json.dumps(
            {
                "login_id": credential.login_id.get_secret_value(),
                "password": credential.password.get_secret_value(),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        encrypted = self._cipher.encrypt(
            nonce, payload, _v2_aad(expected_write.snapshot, "password")
        )
        async with self._write_session(tenant_id, ai_user_id, target_system) as session:
            row = (
                (
                    await session.execute(
                        text(
                            "UPDATE oa_session_credentials SET"
                            " password_cipher_version=:version, password_nonce=:nonce,"
                            "encrypted_password_payload=:payload,"
                            " binding_revision=binding_revision+1,"
                            "credential_write_revision=credential_write_revision+1,"
                            " binding_state='unverified',"
                            "binding_subject_digest=NULL,binding_subject_verified_at=NULL,"
                            "revoked_at=CASE WHEN poll_status='invalid' THEN NULL ELSE"
                            " revoked_at END,"
                            "poll_status='active',poll_failure_count=0,"
                            "updated_at=clock_timestamp(),"
                            "refresh_operation_id=NULL,refresh_deadline=NULL"
                            f" WHERE {_WRITE_WHERE} RETURNING poll_status,poll_failure_count,"
                            f"updated_at,encrypted_password_payload"
                        ),
                        {
                            **_stamp_params(expected_write),
                            "version": _PASSWORD_V2,
                            "nonce": nonce,
                            "payload": encrypted,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise StaleCredentialWrite("credential write fenced")
            await session.commit()
        return _binding_view(row, target_system)

    async def get_password_binding(
        self, ai_user_id: str, target_system: CredentialTargetSystem, *, tenant_id: str
    ) -> CredentialBindingView:
        _validate_binding_key(ai_user_id, target_system)
        async with self._session_factory() as session:
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT poll_status, poll_failure_count, updated_at,"
                            " encrypted_password_payload"
                            " FROM oa_session_credentials"
                            " WHERE tenant_id = :tenant_id AND ai_user_id = :ai_user_id"
                            " AND target_system = :target_system"
                        ),
                        {
                            "tenant_id": tenant_id,
                            "ai_user_id": ai_user_id,
                            "target_system": target_system,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
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
        *,
        tenant_id: str,
    ) -> CredentialBindingView:
        _validate_binding_key(ai_user_id, target_system)
        async with self._session_factory() as session:
            await session.execute(
                text("SELECT pg_advisory_xact_lock_shared(:key)"), {"key": _BINDING_WRITER_GUARD}
            )
            row = (
                (
                    await session.execute(
                        text(
                            "INSERT INTO oa_session_credentials (tenant_id,ai_user_id,"
                            "target_system,binding_id,"
                            "binding_state,binding_revision,credential_write_revision,"
                            "refresh_epoch)"
                            " VALUES (:tenant_id,:ai_user_id,:target_system,:binding_id,"
                            "'unbound',1,1,1)"
                            " ON CONFLICT (tenant_id,ai_user_id,target_system) DO UPDATE SET"
                            " password_cipher_version=NULL,password_nonce=NULL,"
                            "encrypted_password_payload=NULL,"
                            "poll_status='unbound',poll_failure_count=0,"
                            "updated_at=clock_timestamp(),"
                            "binding_state='unbound',"
                            "binding_revision=oa_session_credentials.binding_revision+1,"
                            "credential_write_revision=oa_session_credentials.credential_write_revision+1,"
                            "refresh_epoch=oa_session_credentials.refresh_epoch+1,"
                            "refresh_operation_id=NULL,"
                            "refresh_deadline=NULL,binding_subject_digest=NULL,"
                            "binding_subject_verified_at=NULL"
                            " RETURNING poll_status,poll_failure_count,updated_at,"
                            "encrypted_password_payload"
                        ),
                        {**_key(tenant_id, ai_user_id, target_system), "binding_id": uuid4().hex},
                    )
                )
                .mappings()
                .one()
            )
            await session.commit()
        return _binding_view(row, target_system)

    async def list_poll_candidates(self, *, tenant_id: str) -> list[CredentialPollCandidate]:
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT tenant_id, ai_user_id, target_system, poll_failure_count,"
                            " updated_at, binding_id, binding_revision,"
                            " credential_write_revision, refresh_epoch FROM oa_session_credentials"
                            " WHERE tenant_id = :tenant_id"
                            " AND encrypted_password_payload IS NOT NULL"
                            " AND target_system = 'oa'"
                            " AND revoked_at IS NULL"
                            " AND poll_status IN ('active', 'retrying')"
                            " ORDER BY updated_at ASC, ai_user_id ASC, target_system ASC"
                        ),
                        {"tenant_id": tenant_id},
                    )
                )
                .mappings()
                .all()
            )
        return [_candidate(row) for row in rows]

    async def refresh_poll_candidate(
        self, ai_user_id: str, target_system: CredentialTargetSystem, *, tenant_id: str
    ) -> CredentialPollCandidate | None:
        _validate_binding_key(ai_user_id, target_system)
        async with self._session_factory() as session:
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT tenant_id, ai_user_id, target_system, poll_failure_count,"
                            " updated_at, binding_id, binding_revision,"
                            " credential_write_revision, refresh_epoch FROM oa_session_credentials"
                            " WHERE tenant_id = :tenant_id AND ai_user_id = :ai_user_id"
                            " AND target_system = :target_system"
                            " AND encrypted_password_payload IS NOT NULL"
                            " AND revoked_at IS NULL"
                            " AND poll_status IN ('active', 'retrying')"
                        ),
                        {
                            "tenant_id": tenant_id,
                            "ai_user_id": ai_user_id,
                            "target_system": target_system,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        return _candidate(row)

    @asynccontextmanager
    async def _write_session(
        self,
        tenant_id: str,
        ai_user_id: str,
        target_system: str,
    ) -> AsyncIterator[AsyncSession]:
        active = self._coordinator.get()
        key = (tenant_id, ai_user_id, target_system)
        if active is None:
            async with self.poll_lock(ai_user_id, target_system, tenant_id=tenant_id) as acquired:
                if not acquired:
                    raise StaleCredentialWrite("credential writer busy")
                async with self._write_session(tenant_id, ai_user_id, target_system) as session:
                    yield session
            return
        slot, connection, owner_task = active
        if owner_task is not asyncio.current_task():
            raise StaleCredentialWrite("credential coordinator task mismatch")
        # Never let an invalidated coordinator reconnect or hand a write to the pool.
        if slot != key or connection.closed or connection.invalidated:
            raise StaleCredentialWrite("credential coordinator lost")
        async with self._session_factory(bind=connection) as session:
            yield session

    @asynccontextmanager
    async def writer_guard(self) -> AsyncIterator[None]:
        existing = self._ddl_guard.get()
        if existing is not None and existing[1] is asyncio.current_task():
            if existing[0].closed or existing[0].invalidated:
                raise StaleCredentialWrite("credential coordinator lost")
            yield
            return
        engine = self._session_factory.kw.get("bind")
        if not isinstance(engine, AsyncEngine):
            raise CredentialStoreError("credential coordinator unavailable")
        async with engine.connect() as connection:
            await connection.execute(
                text("SELECT pg_advisory_lock_shared(:key)"), {"key": _BINDING_WRITER_GUARD}
            )
            await connection.commit()
            token = self._ddl_guard.set((connection, asyncio.current_task()))
            try:
                yield
            finally:
                self._ddl_guard.reset(token)
                if not connection.closed and not connection.invalidated:
                    await connection.execute(
                        text("SELECT pg_advisory_unlock_shared(:key)"),
                        {"key": _BINDING_WRITER_GUARD},
                    )
                    await connection.commit()

    @asynccontextmanager
    async def poll_lock(
        self,
        ai_user_id: str,
        target_system: str,
        *,
        tenant_id: str,
    ) -> AsyncIterator[bool]:
        _validate_binding_key(ai_user_id, target_system)
        key = (tenant_id, ai_user_id, target_system)
        active = self._coordinator.get()
        if active is not None and active[2] is asyncio.current_task():
            if active[0] != key or active[1].closed or active[1].invalidated:
                raise StaleCredentialWrite("credential coordinator lost")
            yield True
            return
        guard = self._ddl_guard.get()
        if guard is None or guard[1] is not asyncio.current_task():
            async with self.writer_guard():
                async with self.poll_lock(ai_user_id, target_system, tenant_id=tenant_id) as locked:
                    yield locked
            return
        guard = self._ddl_guard.get()
        if guard is None:
            raise StaleCredentialWrite("credential coordinator lost")
        connection = guard[0]
        if connection.closed or connection.invalidated:
            raise StaleCredentialWrite("credential coordinator lost")
        lock_key = _advisory_lock_key(tenant_id, ai_user_id, target_system)
        acquired = bool(
            (
                await connection.execute(
                    text("SELECT pg_try_advisory_lock(:lock_key)"), {"lock_key": lock_key}
                )
            ).scalar_one()
        )
        await connection.commit()
        token = (
            self._coordinator.set((key, connection, asyncio.current_task())) if acquired else None
        )
        try:
            yield acquired
        finally:
            if token is not None:
                self._coordinator.reset(token)
            if acquired and not connection.closed and not connection.invalidated:
                await connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_key)"), {"lock_key": lock_key}
                )
                await connection.commit()

    async def load_password_for_poll(
        self,
        ai_user_id: str,
        target_system: CredentialTargetSystem,
        *,
        tenant_id: str,
        expected_write: CredentialWriteStamp,
    ) -> PasswordBindingCredential:
        _assert_stamp_key(expected_write, tenant_id, ai_user_id, target_system)
        try:
            async with self._write_session(tenant_id, ai_user_id, target_system) as session:
                row = (
                    (
                        await session.execute(
                            text(
                                "SELECT password_cipher_version, password_nonce,"
                                " encrypted_password_payload, tenant_id, target_system, binding_id"
                                " FROM oa_session_credentials"
                                f" WHERE {_WRITE_WHERE}"
                                " AND revoked_at IS NULL"
                                " AND poll_status IN ('active', 'retrying')"
                            ),
                            _stamp_params(expected_write),
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
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
        *,
        tenant_id: str,
        expected_write: CredentialWriteStamp,
    ) -> None:
        await self._update_poll_state(
            ai_user_id,
            target_system,
            tenant_id=tenant_id,
            expected_write=expected_write,
            status="active",
            revoke_session=False,
            increment_non_authentication_failure=False,
        )

    async def mark_non_authentication_failure(
        self,
        ai_user_id: str,
        target_system: CredentialTargetSystem,
        *,
        tenant_id: str,
        expected_write: CredentialWriteStamp,
    ) -> None:
        """Count only transport, 5xx, timeout, or invalid-response failures."""

        await self._update_poll_state(
            ai_user_id,
            target_system,
            tenant_id=tenant_id,
            expected_write=expected_write,
            status="retrying",
            revoke_session=False,
            increment_non_authentication_failure=True,
        )

    async def mark_non_counted_failure(
        self,
        ai_user_id: str,
        target_system: CredentialTargetSystem,
        *,
        tenant_id: str,
        expected_write: CredentialWriteStamp,
    ) -> None:
        """Delay an unknown/local failure without consuming the external counter."""

        await self._update_poll_state(
            ai_user_id,
            target_system,
            tenant_id=tenant_id,
            expected_write=expected_write,
            status="retrying",
            revoke_session=False,
            increment_non_authentication_failure=False,
            preserve_failure_count=True,
        )

    async def mark_terminal_authentication_failure(
        self,
        ai_user_id: str,
        target_system: CredentialTargetSystem,
        failure: CredentialTerminalFailure,
        *,
        tenant_id: str,
        expected_write: CredentialWriteStamp,
    ) -> None:
        """Stop polling; only an explicit authentication denial revokes the Session."""

        await self._update_poll_state(
            ai_user_id,
            target_system,
            tenant_id=tenant_id,
            expected_write=expected_write,
            status=failure,
            revoke_session=failure == "invalid",
            increment_non_authentication_failure=False,
        )

    async def _update_poll_state(
        self,
        ai_user_id: str,
        target_system: CredentialTargetSystem,
        *,
        tenant_id: str,
        expected_write: CredentialWriteStamp,
        status: str,
        revoke_session: bool,
        increment_non_authentication_failure: bool,
        preserve_failure_count: bool = False,
    ) -> None:
        _assert_stamp_key(expected_write, tenant_id, ai_user_id, target_system)
        now = datetime.now(UTC)
        if increment_non_authentication_failure:
            failure_expression = "poll_failure_count + 1"
        elif preserve_failure_count:
            failure_expression = "poll_failure_count"
        else:
            failure_expression = "0"
        revoked_expression = ":updated_at" if revoke_session else "revoked_at"
        binding_mutation = (
            ", binding_state='revoked',binding_revision=binding_revision+1,"
            "binding_subject_digest=NULL,binding_subject_verified_at=NULL"
            if revoke_session
            else ""
        )
        async with self._write_session(tenant_id, ai_user_id, target_system) as session:
            result = await session.execute(
                text(
                    "UPDATE oa_session_credentials SET poll_status = :poll_status,"
                    f" poll_failure_count = {failure_expression},"
                    f" revoked_at = {revoked_expression}, updated_at = :updated_at,"
                    " credential_write_revision=credential_write_revision+1,"
                    " refresh_operation_id=NULL,refresh_deadline=NULL"
                    + binding_mutation
                    + f" WHERE {_WRITE_WHERE}"
                    " AND encrypted_password_payload IS NOT NULL"
                    " AND revoked_at IS NULL"
                    " AND poll_status IN ('active', 'retrying') RETURNING binding_id"
                ),
                {
                    **_stamp_params(expected_write),
                    "poll_status": status,
                    "updated_at": now,
                },
            )
            if result.scalar_one_or_none() is None:
                raise StaleCredentialWrite("credential write fenced")
            await session.commit()


class PostgreSQLPrincipalRoleReader:
    """Read locally assigned roles; an absent mapping is intentionally empty."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def list_roles(self, ai_user_id: str, *, tenant_id: str) -> tuple[str, ...]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT role FROM principal_roles"
                        " WHERE tenant_id = :tenant_id AND ai_user_id = :ai_user_id"
                        " ORDER BY role ASC"
                    ),
                    {"tenant_id": tenant_id, "ai_user_id": ai_user_id},
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
    target_system: str,
) -> bytes:
    return f"{_PASSWORD_CIPHER_VERSION}\x00{ai_user_id}\x00{target_system}".encode("utf-8")


def _validate_binding_key(
    ai_user_id: str,
    target_system: str,
) -> None:
    if not ai_user_id or target_system not in _SUPPORTED_TARGET_SYSTEMS:
        raise ValueError("credential binding key is invalid")


def _key(tenant_id: str, ai_user_id: str, target_system: str) -> dict[str, object]:
    return {"tenant_id": tenant_id, "ai_user_id": ai_user_id, "target_system": target_system}


def _snapshot(row: RowMapping) -> CredentialSnapshot:
    return CredentialSnapshot(**{name: row[name] for name in _STAMP_COLUMNS.split(", ")})


def _snapshot_params(snapshot: CredentialSnapshot) -> dict[str, object]:
    if (
        max(snapshot.binding_revision, snapshot.credential_write_revision, snapshot.refresh_epoch)
        >= 9007199254740991
    ):
        raise StaleCredentialWrite("credential_revision_exhausted")
    return {name: getattr(snapshot, name) for name in _STAMP_COLUMNS.split(", ")}


def _stamp_params(stamp: CredentialWriteStamp) -> dict[str, object]:
    return {
        **_snapshot_params(stamp.snapshot),
        "operation_id": stamp.operation_id,
        "deadline": stamp.deadline,
    }


def _assert_stamp_key(
    stamp: CredentialWriteStamp, tenant_id: str, ai_user_id: str, target_system: str
) -> None:
    if (stamp.snapshot.tenant_id, stamp.snapshot.ai_user_id, stamp.snapshot.target_system) != (
        tenant_id,
        ai_user_id,
        target_system,
    ):
        raise StaleCredentialWrite("credential write fenced")


def _v2_aad(snapshot: CredentialSnapshot, purpose: str) -> bytes:
    if purpose not in {"session", "password"}:
        raise ValueError("credential_purpose_invalid")
    return json.dumps(
        [
            "credential-v2",
            _SESSION_V2 if purpose == "session" else _PASSWORD_V2,
            "runtime-single-injected-key-v1",
            purpose,
            snapshot.tenant_id,
            snapshot.ai_user_id,
            snapshot.target_system,
            snapshot.binding_id,
        ],
        separators=(",", ":"),
    ).encode()


def _row_v2_aad(row: RowMapping, ai_user_id: str, purpose: str) -> bytes:
    return _v2_aad(
        CredentialSnapshot(
            row["tenant_id"], ai_user_id, row["target_system"], row["binding_id"], 1, 0, 0
        ),
        purpose,
    )


def _candidate(row: RowMapping) -> CredentialPollCandidate:
    return CredentialPollCandidate(
        tenant_id=row["tenant_id"],
        ai_user_id=row["ai_user_id"],
        target_system=row["target_system"],
        poll_failure_count=row["poll_failure_count"],
        updated_at=row["updated_at"],
        snapshot=_snapshot(row),
    )


def _advisory_lock_key(
    tenant_id: str,
    ai_user_id: str,
    target_system: str,
) -> int:
    digest = sha256(
        json.dumps(
            ["credential-poll", tenant_id, ai_user_id, target_system], separators=(",", ":")
        ).encode()
    ).digest()
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
    target_system: str,
    row: RowMapping,
) -> PasswordBindingCredential:
    if row.get("password_cipher_version") not in {_PASSWORD_CIPHER_VERSION, _PASSWORD_V2}:
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
        (
            _row_v2_aad(row, ai_user_id, "password")
            if row["password_cipher_version"] == _PASSWORD_V2
            else _password_associated_data(ai_user_id, target_system)
        ),
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
    if row.get("cipher_version") not in {_CIPHER_VERSION, _SESSION_V2}:
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
        (
            _row_v2_aad(row, ai_user_id, "session")
            if row["cipher_version"] == _SESSION_V2
            else _associated_data(ai_user_id)
        ),
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
