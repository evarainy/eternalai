"""Trusted host installation for registered local READ_ONLY Sites.

The operator supplies durable keys and independently verified grants. This module
does not discover configuration, generate keys, register capabilities, mutate
capacity or start browsers. Explicit publication operations remain separate.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import httpx2
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.browser_skill.models import BrowserOwner, LocatorHint
from app.browser_skill.publication_contracts import BrowserPublicationManifest
from app.infra.auth.crypto import PrincipalSessionBinder
from app.infra.browser.chat_inputs import (
    FrozenBrowserChatParser,
    FrozenSyntheticStructuredParser,
    PostgreSQLBrowserChatBindingResolver,
)
from app.infra.browser.composition import (
    BrowserPublicationGrants,
    BrowserRegisteredSourceVerifier,
    BrowserVerticalComponents,
    BrowserVerticalDependencies,
    build_browser_vertical,
)
from app.infra.browser.decision_adapters import LocalChoiceCodec
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
    observe_attempt_id_from_version,
)
from app.infra.browser.local_file_artifacts import (
    FileArtifactCleanupScheduler,
    LocalFileArtifacts,
    initialize_file_artifact_storage_scope,
)
from app.infra.browser.local_profile_cleanup import ProfileCleanupScheduler
from app.infra.browser.local_read_execution import LocalBrowserReadExecutionFactory
from app.infra.browser.local_resource_lifecycle import (
    LocalBrowserReadLifecycle,
    LocalBrowserResources,
    LocalChromiumDeployment,
    LocalPlaywrightDriver,
    RegisteredLocalSite,
)
from app.infra.browser.multi_installation import BrowserVerticalCollection
from app.infra.browser.read_execution import BrowserReadExecutionFactory
from app.infra.browser.runtime_exploration import RuntimeLLMExplorationSuggester
from app.infra.browser.systemone_http import DecisionDeployment, DecisionHTTPProvider
from app.infra.browser.teaching_installation import (
    InstalledReplayProofLookup,
    PostgreSQLTeachingPublicationReview,
    reviewed_publication_authority,
)
from app.infra.persistence.browser.authorization import CleanupAuthorize
from app.infra.persistence.browser.crypto import BrowserClaimProofContext, BrowserResourceCipher
from app.infra.persistence.browser.file_artifacts import (
    PostgreSQLBrowserFileArtifactAuthority,
)
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
from app.ports.browser_publication_store import BrowserPublicationReviewPort
from app.ports.browser_run_store import RunSnapshot
from app.ports.browser_store import (
    BrowserBindingKey,
    BrowserCleanupAuthorityPort,
    BrowserLeaseClaim,
    BrowserLeaseError,
)
from app.ports.credential_vault import BrowserBindingFact
from app.ports.human_gate import HumanGateDecisionRecord, HumanGateRequest
from app.ports.llm_provider import LLMProviderPort
from app.ports.policy_guard import PolicyGuardPort
from app.ports.structured_output import StructuredOutputPort
from app.ports.task_store import SessionStorePort
from app.ports.trace import TracePort
from services.decision.manifest import PinnedCheckpoint

if TYPE_CHECKING:
    from app.composition import ProductionComponents
    from app.config import ProductionSettings


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

    async def authorize_historical_run(self, session: AsyncSession, run: RunSnapshot) -> None:
        """This service grant plus the exact persisted Run, nothing more.

        Only the bounded handoff closure uses it; the installation caps the Run's
        binding revision at its own. It grants no business action, generic
        cleanup, lease release or decryption of input/resource references.
        """
        await self.authorize_run(session, run)
        admission, owner = run.admission, run.owner
        found = (await session.execute(text(
            "SELECT 1 FROM browser_runs WHERE tenant_id=:tenant AND ai_user_id=:user"
            " AND session_id=:session AND run_id=:run AND task_id=:task"
            " AND publication_digest=:digest AND target_system=:target"
            " AND binding_id=:binding AND binding_revision=:revision"
            " AND auth_fingerprint=:fingerprint AND auth_expires_at=:expires"
            " AND auth_evidence_version='verified-session-v1'"
        ), {
            "tenant": owner.tenant_id, "user": owner.user_id,
            "session": owner.session_id, "run": run.run_id, "task": run.task_id,
            "digest": self._publication, "target": admission.target_system,
            "binding": admission.binding_id, "revision": admission.binding_revision,
            "fingerprint": admission.auth_fingerprint,
            "expires": admission.auth_expires_at,
        })).scalar_one_or_none()
        if found != 1:
            raise BrowserLeaseError("browser_local_cleanup_grant_denied")


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


class RegisteredLocalProfileCleanupAuthority:
    """The installed operator grant and exact local binding remain current."""

    def __init__(
        self, grant: BrowserCleanupAuthorityPort, binding: BrowserBindingFact,
        source: RegisteredLocalSite,
    ) -> None:
        self._grant, self._binding, self._source = grant, binding, source

    async def check_cleanup(self, context: BrowserProfileContext) -> None:
        if not isinstance(context, BrowserProfileContext):
            raise BrowserProfileError("browser_local_profile_cleanup_denied")
        binding = context.claim.binding
        if (not isinstance(binding, BrowserBindingFact)
                or (binding.tenant_id, binding.ai_user_id, binding.target_system,
                    binding.binding_id) != (
                        self._binding.tenant_id, self._binding.ai_user_id,
                        self._binding.target_system, self._binding.binding_id,
                    )
                or context.claim.auth.owner.tenant_id != self._binding.tenant_id
                or context.claim.auth.owner.user_id != self._binding.ai_user_id
                or self._source.manifest.source_kind != "registered_site"):
            raise BrowserProfileError("browser_local_profile_cleanup_denied")
        await self._grant.check_cleanup(context.claim)


class RegisteredLocalSiteVerifier:
    """Only the installed source object may certify its exact publication."""

    def __init__(
        self, factory: LocalBrowserReadExecutionFactory, source: RegisteredLocalSite,
    ) -> None:
        if factory.source is not source:
            raise ValueError("browser_local_site_factory_mismatch")
        self._factory, self._source = factory, source

    async def verify(self, manifest: object) -> bool:
        return (
            manifest == self._source.manifest
            and self._source.manifest.source_kind == "registered_site"
            and await self._factory.verify_source(self._source.manifest)
        )


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
    source: FixedSyntheticSource | RegisteredLocalSite
    binding: BrowserBindingFact
    deployment: LocalChromiumDeployment
    decision: DecisionProvider | None
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
    publication_review: BrowserPublicationReviewPort | None = None
    file_artifacts: LocalFileArtifacts | None = None
    teaching_base: BrowserPublicationManifest | None = None
    locator_repair_hints: Mapping[str, tuple[LocatorHint, ...]] = field(
        default_factory=dict, repr=False,
    )
    approved_checkpoint: PinnedCheckpoint | None = None
    profile_retention_seconds: int | None = None
    profile_cleanup_contexts: (
        Callable[[], Awaitable[tuple[BrowserProfileContext, ...]]] | None
    ) = None


@dataclass(frozen=True, slots=True, repr=False)
class RegisteredProductionInputs:
    """Real operator facts; shared app authorities are wired in this repository.

    A reviewed factory supplies these immutable Site-specific objects and owns
    their lifetimes. No actor, grant, binding, key or browser asset is invented
    here. Source registration and current DB authority are checked downstream.
    """

    source: RegisteredLocalSite
    binding: BrowserBindingFact
    deployment: LocalChromiumDeployment
    decision: DecisionProvider | None
    provider_pools: BrowserProviderPoolRegistry
    proof_context: BrowserClaimProofContext = field(repr=False)
    publication_grants: BrowserPublicationGrants
    cleanup_authority: BrowserCleanupAuthorityPort
    payload_keys: Mapping[str, bytes] = field(repr=False)
    active_payload_key_id: str
    resource_keys: Mapping[str, bytes] = field(repr=False)
    active_resource_key_id: str
    request_digest_keys: Mapping[str, bytes] = field(repr=False)
    active_request_digest_key_id: str
    input_digest_key: bytes = field(repr=False)
    result_digest_key: bytes = field(repr=False)
    input_mode: Literal["chat", "structured"] = "structured"
    ttl_seconds: int = 60
    execution_timeout_seconds: int | None = None
    publication_review: BrowserPublicationReviewPort | None = None
    file_artifacts: LocalFileArtifacts | None = None
    teaching_base: BrowserPublicationManifest | None = None
    locator_repair_hints: Mapping[str, tuple[LocatorHint, ...]] = field(
        default_factory=dict, repr=False,
    )
    approved_checkpoint: PinnedCheckpoint | None = None
    profile_retention_seconds: int | None = None
    profile_cleanup_contexts: (
        Callable[[], Awaitable[tuple[BrowserProfileContext, ...]]] | None
    ) = None
    file_artifact_root: Path | None = None


def build_registered_production_installation(
    components: ProductionComponents, inputs: RegisteredProductionInputs,
    *, artifact_scope_create: bool = True,
) -> LocalBrowserInstallationDependencies:
    """Bind one real registration to the already built main Runtime authorities."""
    from app.composition import ProductionComponents

    if (type(components) is not ProductionComponents
            or type(inputs) is not RegisteredProductionInputs
            or type(inputs.source) is not RegisteredLocalSite
            or not isinstance(inputs.cleanup_authority, PostgreSQLLocalCleanupGrant)
            or inputs.cleanup_authority._sessions is not components.browser_session_factory
            or inputs.cleanup_authority._binder is not components.session_binder
            or inputs.cleanup_authority._binding != BrowserBindingKey(
                inputs.binding.tenant_id, inputs.binding.ai_user_id,
                inputs.binding.target_system, inputs.binding.binding_id,
            )
            or inputs.cleanup_authority._publication
            != bytes.fromhex(inputs.source.manifest.digest)):
        raise ValueError("browser_production_inputs_invalid")
    files = inputs.file_artifacts
    if inputs.file_artifact_root is not None:
        if files is not None or type(artifact_scope_create) is not bool:
            raise ValueError("browser_file_authority_installation_invalid")
        scope = initialize_file_artifact_storage_scope(
            inputs.file_artifact_root, create=artifact_scope_create,
        )
        files = LocalFileArtifacts(
            inputs.file_artifact_root, PostgreSQLBrowserFileArtifactAuthority(
                components.browser_session_factory, dict(inputs.request_digest_keys),
                inputs.active_request_digest_key_id, storage_scope=scope,
            ),
        )
    return LocalBrowserInstallationDependencies(
        session_factory=components.browser_session_factory,
        capability_registry=components.browser_capability_registry,
        session_binder=components.session_binder, policy=components.browser_policy,
        source=inputs.source, binding=inputs.binding,
        deployment=inputs.deployment, decision=inputs.decision,
        provider_pools=inputs.provider_pools, proof_context=inputs.proof_context,
        publication_grants=inputs.publication_grants,
        cleanup_authority=inputs.cleanup_authority,
        cleanup_authorize=inputs.cleanup_authority.authorize_run,
        payload_keys=inputs.payload_keys,
        active_payload_key_id=inputs.active_payload_key_id,
        resource_keys=inputs.resource_keys,
        active_resource_key_id=inputs.active_resource_key_id,
        request_digest_keys=inputs.request_digest_keys,
        active_request_digest_key_id=inputs.active_request_digest_key_id,
        input_digest_key=inputs.input_digest_key,
        result_digest_key=inputs.result_digest_key,
        llm_provider=(components.browser_llm_provider
                      if inputs.input_mode == "chat" else None),
        structured_output=(components.browser_structured_output
                           if inputs.input_mode == "chat" else None),
        intent_model=(components.browser_intent_model
                      if inputs.input_mode == "chat" else None),
        trace=components.browser_trace, sessions=components.browser_sessions,
        worker_id="browser_" + inputs.source.manifest.digest[:32], enabled=True,
        ttl_seconds=inputs.ttl_seconds, input_mode=inputs.input_mode,
        execution_timeout_seconds=inputs.execution_timeout_seconds,
        publication_review=inputs.publication_review,
        file_artifacts=files,
        teaching_base=inputs.teaching_base,
        locator_repair_hints=inputs.locator_repair_hints,
        approved_checkpoint=inputs.approved_checkpoint,
        profile_retention_seconds=inputs.profile_retention_seconds,
        profile_cleanup_contexts=inputs.profile_cleanup_contexts,
    )


@asynccontextmanager
async def open_registered_local_decision(
    *, source: RegisteredLocalSite, checkpoint: PinnedCheckpoint,
    endpoint_origin: str, request_model: str, deployment_model: str,
) -> AsyncIterator[DecisionHTTPProvider]:
    """Own the isolated local Decision transport for one approved Site.

    The caller keeps this context open across the API/worker lifetime and passes
    its provider into LocalBrowserInstallationDependencies. No model is loaded
    or source registered by constructing this client.
    """
    model = source.manifest.site.decision_manifest
    if (source.manifest.source_kind != "registered_site"
            or model.request_model != request_model
            or model.deployment_model != deployment_model
            or checkpoint.digest != model.manifest_digest
            or checkpoint.deployment != deployment_model):
        raise ValueError("browser_local_decision_installation_invalid")
    deployment = DecisionDeployment(
        disposition="local_model", endpoint_origin=endpoint_origin,
        manifest_digest=checkpoint.digest,
        registered_sources=(source.manifest.site.source,),
    )
    async with httpx2.AsyncClient(
        base_url=endpoint_origin, trust_env=False, follow_redirects=False,
        timeout=30,
    ) as client:
        yield DecisionHTTPProvider(client, LocalChoiceCodec(), deployment)


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
    source = deps.source
    if ((deps.profile_retention_seconds is None)
            != (deps.profile_cleanup_contexts is None)):
        raise ValueError("browser_profile_cleanup_schedule_invalid")
    synthetic = type(source) is FixedSyntheticSource
    registered = type(source) is RegisteredLocalSite
    if ((not registered and deps.profile_retention_seconds is not None)
            or not (synthetic or registered)
            or type(deps.deployment) is not LocalChromiumDeployment
            or deps.deployment.enabled is not True
            or type(deps.provider_pools) is not BrowserProviderPoolRegistry
            or type(deps.proof_context) is not BrowserClaimProofContext
            or not callable(deps.cleanup_authorize)
            or not callable(getattr(deps.cleanup_authority, "check_recovery", None))
            or not callable(getattr(deps.cleanup_authority, "check_cleanup", None))):
        raise ValueError("browser_local_installation_invalid")
    if synthetic and deps.locator_repair_hints:
        raise ValueError("browser_repair_installation_invalid")
    if isinstance(source, RegisteredLocalSite):
        if any(
            type(hints) is not tuple or len(hints) > 32
            or any(not isinstance(hint, LocatorHint) for hint in hints)
            or len({(hint.kind, hint.value) for hint in hints}) != len(hints)
            for hints in deps.locator_repair_hints.values()
        ):
            raise ValueError("browser_repair_installation_invalid")
        expected_repairs = {
            (step_id, hint.kind, hint.value)
            for step_id, hints in deps.locator_repair_hints.items()
            for hint in hints
        }
        installed_repairs = {
            (item.step_id, item.hint.kind, item.hint.value)
            for item in source.rules.repair_targets
        }
        if expected_repairs != installed_repairs:
            raise ValueError("browser_repair_installation_invalid")
    replay_lookup: InstalledReplayProofLookup | None = None
    review: PostgreSQLTeachingPublicationReview | None = None
    if registered:
        if deps.publication_review is None:
            replay_lookup = InstalledReplayProofLookup(deps.session_factory)
            review = PostgreSQLTeachingPublicationReview(
                deps.session_factory, replay_lookup,
            )
        elif isinstance(deps.publication_review, PostgreSQLTeachingPublicationReview):
            review = deps.publication_review
            existing_lookup = review._load_proof
            if type(existing_lookup) is InstalledReplayProofLookup:
                replay_lookup = existing_lookup
        if (review is None or review._sessions is not deps.session_factory
                or replay_lookup is None
                or replay_lookup._sessions is not deps.session_factory
                or (isinstance(source, RegisteredLocalSite)
                    and bool(source.download_expectations)
                    and not isinstance(deps.file_artifacts, LocalFileArtifacts))):
            raise ValueError("browser_local_review_installation_invalid")
    if registered and review is None:
        raise ValueError("browser_local_review_installation_invalid")
    if registered and isinstance(source, RegisteredLocalSite):
        decision = deps.decision
        checkpoint = deps.approved_checkpoint
        if decision is None:
            if (
                checkpoint is not None or deps.input_mode != "structured"
                or source.manifest.site.decision_budget.max_candidates != 1
                or source.manifest.site.exploration is not None
            ):
                raise ValueError("browser_local_decision_installation_invalid")
        elif (type(decision) is not DecisionHTTPProvider
              or not isinstance(checkpoint, PinnedCheckpoint)
              or decision._deployment.disposition != "local_model"
              or checkpoint.digest != source.manifest.site.decision_manifest.manifest_digest
              or checkpoint.deployment
              != source.manifest.site.decision_manifest.deployment_model
              or decision._deployment.manifest_digest != checkpoint.digest
              or decision._deployment.registered_sources
              != (source.manifest.site.source,)):
            raise ValueError("browser_local_decision_installation_invalid")
        if source.manifest.site.exploration is not None:
            suggester = source.exploration_suggest
            if (not isinstance(suggester, RuntimeLLMExplorationSuggester)
                    or deps.llm_provider is None
                    or suggester.provider is not deps.llm_provider
                    or deps.intent_model is None
                    or suggester.model != deps.intent_model):
                raise ValueError("browser_runtime_exploration_installation_invalid")
    file_authority: PostgreSQLBrowserFileArtifactAuthority | None = None
    if (registered and isinstance(source, RegisteredLocalSite)
            and source.download_expectations):
        assert deps.file_artifacts is not None
        installed_file_authority = deps.file_artifacts._authority
        if (not isinstance(installed_file_authority, PostgreSQLBrowserFileArtifactAuthority)
                or installed_file_authority._sessions is not deps.session_factory
                or installed_file_authority.storage_scope
                != initialize_file_artifact_storage_scope(deps.file_artifacts._root, create=False)):
            raise ValueError("browser_file_authority_installation_invalid")
        file_authority = installed_file_authority
    wrapped: BrowserReadExecutionFactory
    source_verifier: BrowserRegisteredSourceVerifier
    if isinstance(source, FixedSyntheticSource):
        builder = (build_fixed_synthetic_observe_only_source
               if (deps.source.manifest.skill.version == OBSERVE_SKILL_VERSION
                   or deps.source.manifest.skill.version.startswith(OBSERVE_SKILL_VERSION + "_"))
               else build_fixed_synthetic_visible_query_source
               if deps.source.manifest.skill.version == VISIBLE_QUERY_SKILL_VERSION
               else build_fixed_synthetic_diagnostic_source
               if deps.source.manifest.skill.version == DIAGNOSTIC_SKILL_VERSION
               else build_fixed_synthetic_query_source)
        expected = (build_fixed_synthetic_observe_only_source(
                    deps.source.manifest.site.decision_manifest,
                    attempt_id=observe_attempt_id_from_version(deps.source.manifest.skill.version),
                ) if builder is build_fixed_synthetic_observe_only_source
                else builder(deps.source.manifest.site.decision_manifest))
        if (source.manifest != expected.manifest or source.html != expected.html
                or source.rules != expected.rules or source.region != expected.region
                or type(source.projector) is not type(expected.projector)):
            raise ValueError("browser_local_source_invalid")
    elif isinstance(source, RegisteredLocalSite) and (
            source.manifest.source_kind != "registered_site"
            or source.manifest.capability.target_system != deps.binding.target_system
            or deps.deployment.offline_manifest is None
            or deps.deployment.assets_root is None
            or source.registration_digest()
            != source.manifest.site.source.fixture_digest):
        raise ValueError("browser_local_source_invalid")
    chat_parser: BrowserChatParserPort | None
    if deps.input_mode == "structured":
        chat_parser = (
            FrozenSyntheticStructuredParser(seed=source.manifest)
            if isinstance(source, FixedSyntheticSource) else None
        )
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
    driver = LocalPlaywrightDriver(deps.deployment)
    resources = LocalBrowserResources(
        deps.deployment, deps.proof_context, deps.cleanup_authority, driver,
        file_artifacts=deps.file_artifacts if registered else None,
    )
    factory = LocalBrowserReadExecutionFactory(
        deps.source, resources, deps.binding, deps.decision, ttl_seconds=deps.ttl_seconds,
        execution_timeout_seconds=deps.execution_timeout_seconds,
        session_factory=deps.session_factory if registered else None,
        file_artifacts=deps.file_artifacts if registered else None,
    )
    async def no_resource(run: RunSnapshot) -> bool:
        return await leases._prove_no_run_resource(run)

    async def no_resource_in_session(transaction: object, run: RunSnapshot) -> bool:
        if not isinstance(transaction, AsyncSession):
            raise BrowserLeaseError("browser_lease_stale")
        return await leases._prove_no_run_resource_in_session(transaction, run)

    lifecycle = LocalBrowserReadLifecycle(
        factory, deps.cleanup_authority,
        no_resource=no_resource, no_resource_in_session=no_resource_in_session,
    )
    if isinstance(source, FixedSyntheticSource):
        wrapped = FixedSourceReadFactory(factory, source)
        source_verifier = FixedSyntheticSourceVerifier(wrapped)
    else:
        wrapped = factory
        source_verifier = RegisteredLocalSiteVerifier(factory, source)
    unsupported = UnsupportedLocalProfiles()
    async def readiness(run: RunSnapshot, session: AsyncSession | None) -> bool:
        if (run.owner.tenant_id != deps.binding.tenant_id
                or run.owner.user_id != deps.binding.ai_user_id
                or run.admission.binding_id != deps.binding.binding_id
                or run.admission.binding_revision != deps.binding.binding_revision
                or run.admission.target_system != deps.binding.target_system
                or run.admission.publication_digest.hex() != deps.source.manifest.digest):
            return False
        # Only this candidate's persisted progress/reservation is exempt.
        # worker_epoch alone cannot exempt a retry that still needs a new slot.
        if (run.cancel_requested or run.verification == "verified"
                or run.effect != "not_sent" or run.lease_epoch is not None
                or run.phase not in {"queued", "acquiring"}):
            return True
        if not driver.admitting:
            if not driver.ready or not await driver.probe():
                return False
        elif driver.ready and not await driver.probe():
            return False
        held = (await leases.has_run_lease(run) if session is None
                else await leases._has_run_lease_in_session(session, run))
        if held:
            return True
        return (await leases.has_capacity(deps.binding, deps.deployment.provider_key)
                if session is None else await leases._has_capacity_in_session(
                    session, deps.binding, deps.deployment.provider_key,
                ))

    async def business_ready(run: RunSnapshot) -> bool:
        return await readiness(run, None)

    async def business_ready_in_session(session: AsyncSession, run: RunSnapshot) -> bool:
        # Both hints are sequential in this exact claim transaction.
        return await readiness(run, session)

    async def request_teaching_review(
        owner: BrowserOwner, manifest: BrowserPublicationManifest,
        draft_digest: str, replay_digest: str, request_id: str,
    ) -> HumanGateRequest:
        if (not isinstance(source, RegisteredLocalSite)
                or not isinstance(review, PostgreSQLTeachingPublicationReview)
                or owner.tenant_id != deps.binding.tenant_id
                or manifest != source.manifest
                or await deps.publication_grants.check(
                    owner, manifest.skill.skill_id, "activate", manifest.digest,
                ) is not True):
            raise ValueError("browser_teaching_review_denied")
        return await review.request_review(
            owner, manifest, draft_digest, replay_digest, request_id,
        )

    async def decide_teaching_review(
        owner: BrowserOwner, manifest: BrowserPublicationManifest,
        draft_digest: str, replay_digest: str, request_id: str,
        decision: Literal["confirmed", "rejected"],
    ) -> HumanGateDecisionRecord:
        if (not isinstance(source, RegisteredLocalSite)
                or not isinstance(review, PostgreSQLTeachingPublicationReview)
                or owner.tenant_id != deps.binding.tenant_id
                or manifest != source.manifest
                or await deps.publication_grants.check(
                    owner, manifest.skill.skill_id, "activate", manifest.digest,
                ) is not True):
            raise ValueError("browser_teaching_review_denied")
        return await review.decide_review(
            owner, manifest, draft_digest, replay_digest, request_id, decision,
        )

    async def request_initial_review(
        owner: BrowserOwner, manifest: BrowserPublicationManifest,
        task_id: str, run_id: str, request_id: str,
    ) -> HumanGateRequest:
        if (not isinstance(source, RegisteredLocalSite)
                or not isinstance(review, PostgreSQLTeachingPublicationReview)
                or deps.teaching_base is not None
                or owner.tenant_id != deps.binding.tenant_id
                or manifest != source.manifest
                or await deps.publication_grants.check(
                    owner, manifest.skill.skill_id, "activate", manifest.digest,
                ) is not True):
            raise ValueError("browser_initial_review_denied")
        return await review.request_initial(
            owner, manifest, task_id, run_id, request_id,
        )

    async def decide_initial_review(
        owner: BrowserOwner, manifest: BrowserPublicationManifest,
        task_id: str, run_id: str, request_id: str,
        decision: Literal["confirmed", "rejected"],
    ) -> HumanGateDecisionRecord:
        if (not isinstance(source, RegisteredLocalSite)
                or not isinstance(review, PostgreSQLTeachingPublicationReview)
                or deps.teaching_base is not None
                or owner.tenant_id != deps.binding.tenant_id
                or manifest != source.manifest
                or await deps.publication_grants.check(
                    owner, manifest.skill.skill_id, "activate", manifest.digest,
                ) is not True):
            raise ValueError("browser_initial_review_denied")
        return await review.decide_initial(
            owner, manifest, task_id, run_id, request_id, decision,
        )

    configured = build_browser_vertical(BrowserVerticalDependencies(
        session_factory=deps.session_factory, capability_registry=deps.capability_registry,
        session_binder=deps.session_binder, policy=deps.policy, tenant_id=deps.binding.tenant_id,
        seed=deps.source.manifest, publication_grants=deps.publication_grants,
        source_verifier=source_verifier, publication_review=review,
        payload_keys=deps.payload_keys,
        active_payload_key_id=deps.active_payload_key_id,
        request_digest_keys=deps.request_digest_keys,
        active_request_digest_key_id=deps.active_request_digest_key_id,
        input_digest_key=deps.input_digest_key, result_digest_key=deps.result_digest_key,
        execution_factory=wrapped, lifecycle=lifecycle,
        profile_capture_proof=resources if registered else unsupported,
        profile_cleanup_proof=resources if registered else unsupported,
        profile_cleanup_authority=(
            RegisteredLocalProfileCleanupAuthority(
                deps.cleanup_authority, deps.binding, source,
            ) if isinstance(source, RegisteredLocalSite) else unsupported
        ), cancel_check=lifecycle.check_cancel,
        cancel_check_in_session=lifecycle.check_cancel_in_session,
        cleanup_authorize=deps.cleanup_authorize, cleanup_check=lifecycle.check_cleanup,
        chat_parser=chat_parser,
        chat_bindings=PostgreSQLBrowserChatBindingResolver(
            deps.session_factory, deps.session_binder, seed=deps.source.manifest,
        ),
        trace=deps.trace, sessions=deps.sessions, worker_id=deps.worker_id,
        enabled=True, worker_ttl_seconds=deps.ttl_seconds, business_ready=business_ready,
        business_ready_in_session=business_ready_in_session,
        confirmed_key_digest=lambda digest, key: deps.proof_context.digest(
            "browser-local-admitted-key.v1", digest + key.encode("utf-8"),
        ).hex(),
        file_artifacts=deps.file_artifacts if registered else None,
        teaching_base=deps.teaching_base if registered else None,
        locator_repair_hints=deps.locator_repair_hints if registered else {},
        teaching_review_request=request_teaching_review if registered else None,
        teaching_review_decide=decide_teaching_review if registered else None,
        initial_review_request=(request_initial_review if registered
                                and deps.teaching_base is None else None),
        initial_review_decide=(decide_initial_review if registered
                               and deps.teaching_base is None else None),
        profile_retention_seconds=deps.profile_retention_seconds,
    ))
    if configured is None:
        raise ValueError("browser_local_composition_unavailable")
    if replay_lookup is not None:
        replay_lookup.install_runs(configured.runs)
    if file_authority is not None:
        file_authority.install_runs(configured.runs)
    leases = PostgreSQLBrowserLeaseStore(
        session_factory=deps.session_factory, current_auth=configured.current_auth,
        binding_reader=configured.binding_reader, registry=deps.provider_pools,
        resource_cipher=BrowserResourceCipher(
            deps.resource_keys, active_key_id=deps.active_resource_key_id,
        ),
        proof_context=deps.proof_context, proof_verifier=resources,
        cleanup_authority=deps.cleanup_authority, resource_subject=resources,
        current_in_session=configured.current_auth.check_current_in_session,
    )
    factory.install_authority(
        leases=leases, current_auth=configured.current_auth,
        binding_reader=configured.binding_reader,
        lease_reserved=leases.has_run_lease,
    )
    if registered:
        factory.install_profiles(configured.profiles, configured.cipher)

    file_cleanup = (
        FileArtifactCleanupScheduler(deps.file_artifacts)
        if registered and deps.file_artifacts is not None else None
    )
    profile_cleanup = (
        ProfileCleanupScheduler(configured.profiles, deps.profile_cleanup_contexts)
        if registered and deps.profile_cleanup_contexts is not None else None
    )

    async def start_resources() -> None:
        await resources.start()
        try:
            if file_cleanup is not None:
                await file_cleanup.start()
            if profile_cleanup is not None:
                await profile_cleanup.start()
        except Exception:
            if profile_cleanup is not None:
                await profile_cleanup.stop()
            if file_cleanup is not None:
                await file_cleanup.stop()
            await resources.stop()
            raise

    async def stop_resources() -> None:
        if profile_cleanup is not None:
            await profile_cleanup.stop()
        if file_cleanup is not None:
            await file_cleanup.stop()
        # Supervisor cancellation normally settles the claimed Run. Reconcile
        # any still-owned local resource before closing the shared transport.
        if configured._active_owners or configured._cleanup_owners:
            raise RuntimeError("browser_worker_shutdown_pending")
        unsettled = False
        candidates = dict(configured.worker.shutdown_unsettled_runs)
        for state in tuple(factory._states.values()):
            if state.resource is not None and not state.released:
                candidates[state.run.run_id] = state.run
        for run in candidates.values():
            try:
                historical = await configured.runs.get_for_cleanup(
                    run.owner, run.task_id, run.run_id,
                )
                if historical.cleanup not in {"released", "terminated", "quarantined"}:
                    historical = await configured.worker.cleanup(historical)
                if (historical.status not in {"completed", "failed", "cancelled"}
                        or historical.cleanup not in {"released", "terminated", "quarantined"}):
                    unsettled = True
            except Exception:
                unsettled = True
        if unsettled:
            raise RuntimeError("browser_worker_shutdown_pending")
        configured.worker.shutdown_unsettled_runs.clear()
        configured.worker.shutdown_unsettled = False
        try:
            await resources.stop()
        except Exception as error:
            raise RuntimeError("browser_worker_shutdown_pending") from error

    reviewed = (
        reviewed_publication_authority(
            configured, source.manifest, deps.publication_grants, review,
        ) if isinstance(source, RegisteredLocalSite)
        and isinstance(review, PostgreSQLTeachingPublicationReview)
        else None
    )
    return replace(
        configured, _start_resources=start_resources, _stop_resources=stop_resources,
        reviewed_publication=reviewed,
    )


def build_production_browser_vertical(
    components: ProductionComponents, settings: ProductionSettings,
    supplied: (
        LocalBrowserInstallationDependencies | tuple[LocalBrowserInstallationDependencies, ...]
    ),
) -> BrowserVerticalComponents | BrowserVerticalCollection:
    """Validate and assemble the same registered lanes for API and worker hosts."""
    installations = supplied if isinstance(supplied, tuple) else (supplied,)
    if not installations or settings.browser.transport != "playwright":
        raise RuntimeError("browser_production_authority_mismatch")
    model_settings = (
        settings.browser.decision_origin,
        settings.browser.request_model,
        settings.browser.deployment_model,
    )
    built: list[BrowserVerticalComponents] = []
    for installation in installations:
        if (type(installation) is not LocalBrowserInstallationDependencies
                or not isinstance(installation.source, RegisteredLocalSite)
                or installation.session_factory is not components.browser_session_factory
                or installation.capability_registry
                is not components.browser_capability_registry
                or installation.session_binder is not components.session_binder
                or installation.policy is not components.browser_policy
                or installation.sessions is not components.browser_sessions
                or installation.trace is not components.browser_trace):
            raise RuntimeError("browser_production_authority_mismatch")
        site = installation.source.manifest.site
        if all(value is None for value in model_settings):
            if (
                site.decision_budget.max_candidates != 1
                or site.exploration is not None
                or installation.input_mode != "structured"
                or installation.decision is not None
                or installation.approved_checkpoint is not None
            ):
                raise RuntimeError("browser_production_authority_mismatch")
        elif (
            any(value is None for value in model_settings)
            or type(installation.decision) is not DecisionHTTPProvider
            or installation.decision._deployment.disposition != "local_model"
            or installation.decision._deployment.endpoint_origin != model_settings[0]
            or site.decision_manifest.request_model != model_settings[1]
            or site.decision_manifest.deployment_model != model_settings[2]
        ):
            raise RuntimeError("browser_production_authority_mismatch")
        installed = build_local_browser_vertical(installation)
        if installed is None:
            raise RuntimeError("browser_vertical_installation_required")
        built.append(installed)
    return built[0] if len(built) == 1 else BrowserVerticalCollection(tuple(built))
