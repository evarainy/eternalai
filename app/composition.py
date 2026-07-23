"""Application composition root for the Runtime implementation."""

from __future__ import annotations

import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.admin.actions import ADMIN_LITE_POLICY_CAPABILITY_IDS
from app.admin.registry import AdminRegistryService
from app.db.session import make_async_session_factory
from app.evaluator import TerminalEvaluator
from app.infra.observability.noop_trace_writer import NoopTraceWriter
from app.infra.observability.postgresql_trace import (
    PostgreSQLTraceReader,
    PostgreSQLTraceWriter,
)
from app.infra.policy.minimal_policy_guard import MinimalPolicyGuard
from app.infra.sdui.response_envelope_builder import ResponseEnvelopeBuilder
from app.knowledge import BasicKnowledge
from app.memory import SessionMemory
from app.ports.capability_gateway import CapabilityGatewayPort
from app.ports.capability_registry import CapabilityRegistryPort
from app.ports.identity_mapping import IdentityMappingPort
from app.ports.llm_provider import LLMProviderPort
from app.ports.structured_output import StructuredOutputPort
from app.ports.task_store import SessionStorePort, TaskStorePort
from app.ports.trace import TracePort, TraceQueryPort
from app.runtime.runtime import RuntimeImpl
from app.workflow.engine import WorkflowEngine


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
    "build_admin_registry_service",
    "build_runtime",
    "build_trace_port",
    "build_trace_query",
)
