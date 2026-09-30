"""Persistent OAuth records; storage implementations own atomicity and encryption."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.mcp.models import Ownership, ServiceConfig, ToolBinding
from app.ports.mcp import McpAuthorizationContext


class Connection(Ownership):
    registration_id: str
    service_config_version: int
    binding_epoch: int
    grant_epoch: int
    login_session_fingerprint: str = Field(repr=False)
    state: str
    identity_policy_version: str | None = None
    identity_evidence: str | None = Field(default=None, repr=False)
    expires_at: datetime | None = None


class AuthorizationTransaction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    state_digest: str
    owner: Ownership
    registration_id: str
    client_id: str
    binding_epoch: int
    service_config_version: int
    login_session_fingerprint: str = Field(repr=False)
    issuer: str
    resource: str
    callback_uri: str
    expires_at: datetime
    verifier: SecretStr = Field(repr=False)


class McpStorePort(Protocol):
    async def configure(self, config: ServiceConfig) -> None: ...
    async def bind_capability(self, binding: ToolBinding) -> None: ...
    async def mapping(self, capability_id: str, version: str) -> ToolBinding | None: ...
    async def begin_registration(self, config: ServiceConfig) -> tuple[str, str | None, bool]: ...
    async def complete_registration(self, registration_id: str, client_id: str) -> None: ...
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
    ) -> AuthorizationTransaction: ...
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
    ) -> AuthorizationTransaction: ...
    async def authorization_service(
        self,
        state_digest: str,
        *,
        tenant_id: str,
        user_id: str,
        fingerprint: str,
    ) -> str: ...
    async def store_grant(
        self,
        transaction: AuthorizationTransaction,
        *,
        token: str,
        expires_at: datetime,
        identity_policy_version: str | None,
        identity_evidence: str | None,
    ) -> None: ...
    async def connection(
        self,
        *,
        tenant_id: str,
        user_id: str,
        service_config_id: str,
    ) -> Connection | None: ...
    async def list_connections(self, *, tenant_id: str, user_id: str) -> list[Connection]: ...
    async def disconnect(self, *, tenant_id: str, user_id: str, connection_id: str) -> bool: ...
    async def resolve(self, context: McpAuthorizationContext) -> str: ...
    async def reject(self, context: McpAuthorizationContext) -> None: ...


class IdentityAssertionPort(Protocol):
    version: str
    approved: bool

    async def verify(
        self,
        *,
        tenant_id: str,
        user_id: str,
        service_config_id: str,
        token_metadata: dict[str, Any],
    ) -> str | None:
        """Return approved same-subject evidence, or None to keep connection pending."""
        ...
