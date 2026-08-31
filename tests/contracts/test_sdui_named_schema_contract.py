"""Cross-language Runtime/SDUI schema contract regressions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from app.contracts.sdui.models import ConfirmCardPayload, ResponseEnvelope, TargetSystem
from app.main import create_app
from app.ports.capability_registry import CapabilityTargetSystem


def _confirm_payload(**updates: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "capability_id": "oa.synthetic.approve",
        "operation_summary": "提交审批",
        "target_system": "oa",
        "field_names": ["decision"],
        "displayed_argument_values": {"decision": "同意"},
    }
    payload.update(updates)
    return payload


def _envelope_with_confirm_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "response_id": "response-schema",
        "task_id": "task-schema",
        "session_id": "session-schema",
        "status": "waiting_user",
        "message": "请确认",
        "fallback_text": "Please confirm.",
        "ui": {
            "component_type": "confirm_card",
            "action": "confirm",
            "target_system": "oa",
            "payload": payload,
        },
        "trace_id": "trace-schema",
    }


def test_target_system_reuses_capability_registry_value_authority() -> None:
    assert TargetSystem is CapabilityTargetSystem
    source = Path("app/contracts/sdui/models.py").read_text(encoding="utf-8")
    assert "TargetSystem: TypeAlias = CapabilityTargetSystem" in source
    assert 'TargetSystem: TypeAlias = Literal["oa"' not in source


def test_confirm_card_payload_is_named_exact_pydantic_contract() -> None:
    assert issubclass(ConfirmCardPayload, BaseModel)
    schema = ConfirmCardPayload.model_json_schema()

    assert schema["additionalProperties"] is False
    assert schema["required"] == [
        "capability_id",
        "operation_summary",
        "target_system",
        "field_names",
        "displayed_argument_values",
    ]
    assert schema["properties"]["displayed_argument_values"][
        "additionalProperties"
    ] == {"type": "string"}


def test_confirm_component_cannot_fall_back_to_generic_payload_branch() -> None:
    payload = _confirm_payload(unexpected="blocked")

    with pytest.raises(ValidationError) as invalid_confirm:
        ResponseEnvelope.model_validate(_envelope_with_confirm_payload(payload))

    assert any(
        error["type"] == "extra_forbidden"
        and error["loc"][-2:] == ("payload", "unexpected")
        for error in invalid_confirm.value.errors()
    )


def test_runtime_openapi_wires_named_action_and_confirm_contracts() -> None:
    document = create_app().openapi()
    schemas = document["components"]["schemas"]
    action_schema = document["paths"]["/api/v1/runtime/action"]["post"][
        "responses"
    ]["200"]["content"]["application/json"]["schema"]
    handle_schema = document["paths"]["/api/v1/runtime/handle"]["post"][
        "responses"
    ]["200"]["content"]["application/json"]["schema"]

    assert action_schema == {"$ref": "#/components/schemas/ActionResponseEnvelope"}
    assert handle_schema == {"$ref": "#/components/schemas/ResponseEnvelope"}
    assert {
        "ActionResponseData",
        "ActionResponseEnvelope",
        "ConfirmCardPayload",
        "UserActionOutcome",
    } <= schemas.keys()

    action_data = schemas["ActionResponseData"]
    assert action_data["additionalProperties"] is False
    assert action_data["required"] == ["action_outcome", "result"]
    assert set(action_data["properties"]) == {"action_outcome", "result"}
    assert action_data["properties"]["action_outcome"] == {
        "$ref": "#/components/schemas/UserActionOutcome"
    }
    assert action_data["properties"]["result"]["anyOf"] == [
        {"$ref": "#/components/schemas/ProjectedActionResult"},
        {"type": "null"},
    ]

    projected_result = schemas["ProjectedActionResult"]
    assert projected_result["additionalProperties"] == {}
    assert "project_response_data" in projected_result["description"]
    assert "CapabilitySpec.output_schema" in projected_result["description"]

    action_envelope = schemas["ActionResponseEnvelope"]
    assert action_envelope["properties"]["data"] == {
        "$ref": "#/components/schemas/ActionResponseData"
    }
    assert "data" in action_envelope["required"]
    assert set(action_envelope["properties"]) == set(
        schemas["ResponseEnvelope"]["properties"]
    )

    confirm_payload = schemas["ConfirmCardPayload"]
    assert confirm_payload["additionalProperties"] is False
    assert list(confirm_payload["properties"]) == [
        "capability_id",
        "operation_summary",
        "target_system",
        "field_names",
        "displayed_argument_values",
    ]
    assert confirm_payload["required"] == [
        "capability_id",
        "operation_summary",
        "target_system",
        "field_names",
        "displayed_argument_values",
    ]
    assert confirm_payload["properties"]["displayed_argument_values"][
        "additionalProperties"
    ] == {"type": "string"}

    confirm_schema = schemas["ConfirmCard"]
    assert confirm_schema["properties"]["payload"] == {
        "$ref": "#/components/schemas/ConfirmCardPayload"
    }
    ui_schema = schemas["UIComponent"]
    assert "confirm_card" not in ui_schema["properties"]["component_type"]["enum"]
    discriminator = schemas["ResponseEnvelope"]["properties"]["ui"]
    assert discriminator["discriminator"]["mapping"]["confirm_card"] == (
        "#/components/schemas/ConfirmCard"
    )
