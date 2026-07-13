"""Trace attribute sanitizer for Phase 0 no-op trace writes."""

from __future__ import annotations

import re
from typing import Any

REDACTED_TRACE_VALUE = "[REDACTED]"

_CREDENTIAL_KEYS = frozenset(
    {
        "authorization",
        "bearer",
        "cookie",
        "set_cookie",
        "setcookie",
        "session",
        "session_id",
        "sessionid",
        "access_token",
        "accesstoken",
        "refresh_token",
        "refreshtoken",
        "password",
        "passwd",
        "api_key",
        "apikey",
        "secret",
        "client_secret",
        "clientsecret",
        "private_key",
        "privatekey",
    }
)
_CREDENTIAL_VALUE_PATTERNS = (
    re.compile(r"bearer\s+\S+", re.IGNORECASE),
    re.compile(
        r"(?:authorization|session(?:[\s_-]?id)?|access[\s_-]?token|"
        r"refresh[\s_-]?token|set[\s_-]?cookie|cookie|password|passwd|"
        r"api[\s_-]?key|secret|client[\s_-]?secret|private[\s_-]?key)"
        r"\s*[:=]\s*\S+",
        re.IGNORECASE,
    ),
)


def redact_trace_attributes(attributes: dict[str, Any]) -> dict[str, Any]:
    """Return a sanitized copy of trace attributes while preserving keys."""

    return {
        key: REDACTED_TRACE_VALUE if _is_credential_key(key) else _redact_value(value)
        for key, value in attributes.items()
    }


def _redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return redact_trace_attributes(value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, str) and _is_credential_value(value):
        return REDACTED_TRACE_VALUE
    return value


def _is_credential_key(key: str) -> bool:
    normalized = re.sub(r"[\s_-]+", "_", key.strip().lower())
    return normalized in _CREDENTIAL_KEYS


def _is_credential_value(value: str) -> bool:
    return any(pattern.search(value) for pattern in _CREDENTIAL_VALUE_PATTERNS)


# app.infra.security.make_credential_sanitizer() emits "<redacted>"; this task
# requires "[REDACTED]", so that sanitizer is not reusable as-is here.
