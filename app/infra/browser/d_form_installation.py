"""Trusted local installation of the finite D form adapter; no startup DB writes."""

from __future__ import annotations

import hmac
from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, AsyncIterator, Literal, Protocol, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.browser_skill.models import BrowserOwner
from app.browser_skill.publication_contracts import BrowserPublicationManifest
from app.infra.auth.crypto import PrincipalSessionBinder
from app.infra.auth.postgresql import PostgreSQLCredentialStore
from app.infra.browser.chat_inputs import PostgreSQLBrowserChatBindingResolver
from app.infra.browser.composition import (
    BrowserPublicationGrants,
    BrowserVerticalComponents,
    BrowserVerticalDependencies,
    build_browser_vertical,
)
from app.infra.browser.d_form_execution import DFormOptions, DFormRuntime
from app.infra.browser.d_form_manifest import build_fill_stop_manifest
from app.infra.browser.oa_browser_login import RunBoundOACredentialVault
from app.infra.persistence.browser.crypto import BrowserResourceCipher
from app.infra.persistence.browser.leases import (
    _B,
    _WRITER_LOCK,
    BrowserProviderPool,
    BrowserProviderPoolRegistry,
    PostgreSQLBrowserLeaseStore,
    Row,
    _key,
)
from app.infra.persistence.capability_registry.repository import PostgreSQLCapabilityRegistry
from app.ports.auth import Principal
from app.ports.browser_chat import BrowserChatBindingResolverPort, BrowserChatError
from app.ports.browser_publication_store import BrowserPublicationReviewPort
from app.ports.browser_run_store import RunCleanup, RunSnapshot
from app.ports.browser_store import (
    BrowserBindingKey,
    BrowserCleanupAuthorityPort,
    BrowserLeaseError,
)
from app.ports.capability_registry import CapabilitySpec
from app.ports.credential_vault import BrowserBindingFact
from app.ports.human_gate import HumanGateDecisionRecord, HumanGateRequest
from app.ports.policy_guard import PolicyGuardPort
from app.ports.task_store import SessionStorePort
from app.ports.trace import TracePort

if TYPE_CHECKING:
    from app.composition import ProductionComponents


class DFormCleanupGrant(BrowserCleanupAuthorityPort, Protocol):
    async def authorize_run(self, session: AsyncSession, run: RunSnapshot) -> None: ...


@dataclass(frozen=True, slots=True, repr=False)
class DFormInstallationDependencies:
    session_factory: async_sessionmaker[AsyncSession]
    capability_registry: PostgreSQLCapabilityRegistry
    session_binder: PrincipalSessionBinder
    policy: PolicyGuardPort
    binding: BrowserBindingFact
    origin: str = field(repr=False)
    options: DFormOptions
    provider_pools: BrowserProviderPoolRegistry
    publication_grants: BrowserPublicationGrants
    publication_review: BrowserPublicationReviewPort
    cleanup_authority: DFormCleanupGrant
    resource_cipher: BrowserResourceCipher
    payload_keys: Mapping[str, bytes] = field(repr=False)
    active_payload_key_id: str
    request_digest_keys: Mapping[str, bytes] = field(repr=False)
    active_request_digest_key_id: str
    input_digest_key: bytes = field(repr=False)
    result_digest_key: bytes = field(repr=False)
    chat_bindings: BrowserChatBindingResolverPort
    trace: TracePort
    sessions: SessionStorePort
    worker_id: str
    credential_store: PostgreSQLCredentialStore | None = field(default=None, repr=False)
    fixed_owner: bool = False
    # Configured category candidates for the AgentScope chat path only.
    category_options: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True, repr=False)
class DFormProductionInputs:
    """Existing operator facts; production authorities are assembled by the host.

    The trusted CLI factory owns these objects and key lifetimes. This input
    neither discovers a D browser nor creates bindings, grants or registrations.
    """

    binding: BrowserBindingFact
    origin: str = field(repr=False)
    options: DFormOptions
    provider_pools: BrowserProviderPoolRegistry
    publication_grants: BrowserPublicationGrants
    publication_review: BrowserPublicationReviewPort
    cleanup_authority: DFormCleanupGrant
    payload_keys: Mapping[str, bytes] = field(repr=False)
    active_payload_key_id: str
    resource_keys: Mapping[str, bytes] = field(repr=False)
    active_resource_key_id: str
    request_digest_keys: Mapping[str, bytes] = field(repr=False)
    active_request_digest_key_id: str
    input_digest_key: bytes = field(repr=False)
    result_digest_key: bytes = field(repr=False)
    fixed_owner: bool = False
    category_options: tuple[str, ...] | None = None


