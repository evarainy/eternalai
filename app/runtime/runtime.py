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
from app.ports.response_envelope import ResponseEnvelope
from app.ports.structured_output import StructuredOutputPort
from app.ports.task_store import (
    SessionRecord,
    SessionStorePort,
    TaskRecord,
    TaskStatus,
    TaskStorePort,
)
from app.ports.trace import TraceEventStatus, TracePort
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
            envelope = self._response_builder.build_message(
                response_id,
                task_id,
                session_id,
                "暂未找到匹配的能力",
                "No capability found.",
                trace_id,
                status="no_capability_found",
            )
            await self._trace_port.record_step(
                trace_id,
                task_id,
                session_id,
                event_type="response_envelope_created",
                status="ok",
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
        request_context = RequestOrgContext(request_id=trace_id, channel=channel)
        await self._trace_port.record_step(
            trace_id,
            task_id,
            session_id,
            event_type="gateway_pre_recorded",
            status="ok",
            capability_id=capability_ref.capability_id,
        )
        exec_result = await self._gateway.execute_capability(
            task_id,
            session_id,
            ai_user_id,
            capability_ref.capability_id,
            capability_ref.arguments,
            request_context,
        )
        gateway_trace_status = _map_exec_to_trace_status(exec_result.status)
        await self._trace_port.record_step(
            trace_id,
            task_id,
            session_id,
            event_type="gateway_post_recorded",
            status=gateway_trace_status,
            capability_id=capability_ref.capability_id,
            error_code=exec_result.error_code,
        )
        envelope = self._build_envelope(
            response_id,
            task_id,
            session_id,
            exec_result,
            trace_id,
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
        if final_task_status == "completed":
            await self._trace_port.record_step(
                trace_id,
                task_id,
                session_id,
                event_type="task_completed",
                status="ok",
                capability_id=capability_ref.capability_id,
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
    ) -> ResponseEnvelope:
        if exec_result.status == "completed":
            return self._response_builder.build_message(
                response_id,
                task_id,
                session_id,
                "操作完成",
                "Operation completed.",
                trace_id,
                status="completed",
                data=exec_result.data,
            )
        if exec_result.status == "denied":
            return self._response_builder.build_operator_handback(
                response_id,
                task_id,
                session_id,
                "无权限，操作被拒绝",
                "Access denied.",
                trace_id,
            )
        if exec_result.status == "binding_required":
            return self._response_builder.build_operator_handback(
                response_id,
                task_id,
                session_id,
                "需要绑定账号才能继续",
                "Identity binding required.",
                trace_id,
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
            return self._response_builder.build_message(
                response_id,
                task_id,
                session_id,
                "暂未找到匹配的能力",
                "No capability found.",
                trace_id,
                status="no_capability_found",
            )
        return self._response_builder.build_confirm_card(
            response_id,
            task_id,
            session_id,
            "请确认操作",
            "Please confirm.",
            trace_id,
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


def _map_task_to_finalize_status(status: TaskStatus) -> TraceEventStatus:
    if status in {"completed", "waiting_user"}:
        return "ok"
    if status == "no_capability_found":
        return "blocked"
    return "failed"


__all__ = ("RuntimeImpl",)
