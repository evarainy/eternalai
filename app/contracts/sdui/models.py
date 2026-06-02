"""Static SDUI response data contracts."""

from __future__ import annotations

from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, Field

UIComponentType: TypeAlias = Literal[
    "none",
    "confirm_card",
    "operator_handback_card",
    "binding_required_card",
]
UIAction: TypeAlias = Literal[
    "confirm",
    "bind_required",
    "clarify_scope",
    "none",
]
TargetSystem: TypeAlias = Literal["oa", "u8", "hikvision_ivms"]
ResponseEnvelopeStatus: TypeAlias = Literal[
    "completed",
    "blocked",
    "waiting_user",
    "failed",
    "no_capability_found",
]


class UIComponent(BaseModel):
    model_config = {"extra": "forbid"}

    component_type: UIComponentType
    action: UIAction | None = None
    target_system: TargetSystem | None = None
    reason_code: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class ResponseEnvelope(BaseModel):
    model_config = {"extra": "forbid"}

    schema_version: str = "phase0.sdui.v1"
    response_id: str
    task_id: str
    session_id: str
    status: ResponseEnvelopeStatus
    message: str
    fallback_text: str
    ui: UIComponent
    data: dict[str, Any] | None = None
    trace_id: str
    trace_summary: str | None = None


class ConfirmCard(UIComponent):
    component_type: Literal["confirm_card"] = "confirm_card"
    action: Literal["confirm"]


class OperatorHandbackCard(UIComponent):
    component_type: Literal["operator_handback_card"] = "operator_handback_card"
    action: Literal["bind_required", "clarify_scope"]


class BindingRequiredCard(UIComponent):
    component_type: Literal["binding_required_card"] = "binding_required_card"
    action: Literal["bind_required"]


class UserAction(BaseModel):
    model_config = {"extra": "forbid"}

    action_type: Literal["confirm"]
    response_id: str
    confirmed: Literal[True]
