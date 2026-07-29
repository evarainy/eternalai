"""Authentication boundary contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, SecretStr


class LoginCredential(BaseModel):
    """One-shot OA login credential accepted only by the authentication adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    loginid: SecretStr
    userpassword: SecretStr


class PrincipalOrgContext(BaseModel):
    """Locally controlled organization context carried by an authenticated principal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str = "default"
    org_id: str | None = None
    department_id: str | None = None


class Principal(BaseModel):
    """Server-issued authenticated identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ai_user_id: str
    display_name: str
    roles: tuple[str, ...]
    org_ctx: PrincipalOrgContext


class OASessionCredential(BaseModel):
    """Credential-grade OA session data that must be encrypted at rest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    oa_user_id: SecretStr
    cookies: dict[str, SecretStr]
    expires_at: datetime


class AuthenticationError(RuntimeError):
    """Generic fail-closed authentication failure with no upstream details."""


class SessionTokenError(AuthenticationError):
    """Raised when an EternalAI session token cannot be trusted."""


class SessionBindingError(RuntimeError):
    """Raised when a conversation session is not bound to the Principal."""


class CredentialStoreError(RuntimeError):
    """Uniform fail-closed error for unreadable encrypted credential storage."""


class AuthenticationPort(Protocol):
    async def authenticate(self, credential: LoginCredential) -> Principal: ...


class SessionTokenPort(Protocol):
    def issue(self, principal: Principal) -> str: ...

    def verify(self, token: str) -> Principal: ...


class CredentialStorePort(Protocol):
    async def store(
        self,
        ai_user_id: str,
        credential: OASessionCredential,
    ) -> None: ...

    async def load(self, ai_user_id: str) -> OASessionCredential | None: ...


__all__ = (
    "AuthenticationError",
    "AuthenticationPort",
    "CredentialStoreError",
    "CredentialStorePort",
    "LoginCredential",
    "OASessionCredential",
    "Principal",
    "PrincipalOrgContext",
    "SessionBindingError",
    "SessionTokenError",
    "SessionTokenPort",
)
