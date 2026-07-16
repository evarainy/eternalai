"""Application composition root for the Runtime implementation."""

from __future__ import annotations

from app.infra.sdui.response_envelope_builder import ResponseEnvelopeBuilder
from app.ports.capability_gateway import CapabilityGatewayPort
from app.ports.capability_registry import CapabilityRegistryPort
from app.ports.structured_output import StructuredOutputPort
from app.ports.task_store import SessionStorePort, TaskStorePort
from app.ports.trace import TracePort
from app.runtime.runtime import RuntimeImpl


def build_runtime(
    *,
    task_store: TaskStorePort,
    session_store: SessionStorePort,
    capability_registry: CapabilityRegistryPort,
    gateway: CapabilityGatewayPort,
    trace_port: TracePort,
    structured_output: StructuredOutputPort,
) -> RuntimeImpl:
    """Wire the six frozen Runtime dependencies without adding behavior."""
    return RuntimeImpl(
        task_store=task_store,
        session_store=session_store,
        capability_registry=capability_registry,
        gateway=gateway,
        trace_port=trace_port,
        structured_output=structured_output,
        response_builder=ResponseEnvelopeBuilder(),
    )


__all__ = ("build_runtime",)
