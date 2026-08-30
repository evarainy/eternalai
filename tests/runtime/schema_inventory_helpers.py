"""Non-test Runtime construction helper exercised by the schema inventory probe."""

from __future__ import annotations

from typing import Any

from app.infra.sdui.response_envelope_builder import ResponseEnvelopeBuilder
from app.ports.capability_gateway import ExecutionResult
from app.runtime.models import CapabilityRef
from app.runtime.response_projection import ProjectionContractSnapshot
from app.runtime.runtime import RuntimeImpl
from tests.runtime.schema_inventory_factory_helpers import build_inventory_capability


def build_completed_envelope(
    capability_id: str,
    schema: dict[str, Any],
    data: dict[str, Any],
) -> Any:
    capability = build_inventory_capability(capability_id, schema)
    runtime = RuntimeImpl.__new__(RuntimeImpl)
    runtime._response_builder = ResponseEnvelopeBuilder()
    return runtime._build_envelope(
        "SYNTHETIC_RESPONSE_ID",
        "SYNTHETIC_TASK_ID",
        "SYNTHETIC_SESSION_ID",
        ExecutionResult(
            status="completed",
            data=data,
            trace_id="SYNTHETIC_EXECUTION_TRACE_ID",
        ),
        "SYNTHETIC_TRACE_ID",
        CapabilityRef(capability_id=capability_id),
        capability=capability,
        projection_snapshot=ProjectionContractSnapshot.from_capability(capability),
    )


__all__ = ("build_completed_envelope",)
