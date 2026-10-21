"""Process-local capacity ledger. No TTL, disconnect or lease can free a remote slot."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Literal

from app.browser_skill.models import BrowserSessionRef, Contract, Digest, ResourceOutcome


class TerminationEvidence(Contract):
    session_ref: str
    manifest_digest: Digest
    resource_digest: Digest
    challenge: str
    evidence_digest: Digest
    remote_terminated: Literal[True]


@dataclass
class ResourceRecord:
    session: BrowserSessionRef
    state: Literal["reserved", "acquiring", "active", "quarantined", "terminated"] = "reserved"
    resource_digest: str | None = None
    challenge: str | None = None
    outcome: ResourceOutcome | None = None


class CapacityLedger:
    """Synchronous mutations are atomic in the provider's single asyncio loop."""

    def __init__(self, limit: int) -> None:
        if type(limit) is not int or limit < 1:
            raise ValueError("browser_capacity_invalid")
        self.limit = limit
        self._records: dict[str, ResourceRecord] = {}

    @property
    def occupied(self) -> int:
        return sum(record.state != "terminated" for record in self._records.values())

    def reserve(self, session: BrowserSessionRef) -> ResourceRecord:
        if self.occupied >= self.limit:
            raise ValueError("browser_capacity_exhausted")
        if session.session_ref in self._records:
            raise ValueError("browser_resource_duplicate")
        record = ResourceRecord(session)
        self._records[session.session_ref] = record
        return record

    def get(self, session: BrowserSessionRef) -> ResourceRecord:
        record = self._records.get(session.session_ref)
        if record is None or record.session != session:
            raise ValueError("browser_resource_owner_mismatch")
        return record

    def quarantine(self, record: ResourceRecord) -> ResourceOutcome:
        if record.state == "terminated" and record.outcome is not None:
            return record.outcome
        record.state = "quarantined"
        return ResourceOutcome(
            status="quarantined",
            evidence_digest=sha256(
                f"quarantined:{record.session.session_ref}".encode()
            ).hexdigest(),
        )

    def finish(
        self,
        record: ResourceRecord,
        evidence: TerminationEvidence,
        manifest_digest: str,
    ) -> ResourceOutcome:
        if (
            evidence.session_ref != record.session.session_ref
            or evidence.manifest_digest != manifest_digest
            or evidence.resource_digest != record.resource_digest
            or evidence.challenge != record.challenge
            or record.state not in {"active", "acquiring", "quarantined"}
        ):
            return self.quarantine(record)
        record.state = "terminated"
        record.outcome = ResourceOutcome(
            status="terminated", evidence_digest=evidence.evidence_digest
        )
        return record.outcome

    def release_reservation(self, record: ResourceRecord) -> ResourceOutcome:
        if record.state != "reserved":
            raise ValueError("browser_resource_was_dispatched")
        record.state = "terminated"
        record.outcome = ResourceOutcome(
            status="released",
            evidence_digest=sha256(
                f"never_dispatched:{record.session.session_ref}".encode()
            ).hexdigest(),
        )
        return record.outcome
