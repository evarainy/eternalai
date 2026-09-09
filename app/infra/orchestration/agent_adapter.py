"""Production implementation of the existing single-turn orchestration behavior."""

from __future__ import annotations

from typing import Any, Mapping

from app.contracts.sdui.models import ConfirmCardPayload
from app.infra.sdui.response_envelope_builder import ResponseEnvelopeBuilder
from app.ports.agent_orchestration import (
    AgentCapabilitySelection,
    AgentResponseContext,
    AgentTaskVersionBindings,
    ConfirmationPreview,
    OrchestrationContractError,
)
from app.ports.capability_gateway import CapabilityGatewayPort, ExecutionResult, RequestOrgContext
from app.ports.capability_registry import (
    CapabilityRegistryPort,
    CapabilitySpec,
    CapabilityTargetSystem,
    CapabilityType,
)
from app.ports.human_gate import VersionBinding, VersionBindingMismatchError
from app.ports.response_envelope import ResponseEnvelope, TargetSystem
from app.ports.response_projection_contract import ProjectionContractSnapshot
from app.ports.workflow_engine import WorkflowEnginePort
from app.runtime.response_projection import project_response_data
from app.version_binding import capability_version_bindings, merge_version_bindings
from app.workflow.models import WorkflowRunResult


class AgentOrchestrationAdapter:
    def __init__(
        self,
        *,
        capability_registry: CapabilityRegistryPort,
        gateway: CapabilityGatewayPort,
        workflow_engine: WorkflowEnginePort | None,
        response_builder: ResponseEnvelopeBuilder,
    ) -> None:
        self._capability_registry = capability_registry
        self._gateway = gateway
        self._workflow_engine = workflow_engine
        self._response_builder = response_builder

    async def select_capability(
        self,
        *,
        capability_id: str,
        target_system: CapabilityTargetSystem | None,
        capability_type: CapabilityType | None,
    ) -> AgentCapabilitySelection | None:
        selector = capability_id
        exact_match = await self._capability_registry.get(selector)
        if exact_match is not None:
            if exact_match.status == "active" and _matches_intent_constraints(
                exact_match,
                target_system,
                capability_type,
            ):
                return AgentCapabilitySelection(exact_match, "exact_id")
            return None

        normalized_selector = _normalize_intent_tag(selector)
        if not normalized_selector:
            return None
        active_capabilities = await self._capability_registry.list(
            target_system=target_system,
            type=capability_type,
            status="active",
        )
        matches = [
            capability
            for capability in active_capabilities
            if capability.status == "active"
            and _matches_intent_constraints(capability, target_system, capability_type)
            and normalized_selector
            in {
                normalized_tag
                for tag in capability.intent_tags
                if (normalized_tag := _normalize_intent_tag(tag))
            }
        ]
        if len(matches) != 1:
            return None
        return AgentCapabilitySelection(matches[0], "unique_intent_tag")

    async def resolve_task_version_bindings(
        self,
        *,
        capability: CapabilitySpec,
        intent_version_binding: VersionBinding,
    ) -> AgentTaskVersionBindings:
        if capability.type == "workflow":
            if self._workflow_engine is None:
                raise VersionBindingMismatchError(
                    "Workflow version binding requires a configured engine"
                )
            workflow_bindings = await self._workflow_engine.version_bindings(
                workflow_capability=capability,
            )
            resource_bindings = workflow_bindings.bindings
            projection_binding = workflow_bindings.workflow_binding
        else:
            resource_bindings = capability_version_bindings(capability)
            matching = tuple(
                binding
                for binding in resource_bindings
                if binding.resource_type == "tool"
                and binding.resource_id == capability.capability_id
            )
            if len(matching) != 1:
                raise VersionBindingMismatchError(
                    "Selected capability has no unique projection binding"
                )
            projection_binding = matching[0]
        return AgentTaskVersionBindings(
            bindings=merge_version_bindings(
                (intent_version_binding,),
                resource_bindings,
            ),
            projection_binding=projection_binding,
        )

    async def execute_capability(
        self,
        *,
        task_id: str,
        session_id: str,
        ai_user_id: str,
        capability: CapabilitySpec,
        arguments: dict[str, Any],
        request_context: RequestOrgContext,
    ) -> ExecutionResult:
        if capability.type == "workflow":
            if self._workflow_engine is None:
                return ExecutionResult(
                    status="failed",
                    error_code="internal_error",
                    trace_id=request_context.request_id,
                )
            result = await self._workflow_engine.execute(
                workflow_id=capability.capability_id,
                expected_version=capability.version,
                workflow_capability=capability,
                task_id=task_id,
                session_id=session_id,
                ai_user_id=ai_user_id,
                initial_input=arguments,
                request_context=request_context,
            )
            return _workflow_execution_result(result)
        return await self._gateway.execute_capability(
            task_id,
            session_id,
            ai_user_id,
            capability.capability_id,
            arguments,
            request_context,
        )

    async def resume_capability(
        self,
        *,
        task_id: str,
        confirmed: bool,
        expected_action_digest: str | None = None,
    ) -> ExecutionResult:
        if self._workflow_engine is None:
            raise RuntimeError("pending Workflow has no configured engine")
        result = await self._workflow_engine.resume(
            task_id=task_id,
            confirmed=confirmed,
            expected_action_digest=expected_action_digest,
        )
        return _workflow_execution_result(result)

    def prepare_confirmation(
        self,
        *,
        capability_id: str,
        arguments: Mapping[str, Any],
        capability: CapabilitySpec | None,
    ) -> ConfirmationPreview:
        payload = _confirm_card_payload(
            capability_id,
            arguments,
            capability,
            _target_system_for_capability(capability_id),
        )
        return ConfirmationPreview(
            capability_id=payload["capability_id"],
            operation_summary=payload["operation_summary"],
            target_system=payload["target_system"],
            field_names=tuple(payload["field_names"]),
            displayed_argument_values=tuple(payload["displayed_argument_values"].items()),
        )

    def build_response(
        self,
        *,
        context: AgentResponseContext,
        execution: ExecutionResult,
        projection: ProjectionContractSnapshot | None,
        confirmation: ConfirmationPreview | None = None,
    ) -> ResponseEnvelope:
        target_system = _target_system_for_capability(context.capability_id)
        if execution.status == "completed":
            data = project_response_data(
                execution.data,
                projection.load_output_schema() if projection is not None else None,
            )
            message = _format_capability_response(
                context.capability_id,
                data,
            )
            return self._response_builder.build_message(
                context.response_id,
                context.task_id,
                context.session_id,
                message,
                "Operation completed.",
                context.trace_id,
                status="completed",
                data=data,
            )
        if execution.status == "denied":
            return self._response_builder.build_policy_denied(
                context.response_id,
                context.task_id,
                context.session_id,
                "无权限，操作被拒绝",
                "Access denied.",
                context.trace_id,
            )
        if execution.status == "binding_required":
            if execution.error_code == "needs_binding_scope":
                return self._response_builder.build_operator_handback(
                    context.response_id,
                    context.task_id,
                    context.session_id,
                    "请先选择明确的账套、设备域或资源范围后继续",
                    "Binding scope required.",
                    context.trace_id,
                    target_system=target_system,
                )
            identity_message, identity_fallback = _identity_block_message(execution.error_code)
            return self._response_builder.build_operator_handback_bind_required(
                context.response_id,
                context.task_id,
                context.session_id,
                identity_message,
                identity_fallback,
                context.trace_id,
                target_system,
                reason_code=execution.error_code or "identity_unbound",
            )
        if execution.status == "timeout":
            return self._response_builder.build_failed(
                context.response_id,
                context.task_id,
                context.session_id,
                "操作超时，请重试",
                "Gateway timeout.",
                execution.trace_id or context.trace_id,
            )
        if execution.status == "failed":
            return self._response_builder.build_failed(
                context.response_id,
                context.task_id,
                context.session_id,
                "操作失败",
                "Operation failed.",
                execution.trace_id or context.trace_id,
            )
        if execution.status == "no_capability_found":
            return self._response_builder.build_no_capability_found(
                context.response_id,
                context.task_id,
                context.session_id,
                "暂未接入该能力",
                "No capability found.",
                context.trace_id,
            )
        if confirmation is None:
            raise OrchestrationContractError("Waiting response requires a confirmation preview")
        if confirmation.capability_id != context.capability_id:
            raise OrchestrationContractError(
                "Confirmation preview capability differs from response"
            )
        if confirmation.target_system != target_system:
            raise OrchestrationContractError("Confirmation preview target differs from response")
        return self._response_builder.build_confirm_card(
            context.response_id,
            context.task_id,
            context.session_id,
            "请确认提交操作",
            "Please confirm.",
            context.trace_id,
            payload=confirmation.to_payload(),
            target_system=target_system,
        )


