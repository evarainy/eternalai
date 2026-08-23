"""Password binding and background credential polling contracts."""

from __future__ import annotations

from datetime import datetime
from typing import AsyncContextManager, Literal, Protocol, TypeAlias

from pydantic import BaseModel, ConfigDict, SecretStr

from app.ports.auth import LoginCredential, Principal

CredentialTargetSystem: TypeAlias = Literal["oa", "u8", "hikvision_ivms"]
CredentialPollStatus: TypeAlias = Literal[
    "unbound",
    "active",
    "retrying",
    "invalid",
    "captcha_required",
]
CredentialTerminalFailure: TypeAlias = Literal["invalid", "captcha_required"]
CredentialAcquisitionFailureCode: TypeAlias = Literal[
    "credentials_rejected",
    "captcha_required",
    "identity_mismatch",
    "network_unreachable",
    "timeout",
    "upstream_5xx",
    "invalid_response",
    "local_failure",
    "unsupported_target",
]
CredentialCountedFailureCode: TypeAlias = Literal[
    "network_unreachable",
    "timeout",
    "upstream_5xx",
    "invalid_response",
]


class PasswordBindingCredential(BaseModel):
    """One encrypted-at-rest user credential; repr and dumps stay masked."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    login_id: SecretStr
    password: SecretStr


class CredentialBindingView(BaseModel):
    """Value-free status safe for authenticated API responses."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    target_system: CredentialTargetSystem
    poll_status: CredentialPollStatus
    poll_failure_count: int
    updated_at: datetime | None
    bound: bool


class CredentialPollCandidate(BaseModel):
    """Value-free due-candidate metadata; never carries a password."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ai_user_id: str
    target_system: CredentialTargetSystem
    poll_failure_count: int
    updated_at: datetime


class CredentialAcquisitionError(RuntimeError):
    """Fixed-code failure without upstream text or credential values."""

    def __init__(self, code: CredentialAcquisitionFailureCode) -> None:
        super().__init__("credential acquisition failed")
        self.code = code


class CredentialBindingStorePort(Protocol):
    async def bind_password(
        self,
        ai_user_id: str,
        target_system: CredentialTargetSystem,
        credential: PasswordBindingCredential,
    ) -> CredentialBindingView: ...

    async def get_password_binding(
        self,
        ai_user_id: str,
        target_system: CredentialTargetSystem,
    ) -> CredentialBindingView: ...

    async def unbind_password(
        self,
        ai_user_id: str,
        target_system: CredentialTargetSystem,
    ) -> CredentialBindingView: ...


class CredentialBindingVerifierPort(Protocol):
    async def verify_for_binding(self, credential: LoginCredential) -> Principal: ...


class CredentialPollingStorePort(Protocol):
    async def list_poll_candidates(self) -> list[CredentialPollCandidate]: ...

    async def refresh_poll_candidate(
        self,
        ai_user_id: str,
        target_system: CredentialTargetSystem,
    ) -> CredentialPollCandidate | None: ...

    def poll_lock(
        self,
        ai_user_id: str,
        target_system: CredentialTargetSystem,
    ) -> AsyncContextManager[bool]: ...

    async def mark_poll_succeeded(
        self,
        ai_user_id: str,
        target_system: CredentialTargetSystem,
    ) -> None: ...

    async def mark_non_authentication_failure(
        self,
        ai_user_id: str,
        target_system: CredentialTargetSystem,
    ) -> None: ...

    async def mark_non_counted_failure(
        self,
        ai_user_id: str,
        target_system: CredentialTargetSystem,
    ) -> None: ...

    async def mark_terminal_authentication_failure(
        self,
        ai_user_id: str,
        target_system: CredentialTargetSystem,
        failure: CredentialTerminalFailure,
    ) -> None: ...


class PasswordBindingReaderPort(Protocol):
    async def load_password_for_poll(
        self,
        ai_user_id: str,
        target_system: CredentialTargetSystem,
    ) -> PasswordBindingCredential: ...


class BackgroundCredentialAcquirerPort(Protocol):
    async def acquire(self, candidate: CredentialPollCandidate) -> Principal: ...


class BackgroundWorkObjectSyncPort(Protocol):
    async def sync_for_background(self, principal: Principal) -> object: ...


class BackgroundWorkObjectSyncError(RuntimeError):
    """Classified value-free sync failure for retry accounting."""

    def __init__(
        self,
        *,
        authentication_denied: bool,
        failure_code: CredentialCountedFailureCode | None = None,
    ) -> None:
        super().__init__("background Work Object synchronization failed")
        self.authentication_denied = authentication_denied
        self.failure_code = failure_code


__all__ = (
    "BackgroundCredentialAcquirerPort",
    "BackgroundWorkObjectSyncPort",
    "BackgroundWorkObjectSyncError",
    "CredentialAcquisitionError",
    "CredentialAcquisitionFailureCode",
    "CredentialBindingStorePort",
    "CredentialBindingVerifierPort",
    "CredentialBindingView",
    "CredentialCountedFailureCode",
    "CredentialPollingStorePort",
    "CredentialPollCandidate",
    "CredentialPollStatus",
    "CredentialTargetSystem",
    "CredentialTerminalFailure",
    "PasswordBindingCredential",
    "PasswordBindingReaderPort",
)
