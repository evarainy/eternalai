"""Secret provider interface contract."""

from __future__ import annotations

from typing import Any, Literal, Protocol, TypeAlias

from pydantic import BaseModel, Field

from app.ports.auth import OASessionCredential

RedactedSecretPlaceholder: TypeAlias = Literal["<redacted>"]
MockSecretInjected: TypeAlias = Literal[True]


class SecretResolutionResult(BaseModel):
    model_config = {"extra": "forbid"}

    credential_ref: str
    redacted_placeholder: RedactedSecretPlaceholder = Field(default="<redacted>")


class SecretInjectionResult(BaseModel):
    model_config = {"extra": "forbid"}

    credential_ref: str
    mock_secret_injected: MockSecretInjected = Field(default=True)


class SecretProviderError(RuntimeError):
    """Safe base error for OA credential resolution failures."""


class InvalidCredentialReferenceError(SecretProviderError):
    """The supplied reference is not a server-issued OA Session reference."""

    def __init__(self) -> None:
        super().__init__("OA session credential reference is invalid")


class CredentialNotFoundError(SecretProviderError):
    """No OA Session credential exists for the trusted reference."""

    def __init__(self) -> None:
        super().__init__("OA session credential is unavailable")


class CredentialExpiredError(SecretProviderError):
    """The referenced OA Session credential has passed its local TTL."""

    def __init__(self) -> None:
        super().__init__("OA session credential has expired")


class CredentialStorageError(SecretProviderError):
    """The encrypted OA Session credential cannot be safely read."""

    def __init__(self) -> None:
        super().__init__("OA session credential cannot be resolved")


class SecretProviderPort(Protocol):
    async def resolve_secret_ref(
        self,
        credential_ref: str,
        task_id: str,
        capability_id: str,
    ) -> dict[str, Any]: ...

    async def inject_execution_secret(
        self,
        execution_context: dict[str, Any],
        credential_ref: str,
    ) -> dict[str, Any]: ...

    async def resolve_oa_session(
        self,
        credential_ref: str,
    ) -> OASessionCredential: ...


__all__ = (
    "CredentialExpiredError",
    "CredentialNotFoundError",
    "CredentialStorageError",
    "InvalidCredentialReferenceError",
    "MockSecretInjected",
    "RedactedSecretPlaceholder",
    "SecretInjectionResult",
    "SecretProviderError",
    "SecretProviderPort",
    "SecretResolutionResult",
)
