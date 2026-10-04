"""Trusted host installation for the bounded local synthetic READ_ONLY route.

The operator supplies durable keys and independently verified grants. This module
does not discover configuration, generate keys, register capabilities, mutate
capacity or start browsers. Explicit publication operations remain separate.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infra.auth.crypto import PrincipalSessionBinder
from app.infra.browser.chat_inputs import (
    FrozenBrowserChatParser,
    FrozenSyntheticStructuredParser,
    PostgreSQLBrowserChatBindingResolver,
)
from app.infra.browser.composition import (
    BrowserPublicationGrants,
    BrowserVerticalComponents,
    BrowserVerticalDependencies,
    build_browser_vertical,
)
from app.infra.browser.fixed_synthetic_seed import (
    DIAGNOSTIC_SKILL_VERSION,
    OBSERVE_SKILL_VERSION,
    VISIBLE_QUERY_SKILL_VERSION,
    FixedSourceReadFactory,
    FixedSyntheticSource,
    FixedSyntheticSourceVerifier,
    build_fixed_synthetic_diagnostic_source,
    build_fixed_synthetic_observe_only_source,
    build_fixed_synthetic_query_source,
    build_fixed_synthetic_visible_query_source,
)
from app.infra.browser.local_read_execution import LocalBrowserReadExecutionFactory
from app.infra.browser.local_resource_lifecycle import (
    LocalBrowserReadLifecycle,
    LocalBrowserResources,
    LocalChromiumDeployment,
)
from app.infra.persistence.browser.authorization import CleanupAuthorize
from app.infra.persistence.browser.crypto import BrowserClaimProofContext, BrowserResourceCipher
from app.infra.persistence.browser.leases import (
    BrowserProviderPoolRegistry,
    PostgreSQLBrowserLeaseStore,
)
from app.infra.persistence.capability_registry.repository import PostgreSQLCapabilityRegistry
from app.ports.auth import VerifiedSessionToken
from app.ports.browser import DecisionProvider
from app.ports.browser_chat import BrowserChatParserPort
from app.ports.browser_profile_store import (
    BrowserProfileCaptureFact,
    BrowserProfileCleanupFact,
    BrowserProfileContext,
    BrowserProfileError,
    BrowserProfileGeneration,
)
from app.ports.browser_run_store import RunSnapshot
from app.ports.browser_store import (
    BrowserBindingKey,
    BrowserCleanupAuthorityPort,
    BrowserLeaseClaim,
    BrowserLeaseError,
)
from app.ports.credential_vault import BrowserBindingFact
from app.ports.llm_provider import LLMProviderPort
from app.ports.policy_guard import PolicyGuardPort
from app.ports.structured_output import StructuredOutputPort
from app.ports.task_store import SessionStorePort
from app.ports.trace import TracePort


class PostgreSQLLocalCleanupGrant:
    """One independently authenticated service actor, exact binding and publication.

    Role and operation scope are explicit trusted host authorization configuration;
    no role is implicitly an administrator. The verified actor must originate from
    the existing authentication boundary, never from request JSON or Run ownership.
    Business-session revocation cannot supply or remove this independent grant.
    """

    def __init__(
        self, *, session_factory: async_sessionmaker[AsyncSession],
        session_binder: PrincipalSessionBinder, verified_actor: VerifiedSessionToken,
        bound_session: str, binding: BrowserBindingKey, publication_digest: str,
        required_role: str,
        allowed_operations: frozenset[Literal["recover", "cleanup"]],
    ) -> None:
        if (not isinstance(session_binder, PrincipalSessionBinder)
                or type(verified_actor) is not VerifiedSessionToken
                or type(binding) is not BrowserBindingKey
                or type(required_role) is not str or not required_role.strip()
                or required_role != required_role.strip()
                or type(allowed_operations) is not frozenset or not allowed_operations
                or not allowed_operations <= {"recover", "cleanup"}
                or verified_actor.principal.org_ctx.tenant_id != binding.tenant_id
                or required_role not in verified_actor.principal.roles
                or type(verified_actor.fingerprint) is not bytes
                or len(verified_actor.fingerprint) != 32
                or verified_actor.expires_at.tzinfo is None
                or verified_actor.expires_at.utcoffset() is None
                or not bound_session.startswith("sid_v1.")
                or session_binder.bind(verified_actor.principal, bound_session) != bound_session
                or len(publication_digest) != 64
                or any(c not in "0123456789abcdef" for c in publication_digest)):
            raise ValueError("browser_local_cleanup_grant_invalid")
        self._sessions, self._binder = session_factory, session_binder
        self._actor, self._session = verified_actor, bound_session
        self._binding, self._publication = binding, bytes.fromhex(publication_digest)
        self._role, self._operations = required_role, allowed_operations

    def _scope(self, key: BrowserBindingKey, operation: str) -> None:
        if key != self._binding or operation not in self._operations:
            raise BrowserLeaseError("browser_local_cleanup_grant_denied")

    async def _current(self, session: AsyncSession) -> None:
        actor = self._actor
        if (actor.expires_at <= datetime.now(UTC)
                or self._binder.bind(actor.principal, self._session) != self._session):
            raise BrowserLeaseError("browser_local_cleanup_grant_expired")
        authorized = (await session.execute(text(
            "SELECT 1 WHERE :expires_at > clock_timestamp()"
            " AND EXISTS (SELECT 1 FROM principal_roles"
            " WHERE tenant_id=:tenant AND ai_user_id=:actor AND role=:role)"
            " AND EXISTS (SELECT 1 FROM sessions"
            " WHERE tenant_id=:tenant AND session_id=:session)"
            " AND NOT EXISTS (SELECT 1 FROM auth_session_revocations"
            " WHERE token_fingerprint=:fingerprint)"
        ), {
            "expires_at": actor.expires_at, "tenant": actor.principal.org_ctx.tenant_id,
            "actor": actor.principal.ai_user_id, "role": self._role,
            "session": self._session, "fingerprint": actor.fingerprint,
        })).scalar_one_or_none()
        if authorized != 1:
            raise BrowserLeaseError("browser_local_cleanup_grant_denied")

    async def check_recovery(self, key: BrowserBindingKey) -> None:
        self._scope(key, "recover")
        async with self._sessions() as session:
            await self._current(session)

    async def check_cleanup(self, claim: BrowserLeaseClaim) -> None:
        binding = claim.binding
        self._scope(BrowserBindingKey(
            binding.tenant_id, binding.ai_user_id, binding.target_system, binding.binding_id,
        ), "cleanup")
        if (claim.auth.evidence_version != "verified-session-v1"
                or claim.auth.authorization_run_id is None):
            raise BrowserLeaseError("browser_local_cleanup_grant_denied")
        async with self._sessions() as session:
            await self._current(session)
            publication = (await session.execute(text(
                "SELECT publication_digest FROM browser_runs WHERE tenant_id=:tenant"
                " AND ai_user_id=:user AND session_id=:session AND run_id=:run"
                " AND auth_fingerprint=:fingerprint AND auth_expires_at=:expires_at"
                " AND auth_evidence_version='verified-session-v1'"
                " AND target_system=:target AND binding_id=:binding"
                " AND binding_revision=:revision"
            ), {
                "tenant": claim.auth.owner.tenant_id, "user": claim.auth.owner.user_id,
                "session": claim.auth.owner.session_id, "run": claim.auth.authorization_run_id,
                "fingerprint": claim.auth.fingerprint, "expires_at": claim.auth.expires_at,
                "target": binding.target_system, "binding": binding.binding_id,
                "revision": binding.binding_revision,
            })).scalar_one_or_none()
            if publication != self._publication:
                raise BrowserLeaseError("browser_local_cleanup_grant_denied")

    async def authorize_run(self, session: AsyncSession, run: RunSnapshot) -> None:
        admission = run.admission
        self._scope(BrowserBindingKey(
            run.owner.tenant_id, run.owner.user_id, admission.target_system, admission.binding_id,
        ), "cleanup")
        if admission.publication_digest != self._publication:
            raise BrowserLeaseError("browser_local_cleanup_grant_denied")
        await self._current(session)


class UnsupportedLocalProfiles:
    """An explicit refusal, never a claimed capture or cleanup success."""

    async def verify_capture(
        self, context: BrowserProfileContext, generation: BrowserProfileGeneration,
        evidence: bytes,
    ) -> BrowserProfileCaptureFact:
        raise BrowserProfileError("browser_local_profile_unsupported")

    async def check_live_subject(
        self, context: BrowserProfileContext, generation: BrowserProfileGeneration,
    ) -> None:
        raise BrowserProfileError("browser_local_profile_unsupported")

    async def verify_cleanup(
        self, context: BrowserProfileContext, generation: BrowserProfileGeneration,
        evidence: bytes,
    ) -> BrowserProfileCleanupFact:
        raise BrowserProfileError("browser_local_profile_unsupported")

    async def check_cleanup(self, context: BrowserProfileContext) -> None:
        raise BrowserProfileError("browser_local_profile_unsupported")


@dataclass(frozen=True, slots=True, repr=False)
class LocalBrowserInstallationDependencies:
    """Trusted host DI; each object is installed outside request argument parsing.

    The existing DB must already contain the exact registered Capability, active
    binding/subject, provider capacity pool, service roles and owned sessions.
    Constructing this record creates none of them. Pool admission and current
    grants are rechecked through the existing durable stores on every action.
    Source bytes and the real helper/package deployment are fixed registrations.
    Keys must survive restarts; historical IDs must remain in their keyrings.
    """

    session_factory: async_sessionmaker[AsyncSession]
    capability_registry: PostgreSQLCapabilityRegistry
    session_binder: PrincipalSessionBinder
    policy: PolicyGuardPort
    source: FixedSyntheticSource
    binding: BrowserBindingFact
    deployment: LocalChromiumDeployment
    decision: DecisionProvider
    provider_pools: BrowserProviderPoolRegistry
    proof_context: BrowserClaimProofContext = field(repr=False)
    publication_grants: BrowserPublicationGrants
    cleanup_authority: BrowserCleanupAuthorityPort
    cleanup_authorize: CleanupAuthorize
    payload_keys: Mapping[str, bytes] = field(repr=False)
    active_payload_key_id: str
    resource_keys: Mapping[str, bytes] = field(repr=False)
    active_resource_key_id: str
    request_digest_keys: Mapping[str, bytes] = field(repr=False)
    active_request_digest_key_id: str
    input_digest_key: bytes = field(repr=False)
    result_digest_key: bytes = field(repr=False)
    llm_provider: LLMProviderPort | None
    structured_output: StructuredOutputPort | None
    intent_model: str | None
    trace: TracePort
    sessions: SessionStorePort
    worker_id: str
    enabled: bool = False
    ttl_seconds: int = 60
    input_mode: Literal["chat", "structured"] = "chat"
    execution_timeout_seconds: int | None = None


def build_local_browser_vertical(
    dependencies: LocalBrowserInstallationDependencies | None = None,
) -> BrowserVerticalComponents | None:
    """Wire the concrete local route; do not launch, publish or grant anything.

    An audited operator async context manager can yield the returned components.
    Host construction owns the lifetime of model/DB dependencies. Missing provider
    pool registration, grant implementations or persistent keys fail construction;
    missing/stale DB capacity and authorization fail the existing admission gates.
    """
    if dependencies is None:
        return None
    if type(dependencies.enabled) is not bool:
        raise ValueError("browser_local_enablement_invalid")
    if not dependencies.enabled:
        return None
    deps = dependencies
    if (type(deps.source) is not FixedSyntheticSource
            or type(deps.deployment) is not LocalChromiumDeployment
            or deps.deployment.enabled is not True
            or type(deps.provider_pools) is not BrowserProviderPoolRegistry
            or type(deps.proof_context) is not BrowserClaimProofContext
            or not callable(deps.cleanup_authorize)
            or not callable(getattr(deps.cleanup_authority, "check_recovery", None))
            or not callable(getattr(deps.cleanup_authority, "check_cleanup", None))):
        raise ValueError("browser_local_installation_invalid")
    builder = (build_fixed_synthetic_observe_only_source
               if deps.source.manifest.skill.version == OBSERVE_SKILL_VERSION
               else build_fixed_synthetic_visible_query_source
               if deps.source.manifest.skill.version == VISIBLE_QUERY_SKILL_VERSION
               else build_fixed_synthetic_diagnostic_source
               if deps.source.manifest.skill.version == DIAGNOSTIC_SKILL_VERSION
               else build_fixed_synthetic_query_source)
    expected = builder(deps.source.manifest.site.decision_manifest)
    if (deps.source.manifest != expected.manifest or deps.source.html != expected.html
            or deps.source.rules != expected.rules or deps.source.region != expected.region
            or type(deps.source.projector) is not type(expected.projector)):
        raise ValueError("browser_local_source_invalid")
    chat_parser: BrowserChatParserPort
    if deps.input_mode == "structured":
        chat_parser = FrozenSyntheticStructuredParser(seed=deps.source.manifest)
    elif deps.input_mode == "chat":
        if (deps.llm_provider is None or deps.structured_output is None
                or deps.intent_model is None):
            raise ValueError("browser_local_chat_provider_required")
        chat_parser = FrozenBrowserChatParser(
            deps.llm_provider, deps.structured_output, seed=deps.source.manifest,
            model=deps.intent_model,
        )
    else:
        raise ValueError("browser_local_input_mode_invalid")
    pool = deps.provider_pools.resolve(deps.deployment.provider_key)
    if (pool.provider_key != deps.deployment.provider_key
            or pool.manifest_digest != deps.deployment.manifest_digest):
        raise ValueError("browser_local_pool_registration_invalid")
    resources = LocalBrowserResources(deps.deployment, deps.proof_context, deps.cleanup_authority)
    factory = LocalBrowserReadExecutionFactory(
        deps.source, resources, deps.binding, deps.decision, ttl_seconds=deps.ttl_seconds,
        execution_timeout_seconds=deps.execution_timeout_seconds,
    )
    lifecycle = LocalBrowserReadLifecycle(factory, deps.cleanup_authority)
    wrapped = FixedSourceReadFactory(factory, deps.source)
    unsupported = UnsupportedLocalProfiles()
    configured = build_browser_vertical(BrowserVerticalDependencies(
        session_factory=deps.session_factory, capability_registry=deps.capability_registry,
        session_binder=deps.session_binder, policy=deps.policy, tenant_id=deps.binding.tenant_id,
        seed=deps.source.manifest, publication_grants=deps.publication_grants,
        source_verifier=FixedSyntheticSourceVerifier(wrapped), payload_keys=deps.payload_keys,
        active_payload_key_id=deps.active_payload_key_id,
        request_digest_keys=deps.request_digest_keys,
        active_request_digest_key_id=deps.active_request_digest_key_id,
        input_digest_key=deps.input_digest_key, result_digest_key=deps.result_digest_key,
        execution_factory=wrapped, lifecycle=lifecycle,
        profile_capture_proof=unsupported, profile_cleanup_proof=unsupported,
        profile_cleanup_authority=unsupported, cancel_check=lifecycle.check_cancel,
        cleanup_authorize=deps.cleanup_authorize, cleanup_check=lifecycle.check_cleanup,
        chat_parser=chat_parser,
        chat_bindings=PostgreSQLBrowserChatBindingResolver(
            deps.session_factory, deps.session_binder, seed=deps.source.manifest,
        ),
        trace=deps.trace, sessions=deps.sessions, worker_id=deps.worker_id,
        enabled=True, worker_ttl_seconds=deps.ttl_seconds,
    ))
    if configured is None:
        raise ValueError("browser_local_composition_unavailable")
    leases = PostgreSQLBrowserLeaseStore(
        session_factory=deps.session_factory, current_auth=configured.current_auth,
        binding_reader=configured.binding_reader, registry=deps.provider_pools,
        resource_cipher=BrowserResourceCipher(
            deps.resource_keys, active_key_id=deps.active_resource_key_id,
        ),
        proof_context=deps.proof_context, proof_verifier=resources,
        cleanup_authority=deps.cleanup_authority, resource_subject=resources,
    )
    factory.install_authority(
        leases=leases, current_auth=configured.current_auth,
        binding_reader=configured.binding_reader,
    )
    return configured
