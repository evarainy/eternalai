"""Authentication boundary contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class LoginCredential(BaseModel):
    """One-shot OA login credential accepted only by the authentication adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    loginid: SecretStr
    userpassword: SecretStr


class PrincipalOrgContext(BaseModel):
    """Locally controlled organization context carried by an authenticated principal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str = Field(min_length=1)
    org_id: str | None = None
    department_id: str | None = None
    # Stable directory join key only; mutable authorization attributes stay in the mirror.
    directory_user_id: str | None = Field(default=None, repr=False)


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


@dataclass(frozen=True)
class VerifiedSessionToken:
    """Trusted metadata; never carries the original bearer credential."""

    principal: Principal = field(repr=False)
    fingerprint: bytes = field(repr=False)
    expires_at: datetime
    version: Literal[1, 2]


class SessionRevocationStoreError(RuntimeError):
    """Fixed, sanitized failure of persistent session revocation."""


class SessionRevocationStorePort(Protocol):
    async def is_revoked(self, fingerprint: bytes) -> bool: ...

    async def revoke(self, fingerprint: bytes, *, expires_at: datetime) -> None: ...


class SessionBindingError(RuntimeError):
    """Raised when a conversation session is not bound to the Principal."""


class CredentialStoreError(RuntimeError):
    """Uniform fail-closed error for unreadable encrypted credential storage."""


class AuthenticationPort(Protocol):
    async def authenticate(
        self,
        credential: LoginCredential,
        *,
        reactivate_revoked_session: bool = True,
        expected_subject: tuple[str, str] | None = None,
    ) -> Principal: ...


class SessionTokenPort(Protocol):
    def issue(self, principal: Principal) -> str: ...

    def verify(self, token: str) -> Principal: ...

    def inspect(self, token: str) -> VerifiedSessionToken: ...


class CredentialStorePort(Protocol):
    async def store(
        self,
        ai_user_id: str,
        target_system: str,
        credential: OASessionCredential,
        *,
        tenant_id: str,
        reactivate_revoked_session: bool = True,
    ) -> None: ...

    async def load(
        self, ai_user_id: str, target_system: str, *, tenant_id: str
    ) -> OASessionCredential | None: ...


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
    "SessionRevocationStoreError",
    "SessionRevocationStorePort",
    "VerifiedSessionToken",
)
