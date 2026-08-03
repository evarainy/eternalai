"""Value-free failures for the smoke runner."""

from __future__ import annotations


class SmokeError(RuntimeError):
    """Fail closed with a stable code that never embeds input values."""

    def __init__(self, code: str, *, exit_code: int = 1) -> None:
        super().__init__(code)
        self.code = code
        self.exit_code = exit_code


__all__ = ("SmokeError",)
