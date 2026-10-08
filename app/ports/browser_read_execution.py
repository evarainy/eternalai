"""Trusted READ_ONLY worker boundary; never deserialize these objects from HTTP.

The durable store owns worker CAS. Implementations own real browser/provider IO
and must fence each awaited boundary through the supplied checkpoint. No method
accepts an authorization boolean or treats an HTTP acknowledgment as proof.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

from app.ports.browser_profile_store import BrowserProfileContext, BrowserProfileGeneration
from app.ports.browser_run_store import (
    DispatchFailureCode,
    ProtectedRunEnvelope,
    RunCleanup,
    RunEffect,
    RunSnapshot,
    RunVerification,
)


class BrowserReadExecutionError(RuntimeError):
    def __init__(self, code: Literal["unsupported", "denied", "stale", "cancelled", "timeout",
                                       "invalid_response", "unavailable"]) -> None:
        super().__init__("browser read execution refused")
        self.code = code


class BrowserWorkerCheckpoint(Protocol):
    async def refresh(self, *, allow_cancel: bool = False) -> RunSnapshot:
        """Re-read current authority and renew exact worker epoch before/after IO."""
        ...

    async def attach_lease(
        self, *, provider_key: str, provider_manifest_digest: bytes, lease_epoch: int,
    ) -> RunSnapshot: ...

    async def start_execution(self) -> RunSnapshot: ...


@dataclass(frozen=True, slots=True, repr=False)
class BrowserReadOutcome:
    effect: RunEffect
    verification: RunVerification | None
    result: ProtectedRunEnvelope | None = None
    result_digest: bytes | None = None
    evidence_digest: bytes | None = None
    dispatch_failure_code: DispatchFailureCode | None = None

    def __post_init__(self) -> None:
        values = (self.result, self.result_digest, self.evidence_digest)
        if self.verification == "verified":
            if (
                not isinstance(self.result, ProtectedRunEnvelope)
                or self.result.cipher_version != "aes256gcm-browser-result-v1"
                or any(type(v) is not bytes or len(v) != 32
                       for v in (self.result_digest, self.evidence_digest))
            ):
                raise ValueError("browser_read_result_invalid")
        elif any(v is not None for v in values):
            raise ValueError("browser_read_unverified_result")


@dataclass(frozen=True, slots=True, repr=False)
class BrowserCaptureResolution:
    """Provider lookup result. A found generation still needs independent proof.

    absent is an authoritative operation lookup, not a missing local receipt.
    Unknown or unsupported lookups must never cause capture to be sent again.
    """

    status: Literal["found", "absent", "unknown", "unsupported", "failed"]
    context: BrowserProfileContext | None = field(default=None, repr=False)
    generation: BrowserProfileGeneration | None = field(default=None, repr=False)
    evidence: bytes | None = field(default=None, repr=False)
    expected_profile_revision: int | None = None

    def __post_init__(self) -> None:
        if self.status == "found":
            if (
                self.context is None or self.generation is None
                or type(self.evidence) is not bytes or not self.evidence
                or type(self.expected_profile_revision) is not int
                or self.expected_profile_revision < 0
            ):
                raise ValueError("browser_capture_resolution_invalid")
        elif any(v is not None for v in (
            self.context, self.generation, self.evidence, self.expected_profile_revision,
        )):
            raise ValueError("browser_capture_resolution_invalid")


class BrowserReadExecutionPort(Protocol):
    async def execute(
        self, run: RunSnapshot, checkpoint: BrowserWorkerCheckpoint,
    ) -> BrowserReadOutcome:
        """Actual Executor + IndependentVerifier; encrypt only the exact verified read."""
        ...

    def verified_persisted(self, run: RunSnapshot) -> None:
        """Forget private verification emission only after its durable commit."""
        ...

    async def lookup_capture(
        self, run: RunSnapshot, checkpoint: BrowserWorkerCheckpoint,
    ) -> BrowserCaptureResolution:
        """Authoritative native provider lookup for the persisted capture_operation_id."""
        ...

    async def send_capture(
        self, run: RunSnapshot, checkpoint: BrowserWorkerCheckpoint,
    ) -> BrowserCaptureResolution:
        """Only once, after sent was committed; native operation ID must be honored."""
        ...

    async def stop(self, run: RunSnapshot, checkpoint: BrowserWorkerCheckpoint) -> None:
        """Obtain exact termination/barrier evidence for store authority to verify.

        Returning alone is not acknowledgment. The store's cancel authority must
        independently check the actual proof before acknowledging cancellation.
        """
        ...

    async def cleanup(self, run: RunSnapshot) -> RunCleanup:
        """Independent recovery authority and exact resource proof; never execute a query."""
        ...
