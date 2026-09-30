"""Atomic, encrypted, service-scoped MCP OAuth persistence."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import sqlalchemy as sa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infra.persistence.mcp.schema import (
    connections,
    grants,
    mappings,
    registrations,
    services,
    transactions,
)
from app.mcp.models import McpFailure, Ownership, ServiceConfig, ToolBinding, digest
from app.ports.auth import SessionRevocationStorePort
from app.ports.mcp import McpAuthorizationContext
from app.ports.mcp_store import AuthorizationTransaction, Connection


class PostgreSQLMcpStore:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        encryption_key: bytes,
        revocations: SessionRevocationStorePort,
    ) -> None:
        if len(encryption_key) != 32:
            raise ValueError("mcp_encryption_unavailable")
        self.sessions = session_factory
        self._cipher = AESGCM(encryption_key)
        self._revocations = revocations

    def encrypt(self, payload: dict[str, Any], aad: dict[str, Any]) -> bytes:
        nonce = os.urandom(12)
        return nonce + self._cipher.encrypt(
            nonce, json.dumps(payload).encode(), digest(aad).encode()
        )

    def decrypt(self, encrypted: bytes, aad: dict[str, Any]) -> dict[str, Any]:
        try:
            result = json.loads(
                self._cipher.decrypt(encrypted[:12], encrypted[12:], digest(aad).encode())
            )
            if not isinstance(result, dict):
                raise ValueError
            return result
        except Exception:
            raise McpFailure("mcp_secret_unavailable") from None

    async def configure(self, config: ServiceConfig) -> None:
        async with self.sessions.begin() as session:
            result = await session.execute(
                insert(services)
                .values(
                    tenant_id=config.tenant_id,
                    service_config_id=config.service_config_id,
                    version=config.service_config_version,
                    config=config.model_dump(mode="json"),
                )
                .on_conflict_do_nothing()
                .returning(services.c.version)
            )
            if result.scalar_one_or_none() is None:
                old = (
                    (
                        await session.execute(
                            sa.select(services).where(
                                services.c.tenant_id == config.tenant_id,
                                services.c.service_config_id == config.service_config_id,
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                # A deployment must explicitly migrate a profile revision; no silent retargeting.
                if old["config"] != config.model_dump(mode="json"):
                    raise McpFailure("mcp_configuration_drift")

    async def bind_capability(self, binding: ToolBinding) -> None:
        async with self.sessions.begin() as session:
            tenant = (
                await session.execute(
                    sa.select(services.c.tenant_id).where(
                        services.c.service_config_id == binding.service_config_id,
                    )
                )
            ).scalar_one()
            created = (
                await session.execute(
                    insert(mappings)
                    .values(
                        capability_id=binding.capability_id,
                        capability_version=binding.capability_version,
                        tenant_id=tenant,
                        service_config_id=binding.service_config_id,
                        binding=binding.model_dump(mode="json"),
                    )
                    .on_conflict_do_nothing()
                    .returning(mappings.c.capability_id)
                )
            ).scalar_one_or_none()
            if created is None:
                stored = (
                    await session.execute(
                        sa.select(mappings.c.binding).where(
                            mappings.c.capability_id == binding.capability_id,
                            mappings.c.capability_version == binding.capability_version,
                        )
                    )
                ).scalar_one()
                if stored != binding.model_dump(mode="json"):
                    raise McpFailure("mcp_mapping_immutable")

    async def mapping(self, capability_id: str, version: str) -> ToolBinding | None:
        async with self.sessions() as session:
            row = (
                await session.execute(
                    sa.select(mappings.c.binding).where(
                        mappings.c.capability_id == capability_id,
                        mappings.c.capability_version == version,
                    )
                )
            ).scalar_one_or_none()
        return ToolBinding.model_validate(row) if row else None

    async def begin_registration(self, config: ServiceConfig) -> tuple[str, str | None, bool]:
        scope = digest(
            [
                config.deployment_id,
                config.tenant_id,
                config.service_config_id,
                config.issuer,
                config.resource,
                config.callback_config_version,
            ]
        )
        async with self.sessions.begin() as session:
            registration_id = uuid4().hex
            created = (
                await session.execute(
                    insert(registrations)
                    .values(
                        registration_id=registration_id,
                        tenant_id=config.tenant_id,
                        service_config_id=config.service_config_id,
                        scope_digest=scope,
                        state="REGISTERING",
                    )
                    .on_conflict_do_nothing()
                    .returning(registrations.c.registration_id)
                )
            ).scalar_one_or_none()
            if created:
                return registration_id, None, True
            row = (
                (
                    await session.execute(
                        sa.select(registrations).where(
                            registrations.c.scope_digest == scope,
                        )
                    )
                )
                .mappings()
                .one()
            )
            if row["state"] != "REGISTERED":
                raise McpFailure("mcp_registration_requires_reconciliation")
            return str(row["registration_id"]), str(row["client_id"]), False

    async def complete_registration(self, registration_id: str, client_id: str) -> None:
        async with self.sessions.begin() as session:
            updated = (
                await session.execute(
                    sa.update(registrations)
                    .where(
                        registrations.c.registration_id == registration_id,
                        registrations.c.state == "REGISTERING",
                    )
                    .values(
                        client_id=client_id or None, state="REGISTERED" if client_id else "UNKNOWN"
                    )
                    .returning(registrations.c.registration_id)
                )
            ).scalar_one_or_none()
            if updated is None:
                raise McpFailure("mcp_registration_conflict")

    async def begin_authorization(
        self,
        config: ServiceConfig,
        *,
        user_id: str,
        fingerprint: str,
        registration_id: str,
        client_id: str,
        state_digest: str,
        verifier: str,
        expires_at: datetime,
    ) -> AuthorizationTransaction:
        async with self.sessions.begin() as session:
            row = (
                (
                    await session.execute(
                        insert(connections)
                        .values(
                            connection_id=uuid4().hex,
                            tenant_id=config.tenant_id,
                            user_id=user_id,
                            service_config_id=config.service_config_id,
                            service_config_version=config.service_config_version,
                            registration_id=registration_id,
                            binding_epoch=1,
                            grant_epoch=0,
                            login_session_fingerprint=fingerprint,
                            state="AUTHORIZING",
                        )
                        .on_conflict_do_update(
                            index_elements=["tenant_id", "user_id", "service_config_id"],
                            set_={
                                "binding_epoch": connections.c.binding_epoch + 1,
                                "login_session_fingerprint": fingerprint,
                                "state": "AUTHORIZING",
                                "identity_policy_version": None,
                                "identity_evidence": None,
                                "expires_at": None,
                            },
                            where=sa.and_(
                                connections.c.registration_id == registration_id,
                                connections.c.service_config_version
                                == config.service_config_version,
                            ),
                        )
                        .returning(connections)
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise McpFailure("mcp_connection_configuration_conflict")
            owner = Ownership(
                tenant_id=config.tenant_id,
                user_id=user_id,
                service_config_id=config.service_config_id,
                connection_id=row["connection_id"],
            )
            transaction = AuthorizationTransaction(
                state_digest=state_digest,
                owner=owner,
                registration_id=registration_id,
                client_id=client_id,
                binding_epoch=row["binding_epoch"],
                service_config_version=config.service_config_version,
                login_session_fingerprint=fingerprint,
                issuer=config.issuer,
                resource=config.resource,
                callback_uri=config.callback_uri,
                expires_at=expires_at,
                verifier=SecretStr(verifier),
            )
            payload = transaction.model_dump(mode="json")
            payload["verifier"] = verifier
            await session.execute(
                sa.insert(transactions).values(
                    **owner.model_dump(),
                    state_digest=state_digest,
                    binding_epoch=row["binding_epoch"],
                    login_session_fingerprint=fingerprint,
                    expires_at=expires_at,
                    encrypted_payload=self.encrypt(
                        payload, {"state_digest": state_digest, **owner.model_dump()}
                    ),
                )
            )
            return transaction

    async def authorization_service(
        self,
        state_digest: str,
        *,
        tenant_id: str,
        user_id: str,
        fingerprint: str,
    ) -> str:
        async with self.sessions() as session:
            value = (
                await session.execute(
                    sa.select(transactions.c.service_config_id).where(
                        transactions.c.state_digest == state_digest,
                        transactions.c.tenant_id == tenant_id,
                        transactions.c.user_id == user_id,
                        transactions.c.login_session_fingerprint == fingerprint,
                    )
                )
            ).scalar_one_or_none()
        if value is None:
            raise McpFailure("mcp_authorization_invalid")
        return str(value)

    async def claim_authorization(
        self,
        state_digest: str,
        *,
        tenant_id: str,
        user_id: str,
        fingerprint: str,
        issuer: str,
        resource: str,
        callback_uri: str,
        now: datetime,
    ) -> AuthorizationTransaction:
        async with self.sessions.begin() as session:
            row = (
                (
                    await session.execute(
                        sa.select(transactions)
                        .where(
                            transactions.c.state_digest == state_digest,
                            transactions.c.tenant_id == tenant_id,
                            transactions.c.user_id == user_id,
                            transactions.c.login_session_fingerprint == fingerprint,
                            transactions.c.consumed.is_(False),
                            transactions.c.expires_at > now,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise McpFailure("mcp_authorization_invalid")
            owner = {key: row[key] for key in Ownership.model_fields}
            txn = AuthorizationTransaction.model_validate(
                self.decrypt(
                    bytes(row["encrypted_payload"]),
                    {"state_digest": state_digest, **owner},
                )
            )
            conn = (
                (
                    await session.execute(
                        sa.select(connections)
                        .where(
                            connections.c.connection_id == txn.owner.connection_id,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one()
            )
            if (
                (txn.issuer, txn.resource, txn.callback_uri) != (issuer, resource, callback_uri)
                or conn["binding_epoch"] != txn.binding_epoch
                or conn["state"] != "AUTHORIZING"
                or conn["login_session_fingerprint"] != fingerprint
            ):
                raise McpFailure("mcp_authorization_invalid")
            await session.execute(
                sa.update(transactions)
                .where(
                    transactions.c.state_digest == state_digest,
                )
                .values(consumed=True, encrypted_payload=b"")
            )
            return txn

    async def store_grant(
        self,
        transaction: AuthorizationTransaction,
        *,
        token: str,
        expires_at: datetime,
        identity_policy_version: str | None,
        identity_evidence: str | None,
    ) -> None:
        txn = transaction
        async with self.sessions.begin() as session:
            row = (
                (
                    await session.execute(
                        sa.update(connections)
                        .where(
                            connections.c.connection_id == txn.owner.connection_id,
                            connections.c.tenant_id == txn.owner.tenant_id,
                            connections.c.user_id == txn.owner.user_id,
                            connections.c.service_config_id == txn.owner.service_config_id,
                            connections.c.binding_epoch == txn.binding_epoch,
                            connections.c.login_session_fingerprint
                            == txn.login_session_fingerprint,
                            connections.c.state == "AUTHORIZING",
                        )
                        .values(
                            grant_epoch=connections.c.grant_epoch + 1,
                            expires_at=expires_at,
                            identity_policy_version=identity_policy_version,
                            identity_evidence=identity_evidence,
                            state="ACTIVE"
                            if identity_policy_version and identity_evidence
                            else "PENDING_IDENTITY",
                        )
                        .returning(connections)
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise McpFailure("mcp_authorization_stale")
            aad = {
                **txn.owner.model_dump(),
                "binding_epoch": txn.binding_epoch,
                "grant_epoch": row["grant_epoch"],
                "fingerprint": txn.login_session_fingerprint,
            }
            await session.execute(
                sa.insert(grants).values(
                    **txn.owner.model_dump(),
                    grant_epoch=row["grant_epoch"],
                    binding_epoch=txn.binding_epoch,
                    login_session_fingerprint=txn.login_session_fingerprint,
                    expires_at=expires_at,
                    encrypted_payload=self.encrypt({"token": token}, aad),
                )
            )

    async def connection(
        self,
        *,
        tenant_id: str,
        user_id: str,
        service_config_id: str,
    ) -> Connection | None:
        async with self.sessions() as session:
            row = (
                (
                    await session.execute(
                        sa.select(connections).where(
                            connections.c.tenant_id == tenant_id,
                            connections.c.user_id == user_id,
                            connections.c.service_config_id == service_config_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        return Connection.model_validate(dict(row)) if row else None

    async def list_connections(self, *, tenant_id: str, user_id: str) -> list[Connection]:
        async with self.sessions() as session:
            rows = (
                (
                    await session.execute(
                        sa.select(connections).where(
                            connections.c.tenant_id == tenant_id,
                            connections.c.user_id == user_id,
                        )
                    )
                )
                .mappings()
                .all()
            )
        return [Connection.model_validate(dict(row)) for row in rows]

    async def disconnect(self, *, tenant_id: str, user_id: str, connection_id: str) -> bool:
        async with self.sessions.begin() as session:
            row = (
                await session.execute(
                    sa.update(connections)
                    .where(
                        connections.c.tenant_id == tenant_id,
                        connections.c.user_id == user_id,
                        connections.c.connection_id == connection_id,
                    )
                    .values(
                        state="DISCONNECTED",
                        binding_epoch=connections.c.binding_epoch + 1,
                        identity_evidence=None,
                        identity_policy_version=None,
                    )
                    .returning(connections.c.connection_id)
                )
            ).scalar_one_or_none()
        return row is not None

    async def reject(self, context: McpAuthorizationContext) -> None:
        """A late response cannot revoke a newer grant or another service's session."""
        async with self.sessions.begin() as session:
            changed = (
                await session.execute(
                    sa.update(connections)
                    .where(
                        connections.c.tenant_id == context.tenant_id,
                        connections.c.user_id == context.user_id,
                        connections.c.service_config_id == context.service_config_id,
                        connections.c.connection_id == context.connection_id,
                        connections.c.service_config_version == context.service_config_version,
                        connections.c.registration_id == context.registration_id,
                        connections.c.grant_epoch == context.grant_epoch,
                        connections.c.binding_epoch == context.binding_epoch,
                        connections.c.login_session_fingerprint
                        == context.login_session_fingerprint,
                        connections.c.state == "ACTIVE",
                    )
                    .values(
                        state="DISCONNECTED",
                        binding_epoch=connections.c.binding_epoch + 1,
                        identity_evidence=None,
                        identity_policy_version=None,
                    )
                    .returning(connections.c.connection_id)
                )
            ).scalar_one_or_none()
            if changed is not None:
                await session.execute(
                    sa.update(grants)
                    .where(
                        grants.c.connection_id == context.connection_id,
                        grants.c.tenant_id == context.tenant_id,
                        grants.c.user_id == context.user_id,
                        grants.c.service_config_id == context.service_config_id,
                        grants.c.grant_epoch == context.grant_epoch,
                    )
                    .values(expires_at=datetime.now(UTC))
                )

    async def resolve(self, context: McpAuthorizationContext) -> str:
        try:
            if await self._revocations.is_revoked(bytes.fromhex(context.login_session_fingerprint)):
                raise McpFailure("mcp_authorization_invalid")
            async with self.sessions() as session:
                row = (
                    await session.execute(
                        sa.select(grants.c.encrypted_payload)
                        .join(
                            connections,
                            sa.and_(
                                connections.c.connection_id == grants.c.connection_id,
                                connections.c.grant_epoch == grants.c.grant_epoch,
                            ),
                        )
                        .where(
                            connections.c.tenant_id == context.tenant_id,
                            connections.c.user_id == context.user_id,
                            connections.c.service_config_id == context.service_config_id,
                            connections.c.connection_id == context.connection_id,
                            connections.c.service_config_version == context.service_config_version,
                            connections.c.registration_id == context.registration_id,
                            connections.c.binding_epoch == context.binding_epoch,
                            connections.c.grant_epoch == context.grant_epoch,
                            connections.c.login_session_fingerprint
                            == context.login_session_fingerprint,
                            connections.c.state == "ACTIVE",
                            grants.c.expires_at > datetime.now(UTC),
                        )
                    )
                ).scalar_one_or_none()
            if row is None:
                raise McpFailure("mcp_authorization_invalid")
            aad = {key: getattr(context, key) for key in Ownership.model_fields}
            aad.update(
                binding_epoch=context.binding_epoch,
                grant_epoch=context.grant_epoch,
                fingerprint=context.login_session_fingerprint,
            )
            value = self.decrypt(bytes(row), aad).get("token")
            if not isinstance(value, str) or not value:
                raise McpFailure("mcp_secret_unavailable")
            return value
        except Exception:
            raise McpFailure("mcp_authorization_invalid") from None