def build_d_form_production_installation(
    components: ProductionComponents,
    inputs: DFormProductionInputs,
) -> DFormInstallationDependencies:
    """Bind existing D configuration without reading or writing runtime resources."""
    manifest = build_fill_stop_manifest(origin=inputs.origin)
    return DFormInstallationDependencies(
        session_factory=components.browser_session_factory,
        capability_registry=components.browser_capability_registry,
        session_binder=components.session_binder,
        policy=components.browser_policy,
        binding=inputs.binding,
        origin=inputs.origin,
        options=inputs.options,
        provider_pools=inputs.provider_pools,
        publication_grants=inputs.publication_grants,
        publication_review=inputs.publication_review,
        cleanup_authority=inputs.cleanup_authority,
        resource_cipher=BrowserResourceCipher(
            inputs.resource_keys,
            active_key_id=inputs.active_resource_key_id,
        ),
        payload_keys=inputs.payload_keys,
        active_payload_key_id=inputs.active_payload_key_id,
        request_digest_keys=inputs.request_digest_keys,
        active_request_digest_key_id=inputs.active_request_digest_key_id,
        input_digest_key=inputs.input_digest_key,
        result_digest_key=inputs.result_digest_key,
        chat_bindings=PostgreSQLBrowserChatBindingResolver(
            components.browser_session_factory,
            components.session_binder,
            seed=manifest,
        ),
        trace=components.browser_trace,
        sessions=components.browser_sessions,
        worker_id="browser_" + manifest.digest[:32],
        credential_store=components.browser_credential_store,
        fixed_owner=inputs.fixed_owner,
        category_options=inputs.category_options,
    )


