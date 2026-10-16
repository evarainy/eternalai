import pickle
from datetime import UTC, datetime, timedelta

import pytest

from app.browser_skill.models import BrowserFailure, DispatchReceipt, ResourceOutcome
from app.browser_skill.scheduling import (
    ActiveRun,
    CancellationProof,
    CancelSnapshot,
    DispatchAttempt,
    ProcessingSnapshot,
    RequestDigester,
    RequestIdentity,
    acknowledge_cancel,
    begin_send,
    can_accept_run,
    claim_processing,
    compare_request,
    request_cancel,
    resolve_dispatch,
)
from app.browser_skill.session import LeaseSnapshot
from tests.browser_skill.factories import DIGEST, binding

NOW = datetime(2026, 10, 2, tzinfo=UTC)


def lease() -> LeaseSnapshot:
    return LeaseSnapshot(
        binding=binding(), holder_id="worker", deadline=NOW + timedelta(seconds=10), state="held",
    )


def identity() -> RequestIdentity:
    return RequestIdentity(owner=binding().owner, client_request_id="request", digest=DIGEST)


def attempt() -> DispatchAttempt:
    return DispatchAttempt(
        binding=binding(), run_id="run", attempt_id="attempt", skill_digest=DIGEST,
        step_id="step", revision=1,
    )


def cancellation() -> CancelSnapshot:
    return CancelSnapshot(binding=binding(), run_id="run", revision=1)


def test_digest_is_canonical_owner_bound_keyed_and_does_not_retain_inputs() -> None:
    digester = RequestDigester(bytes(range(32)))
    owner = binding().owner
    digest = digester.digest(owner, "v1", b'{"b":2, "a":1}')
    assert digest == digester.digest(owner, "v1", b'{"a":1,"b":2}')
    assert digest != digester.digest(owner, "v2", b'{"a":1,"b":2}')
    assert digest != RequestDigester(bytes(range(1, 33))).digest(owner, "v1", b'{"a":1,"b":2}')
    for field in ("tenant_id", "user_id", "session_id"):
        assert digest != digester.digest(
            owner.model_copy(update={field: "different"}), "v1", b'{"a":1,"b":2}',
        )
    assert repr(digester) == "<RequestDigester: process-local>"
    assert not hasattr(digester, "__dict__")
    with pytest.raises(TypeError, match="serialization_forbidden"):
        pickle.dumps(digester)


@pytest.mark.parametrize("raw", [
    b'{"a":1,"a":2}', b'{"a":{"x":1,"x":2}}', b'{"a":NaN}',
    b'{"a":Infinity}', b'{"a":-Infinity}', b'{"a":1e999}', b'[]',
    b'{"a":"\\ud800"}', b'\xff', b'{"a":undefined}', b'{} trailing',
])
def test_canonical_digest_rejects_ambiguous_or_unstable_json_with_fixed_error(raw: bytes) -> None:
    with pytest.raises(ValueError) as caught:
        RequestDigester(bytes(range(32))).digest(binding().owner, "v1", raw)
    assert str(caught.value) == "browser_request_json_invalid"
    assert caught.value.__suppress_context__ is True


def test_digest_rejects_excessive_depth_and_invalid_key_without_echo() -> None:
    raw = b'{"x":' * 34 + b'0' + b'}' * 34
    with pytest.raises(ValueError, match="browser_request_json_invalid"):
        RequestDigester(bytes(range(32))).digest(binding().owner, "v1", raw)
    with pytest.raises(ValueError) as caught:
        RequestDigester(b"short synthetic marker")
    assert str(caught.value) == "browser_request_key_invalid"


def test_duplicate_requests_reauthorize_and_conflicts_never_reuse() -> None:
    request = identity()
    assert compare_request(request, None, authorized_now=True) == "new"
    assert compare_request(request, request, authorized_now=True) == "reuse"
    assert compare_request(request, request, authorized_now=False) == "unauthorized"
    for update in (
        {"digest": "b" * 64}, {"client_request_id": "another"},
        {"owner": binding().owner.model_copy(update={"tenant_id": "other"})},
    ):
        assert compare_request(
            request, request.model_copy(update=update), authorized_now=True,
        ) == "conflict"


def test_processing_claim_does_not_replay_expired_parser_or_existing_effects() -> None:
    snapshot = ProcessingSnapshot(request=identity(), revision=1)
    def decide(value: ProcessingSnapshot, revision: int = 1, authorized: bool = True) -> str:
        return claim_processing(value, identity(), expected_revision=revision, now=NOW,
                                authorized_now=authorized)
    assert decide(snapshot) == "claim"
    assert decide(snapshot, revision=0) == "conflict"
    assert decide(snapshot, authorized=False) == "unauthorized"
    held = snapshot.model_copy(update={"holder_id": "worker", "deadline": NOW})
    assert decide(held) == "fail_new_request"
    assert decide(held.model_copy(update={"deadline": NOW + timedelta(seconds=1)})) == "busy"
    assert decide(held.model_copy(update={"has_run_or_effect": True})) == "reconcile"


