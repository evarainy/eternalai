"""Explicit single-fixture Windows installation using the existing PG stores.

Does not seed a database, mint credentials, read env, start the browser, or change
the Linux installation. The caller supplies current grants and durable keyrings.
"""

from __future__ import annotations

import hmac
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infra.auth.crypto import PrincipalSessionBinder
from app.infra.browser.chat_read_fixture import (
    CHAT_READ_HTML, CHAT_READ_TENANT, CHAT_READ_USER, ChatReadFixture, build_chat_read_fixture,
)
from app.infra.browser.composition import (
    BrowserPublicationGrants, BrowserVerticalComponents, BrowserVerticalDependencies,
    build_browser_vertical,
)
from app.infra.browser.d_windows_query import DWindowsQueryFactory, DWindowsQueryLifecycle, DWindowsQueryOptions
from app.infra.browser.local_installation import UnsupportedLocalProfiles
from app.infra.persistence.browser.crypto import BrowserClaimProofContext, BrowserResourceCipher
from app.infra.persistence.browser.leases import BrowserProviderPoolRegistry, PostgreSQLBrowserLeaseStore
from app.infra.persistence.capability_registry.repository import PostgreSQLCapabilityRegistry
from app.ports.browser_chat import BrowserChatBindingResolverPort
from app.ports.browser_read_execution import BrowserReadExecutionError
from app.ports.browser_run_store import RunCleanup, RunSnapshot
from app.ports.browser_store import BrowserCleanupAuthorityPort, BrowserLeaseError
from app.ports.credential_vault import BrowserBindingFact
from app.ports.policy_guard import PolicyGuardPort
from app.ports.task_store import SessionStorePort
from app.ports.trace import TracePort


class WindowsQueryCleanupGrant(BrowserCleanupAuthorityPort, Protocol):
    async def authorize_run(self, session: AsyncSession, run: RunSnapshot) -> None: ...


@dataclass(frozen=True, slots=True, repr=False)
class WindowsChatDependencies:
    session_factory: async_sessionmaker[AsyncSession]
    capability_registry: PostgreSQLCapabilityRegistry
    session_binder: PrincipalSessionBinder
    policy: PolicyGuardPort
    source: ChatReadFixture
    binding: BrowserBindingFact
    options: DWindowsQueryOptions
    driver: Any = field(repr=False)
    publication_grants: BrowserPublicationGrants
    provider_pools: BrowserProviderPoolRegistry
    cleanup_authority: WindowsQueryCleanupGrant
    proof_context: BrowserClaimProofContext
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


class _WindowsSourceVerifier:
    def __init__(self, factory: DWindowsQueryFactory) -> None:
        self.factory = factory

    async def verify(self, manifest: object) -> bool:
        source = self.factory.source
        expected = build_chat_read_fixture(origin=source.manifest.site.source.origin, html_path=CHAT_READ_HTML,
                                           decision_manifest=source.manifest.site.decision_manifest)
        return (manifest == source.manifest and source.manifest.source_kind == "registered_synthetic_fixture"
                and source.manifest.effect == "read_only" and self.factory._leases is not None
                and source.manifest == expected.manifest and source.template == expected.template
                and source.region == expected.region and source.rules == expected.rules
                and source.site.bootstrap(source.manifest.skill) == source.manifest.site_plan())


