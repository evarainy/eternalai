from __future__ import annotations

import inspect
from pathlib import Path
from types import NoneType
from typing import Any, Literal, get_args, get_origin, get_type_hints

import pytest
from pydantic import ValidationError

from app.contracts.sdui.models import (
    BindingRequiredCard,
    ConfirmCard,
    ConfirmCardPayload,
    OperatorHandbackCard,
    ResponseEnvelope,
    ResponseEnvelopeStatus,
    TargetSystem,
    UIAction,
    UIComponent,
    UserAction,
)
from app.ports import response_envelope as response_envelope_port
from app.ports.capability_registry import CapabilityTargetSystem
from app.ports.response_envelope import (
    ResponseEnvelope as PortResponseEnvelope,
)
from app.ports.response_envelope import (
    UIComponent as PortUIComponent,
)


def _literal_values(annotation: object) -> tuple[object, ...]:
    assert get_origin(annotation) is Literal
    return get_args(annotation)


def _optional_literal_values(annotation: object) -> tuple[object, ...]:
    literal_annotation, none_annotation = get_args(annotation)
    assert none_annotation is NoneType
    assert get_origin(literal_annotation) is Literal
    return get_args(literal_annotation)


def _optional_type_args(annotation: object) -> tuple[object, object]:
    first_arg, second_arg = get_args(annotation)
    assert second_arg is NoneType
    return first_arg, second_arg


def _dict_args(annotation: object) -> tuple[object, object]:
    assert get_origin(annotation) is dict
    return get_args(annotation)


def _missing_required_error_locations(error: ValidationError) -> set[tuple[str, ...]]:
    return {tuple(item["loc"]) for item in error.errors() if item["type"] == "missing"}


def _extra_forbidden_locations(error: ValidationError) -> set[tuple[str, ...]]:
    return {
        tuple(item["loc"])
        for item in error.errors()
        if item["type"] == "extra_forbidden"
    }


def test_type_alias_literals_match_phase0_response_envelope_contract() -> None:
    assert _literal_values(UIAction) == (
        "confirm",
        "bind_required",
        "clarify_scope",
        "none",
    )
    assert _literal_values(TargetSystem) == ("oa", "u8", "hikvision_ivms")
    assert TargetSystem is CapabilityTargetSystem
    assert _literal_values(ResponseEnvelopeStatus) == (
        "completed",
        "blocked",
        "waiting_user",
        "failed",
        "no_capability_found",
    )


def test_ui_component_fields_types_defaults_and_extra_forbid_are_exact() -> None:
    assert inspect.isclass(UIComponent)
    assert UIComponent.model_config["extra"] == "forbid"
    assert list(UIComponent.model_fields) == [
        "component_type",
        "action",
        "target_system",
        "reason_code",
        "payload",
    ]

    hints = get_type_hints(UIComponent, include_extras=True)
    assert _literal_values(hints["component_type"]) == (
        "none",
        "operator_handback_card",
        "binding_required_card",
    )
    assert _optional_literal_values(hints["action"]) == (
        "confirm",
        "bind_required",
        "clarify_scope",
        "none",
    )
    assert _optional_literal_values(hints["target_system"]) == (
        "oa",
        "u8",
        "hikvision_ivms",
    )
    assert _optional_type_args(hints["reason_code"]) == (str, NoneType)
    assert _dict_args(hints["payload"]) == (str, Any)

    fields = UIComponent.model_fields
    assert fields["component_type"].is_required()
    assert fields["action"].default is None
    assert fields["target_system"].default is None
    assert fields["reason_code"].default is None
    assert fields["payload"].default_factory is dict

    first_component = UIComponent(component_type="none")
    second_component = UIComponent(component_type="none")
    first_component.payload["isolated"] = True
    assert second_component.payload == {}

    for component_type in (
        "none",
        "operator_handback_card",
        "binding_required_card",
    ):
        assert UIComponent(component_type=component_type).component_type == component_type

    with pytest.raises(ValidationError) as missing_component_type:
        UIComponent()
    assert _missing_required_error_locations(missing_component_type.value) == {
        ("component_type",)
    }

    with pytest.raises(ValidationError) as invalid_component:
        UIComponent(component_type="dynamic_widget")
    assert invalid_component.value.errors()[0]["type"] == "literal_error"

    with pytest.raises(ValidationError) as confirm_component:
        UIComponent(component_type="confirm_card")
    assert confirm_component.value.errors()[0]["type"] == "literal_error"

    with pytest.raises(ValidationError) as invalid_action:
        UIComponent(component_type="none", action="cancel")
    assert invalid_action.value.errors()[0]["type"] == "literal_error"

    with pytest.raises(ValidationError) as invalid_target:
        UIComponent(component_type="none", target_system="sap")
    assert invalid_target.value.errors()[0]["type"] == "literal_error"

    with pytest.raises(ValidationError) as extra_field:
        UIComponent(component_type="none", unexpected="blocked")
    assert _extra_forbidden_locations(extra_field.value) == {("unexpected",)}


