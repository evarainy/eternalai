import pytest
from pydantic import ValidationError

from app.browser_skill.models import BrowserFailure, DispatchReceipt, VerificationResult
from app.browser_skill.results import (
    ResultState,
    TerminalRecord,
    derive_result,
    terminal_digest,
    terminal_transition,
)
from tests.browser_skill.factories import DIGEST, binding


def acknowledged() -> DispatchReceipt:
    return DispatchReceipt(skill_digest=DIGEST, step_id="step", state="acknowledged",
                           evidence_digest=DIGEST)


def verified() -> VerificationResult:
    return VerificationResult(status="verified", evidence_digest=DIGEST)


def test_ack_requires_verification_and_cleanup_failure_does_not_undo_success() -> None:
    pending = derive_result(acknowledged(), None, send_started=True, cleanup="pending")
    assert (pending.business, pending.effect, pending.error_code) == (
        "running", "acknowledged", None,
    )
    success = derive_result(acknowledged(), verified(), send_started=True, cleanup="failed")
    assert success.business == "completed"
    assert success.cleanup == "failed"
    assert success.verification == verified()
    assert success.error_code is None


@pytest.mark.parametrize("status", ["mismatch", "incomplete", "unsupported"])
def test_unverified_read_never_reports_success(status: str) -> None:
    verification = VerificationResult.model_validate({"status": status})
    result = derive_result(acknowledged(), verification, send_started=True, cleanup="released")
    assert result.business == "failed"
    assert result.error_code == "browser_verification_failed"
    assert result.verification == verification


def test_lost_send_remains_unknown_after_cancellation_or_cleanup() -> None:
    for receipt in (
        None,
        DispatchReceipt(
            skill_digest=DIGEST, step_id="step", state="possibly_sent",
            failure=BrowserFailure(code="effect_unknown", phase="dispatch",
                                   dispatch_state="possibly_sent", cleanup_required=True),
        ),
    ):
        result = derive_result(receipt, None, send_started=True, cleanup="terminated",
                               cancellation_acknowledged=True)
        assert result.business == "failed"
        assert result.effect == "unknown"
        assert result.error_code == "browser_effect_unknown"
        resolved = derive_result(receipt, verified(), send_started=True, cleanup="quarantined")
        assert resolved.business == "completed"
        assert resolved.effect == "unknown"
        assert resolved.cleanup == "quarantined"


def test_cancelled_unsent_and_explicit_failure_remain_distinct() -> None:
    cancelled = derive_result(None, None, send_started=False, cleanup="released",
                               cancellation_acknowledged=True)
    assert (cancelled.business, cancelled.error_code) == ("cancelled", "browser_cancelled")
    receipt = DispatchReceipt(
        skill_digest=DIGEST, step_id="step", state="not_sent",
        failure=BrowserFailure(code="denied", phase="dispatch",
                               dispatch_state="not_sent", cleanup_required=False),
    )
    failed = derive_result(receipt, None, send_started=True, cleanup="released")
    assert (failed.business, failed.error_code) == ("failed", "browser_not_sent")
    assert failed.dispatch_failure == receipt.failure
    with pytest.raises(ValueError, match="receipt_without_attempt"):
        derive_result(receipt, None, send_started=False, cleanup="released")


def test_terminal_cas_idempotence_owner_isolation_and_different_content_conflict() -> None:
    result = derive_result(acknowledged(), verified(), send_started=True, cleanup="pending")
    owner = binding().owner
    digest = terminal_digest(result, protected_result_digest=DIGEST)
    terminal = TerminalRecord(owner=owner, run_id="run", revision=2, result_digest=digest)
    def transition(value: ResultState, current: TerminalRecord | None, revision: int) -> str:
        return terminal_transition(owner, "run", value, protected_result_digest=DIGEST,
                                   expected_revision=1, current_revision=revision,
                                   current_terminal=current)
    assert transition(result, None, 1) == "commit"
    assert transition(result, None, 2) == "conflict"
    assert transition(result, terminal, 2) == "noop"
    # Cleanup is separately updateable without publishing a second business terminal.
    assert transition(result.model_copy(update={"cleanup": "failed"}), terminal, 2) == "noop"
    assert transition(result.model_copy(update={"cleanup": "failed"}), terminal, 3) == "noop"
    assert transition(result, terminal, 1) == "conflict"
    for changed in (
        terminal.model_copy(update={"result_digest": "b" * 64}),
        terminal.model_copy(update={"run_id": "other"}),
        terminal.model_copy(update={"owner": owner.model_copy(update={"tenant_id": "other"})}),
        terminal.model_copy(update={"owner": owner.model_copy(update={"session_id": "other"})}),
    ):
        assert transition(result, changed, 2) == "conflict"
    other = derive_result(None, None, send_started=True, cleanup="pending")
    assert transition(other, terminal, 2) == "conflict"
    running = derive_result(None, None, send_started=False, cleanup="pending")
    assert transition(running, None, 1) == "not_terminal"


def test_terminal_hash_covers_safe_result_digest_and_verification_but_excludes_cleanup() -> None:
    result = derive_result(acknowledged(), verified(), send_started=True, cleanup="released")
    digest = terminal_digest(result, protected_result_digest=DIGEST)
    assert digest != terminal_digest(result, protected_result_digest="b" * 64)
    changed = result.model_copy(update={
        "verification": VerificationResult(status="verified", evidence_digest="b" * 64),
    })
    assert digest != terminal_digest(changed, protected_result_digest=DIGEST)
    assert digest == terminal_digest(result.model_copy(update={"cleanup": "failed"}),
                                     protected_result_digest=DIGEST)


def test_result_model_rejects_unverified_success_and_failure_without_code() -> None:
    with pytest.raises(ValidationError, match="verification_inconsistent"):
        ResultState(business="completed", effect="acknowledged", cleanup="released")
    with pytest.raises(ValidationError, match="failure_code_required"):
        ResultState(business="failed", effect="unknown", cleanup="quarantined")
    with pytest.raises(ValidationError, match="unknown_effect_requires_failure"):
        ResultState(business="cancelled", effect="unknown", cleanup="terminated",
                    error_code="browser_cancelled")
    valid = derive_result(acknowledged(), verified(), send_started=True, cleanup="released")
    with pytest.raises(ValueError, match="protected_result_digest_invalid"):
        terminal_digest(valid, protected_result_digest="not-a-digest")
