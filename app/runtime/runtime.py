"""Minimal Runtime main chain implementation."""

from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from pydantic import ValidationError

from app.infra.sdui.response_envelope_builder import ResponseEnvelopeBuilder
from app.ports.capability_gateway import (
    CapabilityGatewayPort,
    ExecutionResult,
    ExecutionStatus,
    RequestOrgContext,
)
from app.ports.response_envelope import ResponseEnvelope, TargetSystem
from app.ports.structured_output import StructuredOutputPort
from app.ports.task_store import (
    SessionRecord,
    SessionStorePort,
    TaskRecord,
    TaskStatus,
    TaskStorePort,
)
from app.ports.trace import TraceEventStatus, TraceEventType, TracePort
from app.runtime.models import CapabilityRef


class RuntimeImpl:
    def __init__(
        self,
        task_store: TaskStorePort,
        session_store: SessionStorePort,
        gateway: CapabilityGatewayPort,
        trace_port: TracePort,
        structured_output: StructuredOutputPort,
        response_builder: ResponseEnvelopeBuilder,
    ) -> None:
        self._task_store = task_store
        self._session_store = session_store
        self._gateway = gateway
        self._trace_port = trace_port
        self._structured_output = structured_output
        self._response_builder = response_builder

    async def handle_user_message(
        self,
        channel: Literal["web", "cli", "api", "mock"],
        ai_user_id: str,
        session_id: str,
        message: str,
        client_capabilities: dict[str, Any],
    ) -> ResponseEnvelope:
        task_id = str(uuid4())
        trace_id = str(uuid4())
        response_id = str(uuid4())

        existing = await self._session_store.get_session(session_id)
        if existing is None:
            await self._session_store.create_session(SessionRecord(session_id=session_id))

        await self._task_store.create_task(
            TaskRecord(
                task_id=task_id,
                session_id=session_id,
                ai_user_id=ai_user_id,
                status="running",
                trace_id=trace_id,
            )
        )
        await self._trace_port.start_task_trace(trace_id, task_id, session_id)
        await self._trace_port.record_step(
            trace_id,
            task_id,
            session_id,
            event_type="task_created",
            status="ok",
        )

        result = await self._structured_output.parse_to_schema(message, CapabilityRef)
        capability_ref: CapabilityRef | None = None
        parse_ok = result.error is None and result.parsed is not None
        if parse_ok:
            try:
                capability_ref = CapabilityRef.model_validate(result.parsed)
            except ValidationError:
                parse_ok = False

        await self._trace_port.record_step(
            trace_id,
            task_id,
            session_id,
            event_type="intent_parsed",
            status="ok" if parse_ok else "failed",
        )

        if not parse_ok or capability_ref is None:
            await self._task_store.update_status(task_id, "no_capability_found")
            await self._trace_port.record_step(
                trace_id,
                task_id,
                session_id,
                event_type="no_capability_found",
                status="blocked",
            )
            envelope = self._response_builder.build_no_capability_found(
                response_id,
                task_id,
                session_id,
                "暂未接入该能力",
                "No capability found.",
                trace_id,
            )
            await self._trace_port.record_step(
                trace_id,
                task_id,
                session_id,
                event_type="response_envelope_created",
                status="ok",
            )
            await self._trace_port.record_step(
                trace_id,
                task_id,
                session_id,
                event_type="task_failed",
                status="failed",
            )
            await self._trace_port.finalize_task_trace(
                trace_id,
                task_id,
                session_id,
                status="blocked",
            )
            return envelope

        await self._trace_port.record_step(
            trace_id,
            task_id,
            session_id,
            event_type="capability_selected",
            status="ok",
            capability_id=capability_ref.capability_id,
        )
        request_context = RequestOrgContext(
            request_id=trace_id,
            channel=channel,
            account_set_id=_optional_str_argument(
                capability_ref.arguments,
                "account_set_id",
            ),
            resource_scope=_optional_str_argument(
                capability_ref.arguments,
                "resource_scope",
            ),
            device_domain_id=_optional_str_argument(
                capability_ref.arguments,
                "device_domain_id",
            ),
        )
        exec_result = await self._gateway.execute_capability(
            task_id,
            session_id,
            ai_user_id,
            capability_ref.capability_id,
            capability_ref.arguments,
            request_context,
        )
        envelope = self._build_envelope(
            response_id,
            task_id,
            session_id,
            exec_result,
            trace_id,
            capability_ref,
        )
        await self._trace_port.record_step(
            trace_id,
            task_id,
            session_id,
            event_type="response_envelope_created",
            status="ok",
        )
        final_task_status = _map_exec_to_task_status(exec_result.status)
        await self._task_store.update_status(
            task_id,
            final_task_status,
            exec_result.error_code,
        )
        terminal_event = _terminal_event_for_exec_status(exec_result.status)
        if terminal_event is not None:
            await self._trace_port.record_step(
                trace_id,
                task_id,
                session_id,
                event_type=terminal_event,
                status="ok" if terminal_event == "task_completed" else "failed",
                capability_id=capability_ref.capability_id,
                error_code=exec_result.error_code,
            )

        finalize_status = _map_task_to_finalize_status(final_task_status)
        await self._trace_port.finalize_task_trace(
            trace_id,
            task_id,
            session_id,
            status=finalize_status,
            capability_id=capability_ref.capability_id,
            error_code=exec_result.error_code,
        )
        return envelope

    def _build_envelope(
        self,
        response_id: str,
        task_id: str,
        session_id: str,
        exec_result: ExecutionResult,
        trace_id: str,
        capability_ref: CapabilityRef,
    ) -> ResponseEnvelope:
        target_system = _target_system_for_capability(capability_ref.capability_id)
        if exec_result.status == "completed":
            message = _format_capability_response(
                capability_ref.capability_id,
                exec_result.data,
            )
            return self._response_builder.build_message(
                response_id,
                task_id,
                session_id,
                message,
                "Operation completed.",
                trace_id,
                status="completed",
                data=exec_result.data,
            )
        if exec_result.status == "denied":
            return self._response_builder.build_policy_denied(
                response_id,
                task_id,
                session_id,
                "无权限，操作被拒绝",
                "Access denied.",
                trace_id,
            )
        if exec_result.status == "binding_required":
            if exec_result.error_code == "needs_binding_scope":
                return self._response_builder.build_operator_handback(
                    response_id,
                    task_id,
                    session_id,
                    "请先选择账套后继续",
                    "Binding scope required.",
                    trace_id,
                    target_system=target_system,
                )
            return self._response_builder.build_operator_handback_bind_required(
                response_id,
                task_id,
                session_id,
                "需要绑定账号才能继续",
                "Identity binding required.",
                trace_id,
                target_system,
            )
        if exec_result.status == "timeout":
            return self._response_builder.build_failed(
                response_id,
                task_id,
                session_id,
                "操作超时，请重试",
                "Gateway timeout.",
                exec_result.trace_id or trace_id,
            )
        if exec_result.status == "failed":
            return self._response_builder.build_failed(
                response_id,
                task_id,
                session_id,
                "操作失败",
                "Operation failed.",
                exec_result.trace_id or trace_id,
            )
        if exec_result.status == "no_capability_found":
            return self._response_builder.build_no_capability_found(
                response_id,
                task_id,
                session_id,
                "暂未接入该能力",
                "No capability found.",
                trace_id,
            )
        return self._response_builder.build_confirm_card(
                response_id,
                task_id,
                session_id,
                "请确认提交操作",
                "Please confirm.",
                trace_id,
                target_system=target_system,
        )


