"""Provider-neutral failure and compact JSON helpers shared by local adapters.

Vendor transports may import these names; local execution does not depend on a
Browserless transport module merely to classify an operation failure.
"""

from __future__ import annotations

import json
from typing import Literal, TypeAlias

from app.browser_skill.models import BrowserFailure

Phase: TypeAlias = Literal[
    "acquire", "restore", "observe", "dispatch", "capture", "release", "terminate"
]
Code: TypeAlias = Literal[
    "unavailable", "unsupported", "denied", "stale", "subject_mismatch",
    "invalid_response", "timeout", "cancelled", "effect_unknown", "quarantined",
    "overloaded", "invalid_request", "resource_not_found",
]
Reason: TypeAlias = Literal[
    "disabled", "transport", "bad_input", "unauthenticated", "forbidden",
    "not_found", "deadline", "capacity", "malformed", "redirect", "budget",
    "source", "binding", "subject", "state", "unsupported", "profile_loss",
    "proof", "cancelled",
]


class BrowserProviderError(Exception):
    def __init__(
        self, code: Code, phase: Phase, reason: Reason, *,
        sent: bool = False, cleanup: bool = False,
    ) -> None:
        self.failure = BrowserFailure(
            code=code, phase=phase,
            dispatch_state="possibly_sent" if sent else "not_sent",
            cleanup_required=cleanup,
        )
        self.reason = reason
        super().__init__(f"browser_{code}:{phase}:{reason}")


def json_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    ).encode()