def test_ui_component_and_confirm_card_public_fields_stay_in_sync() -> None:
    assert set(UIComponent.model_fields) == set(ConfirmCard.model_fields)


def test_response_envelope_fields_types_defaults_and_required_shape_are_exact() -> None:
    assert inspect.isclass(ResponseEnvelope)
    assert ResponseEnvelope.model_config["extra"] == "forbid"
    assert list(ResponseEnvelope.model_fields) == [
        "schema_version",
        "response_id",
        "task_id",
        "session_id",
        "status",
        "message",
        "fallback_text",
        "ui",
        "data",
        "trace_id",
        "trace_summary",
    ]

    hints = get_type_hints(ResponseEnvelope, include_extras=True)
    assert hints["schema_version"] is str
    assert hints["response_id"] is str
    assert hints["task_id"] is str
    assert hints["session_id"] is str
    assert _literal_values(hints["status"]) == (
        "completed",
        "blocked",
        "waiting_user",
        "failed",
        "no_capability_found",
    )
    assert hints["message"] is str
    assert hints["fallback_text"] is str
    ui_union, ui_discriminator = get_args(hints["ui"])
    assert set(get_args(ui_union)) == {ConfirmCard, UIComponent}
    assert ui_discriminator.discriminator == "component_type"
    data_annotation, none_annotation = _optional_type_args(hints["data"])
    assert none_annotation is NoneType
    assert _dict_args(data_annotation) == (str, Any)
    assert hints["trace_id"] is str
    assert _optional_type_args(hints["trace_summary"]) == (str, NoneType)

    fields = ResponseEnvelope.model_fields
    assert fields["schema_version"].default == "phase0.sdui.v1"
    assert fields["response_id"].is_required()
    assert fields["task_id"].is_required()
    assert fields["session_id"].is_required()
    assert fields["status"].is_required()
    assert fields["message"].is_required()
    assert fields["fallback_text"].is_required()
    assert fields["ui"].is_required()
    assert fields["data"].default is None
    assert fields["trace_id"].is_required()
    assert fields["trace_summary"].default is None

    with pytest.raises(ValidationError) as missing_required:
        ResponseEnvelope()
    assert _missing_required_error_locations(missing_required.value) == {
        ("response_id",),
        ("task_id",),
        ("session_id",),
        ("status",),
        ("message",),
        ("fallback_text",),
        ("ui",),
        ("trace_id",),
    }

    envelope = ResponseEnvelope(
        response_id="resp-001",
        task_id="task-001",
        session_id="session-001",
        status="blocked",
        message="Binding is required.",
        fallback_text="Binding is required.",
        ui=UIComponent(component_type="binding_required_card"),
        trace_id="trace-001",
    )
    assert envelope.schema_version == "phase0.sdui.v1"
    assert envelope.data is None
    assert envelope.trace_summary is None

    with pytest.raises(ValidationError) as invalid_status:
        ResponseEnvelope(
            response_id="resp-001",
            task_id="task-001",
            session_id="session-001",
            status="clarification_needed",
            message="Unsupported status.",
            fallback_text="Unsupported status.",
            ui=UIComponent(component_type="none"),
            trace_id="trace-001",
        )
    assert invalid_status.value.errors()[0]["type"] == "literal_error"

    with pytest.raises(ValidationError) as extra_field:
        ResponseEnvelope(
            response_id="resp-001",
            task_id="task-001",
            session_id="session-001",
            status="failed",
            message="Failed.",
            fallback_text="Failed.",
            ui=UIComponent(component_type="none"),
            trace_id="trace-001",
            next_action="retry",
        )
    assert _extra_forbidden_locations(extra_field.value) == {("next_action",)}


