"""Trace attribute sanitizer for Phase 0 no-op trace writes."""

from __future__ import annotations

import re
from typing import Any

REDACTED_TRACE_VALUE = "[REDACTED]"

_CREDENTIAL_KEY_PATTERN = re.compile(
    r"(session[-_]?id|access[_-]?token|refresh[_-]?token|set-cookie|cookie|bearer|authorization)",
    re.IGNORECASE,
)
_CREDENTIAL_VALUE_PATTERNS = (
    re.compile(r"bearer\s+\S+", re.IGNORECASE),
    re.compile(r"session[-_]?id\s*=\s*\S+", re.IGNORECASE),
    re.compile(r"access[_-]?token\s*=\s*\S+", re.IGNORECASE),
    re.compile(r"refresh[_-]?token\s*=\s*\S+", re.IGNORECASE),
    re.compile(r"cookie\s*=\s*\S+", re.IGNORECASE),
    re.compile(r"set-cookie\s*=\s*\S+", re.IGNORECASE),
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
    return bool(_CREDENTIAL_KEY_PATTERN.search(key))


def _is_credential_value(value: str) -> bool:
    return any(pattern.search(value) for pattern in _CREDENTIAL_VALUE_PATTERNS)


# app.infra.security.make_credential_sanitizer() emits "<redacted>"; this task
# requires "[REDACTED]", so that sanitizer is not reusable as-is here.
