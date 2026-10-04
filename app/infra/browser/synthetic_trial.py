"""Three fixed receipt namespaces; runtime actions require owner approval, no reset."""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from app.browser_skill.models import BrowserOwner
from app.infra.browser import synthetic_vault as vault

_FILES = frozenset({
    "trial.submit.json", "trial.run.json", "trial.jev-attempt.json", "trial.worker.json",
})
_MAX_BYTES = 4096
ORIGINAL_TRIAL = vault.ORIGINAL_TRIAL
DIAGNOSTIC_TRIAL = vault.DIAGNOSTIC_TRIAL
VISIBLE_TRIAL = vault.VISIBLE_TRIAL
LEGACY_TASK_ID = "dc4ec941bcd245a5a7d9ccc4815a0f71"
LEGACY_RUN_ID = "fa1f474fdfce440c9a7338d80fc70670"
DIAGNOSTIC_TASK_ID = "4a689e26d150452b809ddd152cec0c8c"
DIAGNOSTIC_RUN_ID = "73ba1ea2a54b4d7c98bc24c1446e3c60"


def trial_request_id(trial_id: str = ORIGINAL_TRIAL) -> str:
    return vault.TASK_ID + "-" + vault.approved_trial_id(trial_id)


def trial_publication_digest(trial_id: str = ORIGINAL_TRIAL) -> str:
    vault.approved_trial_id(trial_id)
    from app.infra.browser.fixed_synthetic_seed import (
        build_fixed_synthetic_diagnostic_source,
        build_fixed_synthetic_query_source,
        build_fixed_synthetic_visible_query_source,
    )
    from app.infra.browser.synthetic_configuration import synthetic_jev_manifest

    if trial_id == ORIGINAL_TRIAL:
        builder = build_fixed_synthetic_query_source
    elif trial_id == DIAGNOSTIC_TRIAL:
        builder = build_fixed_synthetic_diagnostic_source
    elif trial_id == VISIBLE_TRIAL:
        builder = build_fixed_synthetic_visible_query_source
    else:
        raise ValueError("browser_trial_id_invalid")
    return builder(synthetic_jev_manifest()).manifest.digest


def trial_reference(trial_id: str = ORIGINAL_TRIAL) -> dict[str, str]:
    vault.approved_trial_id(trial_id)
    if trial_id == ORIGINAL_TRIAL:
        return {}
    return {"trial_id": trial_id,
            "client_request_id": trial_request_id(trial_id),
            "publication_digest": trial_publication_digest(trial_id)}


def diagnostic_reference() -> dict[str, str]:
    return trial_reference(DIAGNOSTIC_TRIAL)


def require_legacy_terminal(row: Mapping[str, Any], owner: BrowserOwner) -> None:
    """Exact approved failed predecessor only; terminal worker deadlines are historical."""
    if (row.get("task_id") != LEGACY_TASK_ID or row.get("run_id") != LEGACY_RUN_ID
            or row.get("ai_user_id") != owner.user_id or row.get("session_id") != owner.session_id
            or row.get("status") != "failed" or row.get("phase") is not None
            or row.get("cleanup") not in {"released", "terminated"}
            or row.get("effect") != "not_sent" or row.get("verification") is not None
            or row.get("error_code") != "browser_verification_failed"
            or row.get("dispatch_failure_code") != "timeout" or row.get("worker_epoch") != 1
            or row.get("client_request_id") != trial_request_id()
            or row.get("publication_digest") != bytes.fromhex(trial_publication_digest())):
        raise ValueError("browser_trial_history_invalid")


def require_diagnostic_terminal(row: Mapping[str, Any], owner: BrowserOwner) -> None:
    """Exact approved failed diagnostic predecessor; no historical receipt substitution."""
    if (row.get("task_id") != DIAGNOSTIC_TASK_ID or row.get("run_id") != DIAGNOSTIC_RUN_ID
            or row.get("ai_user_id") != owner.user_id or row.get("session_id") != owner.session_id
            or row.get("status") != "failed" or row.get("phase") is not None
            or row.get("cancel_requested") is not False
            or row.get("cleanup") not in {"released", "terminated"}
            or row.get("effect") != "not_sent" or row.get("verification") is not None
            or row.get("error_code") != "browser_verification_failed"
            or row.get("dispatch_failure_code") != "timeout" or row.get("worker_epoch") != 1
            or row.get("client_request_id") != trial_request_id(DIAGNOSTIC_TRIAL)
            or row.get("publication_digest") != bytes.fromhex(
                trial_publication_digest(DIAGNOSTIC_TRIAL)
            )):
        raise ValueError("browser_trial_history_invalid")


def approved_trial_budget(value: str) -> Decimal:
    """Explicit non-secret USD 0.01 approval declaration; not a spending cap."""
    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError):
        raise ValueError("browser_trial_budget_invalid") from None
    if not amount.is_finite() or amount != Decimal("0.01"):
        raise ValueError("browser_trial_budget_invalid")
    return amount


def _path(name: str, *, trial_id: str = ORIGINAL_TRIAL) -> Path:
    if name not in _FILES:
        raise ValueError("browser_trial_receipt_invalid")
    vault._check_trial_directory(trial_id)
    return vault.trial_directory(trial_id) / name


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


def read_trial_file(name: str, *, trial_id: str = ORIGINAL_TRIAL) -> dict[str, Any]:
    """Read non-secret metadata only; exact task path/owner/mode/link required."""
    path = _path(name, trial_id=trial_id)
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


def create_trial_file(name: str, document: dict[str, Any], *,
                      trial_id: str = ORIGINAL_TRIAL) -> None:
    """Reserve before IO; an uncertain/failed attempt is never automatically reset."""
    body = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(body) > _MAX_BYTES:
        raise ValueError("browser_trial_receipt_invalid")
    path = _path(name, trial_id=trial_id)
    try:
        fd = os.open(path, _open_flags(os.O_WRONLY | os.O_CREAT | os.O_EXCL), 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        vault._checked_file(path)
        vault._sync_directory(path.parent)
    except OSError:
        raise ValueError("browser_trial_already_attempted") from None


class SingleJevAttempt:
    """Burn the task's only transport attempt before dispatch, including failures."""

    def __init__(self, approved_budget_usd: str, *, trial_id: str = ORIGINAL_TRIAL) -> None:
        self.trial_id = vault.approved_trial_id(trial_id)
        self.budget = approved_trial_budget(approved_budget_usd)
        self.request_count = 0

    def reserve(self) -> None:
        if self.request_count:
            raise ValueError("browser_trial_already_attempted")
        document: dict[str, Any] = {
            "jev_request_count": 1, "max_jev_requests": 1,
            "approved_budget_usd": str(self.budget), "actual_cost_usd": None,
            "cost_status": "unknown", "reservation": "before_transport_dispatch",
        }
        if self.trial_id == ORIGINAL_TRIAL:
            create_trial_file("trial.jev-attempt.json", document)
        else:
            document["trial_id"] = self.trial_id
            create_trial_file("trial.jev-attempt.json", document, trial_id=self.trial_id)
        self.request_count = 1
