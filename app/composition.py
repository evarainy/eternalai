"""Application composition root for the Runtime implementation."""

from __future__ import annotations

import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.admin.actions import ADMIN_LITE_POLICY_CAPABILITY_IDS
from app.admin.registry import AdminRegistryService
from app.db.session import make_async_session_factory
from app.evaluator import TerminalEvaluator
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
from app.infra.observability.noop_trace_writer import NoopTraceWriter
from app.infra.observability.postgresql_trace import (
    PostgreSQLTraceReader,
    PostgreSQLTraceWriter,
)
from app.infra.policy.minimal_policy_guard import MinimalPolicyGuard
from app.infra.sdui.response_envelope_builder import ResponseEnvelopeBuilder
from app.knowledge import BasicKnowledge
from app.memory import SessionMemory
from app.ports.auth import AuthenticationPort, CredentialStorePort, SessionTokenPort
from app.ports.capability_gateway import CapabilityGatewayPort
from app.ports.capability_registry import CapabilityRegistryPort
from app.ports.identity_mapping import IdentityMappingPort
from app.ports.llm_provider import LLMProviderPort
from app.ports.structured_output import StructuredOutputPort
from app.ports.task_store import SessionStorePort, TaskStorePort
from app.ports.trace import TracePort, TraceQueryPort
from app.runtime.runtime import RuntimeImpl
from app.workflow.engine import WorkflowEngine


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


def build_admin_registry_service(
    *,
    capability_registry: CapabilityRegistryPort,
    task_store: TaskStorePort,
    identity_mapping: IdentityMappingPort,
    trace_port: TracePort,
    trace_query: TraceQueryPort,
) -> AdminRegistryService:
    """Wire Admin Lite with the closed management-action allowlist."""
    return AdminRegistryService(
        capability_registry=capability_registry,
        task_store=task_store,
        identity_mapping=identity_mapping,
        policy_guard=MinimalPolicyGuard(
            admin_capability_ids=ADMIN_LITE_POLICY_CAPABILITY_IDS
        ),
        trace_port=trace_port,
        trace_query=trace_query,
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
) -> RuntimeImpl:
    """Wire the frozen Runtime dependencies without adding adapter behavior."""
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
    )


__all__ = (
    "build_authentication_port",
    "build_admin_registry_service",
    "build_credential_store",
    "build_principal_role_reader",
    "build_runtime",
    "build_session_binder",
    "build_session_token_port",
    "build_trace_port",
    "build_trace_query",
)