class _FixedOwnerLeaseStore(PostgreSQLBrowserLeaseStore):
    """Keep the existing exact binding lease without requiring quota rows.

    The fixed installation has one owner and one binding. The provider lock
    serializes its reserve path across local processes; the inherited reserve
    still rejects another held lease and retains all Run/resource checks.
    """

    async def _has_capacity_in_session(
        self,
        session: AsyncSession,
        binding: BrowserBindingFact,
        provider_alias: str,
    ) -> bool:
        pool = self._pool(provider_alias)
        row = (
            (
                await session.execute(
                    text(
                        "SELECT "
                        "(SELECT count(*) FROM browser_binding_leases WHERE provider_key=:provider"
                        " AND capacity_held) AS held,"
                        f"EXISTS (SELECT 1 FROM browser_binding_leases WHERE {_B}"
                        " AND state<>'released') AS binding_busy"
                    ),
                    {**_key(binding), "provider": pool.provider_key},
                )
            )
            .mappings()
            .one()
        )
        return row["held"] == 0 and not row["binding_busy"]

    @asynccontextmanager
    async def _locked(
        self,
        binding: BrowserBindingFact | BrowserBindingKey,
        pool: BrowserProviderPool,
    ) -> AsyncIterator[tuple[AsyncSession, Row, Row | None, datetime, int, int]]:
        params = {**_key(binding), "provider_key": pool.provider_key}
        async with self._sessions() as session, session.begin():
            await session.execute(
                text("SELECT pg_advisory_xact_lock_shared(:key)"), {"key": _WRITER_LOCK}
            )
            # The fixed provider has one installed binding, not configurable
            # global/tenant quotas. This lock makes the inherited count check atomic.
            await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": 746420211001})
            credential = (
                (
                    await session.execute(
                        text(
                            "SELECT binding_revision,binding_state,"
                            "binding_subject_digest,revoked_at"
                            f" FROM oa_session_credentials WHERE {_B} FOR UPDATE"
                        ),
                        params,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if credential is None:
                raise BrowserLeaseError("browser_binding_missing")
            row = (
                (
                    await session.execute(
                        text(f"SELECT * FROM browser_binding_leases WHERE {_B} FOR UPDATE"), params
                    )
                )
                .mappings()
                .one_or_none()
            )
            now = (await session.execute(text("SELECT clock_timestamp()"))).scalar_one()
            yield (session, cast(Row, credential), cast(Row | None, row), now, 1, 1)


class _SourceVerifier:
    def __init__(self, runtime: DFormRuntime) -> None:
        self.runtime = runtime

    async def verify(self, manifest: object) -> bool:
        return manifest == self.runtime.manifest


class _OrdinaryChatOnly:
    async def parse(
        self, principal: Principal, message: str, capability: CapabilitySpec
    ) -> dict[str, object]:
        # Cache identity/me discovery can reuse BrowserChatService. Execution is
        # exclusively the ordinary authenticated Runtime -> Gateway path.
        raise BrowserChatError("browser_fill_stop_ordinary_required", http_status=422)


def build_d_form_installation(deps: DFormInstallationDependencies) -> BrowserVerticalComponents:
    from app.infra.browser.local_installation import UnsupportedLocalProfiles
    from app.infra.browser.teaching_installation import (
        InstalledReplayProofLookup,
        PostgreSQLTeachingPublicationReview,
    )

    binding = deps.binding
    manifest = build_fill_stop_manifest(origin=deps.origin)
    runtime = DFormRuntime(manifest, deps.options)
    if binding.target_system != "oa":
        raise ValueError("browser_fill_stop_installation_invalid")
    leases: PostgreSQLBrowserLeaseStore | None = None

    def scope(run: RunSnapshot) -> None:
        if (
            run.owner.tenant_id != binding.tenant_id
            or run.owner.user_id != binding.ai_user_id
            or run.admission.binding_id != binding.binding_id
            or run.admission.binding_revision != binding.binding_revision
            or run.admission.target_system != binding.target_system
            or run.admission.publication_digest.hex() != manifest.digest
        ):
            raise BrowserLeaseError("browser_fill_stop_scope_denied")

    async def authorize(session: AsyncSession, run: RunSnapshot) -> None:
        scope(run)
        await deps.cleanup_authority.authorize_run(session, run)

    async def authorize_owned_close(run: RunSnapshot) -> None:
        async with deps.session_factory() as session:
            await authorize(session, run)

    runtime.install_cleanup_authority(authorize_owned_close)

    async def cancel_in_session(session: AsyncSession, run: RunSnapshot) -> None:
        await authorize(session, run)
        if leases is not None and await leases._prove_no_run_resource_in_session(session, run):
            state = runtime._states.get(run.run_id)
            if state is None or state.process is None and state.owned_browser is None:
                return
        await runtime.check_cancel(run)

    async def cleanup_check(session: AsyncSession, run: RunSnapshot, outcome: RunCleanup) -> None:
        await authorize(session, run)
        assert leases is not None
        state = runtime._states.get(run.run_id)
        if outcome == "released" and await leases._prove_no_run_resource_in_session(session, run):
            if state is None or state.process is None and state.owned_browser is None:
                return
        row = (
            (
                await session.execute(
                    text(
                        "SELECT state,capacity_held,lease_epoch,release_outcome,"
                        "release_proof_digest,"
                        "authorization_run_id,holder_session_id FROM browser_binding_leases"
                        " WHERE tenant_id=:tenant AND ai_user_id=:user "
                        "AND target_system=:target AND binding_id=:binding"
                    ),
                    {
                        "tenant": run.owner.tenant_id,
                        "user": run.owner.user_id,
                        "target": run.admission.target_system,
                        "binding": run.admission.binding_id,
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        if (
            outcome == "quarantined"
            and row is not None
            and row["capacity_held"]
            and (
                row["state"] in {"held", "quarantined"}
                and row["authorization_run_id"] == run.run_id
                and row["holder_session_id"] == run.owner.session_id
            )
        ):
            return
        # Only the private fact obtained after native process checks and the
        # matching committed lease release can acknowledge attachment cleanup.
        if (
            outcome != "released"
            or state is None
            or not state.cleanup_proven
            or not state.released
            or state.owned_browser is not None
            and state.release_evidence is None
            or state.owned_browser is not None
            and not state.owned_resource_cleanup_proven
            or state.claim is None
            or row is None
            or row["state"] != "released"
            or row["capacity_held"]
            or row["lease_epoch"] != state.claim.lease_epoch
            or row["release_outcome"] != "released"
            or row["release_proof_digest"] is None
        ):
            raise BrowserLeaseError("browser_fill_stop_cleanup_unknown")

    async def readiness(run: RunSnapshot, session: AsyncSession | None) -> bool:
        scope(run)
        # Existing worker recovery settles started/verified Runs without replay.
        if (
            run.cancel_requested
            or run.phase in {"running", "verifying", "waiting_user"}
            or run.effect != "not_sent"
            or run.verification == "verified"
        ):
            return True
        if leases is None or run.run_id in runtime._states:
            return False
        if runtime._login_vault is None or runtime.options.login_contract is None:
            return False
        return (
            await leases.has_capacity(binding, deps.options.provider_key)
            if session is None
            else await leases._has_capacity_in_session(session, binding, deps.options.provider_key)
        )

    async def ready(run: RunSnapshot) -> bool:
        return await readiness(run, None)

    async def ready_in_session(session: AsyncSession, run: RunSnapshot) -> bool:
        return await readiness(run, session)

    unsupported = UnsupportedLocalProfiles()
    review = deps.publication_review

    async def review_scope(owner: BrowserOwner, candidate: object) -> None:
        if (
            candidate != manifest
            or owner.tenant_id != binding.tenant_id
            or await deps.publication_grants.check(
                owner,
                manifest.skill.skill_id,
                "activate",
                manifest.digest,
            )
            is not True
        ):
            raise ValueError("browser_initial_review_denied")

    async def request_initial_review(
        owner: BrowserOwner,
        candidate: BrowserPublicationManifest,
        task_id: str,
        run_id: str,
        request_id: str,
    ) -> HumanGateRequest:
        await review_scope(owner, candidate)
        assert isinstance(review, PostgreSQLTeachingPublicationReview)
        return await review.request_initial(owner, candidate, task_id, run_id, request_id)

    async def decide_initial_review(
        owner: BrowserOwner,
        candidate: BrowserPublicationManifest,
        task_id: str,
        run_id: str,
        request_id: str,
        decision: Literal["confirmed", "rejected"],
    ) -> HumanGateDecisionRecord:
        await review_scope(owner, candidate)
        assert isinstance(review, PostgreSQLTeachingPublicationReview)
        return await review.decide_initial(owner, candidate, task_id, run_id, request_id, decision)

    vertical = build_browser_vertical(
        BrowserVerticalDependencies(
            session_factory=deps.session_factory,
            capability_registry=deps.capability_registry,
            session_binder=deps.session_binder,
            policy=deps.policy,
            tenant_id=binding.tenant_id,
            seed=manifest,
            publication_grants=deps.publication_grants,
            source_verifier=_SourceVerifier(runtime),
            publication_review=deps.publication_review,
            payload_keys=deps.payload_keys,
            active_payload_key_id=deps.active_payload_key_id,
            request_digest_keys=deps.request_digest_keys,
            active_request_digest_key_id=deps.active_request_digest_key_id,
            input_digest_key=deps.input_digest_key,
            result_digest_key=deps.result_digest_key,
            execution_factory=None,
            lifecycle=None,
            fill_stop_runtime=runtime,
            profile_capture_proof=unsupported,
            profile_cleanup_proof=unsupported,
            profile_cleanup_authority=unsupported,
            cancel_check=runtime.check_cancel,
            cancel_check_in_session=cancel_in_session,
            cleanup_authorize=authorize,
            cleanup_check=cleanup_check,
            chat_parser=_OrdinaryChatOnly(),
            chat_bindings=deps.chat_bindings,
            trace=deps.trace,
            sessions=deps.sessions,
            worker_id=deps.worker_id,
            enabled=True,
            business_ready=ready,
            business_ready_in_session=ready_in_session,
            initial_review_request=request_initial_review,
            initial_review_decide=decide_initial_review,
            confirmed_key_digest=lambda digest, value: hmac.digest(
                deps.input_digest_key,
                b"d-form-bound-object-v1\x00" + digest + b"\x00" + value.encode(),
                "sha256",
            ).hex(),
        )
    )
    assert vertical is not None
    if type(review) is PostgreSQLTeachingPublicationReview:
        cast(InstalledReplayProofLookup, review._load_proof).install_runs(vertical.runs)
    lease_type = _FixedOwnerLeaseStore if deps.fixed_owner else PostgreSQLBrowserLeaseStore
    leases = lease_type(
        session_factory=deps.session_factory,
        current_auth=vertical.current_auth,
        binding_reader=vertical.binding_reader,
        registry=deps.provider_pools,
        resource_cipher=deps.resource_cipher,
        proof_context=deps.options.proof_context,
        proof_verifier=runtime,
        cleanup_authority=deps.cleanup_authority,
        resource_subject=runtime,
        current_in_session=vertical.current_auth.check_current_in_session,
    )
    runtime.install_leases(leases)
    credential_store = deps.credential_store
    if credential_store is not None:
        runtime.install_login_vault(
            lambda run: RunBoundOACredentialVault(
                run=run,
                binding=binding,
                origin=deps.origin,
                runs=vertical.runs,
                current_auth=vertical.current_auth,
                binding_reader=vertical.binding_reader,
                credential_store=credential_store,
                check_lease=lambda: runtime.check_login_lease(run),
            )
        )
    return vertical