def test_response_envelope_json_schema_is_producible_for_static_schema_contract() -> None:
    schema = ResponseEnvelope.model_json_schema()
    properties = schema["properties"]
    required = schema["required"]

    assert properties["schema_version"]["default"] == "phase0.sdui.v1"
    assert properties["status"]["enum"] == [
        "completed",
        "blocked",
        "waiting_user",
        "failed",
        "no_capability_found",
    ]
    assert set(required) == {
        "response_id",
        "task_id",
        "session_id",
        "status",
        "message",
        "fallback_text",
        "ui",
        "trace_id",
    }
    assert "$defs" in schema
    assert "UIComponent" in schema["$defs"]
    assert "ConfirmCard" in schema["$defs"]
    assert "ConfirmCardPayload" in schema["$defs"]


def test_confirm_card_is_one_shot_confirm_only_and_rejects_state_fields() -> None:
    assert not issubclass(ConfirmCard, UIComponent)
    assert ConfirmCard.model_config["extra"] == "forbid"

    hints = get_type_hints(ConfirmCard, include_extras=True)
    assert _literal_values(hints["component_type"]) == ("confirm_card",)
    assert _literal_values(hints["action"]) == ("confirm",)
    assert ConfirmCard.model_fields["component_type"].default == "confirm_card"
    assert ConfirmCard.model_fields["action"].is_required()

    payload = ConfirmCardPayload(
        capability_id="oa.synthetic.approve",
        operation_summary="提交审批",
        target_system="oa",
        field_names=["decision"],
        displayed_argument_values={"decision": "同意"},
    )
    card = ConfirmCard(action="confirm", payload=payload)
    assert card.component_type == "confirm_card"
    assert card.action == "confirm"

    with pytest.raises(ValidationError) as missing_action:
        ConfirmCard(payload=payload)
    assert _missing_required_error_locations(missing_action.value) == {("action",)}

    for invalid_action in ("bind_required", "clarify_scope", "none"):
        with pytest.raises(ValidationError) as invalid_card:
            ConfirmCard(action=invalid_action, payload=payload)
        assert invalid_card.value.errors()[0]["type"] == "literal_error"

    with pytest.raises(ValidationError) as state_fields:
        ConfirmCard(
            action="confirm",
            payload=payload,
            state_id="state-001",
            step="review",
            next_action="continue",
        )
    assert _extra_forbidden_locations(state_fields.value) == {
        ("state_id",),
        ("step",),
        ("next_action",),
    }


def test_operator_handback_card_requires_bind_or_clarify_action_only() -> None:
    assert issubclass(OperatorHandbackCard, UIComponent)
    assert OperatorHandbackCard.model_config["extra"] == "forbid"

    hints = get_type_hints(OperatorHandbackCard, include_extras=True)
    assert _literal_values(hints["component_type"]) == ("operator_handback_card",)
    assert _literal_values(hints["action"]) == ("bind_required", "clarify_scope")
    assert (
        OperatorHandbackCard.model_fields["component_type"].default
        == "operator_handback_card"
    )
    assert OperatorHandbackCard.model_fields["action"].is_required()

    for valid_action in ("bind_required", "clarify_scope"):
        card = OperatorHandbackCard(action=valid_action)
        assert card.component_type == "operator_handback_card"
        assert card.action == valid_action

    with pytest.raises(ValidationError) as missing_action:
        OperatorHandbackCard()
    assert _missing_required_error_locations(missing_action.value) == {("action",)}

    for invalid_action in ("confirm", "none"):
        with pytest.raises(ValidationError) as invalid_card:
            OperatorHandbackCard(action=invalid_action)
        assert invalid_card.value.errors()[0]["type"] == "literal_error"


