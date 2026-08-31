"""Application composition root for the Runtime implementation."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from functools import partial
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.admin.actions import (
    ADMIN_AUDIT_READ_POLICY_CAPABILITY_IDS,
    ADMIN_LITE_POLICY_CAPABILITY_IDS,
)
from app.admin.registry import (
    AdminBindingMutationService,
    AdminRegistryService,
    AdminRegistryServiceWithBindingMutations,
)
from app.api.v1.credential_bindings import CredentialBindingService
from app.api.v1.health import HealthCheck
from app.api.v1.work_objects import (
    OA_PENDING_WORKFLOWS_CAPABILITY_ID,
    WorkObjectService,
)
from app.config import ProductionSettings
from app.credential_polling import (
    CREDENTIAL_POLLING_TASK_TYPE,
    CredentialPollingPolicy,
    CredentialPollingScheduler,
    CredentialPollingService,
)
from app.db.health import check_database_health
from app.db.session import make_async_session_factory
from app.evaluator import TerminalEvaluator
from app.execution_fabric.mock_adapters.oa.mock_oa_adapter import MockOAAdapter
from app.infra.adapters.oa.adapter import OAReadAdapter
from app.infra.adapters.oa.provider import (
    LiveOAReadProvider,
    ReplayOAReadProvider,
    report_oa_structural_drift,
)
from app.infra.auth.background import OAPasswordCredentialAcquirer
from app.infra.auth.crypto import HMACSessionToken, PrincipalSessionBinder
from app.infra.auth.oa import (
    OACredentialVerifier,
    PrincipalRoleReader,
    make_urllib_session_factory,
)
from app.infra.auth.postgresql import (
    PostgreSQLCredentialStore,
    PostgreSQLPrincipalRoleReader,
)
from app.infra.auth.secret_provider import CredentialStoreSecretProvider
from app.infra.gateway.capability_gateway import CapabilityGateway
from app.infra.health import RedisHealthCheck
from app.infra.human_gate import PostgreSQLHumanGate
from app.infra.identity.postgresql import PostgreSQLOAIdentityMapping
from app.infra.job_queue.in_memory import InMemoryJobQueue
from app.infra.llm.json_structured_output import JSONStructuredOutputProvider
from app.infra.llm.openai_compatible import OpenAICompatibleLLMProvider
from app.infra.observability.noop_trace_writer import NoopTraceWriter
from app.infra.observability.postgresql_trace import (
    PostgreSQLTraceReader,
    PostgreSQLTraceWriter,
)
from app.infra.persistence.capability_registry.repository import (
    PostgreSQLCapabilityRegistry,
)
from app.infra.persistence.task_store.postgresql import (
    PostgreSQLSessionStore,
    PostgreSQLTaskStore,
)
from app.infra.persistence.work_object.postgresql import PostgreSQLWorkObjectStore
from app.infra.policy.minimal_policy_guard import MinimalPolicyGuard
from app.infra.sdui.response_envelope_builder import ResponseEnvelopeBuilder
from app.knowledge import BasicKnowledge
from app.memory import SessionMemory
from app.ports.adapter import AdapterPort
from app.ports.auth import AuthenticationPort, CredentialStorePort, SessionTokenPort
from app.ports.capability_gateway import CapabilityGatewayPort
from app.ports.capability_registry import CapabilityRegistryPort
from app.ports.credential_binding import CredentialBindingVerifierPort
from app.ports.human_gate import HumanGatePort
from app.ports.identity_mapping import IdentityMappingPort
from app.ports.job_queue import JobQueuePort
from app.ports.llm_provider import LLMProviderPort
from app.ports.structured_output import StructuredOutputPort
from app.ports.task_store import SessionStorePort, TaskStorePort
from app.ports.trace import TracePort, TraceQueryPort
from app.runtime.runtime import RuntimeImpl
from app.workflow.engine import WorkflowEngine


@dataclass(frozen=True, slots=True)
class ProductionComponents:
    """Complete dependency set consumed by the FastAPI composition root."""

    runtime: RuntimeImpl
    admin_registry_service: AdminRegistryService
    work_object_service: WorkObjectService
    credential_binding_service: CredentialBindingService
    credential_polling_job_queue: JobQueuePort
    credential_polling_scheduler: CredentialPollingScheduler
    authentication: AuthenticationPort
    session_tokens: SessionTokenPort
    session_binder: PrincipalSessionBinder
    session_cookie_ttl_seconds: int
    health_timeout_seconds: float
    health_checks: Mapping[str, HealthCheck]


def build_credential_store(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    encryption_key: bytes,
) -> PostgreSQLCredentialStore:
    """Build encrypted OA credential persistence with an explicit key."""

    return PostgreSQLCredentialStore(
        session_factory=session_factory,
        encryption_key=encryption_key,
    )


def build_principal_role_reader(
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> PostgreSQLPrincipalRoleReader:
    """Build the fail-closed local principal-role reader."""

    return PostgreSQLPrincipalRoleReader(session_factory=session_factory)


def build_authentication_port(
    *,
    oa_base_url: str,
    oa_timeout_seconds: float,
    credential_store: CredentialStorePort,
    role_reader: PrincipalRoleReader,
    identity_hmac_key: bytes,
    credential_ttl_seconds: int,
) -> AuthenticationPort:
    """Build the OA verifier without introducing a production HTTP dependency."""

    return OACredentialVerifier(
        session_factory=make_urllib_session_factory(
            base_url=oa_base_url,
            timeout_seconds=oa_timeout_seconds,
        ),
        credential_store=credential_store,
        role_reader=role_reader,
        identity_hmac_key=identity_hmac_key,
        credential_ttl_seconds=credential_ttl_seconds,
    )


def build_session_token_port(
    *,
    signing_key: bytes,
    ttl_seconds: int,
) -> SessionTokenPort:
    """Build the EternalAI session-token signer with an explicit key."""

    return HMACSessionToken(signing_key=signing_key, ttl_seconds=ttl_seconds)


def build_session_binder(
    *,
    binding_key: bytes,
) -> PrincipalSessionBinder:
    """Build the Principal-bound conversation-session binder."""

    return PrincipalSessionBinder(binding_key=binding_key)


def build_oa_read_adapter(
    *,
    settings: ProductionSettings,
    credential_store: CredentialStorePort,
) -> AdapterPort:
    """Build the configured OA read adapter without runtime fallback."""

    _require_safe_mock_oa_configuration(settings)
    if settings.oa_read_adapter_mode == "mock":
        return MockOAAdapter()

    if settings.oa_read_adapter_mode == "replay":
        contract_pack_dir = settings.oa_read_contract_pack_dir
        if contract_pack_dir is None or not contract_pack_dir.is_dir():
            raise RuntimeError(
                "OA_READ_CONTRACT_PACK_DIR must be an existing directory"
            )
        return OAReadAdapter(ReplayOAReadProvider(contract_pack_dir))
    if settings.oa_read_adapter_mode == "live":
        pending_contract_pack_dir = (
            settings.oa_pending_workflows_contract_pack_dir
        )
        if (
            pending_contract_pack_dir is None
            or not pending_contract_pack_dir.is_dir()
        ):
            raise RuntimeError(
                "OA_PENDING_WORKFLOWS_CONTRACT_PACK_DIR must be an existing directory"
            )
        system_message_contract_pack_dir = (
            settings.oa_system_messages_contract_pack_dir
        )
        if (
            system_message_contract_pack_dir is None
            or not system_message_contract_pack_dir.is_dir()
        ):
            raise RuntimeError(
                "OA_SYSTEM_MESSAGES_CONTRACT_PACK_DIR must be an existing directory"
            )
        message_center_path = settings.oa_message_center_path
        if message_center_path is None:
            raise RuntimeError("OA_MESSAGE_CENTER_PATH is required for live mode")
        pending_split_path = settings.oa_pending_workflows_split_page_key_path
        pending_counts_path = settings.oa_pending_workflows_counts_path
        pending_datas_path = settings.oa_pending_workflows_datas_path
        pending_actiontype = settings.oa_pending_workflows_actiontype
        pending_hide_no_data_tab = (
            settings.oa_pending_workflows_hide_no_data_tab
        )
        pending_method = settings.oa_pending_workflows_method
        pending_offical_type = settings.oa_pending_workflows_offical_type
        pending_view_scope = settings.oa_pending_workflows_view_scope
        pending_sort_params = settings.oa_pending_workflows_sort_params
        system_category_id = settings.oa_system_messages_category_id
        system_bizstate = settings.oa_system_messages_bizstate
        system_select_state = settings.oa_system_messages_select_state
        if any(
            value is None
            for value in (
                pending_split_path,
                pending_counts_path,
                pending_datas_path,
                pending_actiontype,
                pending_hide_no_data_tab,
                pending_method,
                pending_offical_type,
                pending_view_scope,
                pending_sort_params,
                system_category_id,
                system_bizstate,
                system_select_state,
            )
        ):
            raise RuntimeError(
                "OA capability parameters are required for live mode"
            )
        assert pending_split_path is not None
        assert pending_counts_path is not None
        assert pending_datas_path is not None
        assert pending_actiontype is not None
        assert pending_hide_no_data_tab is not None
        assert pending_method is not None
        assert pending_offical_type is not None
        assert pending_view_scope is not None
        assert pending_sort_params is not None
        assert system_category_id is not None
        assert system_bizstate is not None
        assert system_select_state is not None
        return OAReadAdapter(
            LiveOAReadProvider(
                base_url=settings.oa_base_url,
                message_center_endpoint_path=message_center_path,
                pending_workflows_split_page_key_path=pending_split_path,
                pending_workflows_counts_path=pending_counts_path,
                pending_workflows_datas_path=pending_datas_path,
                pending_workflows_actiontype=pending_actiontype,
                pending_workflows_hide_no_data_tab=pending_hide_no_data_tab,
                pending_workflows_method=pending_method,
                pending_workflows_offical_type=pending_offical_type,
                pending_workflows_view_scope=pending_view_scope,
                pending_workflows_sort_params=pending_sort_params,
                system_messages_category_id=system_category_id,
                system_messages_bizstate=system_bizstate,
                system_messages_select_state=system_select_state,
                timeout_seconds=settings.oa_timeout_seconds,
                pending_workflows_contract_pack_dir=pending_contract_pack_dir,
                system_messages_contract_pack_dir=(
                    system_message_contract_pack_dir
                ),
                drift_reporter=report_oa_structural_drift,
                page_size=settings.oa_message_center_page_size,
            ),
            secret_provider=CredentialStoreSecretProvider(
                credential_store=credential_store
            ),
        )
    raise RuntimeError("OA_READ_ADAPTER_MODE is invalid")


def _require_safe_mock_oa_configuration(
    settings: ProductionSettings,
) -> None:
    environment_name = (
        settings.environment_name.strip().casefold() or "production"
    )
    if (
        settings.oa_read_adapter_mode == "mock"
        and environment_name != "testing"
        and not settings.phase0_mock_mode
    ):
        raise RuntimeError(
            "OA_READ_ADAPTER_MODE=mock requires ENV=testing "
            "or PHASE0_MOCK_MODE=true"
        )


def build_admin_registry_service(
    *,
    capability_registry: CapabilityRegistryPort,
    task_store: TaskStorePort,
    identity_mapping: IdentityMappingPort,
    trace_port: TracePort,
    trace_query: TraceQueryPort,
) -> AdminRegistryService:
    """Wire Admin Lite with the closed management-action allowlist."""
    policy_guard = MinimalPolicyGuard(
        admin_capability_ids=ADMIN_LITE_POLICY_CAPABILITY_IDS,
        audit_read_capability_ids=ADMIN_AUDIT_READ_POLICY_CAPABILITY_IDS,
    )
    binding_mutations = AdminBindingMutationService(
        identity_mapping=identity_mapping,
        policy_guard=policy_guard,
        trace_port=trace_port,
    )
    return AdminRegistryServiceWithBindingMutations(
        capability_registry=capability_registry,
        task_store=task_store,
        identity_mapping=identity_mapping,
        policy_guard=policy_guard,
        trace_port=trace_port,
        trace_query=trace_query,
        binding_mutations=binding_mutations,
    )


def build_trace_port(
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> TracePort:
    """Select Noop only for explicit test/mock environments."""
    if (
        os.environ.get("ENV", "").lower() == "testing"
        or os.environ.get("PHASE0_MOCK_MODE", "").lower() == "true"
    ):
        return NoopTraceWriter()
    return PostgreSQLTraceWriter(session_factory or make_async_session_factory())


def build_trace_query(
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> TraceQueryPort:
    """Build the bounded PostgreSQL trace query adapter."""
    return PostgreSQLTraceReader(session_factory or make_async_session_factory())


def build_runtime(
    *,
    task_store: TaskStorePort,
    session_store: SessionStorePort,
    capability_registry: CapabilityRegistryPort,
    gateway: CapabilityGatewayPort,
    trace_port: TracePort,
    llm_provider: LLMProviderPort,
    structured_output: StructuredOutputPort,
    intent_model: str,
    workflow_engine: WorkflowEngine | None = None,
    session_memory: SessionMemory | None = None,
    semantic_knowledge: BasicKnowledge | None = None,
    evaluator: TerminalEvaluator | None = None,
    human_gate_port: HumanGatePort | None = None,
) -> RuntimeImpl:
    """Wire the frozen Runtime dependencies without adding adapter behavior."""
    resolved_human_gate = human_gate_port
    if workflow_engine is not None and resolved_human_gate is not None:
        workflow_engine.configure_human_gate_port(resolved_human_gate)
    return RuntimeImpl(
        task_store=task_store,
        session_store=session_store,
        capability_registry=capability_registry,
        gateway=gateway,
        trace_port=trace_port,
        llm_provider=llm_provider,
        structured_output=structured_output,
        intent_model=intent_model,
        response_builder=ResponseEnvelopeBuilder(),
        workflow_engine=workflow_engine,
        session_memory=session_memory or SessionMemory(),
        semantic_knowledge=semantic_knowledge or BasicKnowledge(),
        evaluator=evaluator or TerminalEvaluator(),
        human_gate_port=resolved_human_gate,
    )


def build_production_components(
    settings: ProductionSettings,
    *,
    llm_provider: LLMProviderPort | None = None,
    structured_output: StructuredOutputPort | None = None,
    authentication: AuthenticationPort | None = None,
    credential_binding_verifier: CredentialBindingVerifierPort | None = None,
    identity_mapping: IdentityMappingPort | None = None,
    adapters: Mapping[str, AdapterPort] | None = None,
    trace_port: TracePort | None = None,
    health_checks: Mapping[str, HealthCheck] | None = None,
) -> ProductionComponents:
    """Build the real database/auth/runtime composition with explicit test seams."""

    if adapters is None:
        _require_safe_mock_oa_configuration(settings)
    if settings.oa_read_adapter_mode == "live" and (
        identity_mapping is not None or adapters is not None
    ):
        raise RuntimeError(
            "Live OA composition does not allow identity mapping or adapter overrides"
        )

    session_factory = make_async_session_factory(database_url=settings.database_url)
    task_store = PostgreSQLTaskStore(session_factory)
    session_store = PostgreSQLSessionStore(session_factory)
    capability_registry = PostgreSQLCapabilityRegistry(session_factory)
    human_gate_port = PostgreSQLHumanGate(session_factory)
    credential_store = build_credential_store(
        session_factory=session_factory,
        encryption_key=settings.credential_encryption_key,
    )
    resolved_trace_port = (
        PostgreSQLTraceWriter(session_factory)
        if trace_port is None
        else trace_port
    )
    trace_query = build_trace_query(session_factory=session_factory)
    resolved_identity_mapping = (
        PostgreSQLOAIdentityMapping(session_factory=session_factory)
        if identity_mapping is None
        else identity_mapping
    )
    resolved_adapters = (
        {"oa": build_oa_read_adapter(settings=settings, credential_store=credential_store)}
        if adapters is None
        else dict(adapters)
    )
    policy_guard = MinimalPolicyGuard()
    gateway = CapabilityGateway(
        capability_registry=capability_registry,
        identity_mapping=resolved_identity_mapping,
        policy_guard=policy_guard,
        trace_port=resolved_trace_port,
        adapters=resolved_adapters,
        human_gate_port=human_gate_port,
        unbound_task_capability_ids=frozenset(
            {OA_PENDING_WORKFLOWS_CAPABILITY_ID}
        ),
    )
    gateway.assert_production_wiring()
    production_llm = OpenAICompatibleLLMProvider(
        base_url=settings.llm_base_url,
        timeout_seconds=settings.llm_timeout_seconds,
        max_tokens=settings.llm_max_tokens,
        temperature=settings.llm_temperature,
        top_p=settings.llm_top_p,
        top_k=settings.llm_top_k,
        enable_thinking=settings.llm_enable_thinking,
    )
    resolved_llm = production_llm if llm_provider is None else llm_provider
    runtime = build_runtime(
        task_store=task_store,
        session_store=session_store,
        capability_registry=capability_registry,
        gateway=gateway,
        trace_port=resolved_trace_port,
        llm_provider=resolved_llm,
        structured_output=(
            JSONStructuredOutputProvider()
            if structured_output is None
            else structured_output
        ),
        intent_model=settings.llm_model,
        human_gate_port=human_gate_port,
    )
    resolved_authentication = (
        build_authentication_port(
            oa_base_url=settings.oa_base_url,
            oa_timeout_seconds=settings.oa_timeout_seconds,
            credential_store=credential_store,
            role_reader=build_principal_role_reader(session_factory=session_factory),
            identity_hmac_key=settings.identity_hmac_key,
            credential_ttl_seconds=settings.oa_credential_ttl_seconds,
        )
        if authentication is None
        else authentication
    )
    resolved_binding_verifier = credential_binding_verifier
    if resolved_binding_verifier is None:
        if not isinstance(resolved_authentication, OACredentialVerifier):
            raise RuntimeError(
                "A custom authentication port requires a non-persisting "
                "credential binding verifier"
            )
        resolved_binding_verifier = resolved_authentication
    session_tokens = build_session_token_port(
        signing_key=settings.session_signing_key,
        ttl_seconds=settings.session_cookie_ttl_seconds,
    )
    session_binder = build_session_binder(binding_key=settings.session_binding_key)
    admin_registry_service = build_admin_registry_service(
        capability_registry=capability_registry,
        task_store=task_store,
        identity_mapping=resolved_identity_mapping,
        trace_port=resolved_trace_port,
        trace_query=trace_query,
    )
    work_object_service = WorkObjectService(
        store=PostgreSQLWorkObjectStore(session_factory),
        gateway=gateway,
        capability_registry=capability_registry,
    )
    credential_binding_service = CredentialBindingService(
        store=credential_store,
        verifier=resolved_binding_verifier,
    )
    session_factory_for_background = make_urllib_session_factory(
        base_url=settings.oa_base_url,
        timeout_seconds=settings.oa_timeout_seconds,
    )
    credential_polling_policy = CredentialPollingPolicy(
        interval_seconds=settings.credential_poll_interval_seconds,
        maximum_backoff_seconds=settings.credential_poll_maximum_backoff_seconds,
        work_start_hour=settings.credential_poll_work_start_hour,
        work_end_hour=settings.credential_poll_work_end_hour,
        timezone_name=settings.credential_poll_timezone,
        global_concurrency=settings.credential_poll_global_concurrency,
        scheduler_tick_seconds=settings.credential_poll_scheduler_tick_seconds,
    )
    credential_polling_service = CredentialPollingService(
        binding_store=credential_store,
        acquirer=OAPasswordCredentialAcquirer(
            session_factory=session_factory_for_background,
            authentication=resolved_authentication,
            binding_store=credential_store,
        ),
        work_objects=work_object_service,
        policy=credential_polling_policy,
    )

    async def run_credential_polling_job(payload: dict[str, Any]) -> int:
        if payload:
            raise ValueError("credential polling job payload must be empty")
        return await credential_polling_service.run_due()

    credential_polling_job_queue: JobQueuePort = InMemoryJobQueue(
        handlers={CREDENTIAL_POLLING_TASK_TYPE: run_credential_polling_job},
        max_terminal_records=1,
    )

    credential_polling_scheduler = CredentialPollingScheduler(
        job_queue=credential_polling_job_queue,
        tick_seconds=credential_polling_policy.scheduler_tick_seconds,
    )
    resolved_health_checks: Mapping[str, HealthCheck]
    if health_checks is None:
        resolved_health_checks = {
            "database": partial(
                check_database_health,
                settings.database_url,
                timeout_seconds=settings.health_timeout_seconds,
            ),
            "redis": RedisHealthCheck(
                redis_url=settings.redis_url,
                timeout_seconds=settings.health_timeout_seconds,
            ),
            "vllm": partial(
                production_llm.check_health,
                settings.llm_model,
                timeout_seconds=settings.health_timeout_seconds,
            ),
        }
    else:
        resolved_health_checks = dict(health_checks)
    return ProductionComponents(
        runtime=runtime,
        admin_registry_service=admin_registry_service,
        work_object_service=work_object_service,
        credential_binding_service=credential_binding_service,
        credential_polling_job_queue=credential_polling_job_queue,
        credential_polling_scheduler=credential_polling_scheduler,
        authentication=resolved_authentication,
        session_tokens=session_tokens,
        session_binder=session_binder,
        session_cookie_ttl_seconds=settings.session_cookie_ttl_seconds,
        health_timeout_seconds=settings.health_timeout_seconds,
        health_checks=resolved_health_checks,
    )


__all__ = (
    "ProductionComponents",
    "build_authentication_port",
    "build_admin_registry_service",
    "build_credential_store",
    "build_oa_read_adapter",
    "build_production_components",
    "build_principal_role_reader",
    "build_runtime",
    "build_session_binder",
    "build_session_token_port",
    "build_trace_port",
    "build_trace_query",
)
