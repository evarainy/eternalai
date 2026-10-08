"""Enterprise source preparation, with no admitted remote protocol revision.

Reservations and authority checks work now. All remote starts, restores and
captures remain unsupported rather than manufacturing server capabilities.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import uuid4

from app.browser_skill.models import (
    BrowserCapabilities,
    BrowserSessionRef,
    DecisionSource,
    ProfileRef,
    ResourceOutcome,
    ScopeBinding,
    SessionStartResult,
)
from app.infra.browser.browserless_wire import BrowserProviderError, Phase
from app.infra.browser.enterprise_connector import EnterpriseConnector
from app.infra.browser.enterprise_manifest import (
    DeploymentView,
    EnterpriseAuthority,
    EnterpriseManifest,
    EnterpriseSyntheticSource,
    require_current_view,
)
from app.infra.browser.enterprise_wire import require_verified_codec
from app.infra.browser.playwright_actions import LiveBrowser
from app.infra.browser.resource_lifecycle import CapacityLedger, ResourceRecord


@dataclass(repr=False)
class _Reservation:
    record: ResourceRecord
    source: EnterpriseSyntheticSource
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Must be set BEFORE the first possibly-sent operation if a codec is admitted.
    dispatch_attempted: bool = False


class EnterpriseProvider:
    def __init__(
        self,
        *,
        enabled: bool = False,
        manifest: EnterpriseManifest | None = None,
        authority: EnterpriseAuthority | None = None,
        credential: Callable[[], str] | None = None,
        capacity: int = 1,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._enabled = enabled
        self._manifest = manifest
        self._authority = authority
        self._credential = credential
        self._clock = clock
        self._ledger = CapacityLedger(capacity)
        self._sessions: dict[str, _Reservation] = {}

    @property
    def occupied(self) -> int:
        return self._ledger.occupied

    def _configuration(self, phase: Phase) -> tuple[EnterpriseManifest, EnterpriseAuthority]:
        if not self._enabled:
            raise BrowserProviderError("unsupported", phase, "disabled")
        if self._manifest is None or self._authority is None or self._credential is None:
            raise BrowserProviderError("unsupported", phase, "unsupported")
        return self._manifest, self._authority

    async def capabilities(self) -> BrowserCapabilities:
        # Caller declarations and fixture evidence cannot create server capabilities.
        return BrowserCapabilities(
            transport=self._manifest.transport if self._manifest else "playwright",
            cookies=False, local_storage=False, indexed_db=False,
            immutable_capture=False, confirmed_termination=False,
        )

    async def _authorize(
        self, binding: ScopeBinding, phase: Phase, *, acceptance: bool = False,
    ) -> tuple[EnterpriseSyntheticSource, DeploymentView]:
        manifest, authority = self._configuration(phase)
        try:
            async with asyncio.timeout(manifest.timeout_seconds):
                view = await authority.deployment(manifest)
                require_current_view(manifest, view, self._clock(), phase, acceptance=acceptance)
                if await authority.current(binding) != binding:
                    raise BrowserProviderError("stale", phase, "binding")
                source = await authority.source(binding)
                if not manifest.accepts(source):
                    raise BrowserProviderError("denied", phase, "source")
                # Recheck after async source resolution, including lease/auth revision.
                if await authority.current(binding) != binding:
                    raise BrowserProviderError("stale", phase, "binding")
                current = await authority.deployment(manifest)
                require_current_view(
                    manifest, current, self._clock(), phase, acceptance=acceptance,
                )
                return source, current
        except BrowserProviderError:
            raise
        except TimeoutError:
            raise BrowserProviderError("timeout", phase, "deadline") from None
        except asyncio.CancelledError:
            raise asyncio.CancelledError from None
        except Exception:
            raise BrowserProviderError("denied", phase, "proof") from None

    def _lookup(self, session: BrowserSessionRef, phase: Phase) -> _Reservation:
        try:
            record = self._ledger.get(session)
            result = self._sessions[session.session_ref]
            if result.record is not record:
                raise ValueError
            return result
        except (ValueError, KeyError):
            raise BrowserProviderError("denied", phase, "binding") from None

    async def _current(
        self, session: BrowserSessionRef, item: _Reservation, phase: Phase,
        *, acceptance: bool = False,
    ) -> DeploymentView:
        source, view = await self._authorize(session.binding, phase, acceptance=acceptance)
        if source is not item.source:
            raise BrowserProviderError("denied", phase, "source")
        return view

    async def acquire(self, binding: ScopeBinding) -> BrowserSessionRef:
        source, _ = await self._authorize(binding, "acquire")
        session = BrowserSessionRef(session_ref=uuid4().hex, binding=binding)
        try:
            record = self._ledger.reserve(session)
        except ValueError:
            raise BrowserProviderError("overloaded", "acquire", "capacity") from None
        self._sessions[session.session_ref] = _Reservation(record, source)
        return session

    async def restore(
        self, session: BrowserSessionRef, profile: ProfileRef | None,
    ) -> SessionStartResult:
        manifest, _ = self._configuration("restore")
        item = self._lookup(session, "restore")
        async with item.lock:
            if item.record.state != "reserved":
                raise BrowserProviderError("stale", "restore", "state")
            if profile is not None and profile.binding != session.binding:
                raise BrowserProviderError("denied", "restore", "binding")
            view = await self._current(session, item, "restore", acceptance=True)
            if profile is not None:
                # No caller generation is resolved into a guessed vendor name/id.
                require_verified_codec(manifest, "profile_create", "restore")
            assert self._credential is not None
            await EnterpriseConnector().connect(manifest, view, self._clock(), self._credential)

    async def capture(self, session: BrowserSessionRef) -> ProfileRef:
        manifest, _ = self._configuration("capture")
        item = self._lookup(session, "capture")
        async with item.lock:
            await self._current(session, item, "capture")
            require_verified_codec(manifest, "profile_create", "capture")

    async def resolve_live(self, session: BrowserSessionRef) -> LiveBrowser:
        item = self._lookup(session, "observe")
        await self._current(session, item, "observe")
        # No live handle can exist without an admitted startup protocol.
        raise BrowserProviderError("unsupported", "observe", "state")

    async def assert_business_authority(
        self, session: BrowserSessionRef, source: DecisionSource,
    ) -> LiveBrowser:
        item = self._lookup(session, "dispatch")
        await self._current(session, item, "dispatch")
        if (
            source.source_id != item.source.source_id
            or source.fixture_digest != item.source.fixture_digest
            or source.origin not in item.source.origins
        ):
            raise BrowserProviderError("denied", "dispatch", "source")
        # Reserved or fresh sessions never confer business/subject authority.
        raise BrowserProviderError("denied", "dispatch", "subject")

    async def _authorize_cleanup(self, session: BrowserSessionRef, phase: Phase) -> None:
        manifest, authority = self._configuration(phase)
        try:
            async with asyncio.timeout(manifest.timeout_seconds):
                claim = await authority.cleanup(session.binding)
            if type(claim) is not ScopeBinding or claim != session.binding:
                raise BrowserProviderError("denied", phase, "binding")
        except BrowserProviderError:
            raise
        except TimeoutError:
            raise BrowserProviderError("timeout", phase, "deadline") from None
        except asyncio.CancelledError:
            raise asyncio.CancelledError from None
        except Exception:
            raise BrowserProviderError("denied", phase, "binding") from None

    async def _close(self, session: BrowserSessionRef, phase: Phase) -> ResourceOutcome:
        item = self._lookup(session, phase)
        async with item.lock:
            if item.record.state == "reserved" and not item.dispatch_attempted:
                # Closed-ledger original claim + independent cleanup authorization
                # permits releasing local capacity without a live business lease.
                await self._authorize_cleanup(session, phase)
                return self._ledger.release_reservation(item.record)
            if item.record.state == "terminated" and item.record.outcome is not None:
                await self._authorize_cleanup(session, phase)
                return item.record.outcome
            await self._current(session, item, phase)
            # No accepted remote identity/proof protocol yet. Never infer success
            # from expiry, disconnect, HTTP acknowledgment, or session-list absence.
            return self._ledger.quarantine(item.record)

    async def release(self, session: BrowserSessionRef) -> ResourceOutcome:
        return await self._close(session, "release")

    async def terminate(self, session: BrowserSessionRef) -> ResourceOutcome:
        return await self._close(session, "terminate")
