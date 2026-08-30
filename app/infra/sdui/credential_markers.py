"""Shared credential-marker detection for ResponseEnvelope safety boundaries."""

from __future__ import annotations

import re

_CREDENTIAL_MARKER = re.compile(
    r"(?i)(bearer|token|secret|password|passwd|cookie|session[_-]?id|sessionid|"
    r"session[_-]?key|"
    r"access[_-]?token|refresh[_-]?token|api[_-]?key|authorization)"
)


def has_credential_marker(value: str) -> bool:
    return bool(_CREDENTIAL_MARKER.search(value))


__all__ = ("has_credential_marker",)
