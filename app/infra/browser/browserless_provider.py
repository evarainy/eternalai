"""Cloud synthetic BrowserProvider. Persisted authorization and CAS remain outside infra."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Protocol
from uuid import uuid4

from app.browser_skill.models import (
    BrowserCapabilities,
    BrowserSessionRef,
    Contract,
    DecisionSource,
    Digest,
    ProfileRef,
    ResourceOutcome,
    ScopeBinding,
    SessionStartResult,
)
from app.infra.browser.browserless_wire import (
    BrowserlessWire,
    BrowserProviderError,
    HTTPSBrowserlessTransport,
    HTTPTransport,
    Phase,
    SessionWire,
)
from app.infra.browser.deployment_manifest import BrowserDeployment, SyntheticSource
from app.infra.browser.playwright_actions import BrowserConnector, LiveBrowser, PlaywrightConnector
from app.infra.browser.profile_codec import from_playwright, validate_upload
from app.infra.browser.resource_lifecycle import CapacityLedger, ResourceRecord, TerminationEvidence


class SubjectEvidence(Contract):
    binding: ScopeBinding
    subject_digest: Digest
    evidence_digest: Digest


class BindingAuthority(Protocol):
    async def current(self, binding: ScopeBinding) -> ScopeBinding: ...
    async def source(self, binding: ScopeBinding) -> SyntheticSource: ...

    async def subject(self, session: BrowserSessionRef, live: LiveBrowser) -> SubjectEvidence:
        """Use independent site authority, not the selected row, URL or title."""
        ...


@dataclass(frozen=True, repr=False)
class CleanupRequest:
    """Opaque identities plus infra handle for a trusted deployment-specific proof callback."""

    session: BrowserSessionRef
    manifest_digest: str
    resource_digest: str
    challenge: str
    remote: SessionWire | None = field(repr=False)


TerminationProver = Callable[[CleanupRequest], Awaitable[TerminationEvidence | None]]


@dataclass(repr=False)
class _Session:
    record: ResourceRecord
    source: SyntheticSource
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    remote: SessionWire | None = None
    live: LiveBrowser | None = None
    profile_revision: int = 0
    stop_acknowledged: bool = False
    start_result: SessionStartResult | None = None


@dataclass(frozen=True, repr=False)
class _Profile:
    reference: ProfileRef
    source: SyntheticSource
    vendor_name: str


class BrowserlessProvider:
    def __init__(
        self,
        *,
        enabled: bool = False,
        deployment: BrowserDeployment | None = None,
        authority: BindingAuthority | None = None,
        credential: Callable[[], str] | None = None,
        connector: BrowserConnector | None = None,
        http: HTTPTransport | None = None,
        termination_prover: TerminationProver | None = None,
        capacity: int = 1,
    ) -> None:
        self._enabled = enabled
        self._deployment = deployment
        self._authority = authority
        self._credential = credential
        self._connector = connector
        self._http = http
        self._prover = termination_prover
        self._wire: BrowserlessWire | None = None
        self._ledger = CapacityLedger(capacity)
        self._sessions: dict[str, _Session] = {}
        self._profiles: dict[str, _Profile] = {}
        # Orphan generation identities only. Never retain captured secret state here.
        self._orphans: set[str] = set()

    @property
    def occupied(self) -> int:
        return self._ledger.occupied

    @property
    def orphan_generations(self) -> tuple[str, ...]:
        return tuple(sorted(self._orphans))

    def _configuration(self, phase: Phase) -> tuple[BrowserDeployment, BindingAuthority]:
        if not self._enabled:
            raise BrowserProviderError("unsupported", phase, "disabled")
        if self._deployment is None or self._authority is None or self._credential is None:
            raise BrowserProviderError("unsupported", phase, "unsupported")
        return self._deployment, self._authority

    async def capabilities(self) -> BrowserCapabilities:
        if not self._enabled or self._deployment is None:
            return BrowserCapabilities(
                transport="playwright",
                cookies=False,
                local_storage=False,
                indexed_db=False,
                immutable_capture=False,
                confirmed_termination=False,
            )
        result = self._deployment.capabilities()
        if self._prover is None:
            result = result.model_copy(update={"confirmed_termination": False})
        return result

    async def _authorize(self, binding: ScopeBinding, phase: Phase) -> SyntheticSource:
        deployment, authority = self._configuration(phase)
        try:
            async with asyncio.timeout(deployment.timeout_seconds):
                current = await authority.current(binding)
                if current != binding:
                    raise BrowserProviderError("stale", phase, "binding")
                source = await authority.source(binding)
        except BrowserProviderError:
            raise
        except TimeoutError:
            raise BrowserProviderError("timeout", phase, "deadline") from None
        except Exception:
            raise BrowserProviderError("denied", phase, "binding") from None
        if not deployment.accepts(source):
            raise BrowserProviderError("denied", phase, "source")
        return source

    def _lookup(self, session: BrowserSessionRef, phase: Phase) -> _Session:
        try:
            record = self._ledger.get(session)
        except ValueError:
            raise BrowserProviderError("denied", phase, "binding") from None
        result = self._sessions[session.session_ref]
        if result.record is not record:
            raise BrowserProviderError("denied", phase, "binding")
        return result

    def _client(self) -> BrowserlessWire:
        deployment, _ = self._configuration("restore")
        if self._wire is None:
            assert self._credential is not None
            try:
                self._wire = BrowserlessWire(
                    deployment,
                    self._credential(),
                    self._http or HTTPSBrowserlessTransport(),
                )
            except BrowserProviderError:
                raise
            except Exception:
                raise BrowserProviderError("denied", "restore", "unauthenticated") from None
        return self._wire

    async def acquire(self, binding: ScopeBinding) -> BrowserSessionRef:
        source = await self._authorize(binding, "acquire")
        session = BrowserSessionRef(session_ref=uuid4().hex, binding=binding)
        try:
            record = self._ledger.reserve(session)
        except ValueError:
            raise BrowserProviderError("overloaded", "acquire", "capacity") from None
        self._sessions[session.session_ref] = _Session(record, source)
        return session

    async def _current(self, session: BrowserSessionRef, item: _Session, phase: Phase) -> None:
        if await self._authorize(session.binding, phase) != item.source:
            raise BrowserProviderError("denied", phase, "source")

    async def _subject(
        self,
        session: BrowserSessionRef,
        item: _Session,
        phase: Phase,
    ) -> SubjectEvidence:
        deployment, authority = self._configuration(phase)
        if item.live is None:
            raise BrowserProviderError("stale", phase, "state")
        await self._current(session, item, phase)
        try:
            async with asyncio.timeout(deployment.timeout_seconds):
                evidence = await authority.subject(session, item.live)
            if not isinstance(evidence, SubjectEvidence):
                raise BrowserProviderError("invalid_response", phase, "subject")
        except BrowserProviderError:
            raise
        except TimeoutError:
            raise BrowserProviderError("timeout", phase, "deadline") from None
        except Exception:
            raise BrowserProviderError("denied", phase, "subject") from None
        await self._current(session, item, phase)
        if evidence.binding != session.binding:
            raise BrowserProviderError("subject_mismatch", phase, "subject")
        return evidence

    async def restore(
        self,
        session: BrowserSessionRef,
        profile: ProfileRef | None,
    ) -> SessionStartResult:
        deployment, _ = self._configuration("restore")
        item = self._lookup(session, "restore")
        async with item.lock:
            if item.record.state != "reserved":
                raise BrowserProviderError("stale", "restore", "state")
            await self._current(session, item, "restore")
            name: str | None = None
            if profile is not None:
                saved = self._profiles.get(profile.generation_ref)
                if (
                    saved is None
                    or saved.reference != profile
                    or profile.binding != session.binding
                    or saved.source != item.source
                ):
                    raise BrowserProviderError("denied", "restore", "binding")
                caps = await self.capabilities()
                if not (caps.cookies and caps.local_storage and caps.indexed_db):
                    raise BrowserProviderError("unsupported", "restore", "unsupported")
                name = saved.vendor_name
                item.profile_revision = profile.profile_revision
            connector = self._connector or PlaywrightConnector()
            try:
                connector.preflight(deployment)
            except BrowserProviderError:
                raise
            except Exception:
                raise BrowserProviderError("unsupported", "restore", "unsupported") from None
            wire = self._client()
            # Set before the first network await. Cancellation must never free this slot.
            item.record.state = "acquiring"
            try:
                if deployment.transport == "cdp":
                    item.remote = await wire.create_session(name)
                    endpoint = item.remote.connect
                else:
                    endpoint = wire.url("/chromium/playwright", websocket=True, profile=name)
                item.record.resource_digest = sha256(
                    (session.session_ref + ":" + endpoint).encode()
                ).hexdigest()
                async with asyncio.timeout(deployment.timeout_seconds):
                    item.live = await connector.connect(endpoint, session, deployment, item.source)
                if item.live.session != session:
                    raise BrowserProviderError("denied", "restore", "binding", sent=True)
                await self._current(session, item, "restore")
                if profile is None:
                    result = SessionStartResult(status="fresh")
                else:
                    subject = await self._subject(session, item, "restore")
                    if subject.subject_digest != profile.subject_digest:
                        raise BrowserProviderError(
                            "subject_mismatch", "restore", "subject", sent=True
                        )
                    result = SessionStartResult(
                        status="subject_verified",
                        subject_digest=subject.subject_digest,
                        evidence_digest=subject.evidence_digest,
                    )
                item.start_result = result
                item.record.state = "active"
                return result
            except BaseException as error:
                self._ledger.quarantine(item.record)
                if isinstance(error, asyncio.CancelledError):
                    raise
                if isinstance(error, BrowserProviderError):
                    raise BrowserProviderError(
                        error.failure.code,
                        "restore",
                        error.reason,
                        sent=True,
                        cleanup=True,
                    ) from None
                raise BrowserProviderError(
                    "timeout" if isinstance(error, TimeoutError) else "unavailable",
                    "restore",
                    "deadline" if isinstance(error, TimeoutError) else "transport",
                    sent=True,
                    cleanup=True,
                ) from None

    async def resolve_live(self, session: BrowserSessionRef) -> LiveBrowser:
        item = self._lookup(session, "observe")
        await self._current(session, item, "observe")
        if item.record.state != "active" or item.live is None:
            raise BrowserProviderError("quarantined", "observe", "state", cleanup=True)
        return item.live

    async def assert_business_authority(
        self, session: BrowserSessionRef, source: DecisionSource
    ) -> LiveBrowser:
        """Infra-only business gate; fresh login assistance never implies authority."""
        item = self._lookup(session, "observe")
        async with item.lock:
            await self._current(session, item, "observe")
            if (
                item.record.state != "active"
                or item.live is None
                or item.start_result is None
                or item.start_result.status != "subject_verified"
            ):
                raise BrowserProviderError("denied", "observe", "subject")
            if (
                source.source_id != item.source.source_id
                or source.fixture_digest != item.source.fixture_digest
                or source.origin not in item.source.origins
            ):
                raise BrowserProviderError("denied", "observe", "source")
            subject = await self._subject(session, item, "observe")
            if subject.subject_digest != item.start_result.subject_digest:
                raise BrowserProviderError("subject_mismatch", "observe", "subject")
            await self._current(session, item, "observe")
            if item.record.state != "active" or item.live is None:
                raise BrowserProviderError("quarantined", "observe", "state", cleanup=True)
            return item.live

    async def capture(self, session: BrowserSessionRef) -> ProfileRef:
        deployment, _ = self._configuration("capture")
        item = self._lookup(session, "capture")
        async with item.lock:
            if item.record.state != "active" or item.live is None:
                raise BrowserProviderError("quarantined", "capture", "state", cleanup=True)
            caps = await self.capabilities()
            if not (
                caps.cookies and caps.local_storage and caps.indexed_db and caps.immutable_capture
            ):
                raise BrowserProviderError("unsupported", "capture", "unsupported")
            before = await self._subject(session, item, "capture")
            try:
                async with asyncio.timeout(deployment.timeout_seconds):
                    raw = await item.live.storage_state()
            except TimeoutError:
                raise BrowserProviderError("timeout", "capture", "deadline") from None
            except Exception:
                raise BrowserProviderError("unsupported", "capture", "unsupported") from None
            state = from_playwright(raw, item.source)
            after = await self._subject(session, item, "capture")
            if before.subject_digest != after.subject_digest:
                raise BrowserProviderError("subject_mismatch", "capture", "subject")
            generation = uuid4().hex
            self._orphans.add(generation)
            try:
                result = await self._client().upload(generation, state)
                validate_upload(result, generation, state)
                verified = await self._subject(session, item, "capture")
                if verified.subject_digest != before.subject_digest:
                    raise BrowserProviderError("subject_mismatch", "capture", "subject", sent=True)
                reference = ProfileRef(
                    generation_ref=generation,
                    binding=session.binding,
                    profile_revision=item.profile_revision + 1,
                    subject_digest=before.subject_digest,
                )
                self._profiles[generation] = _Profile(reference, item.source, generation)
                item.profile_revision += 1
                self._orphans.remove(generation)
                return reference
            except BaseException as error:
                if isinstance(error, asyncio.CancelledError):
                    raise
                if isinstance(error, BrowserProviderError):
                    raise BrowserProviderError(
                        error.failure.code,
                        "capture",
                        error.reason,
                        sent=True,
                        cleanup=True,
                    ) from None
                raise BrowserProviderError(
                    "unavailable",
                    "capture",
                    "transport",
                    sent=True,
                    cleanup=True,
                ) from None

    async def release(self, session: BrowserSessionRef) -> ResourceOutcome:
        return await self.terminate(session)

    async def terminate(self, session: BrowserSessionRef) -> ResourceOutcome:
        deployment, _ = self._configuration("terminate")
        item = self._lookup(session, "terminate")
        # Current grants may be revoked. Cleanup of this exact owned resource must
        # remain possible; the immutable session owner check above is still required.
        async with item.lock:
            record = item.record
            if record.outcome is not None and record.state == "terminated":
                return record.outcome
            if record.state == "reserved":
                return self._ledger.release_reservation(record)
            self._ledger.quarantine(record)
            try:
                async with asyncio.timeout(deployment.timeout_seconds):
                    if item.live is not None:
                        await item.live.close()
                        item.live = None
                    if item.remote is not None and not item.stop_acknowledged:
                        await self._client().stop(item.remote)
                        item.stop_acknowledged = True
                if self._prover is None or record.resource_digest is None:
                    return self._ledger.quarantine(record)
                record.challenge = uuid4().hex
                request = CleanupRequest(
                    session,
                    deployment.manifest_digest,
                    record.resource_digest,
                    record.challenge,
                    item.remote,
                )
                async with asyncio.timeout(deployment.timeout_seconds):
                    proof = await self._prover(request)
                if proof is None:
                    return self._ledger.quarantine(record)
                outcome = self._ledger.finish(record, proof, deployment.manifest_digest)
                if outcome.status == "terminated":
                    item.remote = None
                return outcome
            except asyncio.CancelledError:
                self._ledger.quarantine(record)
                raise
            except Exception:
                return self._ledger.quarantine(record)
