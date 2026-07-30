"""CredentialStore-backed resolution for server-issued OA Session references."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from app.ports.auth import CredentialStorePort, OASessionCredential
from app.ports.secret_provider import (
    CredentialExpiredError,
    CredentialNotFoundError,
    CredentialStorageError,
    InvalidCredentialReferenceError,
    SecretInjectionResult,
    SecretResolutionResult,
)

_OA_SESSION_REF_RE = re.compile(
    r"^oa-session-v1:(?P<ai_user_id>usr_v1_[A-Za-z0-9_-]{43})$"
)


class CredentialStoreSecretProvider:
    """Resolve one trusted reference late and keep plaintext out of shared context."""

    def __init__(
        self,
        *,
        credential_store: CredentialStorePort,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._credential_store = credential_store
        self._now = _utc_now if now is None else now

    def __repr__(self) -> str:
        return "CredentialStoreSecretProvider()"

    async def resolve_secret_ref(
        self,
        credential_ref: str,
        task_id: str,
        capability_id: str,
    ) -> dict[str, Any]:
        return SecretResolutionResult(credential_ref=credential_ref).model_dump()

    async def inject_execution_secret(
        self,
        execution_context: dict[str, Any],
        credential_ref: str,
    ) -> dict[str, Any]:
        return SecretInjectionResult(credential_ref=credential_ref).model_dump()

    async def resolve_oa_session(
        self,
        credential_ref: str,
    ) -> OASessionCredential:
        if not isinstance(credential_ref, str):
            raise InvalidCredentialReferenceError
        match = _OA_SESSION_REF_RE.fullmatch(credential_ref)
        if match is None:
            raise InvalidCredentialReferenceError
        ai_user_id = match.group("ai_user_id")

        credential: OASessionCredential | None = None
        load_failed = False
        try:
            credential = await self._credential_store.load(ai_user_id)
        except Exception:
            load_failed = True

        if load_failed:
            raise CredentialStorageError
        if credential is None:
            raise CredentialNotFoundError

        now: datetime | None = None
        ttl_check_failed = False
        try:
            now = self._now()
            if (
                now.tzinfo is None
                or now.utcoffset() is None
                or credential.expires_at.tzinfo is None
                or credential.expires_at.utcoffset() is None
            ):
                raise TypeError
        except Exception:
            ttl_check_failed = True

        if ttl_check_failed or now is None:
            credential = None
            raise CredentialStorageError
        if credential.expires_at <= now:
            credential = None
            raise CredentialExpiredError
        return credential


def _utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = ("CredentialStoreSecretProvider",)