def test_binding_required_card_only_allows_bind_required_action() -> None:
    assert issubclass(BindingRequiredCard, UIComponent)
    assert BindingRequiredCard.model_config["extra"] == "forbid"

    hints = get_type_hints(BindingRequiredCard, include_extras=True)
    assert _literal_values(hints["component_type"]) == ("binding_required_card",)
    assert _literal_values(hints["action"]) == ("bind_required",)
    assert (
        BindingRequiredCard.model_fields["component_type"].default
        == "binding_required_card"
    )
    assert BindingRequiredCard.model_fields["action"].is_required()

    card = BindingRequiredCard(action="bind_required")
    assert card.component_type == "binding_required_card"
    assert card.action == "bind_required"

    with pytest.raises(ValidationError) as missing_action:
        BindingRequiredCard()
    assert _missing_required_error_locations(missing_action.value) == {("action",)}

    for invalid_action in ("confirm", "clarify_scope", "none"):
        with pytest.raises(ValidationError) as invalid_card:
            BindingRequiredCard(action=invalid_action)
        assert invalid_card.value.errors()[0]["type"] == "literal_error"


def test_user_action_is_minimal_confirm_return_structure_only() -> None:
    assert inspect.isclass(UserAction)
    assert UserAction.model_config["extra"] == "forbid"
    assert list(UserAction.model_fields) == ["action_type", "response_id", "confirmed"]

    hints = get_type_hints(UserAction, include_extras=True)
    assert _literal_values(hints["action_type"]) == ("confirm",)
    assert hints["response_id"] is str
    assert _literal_values(hints["confirmed"]) == (True,)
    assert UserAction.model_fields["action_type"].is_required()
    assert UserAction.model_fields["response_id"].is_required()
    assert UserAction.model_fields["confirmed"].is_required()

    action = UserAction(
        action_type="confirm",
        response_id="resp-001",
        confirmed=True,
    )
    assert action.action_type == "confirm"
    assert action.response_id == "resp-001"
    assert action.confirmed is True

    with pytest.raises(ValidationError) as missing_required:
        UserAction()
    assert _missing_required_error_locations(missing_required.value) == {
        ("action_type",),
        ("response_id",),
        ("confirmed",),
    }

    for invalid_action in ("cancel", "deny"):
        with pytest.raises(ValidationError) as invalid_user_action:
            UserAction(
                action_type=invalid_action,
                response_id="resp-001",
                confirmed=True,
            )
        assert invalid_user_action.value.errors()[0]["type"] == "literal_error"

    with pytest.raises(ValidationError) as invalid_confirmed:
        UserAction(
            action_type="confirm",
            response_id="resp-001",
            confirmed=False,
        )
    assert invalid_confirmed.value.errors()[0]["type"] == "literal_error"

    for forbidden_field in ("state_id", "step", "next_action", "extra"):
        with pytest.raises(ValidationError) as forbidden_extra:
            UserAction(
                action_type="confirm",
                response_id="resp-001",
                confirmed=True,
                **{forbidden_field: "blocked"},
            )
        assert _extra_forbidden_locations(forbidden_extra.value) == {
            (forbidden_field,)
        }


def test_port_response_envelope_module_is_reexport_facade() -> None:
    assert PortResponseEnvelope is ResponseEnvelope
    assert PortUIComponent is UIComponent
    assert response_envelope_port.__all__ == (
        "BindingRequiredCard",
        "ConfirmCard",
        "OperatorHandbackCard",
        "ResponseEnvelope",
        "ResponseEnvelopeStatus",
        "TargetSystem",
        "UIAction",
        "UIComponent",
        "UserAction",
    )


def test_implementation_sources_do_not_introduce_forbidden_sdui_scope() -> None:
    implementation_paths = (
        Path("app/contracts/sdui/models.py"),
        Path("app/ports/response_envelope.py"),
    )
    forbidden_terms = (
        "web/src/sdui_renderer",
        "renderer",
        "dynamic form",
        "dynamic_form",
        "orchestrator",
        "state machine",
        "state_machine",
        "capability_gateway",
        "ExecutionStatus",
        "ErrorCode",
        "ExecutionResult",
        "RequestOrgContext",
        "TaskRecord",
    )

    for implementation_path in implementation_paths:
        source_text = implementation_path.read_text(encoding="utf-8")
        for forbidden_term in forbidden_terms:
            assert forbidden_term not in source_text