def test_active_task_and_owner_checks_are_conservative() -> None:
    owner = binding().owner
    active = ActiveRun(owner=owner, task_id="task", run_id="run")
    assert can_accept_run(owner, owner, "task", None, authorized_now=True)
    assert not can_accept_run(owner, owner, "task", active, authorized_now=True)
    assert not can_accept_run(owner, owner, "task", None, authorized_now=False)
    assert not can_accept_run(owner, owner.model_copy(update={"session_id": "other"}),
                              "task", None, authorized_now=True)


def test_send_started_cas_fences_cancel_rebinding_and_replay() -> None:
    original = attempt()
    def begin(value: DispatchAttempt, cancel: CancelSnapshot = cancellation()) -> DispatchAttempt:
        return begin_send(value, cancel, binding(), lease(), holder_id="worker", now=NOW,
                          authorized_now=True, expected_revision=value.revision)
    sent = begin(original)
    assert sent.send_started is True
    assert sent.revision == 2
    assert original.send_started is False
    with pytest.raises(ValueError, match="replay_forbidden"):
        begin(sent)
    with pytest.raises(ValueError, match="cancelled_or_stale"):
        begin(original, request_cancel(cancellation(), expected_revision=1))
    changed = original.model_copy(update={
        "binding": binding().model_copy(update={"binding_revision": 0}),
    })
    with pytest.raises(ValueError, match="binding_or_lease_stale"):
        begin(changed, cancellation().model_copy(update={"binding": changed.binding}))
    with pytest.raises(ValueError, match="revision_conflict"):
        begin_send(original, cancellation(), binding(), lease(), holder_id="worker", now=NOW,
                   authorized_now=True, expected_revision=0)


def test_cancel_request_requires_real_matching_barrier_even_after_send_started() -> None:
    pending = request_cancel(cancellation(), expected_revision=1)
    assert pending.requested is True
    assert pending.acknowledged is False
    proof = CancellationProof(
        binding=binding(), run_id="run", cancel_revision=2, all_dispatch_permits_stopped=False,
    )
    with pytest.raises(ValueError, match="barrier_required"):
        acknowledge_cancel(pending, proof)
    released = proof.model_copy(update={
        "resource_outcome": ResourceOutcome(status="released", evidence_digest=DIGEST),
        "resource_outcome_matches_run": True,
    })
    with pytest.raises(ValueError, match="barrier_required"):
        acknowledge_cancel(pending, released)
    stopped = proof.model_copy(update={"all_dispatch_permits_stopped": True})
    acknowledged = acknowledge_cancel(pending, stopped)
    assert acknowledged.acknowledged is True
    assert acknowledged.revision == 3
    # Cancellation acknowledgement never rewrites an unresolved dispatch attempt.
    assert resolve_dispatch(attempt().model_copy(update={"send_started": True}), None) == "unknown"
    with pytest.raises(ValueError, match="proof_stale"):
        acknowledge_cancel(pending, stopped.model_copy(update={"run_id": "other"}))


def test_remote_cancel_proof_must_match_run_and_unknown_receipt_cannot_be_replayed() -> None:
    pending = request_cancel(cancellation(), expected_revision=1)
    proof = CancellationProof(
        binding=binding(), run_id="run", cancel_revision=2, all_dispatch_permits_stopped=False,
        resource_outcome=ResourceOutcome(status="terminated", evidence_digest=DIGEST),
    )
    with pytest.raises(ValueError, match="barrier_required"):
        acknowledge_cancel(pending, proof)
    assert acknowledge_cancel(
        pending, proof.model_copy(update={"resource_outcome_matches_run": True}),
    ).acknowledged
    quarantine = proof.model_copy(update={
        "resource_outcome": ResourceOutcome(status="quarantined", evidence_digest=DIGEST),
        "resource_outcome_matches_run": True,
    })
    with pytest.raises(ValueError, match="barrier_required"):
        acknowledge_cancel(pending, quarantine)
    assert acknowledge_cancel(
        pending, quarantine.model_copy(update={"remote_dispatch_isolated": True}),
    ).acknowledged
    receipt = DispatchReceipt(
        skill_digest=DIGEST, step_id="step", state="possibly_sent",
        failure=BrowserFailure(code="effect_unknown", phase="dispatch",
                               dispatch_state="possibly_sent", cleanup_required=True),
    )
    sent = attempt().model_copy(update={"send_started": True})
    assert resolve_dispatch(sent, receipt) == "unknown"
    assert resolve_dispatch(attempt(), None) == "not_started"
    with pytest.raises(ValueError, match="receipt_mismatch"):
        resolve_dispatch(sent, receipt.model_copy(update={"step_id": "other"}))