def _normalize_intent_tag(value: str) -> str:
    return value.strip().casefold()


def _matches_intent_constraints(
    capability: CapabilitySpec,
    target_system: CapabilityTargetSystem | None,
    capability_type: CapabilityType | None,
) -> bool:
    if target_system is not None and capability.target_system != target_system:
        return False
    return capability_type is None or capability.type == capability_type


def _workflow_execution_result(result: WorkflowRunResult) -> ExecutionResult:
    if result.status == "completed":
        return ExecutionResult(
            status="completed",
            data=result.output,
            error_code=result.error_code,
            trace_id=result.trace_id,
        )
    if result.status == "denied":
        return ExecutionResult(
            status="denied",
            error_code=result.error_code,
            trace_id=result.trace_id,
        )
    if result.status == "waiting_confirm":
        return ExecutionResult(
            status="waiting_user",
            error_code=result.error_code,
            trace_id=result.trace_id,
        )
    if result.status == "timeout":
        return ExecutionResult(
            status="timeout",
            error_code=result.error_code,
            trace_id=result.trace_id,
        )
    if result.status == "failed":
        return ExecutionResult(
            status="failed",
            error_code=result.error_code,
            trace_id=result.trace_id,
        )
    raise AssertionError("unsupported Workflow terminal status")


