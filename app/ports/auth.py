"""Authentication boundary contracts."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from typing import AsyncContextManager, Literal, Protocol

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


@dataclass(frozen=True)
class AuthenticatedSessionContext:
    principal: Principal
    fingerprint: bytes = field(repr=False)
    expires_at: datetime


# Request tasks inherit this only from the verified HTTP authentication dependency.
# Never populated from Runtime arguments or a client-provided principal object.
authenticated_session: ContextVar[AuthenticatedSessionContext | None] = ContextVar(
    "authenticated_session",
    default=None,
)


class SessionRevocationStoreError(RuntimeError):
    """Fixed, sanitized failure of persistent session revocation."""


class SessionRevocationStorePort(Protocol):
    async def is_revoked(self, fingerprint: bytes) -> bool: ...

    async def revoke(self, fingerprint: bytes, *, expires_at: datetime) -> None: ...


class SessionBindingError(RuntimeError):
    """Raised when a conversation session is not bound to the Principal."""


class CredentialStoreError(RuntimeError):
    """Uniform fail-closed error for unreadable encrypted credential storage."""


@dataclass(frozen=True, slots=True)
class CredentialSnapshot:
    """Read-only optimistic snapshot; absence never authorizes an overwrite."""

    tenant_id: str
    ai_user_id: str
    target_system: str
    binding_id: str
    binding_revision: int
    credential_write_revision: int
    refresh_epoch: int
    absent: bool = False

    def __post_init__(self) -> None:
        if (
            not all(
                isinstance(value, str) and value.strip()
                for value in (self.tenant_id, self.ai_user_id, self.target_system, self.binding_id)
            )
            or type(self.absent) is not bool
            or any(
                type(value) is not int or not 0 <= value <= 9007199254740991
                for value in (
                    self.binding_revision,
                    self.credential_write_revision,
                    self.refresh_epoch,
                )
            )
            or self.binding_revision == 0
        ):
            raise ValueError("credential_snapshot_invalid")


@dataclass(frozen=True, slots=True)
class CredentialWriteStamp:
    """One claimed write, captured before IO and checked against database time."""

    snapshot: CredentialSnapshot
    operation_id: str
    deadline: datetime

    def __post_init__(self) -> None:
        if (
            not isinstance(self.snapshot, CredentialSnapshot)
            or not isinstance(self.operation_id, str)
            or not self.operation_id
            or not isinstance(self.deadline, datetime)
            or self.deadline.tzinfo is None
            or self.deadline.utcoffset() is None
        ):
            raise ValueError("credential_write_stamp_invalid")


class StaleCredentialWrite(CredentialStoreError):
    """A newer binding, writer or expired operation fenced this result."""

    code = "credential_write_stale"


@dataclass(frozen=True, slots=True)
class CredentialAuthenticationResult:
    principal: Principal
    write_stamp: CredentialWriteStamp | None


class AuthenticationPort(Protocol):
    async def refresh_credential(
        self,
        credential: LoginCredential,
        *,
        expected_subject: tuple[str, str],
        expected_write: CredentialWriteStamp,
    ) -> CredentialAuthenticationResult: ...

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
    def writer_guard(self) -> AsyncContextManager[None]: ...

    def poll_lock(
        self, ai_user_id: str, target_system: str, *, tenant_id: str
    ) -> AsyncContextManager[bool]: ...

    async def snapshot(
        self,
        ai_user_id: str,
        target_system: str,
        *,
        tenant_id: str,
    ) -> CredentialSnapshot: ...

    async def claim_write(self, snapshot: CredentialSnapshot) -> CredentialWriteStamp: ...

    async def store(
        self,
        ai_user_id: str,
        target_system: str,
        credential: OASessionCredential,
        *,
        tenant_id: str,
        reactivate_revoked_session: bool = True,
        expected_write: CredentialWriteStamp,
    ) -> CredentialWriteStamp: ...

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
