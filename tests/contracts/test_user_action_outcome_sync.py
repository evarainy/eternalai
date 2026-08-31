"""Keep the frontend UserActionOutcome guard closed over the Runtime contract."""

from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

import pytest

from app.ports.runtime import UserActionOutcome

_FRONTEND_CONTRACT = (
    Path(__file__).resolve().parents[2]
    / "web"
    / "src"
    / "contracts"
    / "userActionOutcome.ts"
)
_RUNTIME_CONTRACT = (
    Path(__file__).resolve().parents[2] / "app" / "ports" / "runtime.py"
)
_OUTCOME_ARRAY = re.compile(
    r"export\s+const\s+USER_ACTION_OUTCOMES\s*=\s*\[(?P<body>.*?)\]\s*as\s+const",
    re.DOTALL,
)
_OUTCOME_ALIAS = re.compile(
    r"type\s+UserActionOutcome\s*=\s*Literal\[(?P<body>.*?)\]",
    re.DOTALL,
)
_STRING_LITERAL = re.compile(r"['\"](?P<value>[^'\"]+)['\"]")


def _assert_outcome_sequences_match(
    frontend_outcomes: tuple[str, ...],
    runtime_outcomes: tuple[str, ...],
) -> bool:
    assert len(frontend_outcomes) == len(set(frontend_outcomes)), (
        "frontend outcome literals must be unique"
    )
    assert len(runtime_outcomes) == len(set(runtime_outcomes)), (
        "runtime outcome literals must be unique"
    )
    assert len(frontend_outcomes) == len(runtime_outcomes)
    assert set(frontend_outcomes) == set(runtime_outcomes)
    return True


def _outcome_sequences(
    frontend_source: str,
    runtime_source: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    array_match = _OUTCOME_ARRAY.search(frontend_source)
    alias_match = _OUTCOME_ALIAS.search(runtime_source)

    assert array_match is not None, "USER_ACTION_OUTCOMES array was not found"
    assert alias_match is not None, "UserActionOutcome alias was not found"
    frontend_outcomes = tuple(
        match.group("value")
        for match in _STRING_LITERAL.finditer(array_match.group("body"))
    )
    runtime_outcomes = tuple(
        match.group("value")
        for match in _STRING_LITERAL.finditer(alias_match.group("body"))
    )
    return frontend_outcomes, runtime_outcomes


def _rewrite_outcome_literals(
    source: str,
    pattern: re.Pattern[str],
    outcomes: tuple[str, ...],
) -> str:
    contract_match = pattern.search(source)
    assert contract_match is not None
    body = contract_match.group("body")
    literal_matches = list(_STRING_LITERAL.finditer(body))
    assert len(literal_matches) == len(outcomes)

    rewritten = body
    for literal_match, outcome in reversed(tuple(zip(literal_matches, outcomes))):
        quote = literal_match.group(0)[0]
        rewritten = (
            rewritten[: literal_match.start()]
            + f"{quote}{outcome}{quote}"
            + rewritten[literal_match.end() :]
        )
    return (
        source[: contract_match.start("body")]
        + rewritten
        + source[contract_match.end("body") :]
    )


def test_frontend_user_action_outcomes_match_runtime_port() -> None:
    frontend_source = _FRONTEND_CONTRACT.read_text(encoding="utf-8")
    runtime_source = _RUNTIME_CONTRACT.read_text(encoding="utf-8")
    frontend_outcomes, runtime_outcomes = _outcome_sequences(
        frontend_source,
        runtime_source,
    )

    assert _assert_outcome_sequences_match(frontend_outcomes, runtime_outcomes)
    assert set(get_args(UserActionOutcome.__value__)) == set(runtime_outcomes)


@pytest.mark.parametrize(
    "duplicate_side",
    ("frontend", "runtime"),
)
def test_outcome_sync_guard_rejects_duplicate_literals_on_either_side(
    duplicate_side: str,
) -> None:
    frontend_source = _FRONTEND_CONTRACT.read_text(encoding="utf-8")
    runtime_source = _RUNTIME_CONTRACT.read_text(encoding="utf-8")
    frontend_outcomes, runtime_outcomes = _outcome_sequences(
        frontend_source,
        runtime_source,
    )
    if duplicate_side == "frontend":
        duplicated = (frontend_outcomes[0], frontend_outcomes[0], *frontend_outcomes[2:])
        frontend_source = _rewrite_outcome_literals(
            frontend_source,
            _OUTCOME_ARRAY,
            duplicated,
        )
    else:
        duplicated = (runtime_outcomes[0], runtime_outcomes[0], *runtime_outcomes[2:])
        runtime_source = _rewrite_outcome_literals(
            runtime_source,
            _OUTCOME_ALIAS,
            duplicated,
        )

    with pytest.raises(AssertionError, match="must be unique"):
        _assert_outcome_sequences_match(
            *_outcome_sequences(frontend_source, runtime_source)
        )


def test_outcome_sync_guard_accepts_reordering() -> None:
    frontend_source = _FRONTEND_CONTRACT.read_text(encoding="utf-8")
    runtime_source = _RUNTIME_CONTRACT.read_text(encoding="utf-8")
    frontend_outcomes, _ = _outcome_sequences(frontend_source, runtime_source)
    reordered = (
        frontend_outcomes[1],
        frontend_outcomes[0],
        *frontend_outcomes[2:],
    )
    frontend_source = _rewrite_outcome_literals(
        frontend_source,
        _OUTCOME_ARRAY,
        reordered,
    )

    assert _assert_outcome_sequences_match(
        *_outcome_sequences(frontend_source, runtime_source)
    )
