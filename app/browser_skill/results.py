"""Pure business/effect/cleanup projection and terminal CAS proposals.

Trusted callers must validate core receipts/evidence against the actual command,
ReadSpec and current owner first. The future store atomically persists terminal
state, canonical Task projection and event; these helpers prove none of that IO.
"""

import hashlib
import hmac
import json
import re
from typing import Literal

from pydantic import Field, model_validator

from app.browser_skill.models import (
    BrowserFailure,
    BrowserOwner,
    Contract,
    Digest,
    DispatchReceipt,
    Epoch,
    OpaqueId,
    VerificationResult,
)


class ResultState(Contract):
    business: Literal["running", "completed", "failed", "cancelled"]
    effect: Literal["not_sent", "acknowledged", "unknown"]
    verification: VerificationResult | None = None
    cleanup: Literal["pending", "released", "terminated", "quarantined", "failed"]
    dispatch_failure: BrowserFailure | None = None
    error_code: Literal[
        "browser_effect_unknown", "browser_verification_failed", "browser_not_sent",
        "browser_cancelled",
    ] | None = None

    @model_validator(mode="after")
    def consistent_business(self) -> "ResultState":
        verified = self.verification is not None and self.verification.status == "verified"
        if (self.business == "completed") != verified:
            raise ValueError("browser_business_verification_inconsistent")
        if self.business == "completed" and self.error_code is not None:
            raise ValueError("browser_success_has_error")
        if self.business == "running" and self.error_code is not None:
            raise ValueError("browser_running_has_error")
        if self.business == "failed" and self.error_code is None:
            raise ValueError("browser_failure_code_required")
        if self.business == "cancelled" and self.error_code != "browser_cancelled":
            raise ValueError("browser_cancel_code_required")
        if self.effect == "unknown" and not verified and (
            self.business != "failed" or self.error_code != "browser_effect_unknown"
        ):
            raise ValueError("browser_unknown_effect_requires_failure")
        return self


def derive_result(
    receipt: DispatchReceipt | None,
    verification: VerificationResult | None,
    *,
    send_started: bool,
    cleanup: Literal["pending", "released", "terminated", "quarantined", "failed"],
    cancellation_acknowledged: bool = False,
) -> ResultState:
    """Only independent verification proves success; cleanup never resubmits work.

    A verified independent read may resolve an uncertain send. It does not grant
    replay, change the receipt, or retract an action already sent.
    """
    if receipt is not None and not send_started:
        raise ValueError("browser_result_receipt_without_attempt")
    effect: Literal["not_sent", "acknowledged", "unknown"] = "not_sent"
    if send_started:
        effect = (
            "unknown" if receipt is None or receipt.state == "possibly_sent" else receipt.state
        )
    failure = receipt.failure if receipt is not None else None
    if verification is not None and verification.status == "verified":
        return ResultState(
            business="completed", effect=effect, verification=verification, cleanup=cleanup,
            dispatch_failure=failure,
        )
    if effect == "unknown":
        return ResultState(
            business="failed", effect=effect, verification=verification, cleanup=cleanup,
            error_code="browser_effect_unknown",
            dispatch_failure=failure,
        )
    if cancellation_acknowledged:
        return ResultState(
            business="cancelled", effect=effect, verification=verification, cleanup=cleanup,
            error_code="browser_cancelled",
            dispatch_failure=failure,
        )
    if verification is not None:
        return ResultState(
            business="failed", effect=effect, verification=verification, cleanup=cleanup,
            error_code="browser_verification_failed",
            dispatch_failure=failure,
        )
    if receipt is not None and receipt.state == "not_sent":
        return ResultState(
            business="failed", effect=effect, cleanup=cleanup, error_code="browser_not_sent",
            dispatch_failure=failure,
        )
    return ResultState(business="running", effect=effect, cleanup=cleanup)


class TerminalRecord(Contract):
    """Immutable business terminal, carrying the Run revision at its commit."""
    owner: BrowserOwner = Field(repr=False)
    run_id: OpaqueId
    revision: Epoch
    result_digest: Digest


def terminal_digest(result: ResultState, *, protected_result_digest: Digest) -> str:
    """Hash closed state plus an already-keyed payload digest; never accept raw results."""
    if re.fullmatch(r"[a-f0-9]{64}", protected_result_digest) is None:
        raise ValueError("browser_protected_result_digest_invalid")
    # Cleanup has its own lifecycle and must not alter the immutable business terminal.
    payload = result.model_dump(mode="json", exclude={"cleanup"})
    payload["protected_result_digest"] = protected_result_digest
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(b"browser-business-terminal-v1\x00" + encoded).hexdigest()


def terminal_transition(
    owner: BrowserOwner,
    run_id: str,
    result: ResultState,
    *,
    protected_result_digest: Digest,
    expected_revision: int,
    current_revision: int,
    current_terminal: TerminalRecord | None,
) -> Literal["commit", "noop", "conflict", "not_terminal"]:
    """Idempotence compares full owner/run/content; never replace a different terminal."""
    if result.business == "running":
        return "not_terminal"
    digest = terminal_digest(result, protected_result_digest=protected_result_digest)
    if current_terminal is not None:
        if (
            current_terminal.owner == owner
            and current_terminal.run_id == run_id
            and current_terminal.revision <= current_revision
            and hmac.compare_digest(current_terminal.result_digest, digest)
        ):
            return "noop"
        return "conflict"
    if current_revision != expected_revision or current_revision < 0 or not run_id:
        return "conflict"
    return "commit"
