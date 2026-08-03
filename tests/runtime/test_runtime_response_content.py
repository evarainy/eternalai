"""Runtime response content and UI mapping tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

import pytest

from app.infra.adapters.oa.adapter import OAReadAdapter
from app.infra.adapters.oa.provider import ReplayOAReadProvider
from app.infra.gateway.capability_gateway import CapabilityGateway
from app.infra.llm.mock_llm.mock_llm_provider import MockLLMProvider
from app.infra.llm.mock_structured_output.mock_structured_output_provider import (
    MockStructuredOutputProvider,
)
from app.infra.sdui.response_envelope_builder import ResponseEnvelopeBuilder
from app.ports.capability_gateway import ExecutionResult, RequestOrgContext
from app.ports.response_envelope import ResponseEnvelope
from app.ports.task_store import SessionRecord, TaskEventRecord, TaskRecord
from app.runtime.models import CapabilityRef
from app.runtime.runtime import RuntimeImpl
from tests.runtime.registry_fakes import StaticCapabilityRegistry

REPO_ROOT = Path(__file__).resolve().parents[2]
SYSTEM_MESSAGE_CONTRACT_PACK = (
    REPO_ROOT
    / "tests"
    / "contract_packs"
    / "oa"
    / "ecology9-system-messages-v1"
)


class SpyTaskStore:
    def __init__(self) -> None:
        self.created: list[TaskRecord] = []
        self.status_updates: list[tuple[str, str, str | None]] = []

    async def create_task(self, record: TaskRecord) -> TaskRecord:
        self.created.append(record)
        return record

    async def get_task(self, task_id: str) -> TaskRecord | None:
        return None

    async def update_status(
        self,
        task_id: str,
        status: str,
        error_code: str | None = None,
    ) -> TaskRecord:
        self.status_updates.append((task_id, status, error_code))
        created = self.created[0]
        return TaskRecord(
            task_id=task_id,
            session_id=created.session_id,
            ai_user_id=created.ai_user_id,
            status=cast(Any, status),
            trace_id=created.trace_id,
            error_code=error_code,
        )

    async def append_event(self, task_id: str, event: Any) -> None:
        return None

    async def list_tasks(
        self,
        *,
        session_id: str | None = None,
        ai_user_id: str | None = None,
    ) -> list[TaskRecord]:
        return []

    async def list_events(self, task_id: str) -> list[TaskEventRecord]:
        return []


class ExistingSessionStore:
    async def create_session(self, record: SessionRecord) -> SessionRecord:
        return record

    async def get_session(self, session_id: str) -> SessionRecord | None:
        return SessionRecord(session_id=session_id)


class SpyTracePort:
    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []

    def set_sanitizer(self, hook: Any) -> None:
        return None

    async def record_event(self, event: Any) -> None:
        return None

    async def start_task_trace(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
    ) -> None:
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
        self.steps.append(
            {
                "event_type": event_type,
                "status": status,
                "capability_id": capability_id,
                "error_code": error_code,
                "attributes": attributes or {},
            }
        )

    async def record_policy_decision(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
        status: str,
        capability_id: str | None = None,
        error_code: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        return None

    async def record_gateway_call(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
        status: str,
        capability_id: str | None = None,
        error_code: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        return None

    async def finalize_task_trace(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
        status: str,
        capability_id: str | None = None,
        error_code: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        return None


class SpyGateway:
    def __init__(self, result: ExecutionResult) -> None:
        self.result = result

    async def execute_capability(
        self,
        task_id: str,
        session_id: str,
        ai_user_id: str,
        capability_id: str,
        arguments: dict[str, Any],
        request_context: RequestOrgContext,
    ) -> ExecutionResult:
        return self.result


def _run_runtime(
    gateway_result: ExecutionResult,
    *,
    capability_id: str = "oa.get_workflow_status",
    malformed: bool = False,
) -> ResponseEnvelope:
    async def exercise_runtime() -> ResponseEnvelope:
        message = f"message for {capability_id}"
        structured_output = MockStructuredOutputProvider()
        if malformed:
            structured_output.register_malformed(message, CapabilityRef)
        else:
            structured_output.register(
                message,
                CapabilityRef,
                CapabilityRef(capability_id=capability_id),
            )
        runtime = RuntimeImpl(
            task_store=SpyTaskStore(),
            session_store=ExistingSessionStore(),
            capability_registry=StaticCapabilityRegistry(capability_id),
            gateway=SpyGateway(gateway_result),
            trace_port=SpyTracePort(),
            llm_provider=MockLLMProvider(),
            structured_output=structured_output,
            intent_model="test-intent-model",
            response_builder=ResponseEnvelopeBuilder(),
        )
        return await runtime.handle_user_message(
            channel="web",
            ai_user_id="ai-user-1",
            session_id="session-1",
            message=message,
            client_capabilities={},
        )

    return asyncio.run(exercise_runtime())


def test_completed_response_message_is_sourced_from_adapter_data() -> None:
    envelope = _run_runtime(
        ExecutionResult(
            status="completed",
            data={"workflow_id": "OA-WF-2026-0001", "current_step": "approved"},
            trace_id="tr-completed",
        )
    )

    assert envelope.status == "completed"
    assert "OA-WF-2026-0001" in envelope.message
    assert "approved" in envelope.message


def test_system_message_response_discloses_incomplete_result_scope() -> None:
    envelope = _run_runtime(
        ExecutionResult(
            status="completed",
            data={
                "messages": [
                    {"message_id": "90000001", "title": "合成系统消息标题"}
                ],
                "returned_count": 1,
                "is_complete": False,
            },
            trace_id="tr-system-messages-partial",
        ),
        capability_id="oa.list_system_messages",
    )

    assert envelope.status == "completed"
    assert "OA系统消息返回1条" in envelope.message
    assert "结果不完整，可能还有更多消息" in envelope.message
    assert "合成系统消息标题" in envelope.message


def test_system_message_response_can_report_complete_result_scope() -> None:
    envelope = _run_runtime(
        ExecutionResult(
            status="completed",
            data={
                "messages": [],
                "returned_count": 0,
                "is_complete": True,
            },
            trace_id="tr-system-messages-complete",
        ),
        capability_id="oa.list_system_messages",
    )

    assert envelope.status == "completed"
    assert envelope.message == "OA系统消息返回0条（结果完整）"


def test_system_message_replay_runs_from_natural_language_through_real_gateway() -> None:
    async def exercise_runtime() -> ResponseEnvelope:
        capability_id = "oa.list_system_messages"
        message = "我有什么系统消息"
        structured_output = MockStructuredOutputProvider()
        structured_output.register(
            message,
            CapabilityRef,
            CapabilityRef(capability_id=capability_id),
        )
        registry = StaticCapabilityRegistry(capability_id)
        gateway = CapabilityGateway(
            adapter=OAReadAdapter(
                ReplayOAReadProvider(SYSTEM_MESSAGE_CONTRACT_PACK)
            ),
            capability_registry=registry,
        )
        runtime = RuntimeImpl(
            task_store=SpyTaskStore(),
            session_store=ExistingSessionStore(),
            capability_registry=registry,
            gateway=gateway,
            trace_port=SpyTracePort(),
            llm_provider=MockLLMProvider(),
            structured_output=structured_output,
            intent_model="test-intent-model",
            response_builder=ResponseEnvelopeBuilder(),
        )
        return await runtime.handle_user_message(
            channel="web",
            ai_user_id="ai-user-system-message",
            session_id="session-system-message",
            message=message,
            client_capabilities={},
        )

    envelope = asyncio.run(exercise_runtime())

    assert envelope.status == "completed"
    assert "OA系统消息返回20条" in envelope.message
    assert "结果不完整，可能还有更多消息" in envelope.message


def test_no_capability_found_uses_operator_handback_none_without_degrading() -> None:
    envelope = _run_runtime(
        ExecutionResult(status="completed", trace_id="unused"),
        capability_id="unknown.capability",
        malformed=True,
    )

    assert envelope.status == "no_capability_found"
    assert envelope.ui.component_type == "operator_handback_card"
    assert envelope.ui.action == "none"


def test_policy_denied_uses_operator_handback_none_without_failed_degradation() -> None:
    envelope = _run_runtime(
        ExecutionResult(
            status="denied",
            error_code="policy_denied",
            trace_id="tr-denied",
        )
    )

    assert envelope.status == "blocked"
    assert envelope.ui.component_type == "operator_handback_card"
    assert envelope.ui.action == "none"


def test_identity_unbound_uses_operator_handback_bind_required_for_target_system() -> None:
    envelope = _run_runtime(
        ExecutionResult(
            status="binding_required",
            error_code="identity_unbound",
            trace_id="tr-bind",
        ),
        capability_id="oa.list_pending_workflows",
    )

    assert envelope.status == "blocked"
    assert envelope.ui.component_type == "operator_handback_card"
    assert envelope.ui.action == "bind_required"
    assert envelope.ui.target_system == "oa"
    assert envelope.ui.reason_code == "identity_unbound"


@pytest.mark.parametrize(
    ("error_code", "message_fragment"),
    (
        ("identity_expired", "过期"),
        ("identity_revoked", "撤销"),
    ),
)
def test_inactive_identity_has_stable_explanatory_handback(
    error_code: str,
    message_fragment: str,
) -> None:
    envelope = _run_runtime(
        ExecutionResult(
            status="binding_required",
            error_code=cast(Any, error_code),
            trace_id=f"tr-{error_code}",
        ),
        capability_id="oa.list_pending_workflows",
    )

    assert envelope.status == "blocked"
    assert envelope.ui.component_type == "operator_handback_card"
    assert envelope.ui.action == "bind_required"
    assert envelope.ui.reason_code == error_code
    assert message_fragment in envelope.message
    if error_code == "identity_expired":
        assert "重新认证" in envelope.message


def test_needs_binding_scope_has_matching_reason_and_clarify_action() -> None:
    envelope = _run_runtime(
        ExecutionResult(
            status="binding_required",
            error_code="needs_binding_scope",
            trace_id="tr-needs-binding-scope",
        ),
        capability_id="u8.get_document_status",
    )

    assert envelope.status == "blocked"
    assert envelope.ui.component_type == "operator_handback_card"
    assert envelope.ui.action == "clarify_scope"
    assert envelope.ui.reason_code == "needs_binding_scope"
    assert envelope.ui.target_system == "u8"


def test_confirm_required_card_carries_target_system() -> None:
    envelope = _run_runtime(
        ExecutionResult(
            status="waiting_user",
            error_code="confirm_required",
            trace_id="tr-confirm",
        ),
        capability_id="oa.submit_leave_request",
    )

    assert envelope.status == "waiting_user"
    assert envelope.ui.component_type == "confirm_card"
    assert envelope.ui.action == "confirm"
    assert envelope.ui.target_system == "oa"
