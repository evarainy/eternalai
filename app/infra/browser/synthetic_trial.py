"""Four private, immutable receipts for this one synthetic trial; no reset path."""

from __future__ import annotations

import json
import os
import stat
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from app.infra.browser import synthetic_vault as vault

_FILES = frozenset({
    "trial.submit.json", "trial.run.json", "trial.jev-attempt.json", "trial.worker.json",
})
_MAX_BYTES = 4096


def approved_trial_budget(value: str) -> Decimal:
    """Explicit non-secret approval argument, bounded to this trial's USD 0.01."""
    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError):
        raise ValueError("browser_trial_budget_invalid") from None
    if not amount.is_finite() or amount != Decimal("0.01"):
        raise ValueError("browser_trial_budget_invalid")
    return amount


def _path(name: str) -> Path:
    if name not in _FILES:
        raise ValueError("browser_trial_receipt_invalid")
    vault._check_directory(create=False)
    return vault.VAULT_DIRECTORY / name


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("browser_trial_receipt_invalid")
        result[key] = value
    return result


def _open_flags(base: int) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    cloexec = getattr(os, "O_CLOEXEC", 0)
    if not nofollow or not cloexec:
        raise ValueError("browser_trial_receipt_unavailable")
    return base | nofollow | cloexec


def read_trial_file(name: str) -> dict[str, Any]:
    """Read non-secret metadata only; exact task path/owner/mode/link required."""
    path = _path(name)
    try:
        vault._checked_file(path)
        fd = os.open(path, _open_flags(os.O_RDONLY))
        with os.fdopen(fd, "rb") as stream:
            meta = os.fstat(stream.fileno())
            if (not stat.S_ISREG(meta.st_mode) or meta.st_uid != vault._current_uid()
                    or meta.st_nlink != 1 or stat.S_IMODE(meta.st_mode) != 0o600
                    or meta.st_size > _MAX_BYTES):
                raise ValueError
            body = stream.read(_MAX_BYTES + 1)
        if len(body) > _MAX_BYTES:
            raise ValueError
        document = json.loads(body, object_pairs_hook=_reject_duplicates)
        if type(document) is not dict:
            raise ValueError
        return document
    except (OSError, ValueError, UnicodeError):
        raise ValueError("browser_trial_receipt_unavailable") from None


def create_trial_file(name: str, document: dict[str, Any]) -> None:
    """Reserve before IO; an uncertain/failed attempt is never automatically reset."""
    body = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(body) > _MAX_BYTES:
        raise ValueError("browser_trial_receipt_invalid")
    path = _path(name)
    try:
        fd = os.open(path, _open_flags(os.O_WRONLY | os.O_CREAT | os.O_EXCL), 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        vault._checked_file(path)
        vault._sync_directory(vault.VAULT_DIRECTORY)
    except OSError:
        raise ValueError("browser_trial_already_attempted") from None


class SingleJevAttempt:
    """Burn the task's only transport attempt before dispatch, including failures."""

    def __init__(self, approved_budget_usd: str) -> None:
        self.budget = approved_trial_budget(approved_budget_usd)
        self.request_count = 0

    def reserve(self) -> None:
        if self.request_count:
            raise ValueError("browser_trial_already_attempted")
        create_trial_file("trial.jev-attempt.json", {
            "jev_request_count": 1, "max_jev_requests": 1,
            "approved_budget_usd": str(self.budget), "actual_cost_usd": None,
            "cost_status": "unknown", "reservation": "before_transport_dispatch",
        })
        self.request_count = 1
