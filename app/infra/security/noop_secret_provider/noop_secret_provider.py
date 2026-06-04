"""Noop/Mock SecretProvider for Phase 0. Never returns plaintext credentials."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from app.ports.secret_provider import SecretInjectionResult, SecretResolutionResult


class NoopSecretProvider:
    """Phase 0 noop implementation of SecretProviderPort.

    Both methods return only safe, redacted/mock values via model_dump().
    Never returns plaintext secrets, passwords, tokens, or session IDs.
    """

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
        # execution_context is intentionally ignored -- never copied into output.
        return SecretInjectionResult(credential_ref=credential_ref).model_dump()


_SENSITIVE_PATTERN = re.compile(
    r"(?i)(bearer|session.?id|access.?token|refresh.?token|password|passwd|api.?key|authorization|cookie)",
)


def make_credential_sanitizer() -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Create a SanitizerHookFn-compatible hook that redacts token-like values."""

    def sanitizer(payload: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in payload.items():
            if _SENSITIVE_PATTERN.search(key):
                result[key] = "<redacted>"
            elif isinstance(value, str) and _SENSITIVE_PATTERN.search(value):
                result[key] = "<redacted>"
            else:
                result[key] = value
        return result

    return sanitizer
