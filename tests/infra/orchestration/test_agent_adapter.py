"""Argument, return-value and fail-closed guards for every orchestration method."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from dataclasses import replace
from typing import Any
from unittest.mock import Mock, call, create_autospec

import pytest

from app.infra.orchestration import agent_adapter as module
from app.infra.orchestration.agent_adapter import AgentOrchestrationAdapter
from app.infra.sdui.response_envelope_builder import ResponseEnvelopeBuilder
from app.ports.agent_orchestration import (
    AgentCapabilitySelection,
    AgentOrchestrationPort,
    AgentResponseContext,
    AgentTaskVersionBindings,
    ConfirmationPreview,
    OrchestrationContractError,
)
from app.ports.capability_gateway import CapabilityGatewayPort, ExecutionResult, RequestOrgContext
from app.ports.capability_registry import CapabilityRegistryPort, CapabilitySpec
from app.ports.human_gate import VersionBinding, VersionBindingMismatchError
from app.ports.response_projection_contract import ProjectionContractSnapshot
from app.ports.workflow_engine import WorkflowEnginePort, WorkflowVersionBindings
from app.workflow.models import WorkflowRunResult


def _capability(capability_id: str = "oa.synthetic.action", **overrides: Any) -> CapabilitySpec:
    values = {
        "capability_id": capability_id,
        "name": "合成操作",
        "type": "action",
        "intent_tags": ["synthetic-action"],
        "input_schema": {
            "type": "object",
            "properties": {
                "remark": {"type": "string"},
                "amount": {"type": "integer"},
            },
        },
        "output_schema": {"type": "object", "properties": {"result": {"type": "string"}}},
        "input_schema_digest": "synthetic-input",
        "output_schema_digest": "synthetic-output",
        "risk_level": "low",
        "owner": "orchestration-test",
        "version": "2.3.0",
        "status": "active",
        "short_description": "核对后提交",
        "target_system": "oa",
        "execution_identity": "user_delegated",
        "binding_required": False,
        "displayable_argument_fields": ["remark", "amount"],
        **overrides,
    }
    return CapabilitySpec(**values)


def _binding(resource_type: str, resource_id: str, digest: str = "a") -> VersionBinding:
    return VersionBinding(
        resource_type=resource_type,
        resource_id=resource_id,
        version="2.3.0",
        digest=digest * 64,
    )


def _context() -> RequestOrgContext:
    return RequestOrgContext(
        request_id="synthetic-request",
        channel="api",
        tenant_id="synthetic-tenant",
        account_set_id="synthetic-account",
        resource_scope="synthetic-scope",
        device_domain_id="synthetic-domain",
    )


def _adapter(*, workflow: bool = True, builder: Any = None) -> tuple[Any, Any, Any, Any]:
    registry = create_autospec(CapabilityRegistryPort, instance=True, spec_set=True)
    gateway = create_autospec(CapabilityGatewayPort, instance=True, spec_set=True)
    engine = create_autospec(WorkflowEnginePort, instance=True, spec_set=True) if workflow else None
    adapter: AgentOrchestrationPort = AgentOrchestrationAdapter(
        capability_registry=registry,
        gateway=gateway,
        workflow_engine=engine,
        response_builder=builder if builder is not None else ResponseEnvelopeBuilder(),
    )
    return adapter, registry, gateway, engine


@pytest.mark.parametrize(
    ("status", "target", "kind", "valid"),
    [
        ("active", "oa", "action", True),
        ("disabled", "oa", "action", False),
        ("active", "u8", "action", False),
        ("active", "oa", "query", False),
    ],
)
def test_select_exact_forwards_id_and_does_not_fall_back(
    status: str,
    target: str,
    kind: str,
    valid: bool,
) -> None:
    adapter, registry, gateway, engine = _adapter()
    capability = _capability(status=status, target_system=target, type=kind)
    registry.get.return_value = capability
    registry.list.return_value = [_capability()]
    selected = asyncio.run(
        adapter.select_capability(
            capability_id="oa.synthetic.action",
            target_system="oa",
            capability_type="action",
        )
    )
    assert registry.get.await_args_list == [call("oa.synthetic.action")]
    assert registry.list.await_args_list == []
    assert selected == (AgentCapabilitySelection(capability, "exact_id") if valid else None)
    assert gateway.mock_calls == engine.mock_calls == []


@pytest.mark.parametrize("count", [0, 1, 2])
@pytest.mark.parametrize("reverse", [False, True])
def test_select_tag_forwards_filters_and_requires_one_active_match(
    count: int, reverse: bool
) -> None:
    adapter, registry, gateway, engine = _adapter()
    registry.get.return_value = None
    matching = [_capability(f"oa.synthetic.{index}") for index in range(count)]
    candidates = [
        _capability("oa.disabled", status="disabled"),
        _capability("u8.wrong-system", target_system="u8"),
        _capability("oa.wrong-type", type="query"),
        _capability("oa.wrong-tag", intent_tags=["different"]),
        *matching,
    ]
    registry.list.return_value = list(reversed(candidates)) if reverse else candidates
    selected = asyncio.run(
        adapter.select_capability(
            capability_id="  SYNTHETIC-ACTION  ",
            target_system="oa",
            capability_type="action",
        )
    )
    assert registry.get.await_args_list == [call("  SYNTHETIC-ACTION  ")]
    assert registry.list.await_args_list == [
        call(target_system="oa", type="action", status="active")
    ]
    assert selected == (
        AgentCapabilitySelection(matching[0], "unique_intent_tag") if count == 1 else None
    )
    assert gateway.mock_calls == engine.mock_calls == []


@pytest.mark.parametrize("selector", ["", "   ", "oa.synthetic.action", "synthetic-action"])
def test_selection_optional_constraints_and_empty_selector(selector: str) -> None:
    adapter, registry, _, _ = _adapter()
    capability = _capability()
    registry.get.return_value = capability if selector == capability.capability_id else None
    registry.list.return_value = [capability]
    result = asyncio.run(
        adapter.select_capability(
            capability_id=selector,
            target_system=None,
            capability_type=None,
        )
    )
    assert registry.get.await_args_list == [call(selector)]
    if not selector.strip():
        assert result is None
        assert registry.list.await_args_list == []
    elif selector == capability.capability_id:
        assert result == AgentCapabilitySelection(capability, "exact_id")
        assert registry.list.await_args_list == []
    else:
        assert result == AgentCapabilitySelection(capability, "unique_intent_tag")
        assert registry.list.await_args_list == [
            call(target_system=None, type=None, status="active")
        ]


def test_resolve_workflow_bindings_forwards_and_preserves_both_fields() -> None:
    adapter, registry, gateway, engine = _adapter()
    capability = _capability(type="workflow")
    intent = _binding("prompt", "intent", "a")
    workflow_binding = _binding("workflow", capability.capability_id, "b")
    step = _binding("tool", "oa.synthetic.step", "c")
    policy = _binding("policy", "oa.synthetic.step", "d")
    engine.version_bindings.return_value = WorkflowVersionBindings(
        bindings=(workflow_binding, step, policy),
        workflow_binding=workflow_binding,
    )
    result = asyncio.run(
        adapter.resolve_task_version_bindings(
            capability=capability,
            intent_version_binding=intent,
        )
    )
    assert engine.version_bindings.await_args_list == [call(workflow_capability=capability)]
    assert result == AgentTaskVersionBindings(
        bindings=(policy, intent, step, workflow_binding),
        projection_binding=workflow_binding,
    )
    assert engine.mock_calls == [call.version_bindings(workflow_capability=capability)]
    assert registry.mock_calls == gateway.mock_calls == []


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def test_resolve_tool_bindings_and_missing_workflow_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, registry, gateway, engine = _adapter()
    capability = _capability(policy_digest="synthetic-policy")
    intent = _binding("prompt", "intent")
    helper = Mock(wraps=module.capability_version_bindings)
    monkeypatch.setattr(module, "capability_version_bindings", helper)
    result = asyncio.run(
        adapter.resolve_task_version_bindings(
            capability=capability,
            intent_version_binding=intent,
        )
    )
    tool = VersionBinding(
        resource_type="tool",
        resource_id=capability.capability_id,
        version="2.3.0",
        digest=_digest(capability.model_dump(mode="json")),
    )
    policy = VersionBinding(
        resource_type="policy",
        resource_id=capability.capability_id,
        version="2.3.0",
        digest=_digest(
            {
                "capability_id": capability.capability_id,
                "capability_version": "2.3.0",
                "policy_digest": "synthetic-policy",
            }
        ),
    )
    assert helper.call_args_list == [call(capability)]
    assert result == AgentTaskVersionBindings(
        bindings=(policy, intent, tool), projection_binding=tool
    )
    assert registry.mock_calls == gateway.mock_calls == engine.mock_calls == []
    without_engine, _, _, _ = _adapter(workflow=False)
    with pytest.raises(VersionBindingMismatchError, match="configured engine"):
        asyncio.run(
            without_engine.resolve_task_version_bindings(
                capability=_capability(type="workflow"),
                intent_version_binding=intent,
            )
        )
    conflicting = tool.model_copy(update={"digest": "f" * 64})
    # Keep merge_version_bindings' existing ValueError; do not reclassify it.
    with pytest.raises(ValueError, match="conflicting resource"):
        asyncio.run(
            adapter.resolve_task_version_bindings(
                capability=capability,
                intent_version_binding=conflicting,
            )
        )
    helper.return_value = ()
    with pytest.raises(VersionBindingMismatchError, match="unique projection binding"):
        asyncio.run(
            adapter.resolve_task_version_bindings(
                capability=capability,
                intent_version_binding=intent,
            )
        )


@pytest.mark.parametrize(
    ("status", "error"),
    [
        ("completed", None),
        ("failed", "adapter_error"),
        ("waiting_user", "confirm_required"),
    ],
)
def test_execute_gateway_forwards_every_argument_once(status: str, error: str | None) -> None:
    adapter, registry, gateway, engine = _adapter()
    capability = _capability()
    context = _context()
    arguments = {"remark": "合成", "amount": 12, "nested": {"values": [1, 2]}}
    returned = ExecutionResult(
        status=status, data={"result": "synthetic"}, error_code=error, trace_id="returned-trace"
    )
    gateway.execute_capability.return_value = returned
    result = asyncio.run(
        adapter.execute_capability(
            task_id="task-1",
            session_id="session-2",
            ai_user_id="user-3",
            capability=capability,
            arguments=arguments,
            request_context=context,
        )
    )
    assert gateway.execute_capability.await_args_list == [
        call(
            "task-1",
            "session-2",
            "user-3",
            "oa.synthetic.action",
            arguments,
            context,
        )
    ]
    assert result is returned
    assert result.model_dump() == {
        "status": status,
        "data": {"result": "synthetic"},
        "error_code": error,
        "trace_id": "returned-trace",
    }
    assert engine.mock_calls == registry.mock_calls == []


_WORKFLOW_CASES = [
    ("completed", "completed", None),
    ("waiting_confirm", "waiting_user", "confirm_required"),
    ("denied", "denied", "policy_denied"),
    ("timeout", "timeout", "adapter_timeout"),
    ("failed", "failed", "adapter_error"),
]


def _workflow_result(status: str, error: str | None) -> WorkflowRunResult:
    return WorkflowRunResult(
        workflow_id="oa.synthetic.action",
        workflow_version="2.3.0",
        trace_id="workflow-trace",
        status=status,
        output={"result": "synthetic-output"},
        step_outputs={"step": {"internal": "SYNTHETIC_PRIVATE_STEP"}},
        error_code=error,
    )


@pytest.mark.parametrize(("status", "mapped", "error"), _WORKFLOW_CASES)
def test_execute_workflow_forwards_and_maps_every_status(
    status: str, mapped: str, error: str | None
) -> None:
    adapter, registry, gateway, engine = _adapter()
    capability = _capability(type="workflow")
    context = _context()
    arguments = {"remark": "合成初始输入", "amount": 12}
    engine.execute.return_value = _workflow_result(status, error)
    result = asyncio.run(
        adapter.execute_capability(
            task_id="task-1",
            session_id="session-2",
            ai_user_id="user-3",
            capability=capability,
            arguments=arguments,
            request_context=context,
        )
    )
    assert engine.execute.await_args_list == [
        call(
            workflow_id="oa.synthetic.action",
            expected_version="2.3.0",
            workflow_capability=capability,
            task_id="task-1",
            session_id="session-2",
            ai_user_id="user-3",
            initial_input=arguments,
            request_context=context,
        )
    ]
    assert result.model_dump() == {
        "status": mapped,
        "data": {"result": "synthetic-output"} if mapped == "completed" else None,
        "error_code": error,
        "trace_id": "workflow-trace",
    }
    assert gateway.mock_calls == registry.mock_calls == []


def test_execute_without_workflow_cannot_fabricate_waiting_or_call_gateway() -> None:
    adapter, registry, gateway, _ = _adapter(workflow=False)
    result = asyncio.run(
        adapter.execute_capability(
            task_id="task-1",
            session_id="session-2",
            ai_user_id="user-3",
            capability=_capability(type="workflow"),
            arguments={},
            request_context=_context(),
        )
    )
    assert result.model_dump() == {
        "status": "failed",
        "data": None,
        "error_code": "internal_error",
        "trace_id": "synthetic-request",
    }
    assert gateway.mock_calls == registry.mock_calls == []


@pytest.mark.parametrize(("status", "mapped", "error"), _WORKFLOW_CASES)
@pytest.mark.parametrize(("confirmed", "digest"), [(True, "c" * 64), (False, None)])
def test_resume_forwards_digest_and_maps_every_status(
    status: str,
    mapped: str,
    error: str | None,
    confirmed: bool,
    digest: str | None,
) -> None:
    adapter, registry, gateway, engine = _adapter()
    engine.resume.return_value = _workflow_result(status, error)
    result = asyncio.run(
        adapter.resume_capability(
            task_id="task-resume",
            confirmed=confirmed,
            expected_action_digest=digest,
        )
    )
    assert engine.resume.await_args_list == [
        call(
            task_id="task-resume",
            confirmed=confirmed,
            expected_action_digest=digest,
        )
    ]
    assert result.model_dump() == {
        "status": mapped,
        "data": {"result": "synthetic-output"} if mapped == "completed" else None,
        "error_code": error,
        "trace_id": "workflow-trace",
    }
    assert engine.discard_checkpoint.call_args_list == []
    assert gateway.mock_calls == registry.mock_calls == []


@pytest.mark.parametrize(
    "error_type", [VersionBindingMismatchError, RuntimeError, asyncio.CancelledError]
)
def test_resume_propagates_errors_and_cancellation_without_cleanup(
    error_type: type[BaseException],
) -> None:
    adapter, _, gateway, engine = _adapter()
    error = error_type("synthetic failure")
    engine.resume.side_effect = error
    with pytest.raises(error_type) as caught:
        asyncio.run(adapter.resume_capability(task_id="task-resume", confirmed=True))
    assert caught.value is error
    assert engine.resume.await_args_list == [
        call(
            task_id="task-resume",
            confirmed=True,
            expected_action_digest=None,
        )
    ]
    assert engine.discard_checkpoint.call_args_list == []
    assert gateway.mock_calls == []
    without_engine, _, _, _ = _adapter(workflow=False)
    with pytest.raises(RuntimeError, match="no configured engine"):
        asyncio.run(without_engine.resume_capability(task_id="task-resume", confirmed=True))


@pytest.mark.parametrize(
    ("capability_id", "target"),
    [
        ("oa.synthetic.action", "oa"),
        ("u8.synthetic.action", "u8"),
        ("ivms.synthetic.action", "hikvision_ivms"),
        ("hikvision_ivms.synthetic.action", "hikvision_ivms"),
        ("synthetic.action", None),
    ],
)
def test_prepare_confirmation_preserves_exact_payload_rules(
    capability_id: str, target: str | None
) -> None:
    adapter, registry, gateway, engine = _adapter()
    capability = _capability(capability_id, target_system="u8" if target == "oa" else "oa")
    arguments = {"remark": "合成说明", "amount": 12, "undeclared": "never display"}
    original = copy.deepcopy(arguments)
    preview = adapter.prepare_confirmation(
        capability_id=capability_id, arguments=arguments, capability=capability
    )
    assert preview.to_payload() == {
        "capability_id": capability_id,
        "operation_summary": "合成操作：核对后提交",
        "target_system": target,
        "field_names": ["amount", "remark"],
        "displayed_argument_values": {"remark": "合成说明", "amount": "12"},
    }
    assert list(preview.to_payload()["displayed_argument_values"]) == ["remark", "amount"]
    assert preview.to_payload()["field_names"] == ["amount", "remark"]
    assert arguments == original
    arguments["remark"] = "changed"
    capability.displayable_argument_fields.reverse()
    assert preview.displayed_argument_values == (("remark", "合成说明"), ("amount", "12"))
    resumed = adapter.prepare_confirmation(
        capability_id=capability_id, arguments={"amount": 99}, capability=None
    )
    assert resumed.to_payload() == {
        "capability_id": capability_id,
        "operation_summary": "",
        "target_system": target,
        "field_names": [],
        "displayed_argument_values": {},
    }
    assert registry.mock_calls == gateway.mock_calls == engine.mock_calls == []


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("s" * 200, "s" * 200),
        ("s" * 201, None),
        (None, None),
        ([], None),
        ({}, None),
        (True, "True"),
        (1.5, "1.5"),
        (0, "0"),
    ],
)
def test_prepare_confirmation_filters_scalar_length_and_allowlist(
    value: Any, expected: str | None
) -> None:
    adapter, _, _, _ = _adapter()
    capability = _capability(displayable_argument_fields=["remark"])
    preview = adapter.prepare_confirmation(
        capability_id=capability.capability_id,
        arguments={"remark": value, "amount": 12},
        capability=capability,
    )
    assert preview.to_payload()["field_names"] == ["amount", "remark"]
    assert preview.to_payload()["displayed_argument_values"] == (
        {} if expected is None else {"remark": expected}
    )


class RecordingBuilder:
    """Records public delegates, then executes the real validated/sanitizing builder."""

    def __init__(self) -> None:
        self.real = ResponseEnvelopeBuilder()
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.results: list[Any] = []

    def __getattr__(self, name: str) -> Any:
        method = getattr(self.real, name)

        def recorded(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, args, kwargs))
            result = method(*args, **kwargs)
            self.results.append(result)
            return result

        return recorded


def _response_context(capability_id: str = "oa.synthetic.action") -> AgentResponseContext:
    return AgentResponseContext(
        response_id="response-1",
        task_id="task-2",
        session_id="session-3",
        trace_id="context-trace",
        capability_id=capability_id,
    )


_RESPONSE_CASES = [
    ("completed", None, "build_message", "操作完成", "Operation completed.", "completed", "none"),
    (
        "denied",
        "policy_denied",
        "build_policy_denied",
        "无权限，操作被拒绝",
        "Access denied.",
        "blocked",
        "operator_handback_card",
    ),
    (
        "binding_required",
        "needs_binding_scope",
        "build_operator_handback",
        "请先选择明确的账套、设备域或资源范围后继续",
        "Binding scope required.",
        "blocked",
        "operator_handback_card",
    ),
    (
        "binding_required",
        "identity_unbound",
        "build_operator_handback_bind_required",
        "需要绑定账号才能继续",
        "Identity binding required.",
        "blocked",
        "operator_handback_card",
    ),
    (
        "binding_required",
        "identity_expired",
        "build_operator_handback_bind_required",
        "账号绑定或上游会话已过期，请重新认证或重新绑定后继续",
        "Identity binding or upstream session expired; reauthentication required.",
        "blocked",
        "operator_handback_card",
    ),
    (
        "binding_required",
        "identity_revoked",
        "build_operator_handback_bind_required",
        "账号绑定已撤销，请重新绑定后继续",
        "Identity binding revoked.",
        "blocked",
        "operator_handback_card",
    ),
    (
        "timeout",
        "adapter_timeout",
        "build_failed",
        "操作超时，请重试",
        "Gateway timeout.",
        "failed",
        "none",
    ),
    ("failed", "adapter_error", "build_failed", "操作失败", "Operation failed.", "failed", "none"),
    (
        "no_capability_found",
        "capability_not_found",
        "build_no_capability_found",
        "暂未接入该能力",
        "No capability found.",
        "no_capability_found",
        "operator_handback_card",
    ),
    (
        "waiting_user",
        "confirm_required",
        "build_confirm_card",
        "请确认提交操作",
        "Please confirm.",
        "waiting_user",
        "confirm_card",
    ),
]


@pytest.mark.parametrize(
    ("status", "error", "method", "message", "fallback", "envelope_status", "component"),
    _RESPONSE_CASES,
    ids=[f"{row[0]}-{row[1]}" for row in _RESPONSE_CASES],
)
@pytest.mark.parametrize("execution_trace", ["execution-trace", ""])
@pytest.mark.parametrize(
    ("capability_id", "target"), [("oa.synthetic.action", "oa"), ("synthetic.action", None)]
)
def test_build_response_forwards_each_branch_to_the_real_builder_contract(
    status: str,
    error: str | None,
    method: str,
    message: str,
    fallback: str,
    envelope_status: str,
    component: str,
    execution_trace: str,
    capability_id: str,
    target: str | None,
) -> None:
    builder = RecordingBuilder()
    adapter, registry, gateway, engine = _adapter(builder=builder)
    context = _response_context(capability_id)
    preview = ConfirmationPreview(
        capability_id, "合成操作", target, ("amount",), (("amount", "12"),)
    )
    snapshot = ProjectionContractSnapshot.from_capability(_capability(capability_id))
    result = adapter.build_response(
        context=context,
        execution=ExecutionResult(
            status=status,
            error_code=error,
            trace_id=execution_trace,
            data={"result": "synthetic-value"},
        ),
        projection=snapshot,
        confirmation=preview if status == "waiting_user" else None,
    )
    trace = (
        (execution_trace or "context-trace") if status in {"timeout", "failed"} else "context-trace"
    )
    args = ("response-1", "task-2", "session-3", message, fallback, trace)
    kwargs: dict[str, Any] = {}
    if status == "completed":
        kwargs = {"status": "completed", "data": {"result": "synthetic-value"}}
    elif method == "build_operator_handback":
        kwargs = {"target_system": target}
    elif method == "build_operator_handback_bind_required":
        args += (target,)
        kwargs = {"reason_code": error}
    elif status == "waiting_user":
        kwargs = {"payload": preview.to_payload(), "target_system": target}
    assert builder.calls == [(method, args, kwargs)]
    assert result is builder.results[0]
    assert (result.response_id, result.task_id, result.session_id) == (
        "response-1",
        "task-2",
        "session-3",
    )
    assert result.trace_id == trace
    assert result.status == envelope_status
    assert result.message == message
    assert result.fallback_text == fallback
    assert result.ui.component_type == component
    assert result.data == ({"result": "synthetic-value"} if status == "completed" else None)
    if status == "waiting_user":
        ui = result.model_dump()["ui"]
        assert ui["target_system"] == ui["payload"]["target_system"] == target
        assert ui["payload"] == preview.to_payload()
    assert registry.mock_calls == gateway.mock_calls == engine.mock_calls == []


@pytest.mark.parametrize("mismatch", ["missing", "id", "target", "valid-copy"])
def test_waiting_requires_matching_preview_and_never_rebuilds_it(
    mismatch: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    builder = RecordingBuilder()
    adapter, registry, _, _ = _adapter(builder=builder)
    original = ConfirmationPreview(
        "oa.synthetic.action", "已冻结预览", "oa", ("amount",), (("amount", "12"),)
    )
    preview = {
        "missing": None,
        "id": replace(original, capability_id="oa.other"),
        "target": replace(original, target_system="u8"),
        "valid-copy": copy.deepcopy(original),
    }[mismatch]
    prepare = Mock(side_effect=AssertionError("build_response cannot prepare again"))
    monkeypatch.setattr(adapter, "prepare_confirmation", prepare)
    kwargs = dict(
        context=_response_context(),
        execution=ExecutionResult(status="waiting_user", trace_id="trace"),
        projection=None,
        confirmation=preview,
    )
    if mismatch != "valid-copy":
        with pytest.raises(OrchestrationContractError):
            adapter.build_response(**kwargs)
        assert builder.calls == []
    else:
        result = adapter.build_response(**kwargs)
        assert result.model_dump()["ui"]["payload"] == original.to_payload()
        assert builder.calls[0][2]["payload"] == original.to_payload()
        assert len(builder.calls) == 1
    assert prepare.call_args_list == []
    assert registry.mock_calls == []


@pytest.mark.parametrize("with_snapshot", [True, False])
def test_completed_response_projects_before_formatting_without_registry_reads(
    with_snapshot: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builder = RecordingBuilder()
    adapter, registry, _, _ = _adapter(builder=builder)
    schema = {"type": "object", "properties": {"result": {"type": "string"}}}
    capability = _capability(output_schema=schema)
    snapshot = ProjectionContractSnapshot.from_capability(capability) if with_snapshot else None
    data = {
        "result": "allowed-value",
        "undeclared": "SYNTHETIC_UNDECLARED_CANARY",
        "access_token": "SYNTHETIC_MARKER_CANARY",
    }
    project = Mock(wraps=module.project_response_data)
    seen: list[Any] = []
    original_formatter = module._format_capability_response

    def formatter(capability_id: str, projected: Any) -> str:
        seen.append(copy.deepcopy(projected))
        return original_formatter(capability_id, projected)

    monkeypatch.setattr(module, "project_response_data", project)
    monkeypatch.setattr(module, "_format_capability_response", formatter)
    envelope = adapter.build_response(
        context=_response_context(),
        execution=ExecutionResult(status="completed", data=data, trace_id="trace"),
        projection=snapshot,
    )
    expected = {"result": "allowed-value"} if with_snapshot else None
    assert project.call_args_list == [call(data, schema if with_snapshot else None)]
    assert seen == [expected]
    assert envelope.data == expected
    assert "SYNTHETIC_UNDECLARED_CANARY" not in envelope.model_dump_json()
    assert "SYNTHETIC_MARKER_CANARY" not in envelope.model_dump_json()
    assert "access_token" not in envelope.model_dump_json()
    assert registry.mock_calls == []
