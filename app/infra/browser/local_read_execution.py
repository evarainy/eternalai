"""Concrete synthetic detail execution on an owned local Chromium process.

Installation is explicit and initially closed. The durable lease store, current
authorization, actual live SUBJECT and existing Executor/Verifier remain the
authorities. No profile, model result, selected row or local boolean grants access.
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

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
    ExecutionContext,
    ParameterPurpose,
    ParameterRef,
    ReadSpec,
    ScopeBinding,
    SealedParameter,
    TargetRef,
)
from app.browser_skill.publication_contracts import BrowserPublicationManifest
from app.browser_skill.site_rules import RegisteredQueryReadRule
from app.browser_skill.verifier import ReadSpecResolver, failure
from app.infra.browser.local_resource_lifecycle import (
    LocalBrowserResources,
    _Resource,
    local_subject_digest,
)
from app.infra.browser.playwright_actions import LiveBrowser
from app.infra.browser.playwright_observer import PlaywrightObserver
from app.infra.browser.read_execution import RegisteredReadExecution
from app.ports.auth import Principal
from app.ports.browser import DecisionProvider
from app.ports.browser_read_execution import BrowserReadExecutionError, BrowserWorkerCheckpoint
from app.ports.browser_run_store import RunCleanup, RunSnapshot
from app.ports.browser_store import BrowserLeaseClaim, BrowserLeaseStorePort
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


class _FencedDecision:
    def __init__(self, factory: LocalBrowserReadExecutionFactory, state: _Execution) -> None:
        self._factory, self._state = factory, state

    async def decide(
        self,
        request: DecisionRequest,
        context: DecisionCallContext,
    ) -> DecisionResult:
        await self._factory._fence(self._state, renew=True)
        result = await self._factory.decision.decide(request, context)
        await self._factory._fence(self._state, renew=True)
        return result


class LocalBrowserReadExecutionFactory:
    def __init__(
        self,
        source: FixedSyntheticSource,
        resources: LocalBrowserResources,
        binding: BrowserBindingFact,
        decision: DecisionProvider,
        *,
        ttl_seconds: int = 60,
    ) -> None:
        from app.infra.browser.fixed_synthetic_seed import (
            SYNTHETIC_TENANT,
            SYNTHETIC_USER,
            FixedSyntheticSource,
        )

        if (
            type(source) is not FixedSyntheticSource
            or type(resources) is not LocalBrowserResources
            or binding.tenant_id != SYNTHETIC_TENANT
            or binding.ai_user_id != SYNTHETIC_USER
            or binding.target_system != "oa"
            or binding.subject_digest != local_subject_digest(SYNTHETIC_TENANT, SYNTHETIC_USER)
            or not isinstance(source.manifest.site.read_rule, RegisteredQueryReadRule)
            or not callable(getattr(decision, "decide", None))
            or type(ttl_seconds) is not int
            or not 30 <= ttl_seconds <= 300
        ):
            raise ValueError("browser_local_installation_invalid")
        self.source, self.resources = source, resources
        self.binding, self.decision = binding, decision
        self.ttl_seconds = ttl_seconds
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

    def install_authority(
        self,
        *,
        leases: BrowserLeaseStorePort,
        current_auth: BrowserCurrentAuthPort,
        binding_reader: BrowserBindingReaderPort,
    ) -> None:
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
        return (
            self.resources.deployment.enabled
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
        final = await state.checkpoint.refresh()
        if (
            final.admission != previous.admission
            or final.worker_id != previous.worker_id
            or final.worker_epoch != previous.worker_epoch
        ):
            state.cancellation.set()
            raise failure("stale")
        state.run = final

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
            from app.infra.browser.fixed_synthetic_seed import SyntheticDetailArguments

            if (
                private_input.get("schema_version") != "browser.request.input.v2"
                or private_input.get("capability_id") != manifest.capability.capability_id
                or private_input.get("channel") not in {"web", "cli", "api", "mock"}
            ):
                raise BrowserReadExecutionError("denied")
            principal = Principal.model_validate(private_input.get("principal"))
            if (
                principal.ai_user_id != run.owner.user_id
                or principal.org_ctx.tenant_id != run.owner.tenant_id
            ):
                raise BrowserReadExecutionError("denied")
            arguments = SyntheticDetailArguments.model_validate(private_input.get("arguments"))
            auth = BrowserAuthFact(
                run.owner,
                None,
                run.admission.auth_fingerprint,
                run.admission.auth_expires_at,
                run.run_id,
                "verified-session-v1",
            )
            state = _Execution(run, checkpoint, auth, arguments.business_key)
            self._states[run.run_id] = state
            await self._fence(state)
            state.claim = await leases.reserve(
                auth,
                self.binding,
                self.resources.deployment.provider_key,
                ttl_seconds=self.ttl_seconds,
            )
            await self._fence(state)
            state.run = await checkpoint.attach_lease(
                provider_key=self.resources.deployment.provider_key,
                provider_manifest_digest=self.resources.deployment.manifest_digest,
                lease_epoch=state.claim.lease_epoch,
            )
            await self._fence(state)
            state.claim = await leases.start_acquisition(state.claim)
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
            )
            await self._fence(state)
            state.claim = await leases.record_acquired(state.claim, state.resource.acquire_token)
            state.acquired = True
            await self._fence(state, renew=True)
            observer = PlaywrightObserver(self, {self.source.region.region_id: self.source.region})
            state.observer = observer
            await observer.register_page(state.session, state.resource.page)
            await self._fence(state, renew=True)
            key_digest = self.resources._proof.digest(
                "browser-local-admitted-key.v1",
                run.admission.input_digest + arguments.business_key.encode("utf-8"),
            ).hex()
            state.confirmed = ConfirmedBusinessKey(
                confirmation_ref=run.run_id,
                object_type=manifest.site.read_rule.object_type,
                key_digest=key_digest,
                value_ref=manifest.site.read_rule.key_ref,
            )
            context = self._context(state, manifest)
            return RegisteredReadExecution(
                state.session,
                context,
                state.confirmed,
                self,
                observer,
                (self.source.rules,),
                self.source.site,
                _FencedDecision(self, state),
                self._resolver(state),
                self._decisions(state),
                self.source.projector,
            )
        except BaseException as error:
            if state is not None:
                state.cancellation.set()
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
        reference = await self.authority()[0].authorize_resource(state.claim)
        await self._fence(state, renew=True)
        if reference != state.resource.reference or state.resource.live is None:
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
            if session != state.session or skill != manifest.skill or binding != scope:
                raise failure("denied")
            if isinstance(subject, ReadSpec):
                manifest.site_plan().validate_read(subject)
                if subject.business_key != state.confirmed:
                    raise failure("denied")
            elif not self.source.site.permits(skill, subject):
                raise failure("denied")
            await self.resolve_live(session)

        async def resolve(
            ref: ParameterRef,
            purpose: ParameterPurpose,
            binding: ScopeBinding,
            skill_digest: str,
            step_id: str,
        ) -> SealedParameter:
            await self._fence(state, renew=True)
            if (
                purpose != "business_key"
                or ref != manifest.site.read_rule.key_ref
                or binding != scope
                or skill_digest != manifest.skill.digest
                or step_id != manifest.verifier.verifier_id
            ):
                raise failure("denied")
            return SealedParameter(
                state.key,
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

        return ExecutionContext(
            execution_id,
            manifest.skill,
            scope,
            manifest.site.source,
            time.monotonic() + self.ttl_seconds,
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
                return tuple(
                    TargetRef(
                        target_id=item.target_id, candidate_epoch=item.epoch, scope=observed.scope
                    )
                    for item in observed.candidates
                )

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
