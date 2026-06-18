"""Secret provider interface contract."""

from __future__ import annotations

from typing import Any, Literal, Protocol, TypeAlias

from pydantic import BaseModel, Field

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
