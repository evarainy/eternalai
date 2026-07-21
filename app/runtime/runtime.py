"""Minimal Runtime main chain implementation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Literal
from uuid import uuid4

from app.infra.sdui.response_envelope_builder import ResponseEnvelopeBuilder
from app.ports.capability_gateway import (
    CapabilityGatewayPort,
    ExecutionResult,
    ExecutionStatus,
    RequestOrgContext,
)
from app.ports.capability_registry import CapabilityRegistryPort, CapabilitySpec
from app.ports.llm_provider import LLMProviderPort
from app.ports.response_envelope import ResponseEnvelope, TargetSystem
from app.ports.structured_output import StructuredOutputPort
from app.ports.task_store import (
    SessionRecord,
    SessionStorePort,
    TaskEventRecord,
    TaskRecord,
    TaskStatus,
    TaskStorePort,
)
from app.ports.trace import TraceEventStatus, TraceEventType, TracePort
from app.runtime.intent_router import IntentFailureReason, IntentRouter
from app.runtime.models import CapabilityRef
from app.workflow.engine import WorkflowEngine


@dataclass(frozen=True)
class _CapabilitySelection:
    capability: CapabilitySpec
    rule: Literal["exact_id", "unique_intent_tag"]


class RuntimeImpl:
    def __init__(
        self,
        task_store: TaskStorePort,
        session_store: SessionStorePort,
        capability_registry: CapabilityRegistryPort,
        gateway: CapabilityGatewayPort,
        trace_port: TracePort,
        llm_provider: LLMProviderPort,
        structured_output: StructuredOutputPort,
        intent_model: str,
        response_builder: ResponseEnvelopeBuilder,
        workflow_engine: WorkflowEngine | None = None,
    ) -> None:
        self._task_store = task_store
        self._session_store = session_store
        self._capability_registry = capability_registry
        self._gateway = gateway
        self._trace_port = trace_port
        self._intent_router = IntentRouter(
            llm_provider=llm_provider,
            structured_output=structured_output,
            model=intent_model,
        )
        self._response_builder = response_builder
        self._workflow_engine = workflow_engine

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

        intent_result = await self._intent_router.parse(
            message,
            trace_metadata={
                "trace_id": trace_id,
                "task_id": task_id,
            },
        )
        capability_ref = intent_result.capability_ref
        parse_ok = capability_ref is not None

        await self._trace_port.record_step(
            trace_id,
            task_id,
            session_id,
            event_type="intent_parsed",
            status="ok" if parse_ok else "failed",
            attributes=_intent_trace_attributes(
                capability_ref,
                intent_result.failure_reason,
            ),
        )

        if not parse_ok or capability_ref is None:
            return await self._finish_no_capability_found(
                response_id,
                task_id,
                session_id,
                trace_id,
                reason=intent_result.failure_reason or "schema_invalid",
            )

        intent_selector = capability_ref.capability_id
        selection = await self._select_capability(capability_ref)
        if selection is None:
            return await self._finish_no_capability_found(
                response_id,
                task_id,
                session_id,
                trace_id,
                reason="no_unique_active_candidate",
            )
        selected_capability = selection.capability
        capability_ref = capability_ref.model_copy(
            update={"capability_id": selected_capability.capability_id}
        )

        await self._task_store.append_event(
            task_id,
            TaskEventRecord(
                event_id=str(uuid4()),
                task_id=task_id,
                event_type="capability_selected",
                timestamp=datetime.now(UTC),
                payload={
                    "capability_id": selected_capability.capability_id,
                    "selection_rule": selection.rule,
                },
            ),
        )

        await self._trace_port.record_step(
            trace_id,
            task_id,
            session_id,
            event_type="capability_selected",
            status="ok",
            capability_id=capability_ref.capability_id,
            attributes={
                "intent_fingerprint": _intent_fingerprint(intent_selector),
                "selection_rule": selection.rule,
            },
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
        if selected_capability.type == "workflow":
            if self._workflow_engine is None:
                exec_result = ExecutionResult(
                    status="failed",
                    error_code="internal_error",
                    trace_id=trace_id,
                )
            else:
                sid = session_id
                workflow_result = await self._workflow_engine.execute(
                    workflow_id=selected_capability.capability_id,
                    expected_version=selected_capability.version,
                    task_id=task_id,
                    session_id=sid,
                    ai_user_id=ai_user_id,
                    initial_input=capability_ref.arguments,
                    request_context=request_context,
                )
                exec_result = ExecutionResult(
                    status="completed",
                    data=workflow_result.output,
                    trace_id=workflow_result.trace_id,
                )
        else:
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

    async def _select_capability(
        self,
        intent: CapabilityRef,
    ) -> _CapabilitySelection | None:
        selector = intent.capability_id
        exact_match = await self._capability_registry.get(selector)
        if exact_match is not None:
            if exact_match.status == "active" and _matches_intent_constraints(
                exact_match,
                intent,
            ):
                return _CapabilitySelection(exact_match, "exact_id")
            return None

        normalized_selector = _normalize_intent_tag(selector)
        if not normalized_selector:
            return None
        active_capabilities = await self._capability_registry.list(
            target_system=intent.target_system,
            type=intent.capability_type,
            status="active",
        )
        matches = [
            capability
            for capability in active_capabilities
            if capability.status == "active"
            and _matches_intent_constraints(capability, intent)
            and normalized_selector
            in {
                normalized_tag
                for tag in capability.intent_tags
                if (normalized_tag := _normalize_intent_tag(tag))
            }
        ]
        if len(matches) != 1:
            return None
        return _CapabilitySelection(matches[0], "unique_intent_tag")

    async def _finish_no_capability_found(
        self,
        response_id: str,
        task_id: str,
        session_id: str,
        trace_id: str,
        reason: str,
    ) -> ResponseEnvelope:
        await self._task_store.update_status(
            task_id,
            "no_capability_found",
            "capability_not_found",
        )
        await self._trace_port.record_step(
            trace_id,
            task_id,
            session_id,
            event_type="no_capability_found",
            status="blocked",
            error_code="capability_not_found",
            attributes={"reason": reason},
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
            error_code="capability_not_found",
        )
        await self._trace_port.finalize_task_trace(
            trace_id,
            task_id,
            session_id,
            status="blocked",
            error_code="capability_not_found",
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
                    "请先选择明确的账套、设备域或资源范围后继续",
                    "Binding scope required.",
                    trace_id,
                    target_system=target_system,
                )
            identity_message, identity_fallback = _identity_block_message(
                exec_result.error_code
            )
            return self._response_builder.build_operator_handback_bind_required(
                response_id,
                task_id,
                session_id,
                identity_message,
                identity_fallback,
                trace_id,
                target_system,
                reason_code=exec_result.error_code or "identity_unbound",
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


def _identity_block_message(error_code: str | None) -> tuple[str, str]:
    if error_code == "identity_expired":
        return "账号绑定已过期，请重新绑定后继续", "Identity binding expired."
    if error_code == "identity_revoked":
        return "账号绑定已撤销，请重新绑定后继续", "Identity binding revoked."
    return "需要绑定账号才能继续", "Identity binding required."


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


def _normalize_intent_tag(value: str) -> str:
    return value.strip().casefold()


def _matches_intent_constraints(
    capability: CapabilitySpec,
    intent: CapabilityRef,
) -> bool:
    if intent.target_system is not None and capability.target_system != intent.target_system:
        return False
    return intent.capability_type is None or capability.type == intent.capability_type


def _intent_trace_attributes(
    intent: CapabilityRef | None,
    failure_reason: IntentFailureReason | None,
) -> dict[str, Any]:
    if intent is None:
        return {
            "result": "invalid",
            "reason": failure_reason or "schema_invalid",
        }
    attributes: dict[str, Any] = {
        "result": "valid",
        "intent_fingerprint": _intent_fingerprint(intent.capability_id),
    }
    if intent.target_system is not None:
        attributes["target_system"] = intent.target_system
    if intent.capability_type is not None:
        attributes["capability_type"] = intent.capability_type
    return attributes


def _intent_fingerprint(selector: str) -> str:
    return sha256(selector.encode("utf-8")).hexdigest()


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