def build_windows_chat_installation(deps: WindowsChatDependencies) -> BrowserVerticalComponents:
    source, binding = deps.source, deps.binding
    if (type(source) is not ChatReadFixture or binding.tenant_id != CHAT_READ_TENANT
            or binding.ai_user_id != CHAT_READ_USER or binding.target_system != "oa"
            or not callable(getattr(deps.cleanup_authority, "authorize_run", None))):
        raise ValueError("windows_chat_installation_invalid")
    leases: PostgreSQLBrowserLeaseStore | None = None

    async def request_lookup(run: RunSnapshot) -> str:
        async with deps.session_factory() as session:
            value = (await session.execute(text(
                "SELECT client_request_id FROM tasks WHERE task_id=:task AND tenant_id=:tenant"
                " AND ai_user_id=:user AND session_id=:session",
            ), {"task": run.task_id, "tenant": run.owner.tenant_id,
                "user": run.owner.user_id, "session": run.owner.session_id})).scalar_one_or_none()
        if type(value) is not str:
            raise BrowserReadExecutionError("stale")
        try:
            return str(UUID(value))
        except ValueError:
            raise BrowserReadExecutionError("stale") from None

    factory = DWindowsQueryFactory(source, binding, deps.options, deps.proof_context, request_lookup, deps.driver)
    pool = deps.provider_pools.resolve(deps.options.provider_key)
    if (pool.provider_key != deps.options.provider_key
            or not hmac.compare_digest(pool.manifest_digest, factory.manifest_digest)):
        raise ValueError("windows_chat_pool_manifest_mismatch")

    def check_scope(run: RunSnapshot) -> None:
        if (run.owner.tenant_id != binding.tenant_id or run.owner.user_id != binding.ai_user_id
                or run.admission.target_system != binding.target_system
                or run.admission.binding_id != binding.binding_id
                or run.admission.binding_revision != binding.binding_revision
                or run.admission.publication_digest.hex() != source.manifest.digest):
            raise BrowserLeaseError("browser_windows_cleanup_scope_denied")

    async def authorize(session: AsyncSession, run: RunSnapshot) -> None:
        check_scope(run)
        await deps.cleanup_authority.authorize_run(session, run)

    async def no_resource(run: RunSnapshot) -> bool:
        if leases is None:
            raise BrowserLeaseError("browser_windows_leases_unavailable")
        async with deps.session_factory() as session:
            await authorize(session, run)
            return await leases._prove_no_run_resource_in_session(session, run)

    lifecycle = DWindowsQueryLifecycle(factory, deps.cleanup_authority, no_resource)

    async def cleanup_check(session: AsyncSession, run: RunSnapshot, outcome: RunCleanup) -> None:
        check_scope(run)
        if leases is None:
            raise BrowserLeaseError("browser_windows_leases_unavailable")
        no_run_resource = await leases._prove_no_run_resource_in_session(session, run)
        if outcome == "released" and no_run_resource:
            return
        state = factory._states.get(run.run_id)
        if state is None or state.run.admission != run.admission:
            if outcome == "quarantined" and not no_run_resource:
                return  # Recovery cannot assert any terminal provider fact.
            raise BrowserLeaseError("browser_windows_cleanup_unconfirmed")
        row = (await session.execute(text(
            "SELECT state,capacity_held,lease_epoch,release_outcome,release_proof_digest,"
            "authorization_run_id,holder_session_id FROM browser_binding_leases"
            " WHERE tenant_id=:tenant AND ai_user_id=:user AND target_system=:target AND binding_id=:binding",
        ), {"tenant": run.owner.tenant_id, "user": run.owner.user_id,
            "target": run.admission.target_system, "binding": run.admission.binding_id})).mappings().one_or_none()
        if outcome == "quarantined":
            if (row is not None and row["capacity_held"] and row["state"] in {"held", "quarantined"}
                    and row["authorization_run_id"] == run.run_id
                    and row["holder_session_id"] == run.owner.session_id):
                return
            raise BrowserLeaseError("browser_windows_quarantine_unconfirmed")
        # Provider enumeration/port reads already happened independently before
        # lease.cleanup. Under existing DB locks inspect only bound private fact
        # and committed PG release, never perform a second provider/network IO.
        if (outcome != "terminated" or not state.cleanup_proven
                or state.cleanup_status != "terminated" or state.termination_evidence is None
                or state.claim is None or state.claim.lease_epoch != run.lease_epoch
                or not state.cancellation.is_set() or not state.closed
                or row is None or row["state"] != "released" or row["capacity_held"]
                or row["lease_epoch"] != state.claim.lease_epoch
                or row["release_outcome"] != "terminated" or row["release_proof_digest"] is None):
            raise BrowserLeaseError("browser_windows_cleanup_unconfirmed")

    async def cancel_in_session(session: AsyncSession, run: RunSnapshot) -> None:
        await authorize(session, run)
        if leases is not None and await leases._prove_no_run_resource_in_session(session, run):
            return
        await cleanup_check(session, run, "terminated")

    async def readiness(run: RunSnapshot, session: AsyncSession | None) -> bool:
        check_scope(run)
        if leases is None:
            return False
        state = factory._states.get(run.run_id)
        if state is not None:
            return False  # Never re-open/re-query a Run once open was attempted.
        if session is None:
            return await leases.has_capacity(binding, deps.options.provider_key)
        return await leases._has_capacity_in_session(session, binding, deps.options.provider_key)

    async def ready(run: RunSnapshot) -> bool:
        return await readiness(run, None)

    async def ready_in_session(session: AsyncSession, run: RunSnapshot) -> bool:
        return await readiness(run, session)

    unsupported = UnsupportedLocalProfiles()
    vertical = build_browser_vertical(BrowserVerticalDependencies(
        session_factory=deps.session_factory, capability_registry=deps.capability_registry,
        session_binder=deps.session_binder, policy=deps.policy, tenant_id=binding.tenant_id,
        seed=source.manifest, publication_grants=deps.publication_grants,
        source_verifier=_WindowsSourceVerifier(factory), payload_keys=deps.payload_keys,
        active_payload_key_id=deps.active_payload_key_id, request_digest_keys=deps.request_digest_keys,
        active_request_digest_key_id=deps.active_request_digest_key_id,
        input_digest_key=deps.input_digest_key, result_digest_key=deps.result_digest_key,
        execution_factory=factory, lifecycle=lifecycle, profile_capture_proof=unsupported,
        profile_cleanup_proof=unsupported, profile_cleanup_authority=unsupported,
        cancel_check=lifecycle.check_cancel, cancel_check_in_session=cancel_in_session,
        cleanup_authorize=authorize, cleanup_check=cleanup_check, chat_parser=None,
        chat_bindings=deps.chat_bindings, trace=deps.trace, sessions=deps.sessions,
        worker_id=deps.worker_id, enabled=True, worker_ttl_seconds=60,
        business_ready=ready, business_ready_in_session=ready_in_session,
    ))
    if vertical is None:
        raise ValueError("windows_chat_vertical_unavailable")
    leases = PostgreSQLBrowserLeaseStore(
        session_factory=deps.session_factory, current_auth=vertical.current_auth,
        binding_reader=vertical.binding_reader, registry=deps.provider_pools,
        resource_cipher=deps.resource_cipher, proof_context=deps.proof_context,
        proof_verifier=factory, cleanup_authority=deps.cleanup_authority, resource_subject=factory,
        current_in_session=vertical.current_auth.check_current_in_session,
    )
    factory.install_leases(leases)
    return vertical
