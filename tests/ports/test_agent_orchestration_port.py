"""Public orchestration signatures and immutable confirmation value contract."""

from __future__ import annotations

import ast
import copy
import inspect
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from typing import Any, Literal, Mapping, get_args, get_type_hints

import pytest

from app.ports import agent_orchestration as contract
from app.ports.agent_orchestration import (
    AgentCapabilitySelection,
    AgentOrchestrationPort,
    AgentResponseContext,
    AgentTaskVersionBindings,
    ConfirmationPreview,
    OrchestrationContractError,
)
from app.ports.capability_gateway import ExecutionResult, RequestOrgContext
from app.ports.capability_registry import CapabilitySpec, CapabilityTargetSystem, CapabilityType
from app.ports.human_gate import HumanGateConflictError, VersionBinding, VersionBindingMismatchError
from app.ports.response_envelope import ResponseEnvelope
from app.ports.response_projection_contract import ProjectionContractSnapshot


def test_public_contract_is_framework_free_and_has_six_methods() -> None:
    expected = {
        "select_capability": {
            "capability_id": str,
            "target_system": CapabilityTargetSystem | None,
            "capability_type": CapabilityType | None,
            "return": AgentCapabilitySelection | None,
        },
        "resolve_task_version_bindings": {
            "capability": CapabilitySpec,
            "intent_version_binding": VersionBinding,
            "return": AgentTaskVersionBindings,
        },
        "execute_capability": {
            "task_id": str,
            "session_id": str,
            "ai_user_id": str,
            "capability": CapabilitySpec,
            "arguments": dict[str, Any],
            "request_context": RequestOrgContext,
            "return": ExecutionResult,
        },
        "resume_capability": {
            "task_id": str,
            "confirmed": bool,
            "expected_action_digest": str | None,
            "return": ExecutionResult,
        },
        "prepare_confirmation": {
            "capability_id": str,
            "arguments": Mapping[str, Any],
            "capability": CapabilitySpec | None,
            "return": ConfirmationPreview,
        },
        "build_response": {
            "context": AgentResponseContext,
            "execution": ExecutionResult,
            "projection": ProjectionContractSnapshot | None,
            "confirmation": ConfirmationPreview | None,
            "return": ResponseEnvelope,
        },
    }
    actual_methods = {
        name: method
        for name, method in vars(AgentOrchestrationPort).items()
        if not name.startswith("_") and callable(method)
    }
    assert set(actual_methods) == set(expected)
    for name, type_contract in expected.items():
        method = actual_methods[name]
        assert get_type_hints(method) == type_contract
        parameters = inspect.signature(method).parameters
        assert list(parameters) == ["self", *[key for key in type_contract if key != "return"]]
        for parameter_name, parameter in parameters.items():
            if parameter_name == "self":
                continue
            assert parameter.kind == inspect.Parameter.KEYWORD_ONLY
            if (name, parameter_name) in {
                ("resume_capability", "expected_action_digest"),
                ("build_response", "confirmation"),
            }:
                assert parameter.default is None
            else:
                assert parameter.default is inspect.Parameter.empty
        assert inspect.iscoroutinefunction(method) == (
            name
            not in {
                "prepare_confirmation",
                "build_response",
            }
        )

    expected_fields = {
        AgentCapabilitySelection: {
            "capability": CapabilitySpec,
            "rule": Literal["exact_id", "unique_intent_tag"],
        },
        AgentTaskVersionBindings: {
            "bindings": tuple[VersionBinding, ...],
            "projection_binding": VersionBinding,
        },
        AgentResponseContext: {
            "response_id": str,
            "task_id": str,
            "session_id": str,
            "trace_id": str,
            "capability_id": str,
        },
        ConfirmationPreview: {
            "capability_id": str,
            "operation_summary": str,
            "target_system": CapabilityTargetSystem | None,
            "field_names": tuple[str, ...],
            "displayed_argument_values": tuple[tuple[str, str], ...],
        },
    }
    for dto, type_contract in expected_fields.items():
        assert {field.name for field in fields(dto)} == set(type_contract)
        assert get_type_hints(dto) == type_contract
        assert dto.__dataclass_params__.frozen is True
        assert set(dto.__slots__) == set(type_contract)

    def assert_public(annotation: Any) -> None:
        module = getattr(annotation, "__module__", "")
        assert not module.startswith(("app.infra", "app.runtime", "app.workflow"))
        for argument in get_args(annotation):
            assert_public(argument)

    for type_contract in [*expected.values(), *expected_fields.values()]:
        for annotation in type_contract.values():
            assert_public(annotation)
    tree = ast.parse(Path(contract.__file__).read_text(encoding="utf-8"))
    imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)] + [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    ]
    assert all(
        module
        and module.startswith(
            (
                "__future__",
                "dataclasses",
                "typing",
                "app.ports",
            )
        )
        for module in imports
    )
    assert OrchestrationContractError.__bases__ == (RuntimeError,)
    assert not issubclass(
        OrchestrationContractError, (VersionBindingMismatchError, HumanGateConflictError)
    )
    assert set(contract.__all__) == {
        "AgentCapabilitySelection",
        "AgentOrchestrationPort",
        "AgentResponseContext",
        "AgentTaskVersionBindings",
        "ConfirmationPreview",
        "OrchestrationContractError",
    }


