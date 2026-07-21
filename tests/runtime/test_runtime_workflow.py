"""RuntimePort integration for a registered lightweight Workflow."""

from __future__ import annotations

import asyncio
from typing import Any

from app.composition import build_runtime
from app.infra.llm.mock_llm.mock_llm_provider import MockLLMProvider
from app.infra.llm.mock_structured_output.mock_structured_output_provider import (
    MockStructuredOutputProvider,
)
from app.ports.capability_gateway import ExecutionResult, RequestOrgContext
from app.ports.capability_registry import CapabilitySpec
from app.ports.task_store import SessionRecord, TaskRecord
from app.runtime.models import CapabilityRef
from app.workflow.engine import WorkflowEngine
from app.workflow.models import WorkflowDefinition, WorkflowInputRef, WorkflowStep


def _capability(capability_id: str, capability_type: str) -> CapabilitySpec:
    return CapabilitySpec(
        capability_id=capability_id,
        name=capability_id,
        type=capability_type,
        input_schema_digest=f"input-{capability_id}",
        output_schema_digest=f"output-{capability_id}",
        risk_level="low",
        owner="runtime-workflow-test",
        version="1.0.0",
        status="active",
        short_description=capability_id,
        target_system="oa",
        execution_identity="user_delegated",
        binding_required=False,
    )


class Registry:
    def __init__(self, *capabilities: CapabilitySpec) -> None:
        self.items = {item.capability_id: item for item in capabilities}

    async def get(self, capability_id: str) -> CapabilitySpec | None:
        return self.items.get(capability_id)

    async def list(
        self,
        target_system: str | None = None,
        type: str | None = None,
        status: str | None = None,
    ) -> list[CapabilitySpec]:
        return list(self.items.values())


class TaskStore:
    def __init__(self) -> None:
        self.created: list[TaskRecord] = []
        self.events: list[Any] = []
        self.statuses: list[str] = []

    async def create_task(self, record: TaskRecord) -> TaskRecord:
        self.created.append(record)
        return record

    async def get_task(self, task_id: str) -> TaskRecord | None:
        return self.created[0] if self.created else None

    async def update_status(
        self, task_id: str, status: str, error_code: str | None = None
    ) -> TaskRecord:
        self.statuses.append(status)
        return self.created[0].model_copy(update={"status": status, "error_code": error_code})

    async def append_event(self, task_id: str, event: Any) -> None:
        self.events.append(event)


class SessionStore:
    async def create_session(self, record: SessionRecord) -> SessionRecord:
        return record

    async def get_session(self, session_id: str) -> SessionRecord | None:
        sid = session_id
        return SessionRecord(session_id=sid)


class Trace:
    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []

    async def start_task_trace(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def record_step(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
        event_type: str,
        status: str,
        capability_id: str | None = None,
        error_code: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        self.steps.append({"event_type": event_type, "attributes": attributes or {}})

    async def finalize_task_trace(self, *args: Any, **kwargs: Any) -> None:
        return None


class Gateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute_capability(
        self,
        task_id: str,
        session_id: str,
        ai_user_id: str,
        capability_id: str,
        arguments: dict[str, Any],
        request_context: RequestOrgContext,
    ) -> ExecutionResult:
        self.calls.append((capability_id, arguments))
        data = (
            {"document_id": "D-1"}
            if capability_id == "oa.document.lookup"
            else {"status": "approved"}
        )
        return ExecutionResult(
            status="completed",
            data=data,
            trace_id=request_context.request_id,
        )


def test_runtime_executes_registered_workflow_without_calling_workflow_as_adapter() -> None:
    async def exercise() -> tuple[Any, Gateway, TaskStore]:
        definition = WorkflowDefinition(
            workflow_id="oa.workflow.document-status",
            version="1.0.0",
            steps=(
                WorkflowStep(
                    step_id="lookup",
                    capability_id="oa.document.lookup",
                    input_mapping={
                        "document_no": WorkflowInputRef(source="workflow_input", key="document_no")
                    },
                ),
                WorkflowStep(
                    step_id="status",
                    capability_id="oa.document.status",
                    input_mapping={
                        "document_id": WorkflowInputRef(
                            source="step_output",
                            step_id="lookup",
                            key="document_id",
                        )
                    },
                ),
            ),
        )
        registry = Registry(
            _capability(definition.workflow_id, "workflow"),
            _capability("oa.document.lookup", "query"),
            _capability("oa.document.status", "query"),
        )
        task_store = TaskStore()
        trace = Trace()
        gateway = Gateway()
        engine = WorkflowEngine(
            definitions={definition.workflow_id: definition},
            capability_registry=registry,
            gateway=gateway,
            task_store=task_store,
            trace_port=trace,
        )
        structured_output = MockStructuredOutputProvider()
        structured_output.register(
            "check document",
            CapabilityRef,
            CapabilityRef(
                capability_id=definition.workflow_id,
                arguments={"document_no": "DOC-7"},
                capability_type="workflow",
            ),
        )
        runtime = build_runtime(
            task_store=task_store,
            session_store=SessionStore(),
            capability_registry=registry,
            gateway=gateway,
            trace_port=trace,
            llm_provider=MockLLMProvider(),
            structured_output=structured_output,
            intent_model="test-intent-model",
            workflow_engine=engine,
        )

        sid = "session-1"
        envelope = await runtime.handle_user_message(
            channel="mock",
            ai_user_id="user-1",
            session_id=sid,
            message="check document",
            client_capabilities={},
        )
        return envelope, gateway, task_store

    envelope, gateway, task_store = asyncio.run(exercise())

    assert [call[0] for call in gateway.calls] == [
        "oa.document.lookup",
        "oa.document.status",
    ]
    assert gateway.calls[0][1] == {"document_no": "DOC-7"}
    assert gateway.calls[1][1] == {"document_id": "D-1"}
    assert envelope.status == "completed"
    assert envelope.data == {"status": "approved"}
    assert task_store.statuses[-1] == "completed"
