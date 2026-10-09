"""Concrete registered detail execution on an owned local Chromium process.

Installation is explicit and initially closed. The durable lease store, current
authorization, actual live SUBJECT and existing Executor/Verifier remain the
authorities. No profile, model result, selected row or local boolean grants access.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from jsonschema import Draft202012Validator
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, async_sessionmaker

from app.browser_skill.executor import DecisionContextFactory
from app.browser_skill.models import (
    ActionCommand,
    BrowserOperationError,
    BrowserSessionRef,
    BrowserSkill,
    ConfirmedBusinessKey,
    DecisionCallContext,
    DecisionRequest,
    DecisionResult,
    DecisionSource,
    DispatchPermit,
    DownloadEvidence,
    ExecutionContext,
    ParameterPurpose,
    ParameterRef,
    ReadSpec,
    ScopeBinding,
    SealedParameter,
    TargetRef,
)
from app.browser_skill.publication_contracts import BrowserPublicationManifest
from app.browser_skill.runtime import _BrowserReadDeferred
from app.browser_skill.site_rules import (
    ExpectedDownload,
    RegisteredQueryReadRule,
    RegisteredReadRule,
)
from app.browser_skill.verifier import ReadSpecResolver, check_liveness, failure
from app.infra.browser.local_file_artifacts import (
    FileArtifactOwner,
    LocalFileArtifacts,
    StoredFileArtifact,
)
from app.infra.browser.local_resource_lifecycle import (
    LocalBrowserResources,
    RegisteredLocalSite,
    _Resource,
    local_subject_digest,
)
from app.infra.browser.playwright_actions import LiveBrowser
from app.infra.browser.playwright_observer import PlaywrightObserver
from app.infra.browser.playwright_web_adapter import OperationAuthority
from app.infra.browser.read_execution import RegisteredReadExecution
from app.infra.browser.systemone_http import DecisionHTTPProvider
from app.infra.persistence.browser.payload_crypto import BrowserPayloadCipher
from app.ports.auth import Principal
from app.ports.browser import DecisionProvider
from app.ports.browser_profile_store import BrowserProfileContext, BrowserProfileStorePort
from app.ports.browser_read_execution import BrowserReadExecutionError, BrowserWorkerCheckpoint
from app.ports.browser_run_store import RunCleanup, RunSnapshot
from app.ports.browser_store import BrowserLeaseClaim, BrowserLeaseError, BrowserLeaseStorePort
from app.ports.credential_vault import (
    BrowserAuthFact,
    BrowserBindingFact,
    BrowserBindingReaderPort,
    BrowserCurrentAuthPort,
)

if TYPE_CHECKING:
    from app.infra.browser.fixed_synthetic_seed import FixedSyntheticSource


@dataclass(slots=True, repr=False)
class _Execution:
    run: RunSnapshot
    checkpoint: BrowserWorkerCheckpoint
    auth: BrowserAuthFact
    key: str
    arguments: dict[str, object] = field(default_factory=dict, repr=False)
    cancellation: asyncio.Event = field(default_factory=asyncio.Event)
    dispatch_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    lease_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    authority_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    claim: BrowserLeaseClaim | None = None
    resource: _Resource | None = None
    session: BrowserSessionRef | None = None
    observer: PlaywrightObserver | None = None
    confirmed: ConfirmedBusinessKey | None = None
    acquired: bool = False
    released: bool = False
    terminal_at: float | None = None
    cleanup_outcome: RunCleanup | None = None
    publication_guard: Callable[[RunSnapshot], Awaitable[None]] | None = None
    operation_context: ExecutionContext | None = None
    authority_counts: dict[str, int] = field(default_factory=dict)
    principal: Principal | None = None
    profile_context: BrowserProfileContext | None = None
    profile_revision: int | None = None
    account_connection: AsyncConnection | None = field(default=None, repr=False)
    account_locks: tuple[int, ...] = ()
    downloads: list[DownloadEvidence] = field(default_factory=list, repr=False)

    def count(self, name: str) -> None:
        self.authority_counts[name] = min(10000, self.authority_counts.get(name, 0) + 1)


class _FencedDecision:
    def __init__(self, factory: LocalBrowserReadExecutionFactory, state: _Execution) -> None:
        self._factory, self._state = factory, state
        self._http_snapshot: dict[str, str | int] = {}

    def http_diagnostic(self) -> dict[str, str | int]:
        return dict(self._http_snapshot)

    async def decide(
        self,
        request: DecisionRequest,
        context: DecisionCallContext,
    ) -> DecisionResult:
        self._http_snapshot = {}
        await self._factory._fence(self._state, renew=True)
        collector: dict[str, str | int] = {}
        try:
            provider = self._factory.decision
            if isinstance(provider, DecisionHTTPProvider):
                result = await provider._decide_with_collector(request, context, collector)
            else:
                result = await provider.decide(request, context)
        finally:
            self._http_snapshot = dict(collector)
        await self._factory._fence(self._state, renew=True)
        return result


class LocalBrowserReadExecutionFactory:
    def __init__(
        self,
        source: FixedSyntheticSource | RegisteredLocalSite,
        resources: LocalBrowserResources,
        binding: BrowserBindingFact,
        decision: DecisionProvider,
        *,
        ttl_seconds: int = 60,
        execution_timeout_seconds: int | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        file_artifacts: LocalFileArtifacts | None = None,
    ) -> None:
        from app.infra.browser.fixed_synthetic_seed import (
            SYNTHETIC_TENANT,
            SYNTHETIC_USER,
            FixedSyntheticSource,
        )

        if execution_timeout_seconds is None:
            execution_timeout_seconds = ttl_seconds
        synthetic = type(source) is FixedSyntheticSource
        registered = type(source) is RegisteredLocalSite
        if (
            not (synthetic or registered)
            or type(resources) is not LocalBrowserResources
            or (synthetic and (
                binding.tenant_id != SYNTHETIC_TENANT
                or binding.ai_user_id != SYNTHETIC_USER
                or binding.target_system != "oa"
                or binding.subject_digest != local_subject_digest(SYNTHETIC_TENANT, SYNTHETIC_USER)
            ))
            or (registered and (session_factory is None or not binding.subject_digest))
            or (isinstance(source, RegisteredLocalSite) and bool(source.download_expectations)
                and not isinstance(file_artifacts, LocalFileArtifacts))
            or not isinstance(source.manifest.site.read_rule, RegisteredQueryReadRule)
            or not callable(getattr(decision, "decide", None))
            or type(ttl_seconds) is not int
            or not 30 <= ttl_seconds <= 300
            or type(execution_timeout_seconds) is not int
            or not 30 <= execution_timeout_seconds <= 300
        ):
            raise ValueError("browser_local_installation_invalid")
        self.source, self.resources = source, resources
        self.binding, self.decision = binding, decision
        self.ttl_seconds = ttl_seconds
        self.execution_timeout_seconds = execution_timeout_seconds
        self._authority: (
            tuple[
                BrowserLeaseStorePort,
                BrowserCurrentAuthPort,
                BrowserBindingReaderPort,
            ]
            | None
        ) = None
        self._states: dict[str, _Execution] = {}
        self._opening: set[str] = set()
        self._lease_reserved: Callable[[RunSnapshot], Awaitable[bool]] | None = None
        self._session_factory = session_factory
        self._file_artifacts = file_artifacts
        self._profiles: BrowserProfileStorePort | None = None
        self._cipher: BrowserPayloadCipher | None = None

    def install_profiles(
        self, store: BrowserProfileStorePort, cipher: BrowserPayloadCipher,
    ) -> None:
        if (self._profiles is not None or self._cipher is not None
                or not callable(getattr(store, "load_active", None))
                or type(cipher) is not BrowserPayloadCipher):
            raise ValueError("browser_local_profile_installation_invalid")
        self._profiles, self._cipher = store, cipher

    @staticmethod
    def _advisory_key(parts: list[str]) -> int:
        digest = hashlib.sha256(json.dumps(parts, separators=(",", ":")).encode()).digest()
        return int.from_bytes(digest[:8], byteorder="big", signed=True)

    async def _account_gate(self, state: _Execution) -> None:
        """Coordinate local Site use across bindings and the existing auth poll.

        The credential-poll key is the exact existing coordinator namespace.
        The subject key additionally serializes two bindings for one business
        account. Both locks stay with this Run until native termination proof.
        """
        if not isinstance(self.source, RegisteredLocalSite):
            return
        factory = self._session_factory
        engine = None if factory is None else factory.kw.get("bind")
        if not isinstance(engine, AsyncEngine):
            raise BrowserReadExecutionError("unavailable")
        poll = self._advisory_key([
            "credential-poll", self.binding.tenant_id, self.binding.ai_user_id,
            self.binding.target_system,
        ])
        account = self._advisory_key([
            "browser-local-account.v1", self.binding.target_system,
            self.binding.subject_digest.hex(),
        ])
        connection = await engine.connect()
        acquired: list[int] = []
        try:
            for key in sorted({poll, account}):
                result = await connection.execute(
                    text("SELECT pg_try_advisory_lock(:key)"), {"key": key},
                )
                await connection.commit()
                if result.scalar_one() is not True:
                    raise _BrowserReadDeferred()
                acquired.append(key)
            state.account_connection = connection
            state.account_locks = tuple(acquired)
        except BaseException:
            await self._unlock_connection(connection, tuple(acquired))
            raise

    @staticmethod
    async def _unlock_connection(connection: AsyncConnection, locks: tuple[int, ...]) -> None:
        try:
            if connection.closed or connection.invalidated:
                return
            for key in reversed(locks):
                unlocked = await connection.execute(
                    text("SELECT pg_advisory_unlock(:key)"), {"key": key},
                )
                await connection.commit()
                if unlocked.scalar_one() is not True:
                    raise BrowserReadExecutionError("unavailable")
        except BaseException:
            await connection.invalidate()
            raise
        finally:
            await connection.close()

    async def release_account_gate(self, state: _Execution) -> None:
        connection, locks = state.account_connection, state.account_locks
        if connection is None:
            return
        state.account_connection = None
        state.account_locks = ()
        await self._unlock_connection(connection, locks)

    def install_authority(
        self,
        *,
        leases: BrowserLeaseStorePort,
        current_auth: BrowserCurrentAuthPort,
        binding_reader: BrowserBindingReaderPort,
        lease_reserved: Callable[[RunSnapshot], Awaitable[bool]] | None = None,
    ) -> None:
        if (lease_reserved is not None and not callable(lease_reserved)):
            raise ValueError("browser_local_authority_installation_invalid")
        if self._authority is not None or not all(
            (
                callable(getattr(leases, "record_acquired", None)),
                callable(getattr(leases, "authorize_resource", None)),
                callable(getattr(current_auth, "check_current", None)),
                callable(getattr(binding_reader, "check_binding", None)),
            )
        ):
            raise ValueError("browser_local_authority_installation_invalid")
        self._authority = (leases, current_auth, binding_reader)
        self._lease_reserved = lease_reserved

    def authority(
        self,
    ) -> tuple[
        BrowserLeaseStorePort,
        BrowserCurrentAuthPort,
        BrowserBindingReaderPort,
    ]:
        if self._authority is None:
            raise BrowserReadExecutionError("unavailable")
        return self._authority

    async def verify_source(self, manifest: BrowserPublicationManifest) -> bool:
        self.authority()
        if isinstance(self.source, RegisteredLocalSite):
            return (
                self.resources.deployment.enabled
                and self._profiles is not None
                and self._cipher is not None
                and manifest == self.source.manifest
                and self.source.registration_digest() == manifest.site.source.fixture_digest
                and self.source.site.bootstrap(manifest.skill) == manifest.site_plan()
                and self.source.rules.site_digest == manifest.site.digest
                and self.source.rules.verifier_digest == manifest.verifier.digest
                and isinstance(manifest.site.read_rule, RegisteredQueryReadRule)
            )
        return (
            self.resources.deployment.enabled
            and manifest.source_kind == "registered_synthetic_fixture"
            and manifest == self.source.manifest
            and hashlib.sha256(self.source.html).hexdigest() == manifest.site.source.fixture_digest
            and self.source.site.bootstrap(manifest.skill) == manifest.site_plan()
            and self.source.rules.site_digest == manifest.site.digest
            and self.source.rules.verifier_digest == manifest.verifier.digest
            and isinstance(manifest.site.read_rule, RegisteredQueryReadRule)
            and self.source.rules.read.object_type is not None
        )

    @staticmethod
    def claim_matches_run(claim: BrowserLeaseClaim, run: RunSnapshot) -> bool:
        return (
            claim.auth.owner == run.owner
            and claim.auth.authorization_run_id == run.run_id
            and claim.auth.fingerprint == run.admission.auth_fingerprint
            and claim.auth.expires_at == run.admission.auth_expires_at
            and claim.auth.evidence_version == "verified-session-v1"
            and claim.binding.binding_id == run.admission.binding_id
            and claim.binding.binding_revision == run.admission.binding_revision
            and claim.binding.target_system == run.admission.target_system
            and (run.lease_epoch is None or claim.lease_epoch == run.lease_epoch)
            and (run.provider_key is None or claim.provider_key == run.provider_key)
        )

    def state_for_cleanup(self, run: RunSnapshot) -> _Execution | None:
        state = self._states.get(run.run_id)
        if state is not None and (
            state.run.admission != run.admission
            or (state.claim is not None and not self.claim_matches_run(state.claim, run))
        ):
            raise BrowserReadExecutionError("denied")
        return state

    async def _fence(self, state: _Execution, *, renew: bool = False) -> None:
        async with state.authority_lock:
            await self._fence_locked(state, renew=renew)

    async def _fence_locked(self, state: _Execution, *, renew: bool) -> None:
        state.count("factory_fence_attempts")
        leases, auth, bindings = self.authority()
        previous = state.run
        if state.cancellation.is_set() or state.released:
            raise failure("cancelled")
        fresh = await state.checkpoint.refresh()
        if (
            fresh.admission != previous.admission
            or fresh.worker_id != previous.worker_id
            or fresh.worker_epoch != previous.worker_epoch
            or fresh.worker_deadline is None
            or fresh.worker_deadline <= datetime.now(UTC)
        ):
            state.cancellation.set()
            raise failure("stale")
        state.run = fresh
        if state.claim is None or not renew:
            await auth.check_current(state.auth)
            await bindings.check_binding(self.binding)
        if state.claim is not None:
            if not self.claim_matches_run(state.claim, fresh):
                raise failure("stale")
            if renew:
                async with state.lease_lock:
                    state.claim = await leases.renew(state.claim, ttl_seconds=self.ttl_seconds)
            elif state.claim.deadline <= datetime.now(UTC):
                raise failure("stale")
        if state.publication_guard is not None:
            await state.publication_guard(state.run)
        final = await state.checkpoint.refresh()
        if (
            final.admission != previous.admission
            or final.worker_id != previous.worker_id
            or final.worker_epoch != previous.worker_epoch
        ):
            state.cancellation.set()
            raise failure("stale")
        state.run = final
        state.count("factory_fence_successes")

    async def open(
        self,
        run: RunSnapshot,
        manifest: BrowserPublicationManifest,
        private_input: Mapping[str, object],
        checkpoint: BrowserWorkerCheckpoint,
    ) -> RegisteredReadExecution:
        leases, _, _ = self.authority()
        for run_id, previous in tuple(self._states.items()):
            if previous.terminal_at is not None and time.monotonic() - previous.terminal_at > 120:
                del self._states[run_id]
        if (
            run.run_id in self._opening
            or run.run_id in self._states
            or len(self._states) >= 32
            or run.phase != "acquiring"
            or run.effect != "not_sent"
            or run.verification == "verified"
            or run.cancel_requested
            or run.lease_epoch is not None
            or run.provider_key is not None
            or run.admission.publication_digest.hex() != manifest.digest
            or run.owner.tenant_id != self.binding.tenant_id
            or run.owner.user_id != self.binding.ai_user_id
            or run.admission.binding_id != self.binding.binding_id
            or run.admission.binding_revision != self.binding.binding_revision
            or run.admission.target_system != self.binding.target_system
        ):
            raise BrowserReadExecutionError("denied")
        self._opening.add(run.run_id)
        state: _Execution | None = None
        try:
            if not await self.verify_source(manifest):
                raise BrowserReadExecutionError("denied")
            if (
                private_input.get("schema_version") != "browser.request.input.v2"
                or private_input.get("capability_id") != manifest.capability.capability_id
                or private_input.get("channel") not in ("web", "cli", "api", "mock")
            ):
                raise BrowserReadExecutionError("denied")
            try:
                principal = Principal.model_validate(private_input.get("principal"))
                if isinstance(self.source, RegisteredLocalSite):
                    arguments = private_input.get("arguments")
                    key_name = manifest.site.read_rule.key_ref.name
                    if (type(arguments) is not dict or not isinstance(arguments.get(key_name), str)
                            or not 1 <= len(arguments[key_name]) <= 256
                            or len(json.dumps(
                                arguments, ensure_ascii=False, allow_nan=False,
                            ).encode()) > 16_384
                            or not Draft202012Validator(
                                manifest.capability.input_schema,
                            ).is_valid(arguments)):
                        raise ValueError("invalid registered arguments")
                    business_key = arguments[key_name]
                else:
                    from app.infra.browser.fixed_synthetic_seed import SyntheticDetailArguments

                    arguments = SyntheticDetailArguments.model_validate(
                        private_input.get("arguments"),
                    )
                    business_key = arguments.business_key
                    arguments = arguments.model_dump()
            except (ValidationError, ValueError, TypeError, OverflowError, UnicodeError):
                raise BrowserReadExecutionError("denied") from None
            if (
                principal.ai_user_id != run.owner.user_id
                or principal.org_ctx.tenant_id != run.owner.tenant_id
            ):
                raise BrowserReadExecutionError("denied")
            invocation = private_input.get("invocation")
            if invocation is not None and (
                type(invocation) is not dict
                or set(invocation) != {
                    "caller", "entrypoint", "operator_user_id", "represented_user_id",
                }
                or invocation.get("caller") != "authenticated_session_v1"
                or invocation.get("entrypoint") not in {
                    "runtime_chat", "structured_internal",
                }
                or invocation.get("operator_user_id") != run.owner.user_id
                or invocation.get("represented_user_id") != run.owner.user_id
            ):
                # An audit assertion in the sealed admission is never a grant.
                raise BrowserReadExecutionError("denied")
            auth = BrowserAuthFact(
                run.owner,
                None,
                run.admission.auth_fingerprint,
                run.admission.auth_expires_at,
                run.run_id,
                "verified-session-v1",
            )
            state = _Execution(
                run, checkpoint, auth, business_key,
                arguments=dict(arguments), principal=principal,
            )
            self._states[run.run_id] = state
            # Register cleanup ownership before worker-driver startup. Invalid
            # private input above never starts or allocates anything.
            await self._account_gate(state)
            await self.resources.ensure_started()
            await self._fence(state)
            try:
                state.claim = await leases.reserve(
                    auth,
                    self.binding,
                    self.resources.deployment.provider_key,
                    ttl_seconds=self.ttl_seconds,
                )
            except BrowserLeaseError as error:
                if error.code == "browser_capacity_exhausted":
                    raise _BrowserReadDeferred() from None
                if (error.code == "browser_binding_busy" and self._lease_reserved is not None
                        and not await self._lease_reserved(state.run)):
                    # Another Run owns the binding. Our first reservation has
                    # not succeeded; retain waiting eligibility without replay.
                    raise _BrowserReadDeferred() from None
                # This Run's old pre-attach reservation needs the existing
                # failure/independent cleanup path, never repeated acquisition.
                raise
            await self._fence(state)
            state.run = await checkpoint.attach_lease(
                provider_key=self.resources.deployment.provider_key,
                provider_manifest_digest=self.resources.deployment.manifest_digest,
                lease_epoch=state.claim.lease_epoch,
            )
            await self._fence(state)
            state.claim = await leases.start_acquisition(state.claim)
            await self._fence(state, renew=True)
            restored: dict[str, object] | None = None
            if isinstance(self.source, RegisteredLocalSite):
                if self._profiles is None or self._cipher is None:
                    raise BrowserReadExecutionError("unavailable")
                state.profile_context = BrowserProfileContext(
                    principal, state.claim, run.task_id, run.run_id,
                    state.run.worker_epoch,
                )
                head = await self._profiles.load_active(state.profile_context)
                state.profile_revision = head.revision
                if head.active is not None:
                    from app.infra.browser.profile_codec import checked_playwright_state

                    if head.active.fact.origin_digest != hashlib.sha256(
                        json.dumps(self.source.origins, separators=(",", ":"),
                                   ensure_ascii=True).encode("ascii"),
                    ).digest():
                        raise BrowserReadExecutionError("denied")
                    envelope = self._cipher.decrypt_profile(
                        run.owner, self.binding, head.active,
                    )
                    encoded_profile = json.dumps(
                        envelope, sort_keys=True, separators=(",", ":"),
                        ensure_ascii=True, allow_nan=False,
                    ).encode("ascii")
                    if (len(encoded_profile) != head.active.fact.captured_bytes
                            or hashlib.sha256(encoded_profile).digest()
                            != head.active.fact.projection_digest):
                        raise BrowserReadExecutionError("denied")
                    restored = checked_playwright_state(
                        envelope.get("storage_state"), self.source,
                    )
                await self._fence(state, renew=True)
            scope = ScopeBinding(
                owner=run.owner,
                binding_id=self.binding.binding_id,
                binding_revision=self.binding.binding_revision,
                authorization_revision=None,
                authorization_run_id=run.run_id,
                evidence_version="verified-session-v1",
                lease_epoch=state.claim.lease_epoch,
            )
            state.session = BrowserSessionRef(session_ref=secrets.token_hex(16), binding=scope)

            async def guard() -> None:
                await self._fence(state, renew=True)

            async def subject_guard() -> None:
                await self._fence(state)

            state.resource = await self.resources.launch(
                state.claim,
                state.session,
                self.source,
                guard=guard,
                subject_guard=subject_guard,
                storage_state=restored,
            )
            if isinstance(self.source, RegisteredLocalSite):
                status = await self.resources.registered_subject_status(state.resource)
                if status == "wrong_account":
                    raise BrowserReadExecutionError("denied")
                if status == "logged_out":
                    if self.source.login is None:
                        raise BrowserReadExecutionError("unsupported")
                    # Exactly one audited Login Skill attempt. It receives only
                    # the trusted binding and a current authority guard.
                    await guard()
                    await asyncio.wait_for(
                        self.source.login(state.resource.page, self.binding, guard),
                        self.resources.deployment.timeout_seconds,
                    )
                    await guard()
                    if await self.resources.registered_subject_status(state.resource) != "matched":
                        raise BrowserReadExecutionError("denied")
                await self.resources.check_subject(state.claim, state.resource.reference)
            await self._fence(state)
            state.claim = await leases.record_acquired(state.claim, state.resource.acquire_token)
            state.acquired = True
            await self._fence(state, renew=True)
            if isinstance(self.source, RegisteredLocalSite):
                state.profile_context = BrowserProfileContext(
                    principal, state.claim, run.task_id, run.run_id,
                    state.run.worker_epoch,
                )
            observer = PlaywrightObserver(self, {self.source.region.region_id: self.source.region})
            state.observer = observer
            await observer.register_page(state.session, state.resource.page)
            await self._fence(state, renew=True)
            key_digest = self.resources._proof.digest(
                "browser-local-admitted-key.v1",
                run.admission.input_digest + business_key.encode("utf-8"),
            ).hex()
            state.confirmed = ConfirmedBusinessKey(
                confirmation_ref=run.run_id,
                object_type=manifest.site.read_rule.object_type,
                key_digest=key_digest,
                value_ref=manifest.site.read_rule.key_ref,
            )
            context = self._context(state, manifest)
            assert state.session is not None
            bound_session = state.session

            def bind_publication_guard(
                guard: Callable[[RunSnapshot], Awaitable[None]],
            ) -> ExecutionContext:
                if state.publication_guard is not None:
                    raise BrowserReadExecutionError("denied")
                state.publication_guard = guard
                return context

            def bind_operation_authority(bound: ExecutionContext) -> OperationAuthority:
                if (state.publication_guard is None or state.operation_context is not None
                        or self._state(bound_session) is not state
                        or bound.execution_id != context.execution_id
                        or bound.skill != context.skill
                        or bound.expected_binding != context.expected_binding
                        or bound.source != context.source
                        or bound.navigation_origins != context.navigation_origins
                        or bound.cancellation is not context.cancellation
                        or bound.deadline_monotonic != context.deadline_monotonic):
                    raise failure("denied")
                state.operation_context = bound

                async def operation_authority(
                    session: BrowserSessionRef, actual: ExecutionContext,
                    subject: ActionCommand | ReadSpec,
                ) -> LiveBrowser:
                    if actual is not bound or state.operation_context is not actual:
                        raise failure("denied")
                    check_liveness(actual)
                    self._check_subject(state, manifest, session, actual.skill,
                                        subject, actual.expected_binding)
                    if actual.source != manifest.site.source:
                        raise failure("denied")
                    live = await self.resolve_live(session)
                    # resolve_live contains both renewing fences and the real
                    # resource authorization, including its post-subject locks.
                    if self._state(session) is not state or live.session != session:
                        raise failure("denied")
                    self._check_subject(state, manifest, session, actual.skill,
                                        subject, actual.expected_binding)
                    check_liveness(actual)
                    return live

                return operation_authority

            async def write_reservation_authority(
                session: BrowserSessionRef, actual: ExecutionContext,
                command: ActionCommand,
            ) -> None:
                if (
                    self._state(session) is not state
                    or actual is not state.operation_context
                    or command.binding != actual.expected_binding
                    or command.step.effect != "may_write"
                ):
                    raise failure("denied")
                await self._fence(state, renew=True)
                self._check_subject(
                    state, manifest, session, actual.skill,
                    command, actual.expected_binding,
                )
                await self.resolve_live(session)
                await checkpoint.assert_write_reservation(command)
                await self._fence(state, renew=True)
                self._check_subject(
                    state, manifest, session, actual.skill,
                    command, actual.expected_binding,
                )

            async def download_sink(
                source_path: Path, rule: ExpectedDownload,
            ) -> StoredFileArtifact:
                artifacts = self._file_artifacts
                if (
                    artifacts is None
                    or not isinstance(self.source, RegisteredLocalSite)
                    or rule not in self.source.download_expectations
                    or state.session is None
                    or state.run.admission.publication_digest.hex() != manifest.digest
                ):
                    raise failure("denied")
                await self._fence(state, renew=True)
                verifier = self.source.download_content_verifier
                if (
                    verifier is None
                    or verifier.rule_digest != rule.content_rule_digest
                    or await verifier.matches(source_path, state.key, rule) is not True
                ):
                    raise failure("denied")
                await self._fence(state, renew=True)
                owner = FileArtifactOwner(
                    tenant_id=state.run.owner.tenant_id,
                    ai_user_id=state.run.owner.user_id,
                    session_id=state.run.owner.session_id,
                    target_system=state.run.admission.target_system,
                    binding_id=state.run.admission.binding_id,
                    binding_revision=state.run.admission.binding_revision,
                    task_id=state.run.task_id,
                    run_id=state.run.run_id,
                )
                artifact = await artifacts.seal_download(
                    source_path, owner, media_type=rule.media_type,
                    ttl_seconds=rule.ttl_seconds, max_bytes=rule.max_bytes,
                )
                await self._fence(state, renew=True)
                if artifact.owner != owner:
                    raise failure("denied")
                state.downloads.append(DownloadEvidence(
                    step_id=rule.step_id,
                    reference=artifact.reference,
                    owner=state.run.owner,
                    target_system=owner.target_system,
                    binding_id=owner.binding_id,
                    binding_revision=owner.binding_revision,
                    task_id=owner.task_id,
                    run_id=owner.run_id,
                    media_type=artifact.media_type,
                    size_bytes=artifact.size_bytes,
                    sha256_digest=artifact.sha256.hex(),
                    expires_at=artifact.expires_at,
                ))
                return artifact

            async def verify_downloads(
                session: BrowserSessionRef, context: ExecutionContext,
                expected: tuple[ExpectedDownload, ...],
            ) -> str:
                artifacts = self._file_artifacts
                source = self.source
                if (
                    artifacts is None
                    or not isinstance(source, RegisteredLocalSite)
                    or context is not state.operation_context
                    or self._state(session) is not state
                    or not expected
                    or expected != source.download_expectations
                    or len(state.downloads) != len(expected)
                    or len({item.step_id for item in state.downloads})
                    != len(state.downloads)
                ):
                    raise failure("denied")
                verifier = source.download_content_verifier
                if verifier is None:
                    raise failure("denied")
                await self._fence(state, renew=True)
                by_step = {item.step_id: item for item in state.downloads}
                facts: list[dict[str, object]] = []
                for rule in expected:
                    item = by_step.get(rule.step_id)
                    if (
                        item is None or item.owner != state.run.owner
                        or item.target_system != state.run.admission.target_system
                        or item.binding_id != state.run.admission.binding_id
                        or item.binding_revision != state.run.admission.binding_revision
                        or item.task_id != state.run.task_id
                        or item.run_id != state.run.run_id
                        or item.media_type != rule.media_type
                        or item.size_bytes > rule.max_bytes
                        or verifier.rule_digest != rule.content_rule_digest
                        or item.expires_at <= datetime.now(UTC)
                    ):
                        raise failure("denied")
                    owner = FileArtifactOwner(
                        tenant_id=item.owner.tenant_id,
                        ai_user_id=item.owner.user_id,
                        session_id=item.owner.session_id,
                        target_system=item.target_system,
                        binding_id=item.binding_id,
                        binding_revision=item.binding_revision,
                        task_id=item.task_id,
                        run_id=item.run_id,
                    )
                    payload = await artifacts.read(item.reference, owner)
                    if (
                        len(payload) != item.size_bytes
                        or hashlib.sha256(payload).hexdigest() != item.sha256_digest
                        or await verifier.matches_bytes(payload, state.key, rule) is not True
                    ):
                        raise failure("denied")
                    await self._fence(state, renew=True)
                    facts.append({
                        "step_id": item.step_id,
                        "reference_digest": hashlib.sha256(
                            item.reference.encode("ascii")
                        ).hexdigest(),
                        "sha256": item.sha256_digest,
                        "media_type": item.media_type,
                        "size_bytes": item.size_bytes,
                        "expires_at": item.expires_at.isoformat(),
                        "content_rule_digest": rule.content_rule_digest,
                    })
                encoded = json.dumps(
                    {"run_id": state.run.run_id, "files": facts},
                    sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                ).encode("ascii")
                return hashlib.sha256(b"browser_download_verified.v1\n" + encoded).hexdigest()

            decision = _FencedDecision(self, state)
            return RegisteredReadExecution(
                state.session,
                context,
                state.confirmed,
                self,
                observer,
                (self.source.rules,),
                self.source.site,
                decision,
                self._resolver(state),
                self._decisions(state),
                self.source.projector,
                bind_publication_guard,
                bind_operation_authority,
                lambda: dict(state.authority_counts),
                decision.http_diagnostic,
                registered_popup_source=(
                    self.source if isinstance(self.source, RegisteredLocalSite) else None
                ),
                write_reservation_authority=(
                    write_reservation_authority
                    if isinstance(self.source, RegisteredLocalSite) else None
                ),
                download_sink=(
                    download_sink if isinstance(self.source, RegisteredLocalSite)
                    and self.source.download_expectations else None
                ),
                expected_downloads=(
                    self.source.download_expectations
                    if isinstance(self.source, RegisteredLocalSite) else ()
                ),
                verify_downloads=(
                    verify_downloads if isinstance(self.source, RegisteredLocalSite)
                    and self.source.download_expectations else None
                ),
            )
        except BaseException as error:
            if state is not None:
                state.cancellation.set()
                if (isinstance(error, _BrowserReadDeferred)
                        and state.claim is None and state.resource is None
                        and self._states.get(run.run_id) is state):
                    self._states.pop(run.run_id)
                if state.claim is None and state.resource is None:
                    await self.release_account_gate(state)
                if state.resource is None and state.claim is not None:
                    state.resource = next(
                        (
                            item
                            for item in self.resources._resources.values()
                            if self.claim_matches_run(item.claim, state.run)
                        ),
                        None,
                    )
            if isinstance(
                error, (BrowserReadExecutionError, BrowserOperationError, asyncio.CancelledError)
            ):
                raise
            if isinstance(error, TimeoutError):
                raise BrowserReadExecutionError("timeout") from None
            raise BrowserReadExecutionError("unavailable") from None
        finally:
            self._opening.discard(run.run_id)

    def _state(self, session: BrowserSessionRef) -> _Execution:
        state = next((state for state in self._states.values() if state.session == session), None)
        if state is None or state.claim is None or state.resource is None or not state.acquired:
            raise failure("denied")
        return state

    async def resolve_live(self, session: BrowserSessionRef) -> LiveBrowser:
        state = self._state(session)
        await self._fence(state, renew=True)
        assert state.claim is not None and state.resource is not None
        state.count("factory_resource_authorization_attempts")
        reference = await self.authority()[0].authorize_resource(state.claim)
        state.count("factory_resource_authorization_successes")
        await self._fence(state, renew=True)
        if not self.resources.driver.ready:
            raise failure("unavailable")
        if (self._state(session) is not state
                or reference != state.resource.reference or state.resource.live is None):
            raise failure("denied")
        return state.resource.live

    async def assert_business_authority(
        self,
        session: BrowserSessionRef,
        source: DecisionSource,
    ) -> LiveBrowser:
        if source != self.source.manifest.site.source:
            raise failure("denied")
        return await self.resolve_live(session)

    def _check_subject(
        self, state: _Execution, manifest: BrowserPublicationManifest,
        session: BrowserSessionRef, skill: BrowserSkill,
        subject: ActionCommand | ReadSpec, binding: ScopeBinding,
    ) -> None:
        """Shared static subject checks for original and operation callbacks."""
        if (self._state(session) is not state or state.session != session
                or skill != manifest.skill or binding != session.binding):
            raise failure("denied")
        if subject.binding != binding:
            raise failure("stale")
        if isinstance(subject, ReadSpec):
            manifest.site_plan().validate_read(subject)
            if subject.business_key != state.confirmed:
                raise failure("denied")
        elif not self.source.site.permits(skill, subject):
            raise failure("denied")

    def _context(self, state: _Execution, manifest: BrowserPublicationManifest) -> ExecutionContext:
        assert state.session is not None
        scope = state.session.binding

        async def current_binding(session: BrowserSessionRef) -> ScopeBinding:
            if self._state(session) is not state:
                raise failure("denied")
            await self._fence(state, renew=True)
            return scope

        async def authorize(
            session: BrowserSessionRef,
            skill: BrowserSkill,
            subject: ActionCommand | ReadSpec,
            binding: ScopeBinding,
        ) -> None:
            if binding != scope:
                raise failure("denied")
            self._check_subject(state, manifest, session, skill, subject, binding)
            await self.resolve_live(session)

        async def resolve(
            ref: ParameterRef,
            purpose: ParameterPurpose,
            binding: ScopeBinding,
            skill_digest: str,
            step_id: str,
        ) -> SealedParameter:
            if state.session is None:
                raise failure("denied")
            await self.resolve_live(state.session)
            if binding != scope or skill_digest != manifest.skill.digest:
                raise failure("denied")
            step = next(
                (item for item in manifest.skill.steps if item.step_id == step_id), None,
            )
            if purpose == "business_key":
                permitted = (
                    ref == manifest.site.read_rule.key_ref
                    and (step_id == manifest.verifier.verifier_id or (
                        step is not None and step.locator.kind == "business_key"
                    ))
                )
            elif purpose == "expected_field":
                permitted = (
                    step_id == manifest.verifier.verifier_id
                    and isinstance(manifest.site.read_rule, RegisteredReadRule)
                    and any(field.value_ref == ref
                            for field in manifest.site.read_rule.fields)
                )
            else:
                permitted = step is not None and {
                    "fill_value": step.value_ref,
                    "option_values": step.option_ref,
                    "navigation_url": step.url_ref,
                }.get(purpose) == ref
            if not permitted or ref.name not in manifest.skill.parameters:
                raise failure("denied")
            value = state.arguments.get(ref.name)
            if purpose == "option_values":
                if (
                    type(value) is not list or not 1 <= len(value) <= 255
                    or any(type(item) is not str or not item or len(item.encode("utf-8")) > 512
                           for item in value)
                    or len(set(value)) != len(value)
                ):
                    raise failure("denied")
                sealed_value: str | tuple[str, ...] = tuple(value)
            else:
                if type(value) is not str or not value or len(value.encode("utf-8")) > 2048:
                    raise failure("denied")
                sealed_value = value
            return SealedParameter(
                sealed_value,
                ref=ref,
                purpose=purpose,
                binding=binding,
                skill_digest=skill_digest,
                step_id=step_id,
            )

        execution_id = secrets.token_hex(16)

        @asynccontextmanager
        async def barrier(
            session: BrowserSessionRef,
            command: ActionCommand,
            binding: ScopeBinding,
        ) -> AsyncIterator[DispatchPermit]:
            async with state.dispatch_lock:
                await authorize(session, manifest.skill, command, binding)

                def begin() -> None:
                    if (
                        state.cancellation.is_set()
                        or state.released
                        or state.claim is None
                        or state.claim.deadline <= datetime.now(UTC)
                        or state.run.worker_deadline is None
                        or state.run.worker_deadline <= datetime.now(UTC)
                    ):
                        raise failure("stale")

                yield DispatchPermit(execution_id, command, begin)
                # Recheck after the actual send/DOM work before returning success.
                await self._fence(state)

        return ExecutionContext(
            execution_id,
            manifest.skill,
            scope,
            manifest.site.source,
            time.monotonic() + self.execution_timeout_seconds,
            state.cancellation,
            manifest.site.navigation_origins,
            current_binding,
            authorize,
            resolve,
            barrier,
        )

    def _resolver(self, state: _Execution) -> ReadSpecResolver:
        async def resolve(
            session: BrowserSessionRef,
            context: ExecutionContext,
            binding: ScopeBinding,
        ) -> ReadSpec:
            await self._fence(state, renew=True)
            if (
                session != state.session
                or binding != session.binding
                or context.skill != self.source.manifest.skill
                or state.confirmed is None
            ):
                raise failure("denied")
            return ReadSpec(
                binding=binding,
                business_key=state.confirmed,
                verifier_id=context.skill.verifier_id,
                verifier_digest=context.skill.verifier_digest,
                fields=self.source.manifest.output.field_ids,
                mode="independent_query_detail_v1",
            )

        return resolve

    def _decisions(self, state: _Execution) -> DecisionContextFactory:
        def create(context: ExecutionContext, request: DecisionRequest) -> DecisionCallContext:
            def targets() -> tuple[TargetRef, ...]:
                observer, session = state.observer, state.session
                if observer is None or session is None or state.cancellation.is_set():
                    return ()
                observed = observer._observed.get((session.session_ref, request.scope.region_id))
                page = observer._pages.get(session.session_ref)
                if (
                    observed is None
                    or observed.scope != request.scope
                    or page is None
                    or page.epoch != request.scope.page_epoch
                    or page.page.is_closed()
                ):
                    return ()
                registered = tuple(
                    TargetRef(
                        target_id=item.target_id, candidate_epoch=item.epoch, scope=observed.scope
                    )
                    for item in observed.candidates
                )
                requested = tuple(item.ref for item in request.candidates)
                if not requested or any(ref not in registered for ref in requested):
                    return ()
                return requested

            if context.skill != self.source.manifest.skill or not targets():
                raise failure("stale")
            return DecisionCallContext(
                context.deadline_monotonic,
                self.source.manifest.site.decision_manifest,
                context.source,
                targets,
                self.source.manifest.site.decision_budget,
                context.cancellation,
            )

        return create