def test_confirmation_preview_is_deeply_immutable_and_round_trips_by_value() -> None:
    preview = ConfirmationPreview(
        capability_id="oa.synthetic.approve",
        operation_summary="合成审批：核对后提交",
        target_system="oa",
        field_names=("amount", "remark"),
        displayed_argument_values=(("remark", "合成说明"), ("amount", "12")),
    )
    expected = {
        "capability_id": "oa.synthetic.approve",
        "operation_summary": "合成审批：核对后提交",
        "target_system": "oa",
        "field_names": ["amount", "remark"],
        "displayed_argument_values": {"remark": "合成说明", "amount": "12"},
    }
    first = preview.to_payload()
    second = preview.to_payload()
    assert first == second == expected
    assert first is not second
    assert first["field_names"] is not second["field_names"]
    assert first["displayed_argument_values"] is not second["displayed_argument_values"]
    assert list(first["displayed_argument_values"]) == ["remark", "amount"]
    assert first["field_names"] == ["amount", "remark"]
    first["field_names"].append("injected")
    first["displayed_argument_values"]["remark"] = "changed"
    first["capability_id"] = "changed"
    assert preview.to_payload() == second == expected
    assert copy.deepcopy(preview) == preview
    assert copy.deepcopy(preview).to_payload() == expected
    for field in fields(preview):
        with pytest.raises(FrozenInstanceError):
            setattr(preview, field.name, "changed")
    with pytest.raises(TypeError):
        preview.field_names[0] = "changed"
    with pytest.raises(TypeError):
        preview.displayed_argument_values[0][1] = "changed"


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("capability_id", [], TypeError),
        ("operation_summary", 1, TypeError),
        ("target_system", [], TypeError),
        ("target_system", "unsupported", TypeError),
        ("field_names", ["amount"], TypeError),
        ("field_names", ("amount", 1), TypeError),
        ("displayed_argument_values", {"amount": "1"}, TypeError),
        ("displayed_argument_values", [("amount", "1")], TypeError),
        ("displayed_argument_values", (["amount", "1"],), TypeError),
        ("displayed_argument_values", (("amount",),), TypeError),
        ("displayed_argument_values", (("amount", 1),), TypeError),
        ("displayed_argument_values", (("amount", "1"), ("amount", "2")), ValueError),
    ],
)
def test_confirmation_preview_rejects_mutable_or_invalid_members(
    field: str,
    value: Any,
    error: type[Exception],
) -> None:
    arguments = {
        "capability_id": "oa.synthetic.approve",
        "operation_summary": "",
        "target_system": None,
        "field_names": (),
        "displayed_argument_values": (),
        field: value,
    }
    with pytest.raises(error):
        ConfirmationPreview(**arguments)
