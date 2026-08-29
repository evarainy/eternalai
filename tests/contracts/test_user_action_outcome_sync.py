"""Keep the frontend UserActionOutcome guard closed over the Runtime contract."""

from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

from app.ports.runtime import UserActionOutcome

_FRONTEND_CONTRACT = (
    Path(__file__).resolve().parents[2]
    / "web"
    / "src"
    / "contracts"
    / "userActionOutcome.ts"
)
_OUTCOME_ARRAY = re.compile(
    r"export\s+const\s+USER_ACTION_OUTCOMES\s*=\s*\[(?P<body>.*?)\]\s*as\s+const",
    re.DOTALL,
)
_STRING_LITERAL = re.compile(r"['\"](?P<value>[^'\"]+)['\"]")


def test_frontend_user_action_outcomes_match_runtime_port() -> None:
    source = _FRONTEND_CONTRACT.read_text(encoding="utf-8")
    array_match = _OUTCOME_ARRAY.search(source)

    assert array_match is not None, "USER_ACTION_OUTCOMES array was not found"
    frontend_outcomes = {
        match.group("value")
        for match in _STRING_LITERAL.finditer(array_match.group("body"))
    }

    assert frontend_outcomes == set(get_args(UserActionOutcome))
