"""Pure request/CAS/dispatch proposals; not a queue or authorization authority.

The eventual store owns uniqueness, current authorization and atomic CAS. All
owner/proof inputs come from trusted callers. Passing a snapshot cannot acquire
a lease, acknowledge cancellation or prove that a network send did not happen.
"""

import hashlib
import hmac
import json
import math
from datetime import datetime
from typing import Literal, NoReturn, SupportsIndex

from pydantic import Field, model_validator

from app.browser_skill.models import (
    BrowserOwner,
    Contract,
    Digest,
    DispatchReceipt,
    Epoch,
    OpaqueId,
    ResourceOutcome,
    ScopeBinding,
)
from app.browser_skill.session import LeaseSnapshot, current_binding_decision, require_aware


def _json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("browser_request_json_invalid")
        result[key] = value
    return result


def _validate_json(value: object, depth: int = 0) -> None:
    if depth > 32:
        raise ValueError("browser_request_json_invalid")
    if value is None or type(value) in {bool, int, str}:
        return
    if type(value) is float and math.isfinite(value):
        return
    if isinstance(value, list):
        for item in value:
            _validate_json(item, depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError("browser_request_json_invalid")
            _validate_json(item, depth + 1)
        return
    raise ValueError("browser_request_json_invalid")


class RequestDigester:
    """Process-local injected key; no raw parameters or key appear in snapshots.

    JSON UTF-8 bytes are parsed strictly before canonical encoding. Callers must
    not log the input. The purpose prefix is fixed, not client-controlled.
    """

    __slots__ = ("__key",)

    def __init__(self, key: bytes) -> None:
        if type(key) is not bytes or len(key) < 32:
            raise ValueError("browser_request_key_invalid")
        self.__key = key

    def __repr__(self) -> str:
        return "<RequestDigester: process-local>"

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        raise TypeError("browser_request_serialization_forbidden")

    def __getstate__(self) -> object:
        raise TypeError("browser_request_serialization_forbidden")

    def digest(self, owner: BrowserOwner, schema: str, semantic_json: bytes) -> str:
        if not schema or not schema.isascii() or len(schema) > 96:
            raise ValueError("browser_request_schema_invalid")
        if type(semantic_json) is not bytes or len(semantic_json) > 1_048_576:
            raise ValueError("browser_request_json_invalid")
        try:
            value = json.loads(semantic_json.decode("utf-8"), object_pairs_hook=_json_object)
            if not isinstance(value, dict):
                raise ValueError
            _validate_json(value)
            payload = json.dumps(
                [schema, [owner.tenant_id, owner.user_id, owner.session_id], value],
                ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
            ).encode("utf-8")
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise ValueError("browser_request_json_invalid") from None
        return hmac.new(
            self.__key, b"browser-canonical-request-v1\x00" + payload, hashlib.sha256,
        ).hexdigest()


class RequestIdentity(Contract):
    owner: BrowserOwner = Field(repr=False)
    client_request_id: OpaqueId
    digest: Digest


def compare_request(
    requested: RequestIdentity,
    existing: RequestIdentity | None,
    *,
    authorized_now: bool,
) -> Literal["new", "reuse", "conflict", "unauthorized"]:
    if not authorized_now:
        return "unauthorized"
    if existing is None:
        return "new"
    if (
        requested.owner != existing.owner
        or requested.client_request_id != existing.client_request_id
        or not hmac.compare_digest(requested.digest, existing.digest)
    ):
        return "conflict"
    return "reuse"


class ProcessingSnapshot(Contract):
    request: RequestIdentity = Field(repr=False)
    revision: Epoch
    holder_id: OpaqueId | None = None
    deadline: datetime | None = None
    has_run_or_effect: bool = False

    @model_validator(mode="after")
    def holder_window(self) -> "ProcessingSnapshot":
        if (self.holder_id is None) != (self.deadline is None):
            raise ValueError("browser_processing_window_invalid")
        if self.deadline is not None:
            require_aware(self.deadline)
        return self


def claim_processing(
    snapshot: ProcessingSnapshot,
    request: RequestIdentity,
    *,
    expected_revision: int,
    now: datetime,
    authorized_now: bool,
) -> Literal["claim", "conflict", "unauthorized", "busy", "fail_new_request", "reconcile"]:
    require_aware(now)
    comparison = compare_request(request, snapshot.request, authorized_now=authorized_now)
    if comparison == "unauthorized":
        return "unauthorized"
    if comparison != "reuse" or snapshot.revision != expected_revision:
        return "conflict"
    if snapshot.has_run_or_effect:
        return "reconcile"
    if snapshot.deadline is not None:
        return "busy" if snapshot.deadline > now else "fail_new_request"
    return "claim"


class ActiveRun(Contract):
    owner: BrowserOwner = Field(repr=False)
    task_id: OpaqueId
    run_id: OpaqueId


def can_accept_run(
    owner: BrowserOwner,
    task_owner: BrowserOwner,
    task_id: str,
    active_run: ActiveRun | None,
    *,
    authorized_now: bool,
) -> bool:
    """The store must atomically enforce active-Task uniqueness after this check."""
    return bool(authorized_now and owner == task_owner and task_id and active_run is None)


class DispatchAttempt(Contract):
    binding: ScopeBinding = Field(repr=False)
    run_id: OpaqueId
    attempt_id: OpaqueId
    skill_digest: Digest
    step_id: OpaqueId
    revision: Epoch
    send_started: bool = False


class CancelSnapshot(Contract):
    binding: ScopeBinding = Field(repr=False)
    run_id: OpaqueId
    revision: Epoch
    requested: bool = False
    acknowledged: bool = False

    @model_validator(mode="after")
    def acknowledgement_requires_request(self) -> "CancelSnapshot":
        if self.acknowledged and not self.requested:
            raise ValueError("browser_cancel_request_required")
        return self


def request_cancel(snapshot: CancelSnapshot, *, expected_revision: int) -> CancelSnapshot:
    if snapshot.revision != expected_revision:
        raise ValueError("browser_cancel_revision_conflict")
    if snapshot.requested:
        return snapshot
    return snapshot.model_copy(update={"revision": snapshot.revision + 1, "requested": True})


class CancellationProof(Contract):
    """Trusted matching worker barrier or provider isolation facts, not their verifier.

    A worker barrier covers every dispatch permit already issued, including the
    gap between send_started persistence and network IO. Merely setting the
    cancel flag, losing a lease or requesting remote termination is insufficient.
    """

    binding: ScopeBinding = Field(repr=False)
    run_id: OpaqueId
    cancel_revision: Epoch
    all_dispatch_permits_stopped: bool
    resource_outcome: ResourceOutcome | None = None
    resource_outcome_matches_run: bool = False
    remote_dispatch_isolated: bool = False


def acknowledge_cancel(
    snapshot: CancelSnapshot, proof: CancellationProof,
) -> CancelSnapshot:
    if (
        not snapshot.requested
        or snapshot.binding != proof.binding
        or snapshot.run_id != proof.run_id
        or snapshot.revision != proof.cancel_revision
    ):
        raise ValueError("browser_cancel_proof_stale")
    # Released connectivity alone does not prove that a remote process stopped.
    stopped_remotely = (
        proof.resource_outcome is not None
        and proof.resource_outcome_matches_run
        and (
            proof.resource_outcome.status == "terminated"
            or (proof.resource_outcome.status == "quarantined" and proof.remote_dispatch_isolated)
        )
    )
    if not proof.all_dispatch_permits_stopped and not stopped_remotely:
        raise ValueError("browser_cancel_barrier_required")
    if snapshot.acknowledged:
        return snapshot
    return snapshot.model_copy(update={"revision": snapshot.revision + 1, "acknowledged": True})


def begin_send(
    attempt: DispatchAttempt,
    cancellation: CancelSnapshot,
    current_binding: ScopeBinding,
    lease: LeaseSnapshot,
    *,
    holder_id: str,
    now: datetime,
    authorized_now: bool,
    expected_revision: int,
) -> DispatchAttempt:
    """Propose a send_started CAS, never a dispatch permit or a network send."""
    if attempt.revision != expected_revision:
        raise ValueError("browser_dispatch_revision_conflict")
    if attempt.send_started:
        raise ValueError("browser_dispatch_replay_forbidden")
    if (
        cancellation.binding != attempt.binding
        or cancellation.run_id != attempt.run_id
        or cancellation.requested
    ):
        raise ValueError("browser_dispatch_cancelled_or_stale")
    check = current_binding_decision(
        attempt.binding, current_binding, lease,
        holder_id=holder_id, now=now, authorized_now=authorized_now,
    )
    if check != "current":
        raise ValueError("browser_dispatch_binding_or_lease_stale")
    return attempt.model_copy(update={"revision": attempt.revision + 1, "send_started": True})


def resolve_dispatch(
    attempt: DispatchAttempt, receipt: DispatchReceipt | None,
) -> Literal["not_started", "not_sent", "verify", "unknown"]:
    """Unresolved send_started is UNKNOWN. No result from this helper grants replay."""
    if receipt is not None and (
        receipt.skill_digest != attempt.skill_digest or receipt.step_id != attempt.step_id
    ):
        raise ValueError("browser_dispatch_receipt_mismatch")
    if not attempt.send_started:
        if receipt is not None:
            raise ValueError("browser_dispatch_receipt_without_attempt")
        return "not_started"
    if receipt is None or receipt.state == "possibly_sent":
        return "unknown"
    return "verify" if receipt.state == "acknowledged" else "not_sent"
