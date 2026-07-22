"""Application composition root for the Runtime implementation."""

from __future__ import annotations

from app.infra.sdui.response_envelope_builder import ResponseEnvelopeBuilder
from app.knowledge import BasicKnowledge
from app.memory import SessionMemory
from app.ports.capability_gateway import CapabilityGatewayPort
from app.ports.capability_registry import CapabilityRegistryPort
from app.ports.llm_provider import LLMProviderPort
from app.ports.structured_output import StructuredOutputPort
from app.ports.task_store import SessionStorePort, TaskStorePort
from app.ports.trace import TracePort
from app.runtime.runtime import RuntimeImpl
from app.workflow.engine import WorkflowEngine


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
    )


__all__ = ("build_runtime",)