def _map_exec_to_task_status(status: ExecutionStatus) -> TaskStatus:
    if status == "completed":
        return "completed"
    if status == "no_capability_found":
        return "no_capability_found"
    if status == "waiting_user":
        return "waiting_user"
    return "failed"


def _map_exec_to_trace_status(status: ExecutionStatus) -> TraceEventStatus:
    if status == "completed":
        return "ok"
    if status in {"denied", "binding_required", "no_capability_found", "waiting_user"}:
        return "blocked"
    return "failed"


def _terminal_event_for_exec_status(status: ExecutionStatus) -> TraceEventType | None:
    if status == "completed":
        return "task_completed"
    if status == "waiting_user":
        return None
    return "task_failed"


def _map_task_to_finalize_status(status: TaskStatus) -> TraceEventStatus:
    if status in {"completed", "waiting_user"}:
        return "ok"
    if status == "no_capability_found":
        return "blocked"
    return "failed"


def _optional_str_argument(arguments: dict[str, Any], key: str) -> str | None:
    value = arguments.get(key)
    if value is None:
        return None
    return str(value)


def _target_system_for_capability(capability_id: str) -> TargetSystem | None:
    if capability_id.startswith("oa."):
        return "oa"
    if capability_id.startswith("u8."):
        return "u8"
    if capability_id.startswith(("ivms.", "hikvision_ivms.")):
        return "hikvision_ivms"
    return None


def _format_capability_response(
    capability_id: str,
    data: dict[str, Any] | None,
) -> str:
    if not data:
        return "操作完成"

    if capability_id == "oa.list_pending_workflows":
        workflows = data.get("workflows")
        count = len(workflows) if isinstance(workflows, list) else 0
        identifiers = _joined_scalar_values(workflows, ("workflow_id", "title"))
        return f"OA待办共{count}条: {identifiers}" if identifiers else f"OA待办共{count}条"

    if capability_id == "oa.get_workflow_status":
        return _join_message_parts(
            "OA流程状态",
            data.get("workflow_id"),
            data.get("current_step"),
            data.get("approver"),
        )

    if capability_id == "u8.get_document_status":
        return _join_message_parts(
            "U8单据状态",
            data.get("document_no"),
            data.get("document_status"),
            data.get("amount"),
            data.get("currency"),
        )

    if capability_id == "u8.get_vendor_balance_summary":
        return _join_message_parts(
            "供应商余额",
            data.get("vendor_id"),
            data.get("vendor_name"),
            data.get("balance"),
            data.get("currency"),
        )

    if capability_id == "ivms.get_device_online_status":
        online_text = "在线" if data.get("online") is True else "离线"
        return _join_message_parts(
            "设备状态",
            data.get("device_id"),
            online_text,
            data.get("last_seen_at"),
        )

    if capability_id == "oa.submit_leave_request.confirmed_mock":
        return _join_message_parts(
            "已提交",
            data.get("draft_id"),
            data.get("workflow_id"),
            data.get("submit_status"),
        )

    generic = _joined_scalar_values(data, tuple(data.keys()))
    return generic if generic else "操作完成"


def _join_message_parts(*parts: Any) -> str:
    return " ".join(str(part) for part in parts if part is not None and part != "")


def _joined_scalar_values(value: Any, keys: tuple[str, ...]) -> str:
    values: list[str] = []
    if isinstance(value, dict):
        for key in keys:
            item = value.get(key)
            if item is not None and not isinstance(item, (dict, list)):
                values.append(str(item))
    elif isinstance(value, list):
        for item in value:
            values.append(_joined_scalar_values(item, keys))
    return " ".join(item for item in values if item)


__all__ = ("RuntimeImpl",)
