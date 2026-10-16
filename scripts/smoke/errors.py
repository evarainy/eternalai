"""Value-free failures for the smoke runner."""

from __future__ import annotations

from typing import Literal

EnvParseReason = Literal[
    "missing_assignment", "invalid_key", "duplicate_key", "quote_boundary_invalid",
]
ENV_PARSE_REASONS = frozenset({
    "missing_assignment", "invalid_key", "duplicate_key", "quote_boundary_invalid",
})


class SmokeError(RuntimeError):
    """Fail closed with a stable code that never embeds input values."""

    def __init__(
        self, code: str, *, exit_code: int = 1,
        parse_reason: EnvParseReason | None = None, line_number: int | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.exit_code = exit_code
        self._env_parse_diagnostic = (parse_reason, line_number)

    def safe_env_diagnostic(self) -> tuple[EnvParseReason | None, int | None]:
        reason, line = self._env_parse_diagnostic
        return (
            reason if type(reason) is str and reason in ENV_PARSE_REASONS else None,
            line if type(line) is int and line > 0 else None,
        )


__all__ = ("ENV_PARSE_REASONS", "EnvParseReason", "SmokeError")
