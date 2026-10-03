"""Durable browser request/Run contracts; stored ownership is never authority.

Protected bytes are opaque to this port and must be produced by trusted admission
and verifier code. Authority checks are mandatory bounded local/database checks;
the transaction handle intentionally has no infrastructure type in this port.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

from app.browser_skill.models import BrowserOwner
from app.browser_skill.run_contracts import CommitFact

RunStatus = Literal["running", "waiting_user", "completed", "failed", "cancelled"]
RunPhase = Literal["queued", "acquiring", "running", "verifying", "waiting_user"]
CaptureStatus = Literal[
    "not_requested",
    "prepared",
    "sent",
    "unknown",
    "validated",
    "promoted",
    "failed",
    "quarantined",
]
RunEffect = Literal["not_sent", "acknowledged", "unknown"]
RunVerification = Literal["verified", "mismatch", "incomplete", "unsupported"]
RunCleanup = Literal["pending", "released", "terminated", "quarantined", "failed"]
RunAction = Literal[
    "read", "claim", "renew", "advance", "verify", "capture_prepare", "capture", "capture_settle",
    "effect_settle", "finalize", "cancel", "cleanup",
]
DispatchFailureCode = Literal[
    "unavailable",
    "overloaded",
    "invalid_request",
    "resource_not_found",
    "unsupported",
    "denied",
    "stale",
    "subject_mismatch",
    "invalid_response",
    "timeout",
    "cancelled",
    "effect_unknown",
    "quarantined",
]
MAX_RUN_REVISION = 9_007_199_254_740_991


class BrowserRunStoreError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__("browser run operation refused")
        self.code = code


def checked_run_id(value: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,96}", value) is None:
        raise BrowserRunStoreError("browser_run_identity_invalid")


def checked_run_digest(value: bytes) -> None:
    if not isinstance(value, bytes) or len(value) != 32:
        raise BrowserRunStoreError("browser_run_digest_invalid")


@dataclass(frozen=True, slots=True)
class ProtectedRunEnvelope:
    """Purpose-bound AEAD envelope; key management/decryption belong to trusted callers."""

    cipher_version: str
    key_id: str
    nonce: bytes = field(repr=False)
    ciphertext: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if (
            self.cipher_version
            not in {
                "aes256gcm-browser-input-v1",
                "aes256gcm-browser-result-v1",
            }
            or not isinstance(self.key_id, str)
            or not self.key_id.strip()
            or not isinstance(self.nonce, bytes)
            or len(self.nonce) != 12
            or not isinstance(self.ciphertext, bytes)
            or not 16 <= len(self.ciphertext) <= 1_048_592
        ):
            raise BrowserRunStoreError("browser_run_envelope_invalid")


@dataclass(frozen=True, slots=True)
class RunAdmission:
    """Immutable server-authorized identity, encrypted input and captured session proof.

    Allocate run_id before encrypting so AEAD binds it. The authority callback
    independently checks the verified Principal/session, org claims, current
    binding, policy, publication/catalog and envelope metadata before admission.
    """

    owner: BrowserOwner = field(repr=False)
    task_id: str
    run_id: str
    target_system: str
    binding_id: str
    binding_revision: int
    auth_fingerprint: bytes = field(repr=False)
    auth_expires_at: datetime = field(repr=False)
    publication_digest: bytes = field(repr=False)
    input_revision: int
    input_digest: bytes = field(repr=False)
    protected_input: ProtectedRunEnvelope = field(repr=False)
    auth_evidence_version: Literal["verified-session-v1"] = "verified-session-v1"

    def __post_init__(self) -> None:
        BrowserOwner(
            tenant_id=self.owner.tenant_id,
            user_id=self.owner.user_id,
            session_id=self.owner.session_id,
        )
        for value in (self.task_id, self.run_id, self.binding_id):
            checked_run_id(value)
        for digest in (self.auth_fingerprint, self.publication_digest, self.input_digest):
            checked_run_digest(digest)
        if (
            self.target_system not in {"oa", "u8", "hikvision_ivms"}
            or any(
                type(v) is not int or not 1 <= v <= MAX_RUN_REVISION
                for v in (self.binding_revision, self.input_revision)
            )
            or self.auth_evidence_version != "verified-session-v1"
            or self.auth_expires_at.tzinfo is None
            or self.auth_expires_at.utcoffset() is None
            or self.protected_input.cipher_version != "aes256gcm-browser-input-v1"
        ):
            raise BrowserRunStoreError("browser_run_admission_invalid")


@dataclass(frozen=True, slots=True)
class RunSnapshot:
    admission: RunAdmission = field(repr=False)
    state_revision: int
    status: RunStatus
    phase: RunPhase | None
    worker_id: str | None
    worker_epoch: int
    worker_deadline: datetime | None
    provider_key: str | None
    provider_manifest_digest: bytes | None = field(repr=False)
    lease_epoch: int | None
    profile_generation_id: str | None
    capture_operation_id: str | None
    capture_status: CaptureStatus
    cancel_requested: bool
    cancel_acknowledged: bool
    effect: RunEffect
    verification: RunVerification | None
    verification_evidence_digest: bytes | None = field(repr=False)
    cleanup: RunCleanup
    error_code: str | None
    dispatch_failure_code: DispatchFailureCode | None
    terminal_revision: int | None
    terminal_event_id: str | None
    result_digest: bytes | None = field(repr=False)
    protected_result: ProtectedRunEnvelope | None = field(repr=False)

    @property
    def owner(self) -> BrowserOwner:
        return self.admission.owner

    @property
    def task_id(self) -> str:
        return self.admission.task_id

    @property
    def run_id(self) -> str:
        return self.admission.run_id


@dataclass(frozen=True, slots=True)
class CanonicalRequest:
    owner: BrowserOwner = field(repr=False)
    task_id: str
    trace_id: str | None
    client_request_id: str
    request_digest_key_id: str
    request_digest: bytes = field(repr=False)
    processing_owner: str | None = field(repr=False)
    processing_deadline: datetime | None
    parse_winner: bool
    task_status: str
    error_code: str | None
    run: RunSnapshot | None = field(repr=False)


class BrowserRunAuthorityPort(Protocol):
    """Trusted bounded checks, never provider/Decision/network IO inside a transaction.

    These callbacks may read current facts but must not acquire credential/lease
    locks after Task/Run locks. Mutations requiring those locks must acquire them
    in before_run_lock, following credential -> lease -> session -> Task -> Run.
    check_run normally receives a freshly read persisted snapshot. Before lease
    attachment, verified-result persistence or cancellation acknowledgement it
    also receives a candidate built from that snapshot. These candidates are
    requests to verify real matching evidence, never authority in themselves.
    The verify candidate includes the exact envelope/digests and existing worker
    fences; the cancel candidate has cancel_acknowledged=True and must match a
    trusted executor's independently verified stop/barrier fact.
    renew only extends the worker claim; capture_prepare only persists an operation
    identity; capture_settle only records failed or quarantined capture. None of
    these actions grants new provider IO or promotion.
    effect_settle can only record unknown effect at the same persisted phase;
    it cannot advance execution, acknowledge an effect or replace verified output.
    """

    async def check_owner(self, transaction: object, owner: BrowserOwner) -> None: ...

    async def check_admission(
        self,
        transaction: object,
        request: CanonicalRequest,
        admission: RunAdmission,
    ) -> None: ...

    async def before_run_lock(
        self,
        transaction: object,
        run: RunSnapshot,
        action: RunAction,
    ) -> None:
        """Lock/recheck earlier authority rows before Task/Run, in fixed global order."""
        ...

    async def check_run(
        self,
        transaction: object,
        run: RunSnapshot,
        action: RunAction,
    ) -> None: ...

    async def check_cleanup(
        self,
        transaction: object,
        run: RunSnapshot,
        outcome: RunCleanup,
    ) -> None:
        """Authorize trusted historical recovery and prove this exact cleanup outcome.

        This is independent of user-session expiry/revocation. Verify the full
        persisted owner/Run/lease identity and matching provider lifecycle proof;
        a requested enum or a past business grant is not proof. Public request
        handlers must not expose this service-only operation. Any earlier locks
        are taken by before_run_lock(action='cleanup'), never here after Run.
        """
        ...

    async def authorize_cleanup(self, transaction: object, run: RunSnapshot) -> None:
        """Recheck independent service authority for an exact historical Run.

        Called after Task/Run locks; do not acquire earlier lifecycle locks here.
        This authorizes recovery access only, not business execution or a cleanup
        outcome. Expired/revoked business grants do not grant or prevent this
        independently authorized recovery access.
        """
        ...


class BrowserRunStorePort(Protocol):
    async def get_or_create_request(
        self,
        owner: BrowserOwner,
        client_request_id: str,
        semantic_input: bytes,
        *,
        processing_owner: str,
        ttl_seconds: int = 120,
    ) -> CanonicalRequest: ...

    async def reject_request(self, request: CanonicalRequest, error_code: str) -> None: ...

    async def accept(self, request: CanonicalRequest, admission: RunAdmission) -> CommitFact:
        """Insert Run and transition the same canonical Task; mint only after commit."""
        ...

    async def get(self, owner: BrowserOwner, task_id: str, run_id: str) -> RunSnapshot: ...

    async def get_for_cleanup(
        self, owner: BrowserOwner, task_id: str, run_id: str,
    ) -> RunSnapshot:
        """Trusted service-only historical lookup, never a public HTTP authorization.

        The caller must independently authorize cleanup; owner/Run IDs confer no
        grant. The returned snapshot permits neither new business execution nor
        an assertion of successful cleanup. Provider IO follows transaction close;
        record_cleanup still requires exact independent lifecycle outcome proof.
        """
        ...

    async def claim_next(
        self,
        owner: BrowserOwner,
        *,
        worker_id: str,
        ttl_seconds: int = 60,
    ) -> RunSnapshot | None: ...

    async def renew(self, claim: RunSnapshot, *, ttl_seconds: int = 60) -> RunSnapshot: ...

    async def advance(
        self,
        claim: RunSnapshot,
        *,
        phase: RunPhase,
        effect: RunEffect,
    ) -> RunSnapshot: ...

    async def attach_lease(
        self,
        claim: RunSnapshot,
        *,
        provider_key: str,
        provider_manifest_digest: bytes,
        lease_epoch: int,
    ) -> RunSnapshot: ...

    async def persist_verified(
        self,
        claim: RunSnapshot,
        *,
        result: ProtectedRunEnvelope,
        result_digest: bytes,
        evidence_digest: bytes,
    ) -> RunSnapshot: ...

    async def prepare_capture(self, claim: RunSnapshot) -> RunSnapshot: ...

    async def transition_capture(
        self,
        claim: RunSnapshot,
        *,
        status: CaptureStatus,
        profile_generation_id: str | None = None,
    ) -> RunSnapshot: ...

    async def request_cancel(
        self,
        owner: BrowserOwner,
        task_id: str,
        run_id: str,
    ) -> RunSnapshot: ...

    async def acknowledge_cancel(self, claim: RunSnapshot) -> RunSnapshot: ...

    async def finalize(
        self,
        claim: RunSnapshot,
        *,
        status: Literal["completed", "failed", "cancelled"],
        error_code: str | None = None,
        verification: RunVerification | None = None,
        dispatch_failure_code: DispatchFailureCode | None = None,
    ) -> RunSnapshot: ...

    async def record_cleanup(self, claim: RunSnapshot, *, cleanup: RunCleanup) -> RunSnapshot:
        """Trusted authority must verify the independent exact provider/lease cleanup proof."""
        ...