def _identity_block_message(error_code: str | None) -> tuple[str, str]:
    if error_code == "identity_expired":
        return (
            "账号绑定或上游会话已过期，请重新认证或重新绑定后继续",
            "Identity binding or upstream session expired; reauthentication required.",
        )
    if error_code == "identity_revoked":
        return "账号绑定已撤销，请重新绑定后继续", "Identity binding revoked."
    return "需要绑定账号才能继续", "Identity binding required."


def _target_system_for_capability(capability_id: str) -> TargetSystem | None:
    if capability_id.startswith("oa."):
        return "oa"
    if capability_id.startswith("u8."):
        return "u8"
    if capability_id.startswith(("ivms.", "hikvision_ivms.")):
        return "hikvision_ivms"
    return None


def _confirm_card_payload(
    capability_id: str,
    arguments: Mapping[str, Any],
    capability: CapabilitySpec | None,
    target_system: TargetSystem | None,
) -> dict[str, Any]:
    payload = ConfirmCardPayload(
        capability_id=capability_id,
        operation_summary=_operation_summary(capability),
        target_system=target_system,
        field_names=_confirm_field_names(arguments, capability),
        displayed_argument_values=_displayed_argument_values(
            arguments,
            capability,
        ),
    )
    return payload.model_dump()


def _confirm_field_names(
    arguments: Mapping[str, Any],
    capability: CapabilitySpec | None,
) -> list[str]:
    if capability is None:
        return []
    properties = capability.input_schema.get("properties")
    if not isinstance(properties, dict):
        return []
    return sorted(key for key in arguments if key in properties)


def _displayed_argument_values(
    arguments: Mapping[str, Any],
    capability: CapabilitySpec | None,
) -> dict[str, str]:
    if capability is None:
        return {}
    properties = capability.input_schema.get("properties")
    if not isinstance(properties, dict):
        return {}

    displayed: dict[str, str] = {}
    for field_name in capability.displayable_argument_fields:
        if field_name not in properties or field_name not in arguments:
            continue
        value = arguments[field_name]
        if value is None or not isinstance(value, (str, int, float, bool)):
            continue
        rendered = str(value)
        if len(rendered) <= 200:
            displayed[field_name] = rendered
    return displayed


def _operation_summary(capability: CapabilitySpec | None) -> str:
    if capability is None:
        return ""
    return f"{capability.name}：{capability.short_description}"


def _format_capability_response(
    capability_id: str,
    data: dict[str, Any] | None,
) -> str:
    if not data:
        return "操作完成"

    if capability_id == "oa.list_pending_workflows":
        workflows = data.get("workflows")
        count = len(workflows) if isinstance(workflows, list) else 0
        # Keep the conversational projection narrow: the dedicated to-do module
        # proves completeness upstream, while only titles belong in plain text.
        titles = _joined_scalar_values(workflows, ("title",))
        prefix = f"OA待办共{count}条{_completeness_note(data, '待办')}"
        return f"{prefix}: {titles}" if titles else prefix

    if capability_id == "oa.list_system_messages":
        messages = data.get("messages")
        count = len(messages) if isinstance(messages, list) else 0
        titles = _joined_scalar_values(messages, ("title",))
        prefix = f"OA系统消息返回{count}条{_completeness_note(data, '消息')}"
        return f"{prefix}: {titles}" if titles else prefix

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

    return "操作完成"


def _completeness_note(data: dict[str, Any], noun: str) -> str:
    """Report completeness only when the producer actually claims it.

    A producer that omits ``is_complete`` makes no claim; calling the result
    incomplete there would state a fact we do not have.
    """

    is_complete = data.get("is_complete")
    if is_complete is True:
        return "（结果完整）"
    if is_complete is False:
        return f"（结果不完整，可能还有更多{noun}）"
    return ""


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


__all__ = ("AgentOrchestrationAdapter",)
