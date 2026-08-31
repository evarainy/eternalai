"""Static SDUI response data contracts."""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, StrictStr

from app.ports.capability_registry import CapabilityTargetSystem

UIAction: TypeAlias = Literal[
    "confirm",
    "bind_required",
    "clarify_scope",
    "none",
]
TargetSystem: TypeAlias = CapabilityTargetSystem
ResponseEnvelopeStatus: TypeAlias = Literal[
    "completed",
    "blocked",
    "waiting_user",
    "failed",
    "no_capability_found",
]


class ConfirmCardPayload(BaseModel):
    """Runtime-owned payload contract for operation confirmation cards."""

    model_config = ConfigDict(extra="forbid")

    capability_id: StrictStr
    operation_summary: StrictStr
    target_system: CapabilityTargetSystem | None
    field_names: list[StrictStr]
    displayed_argument_values: dict[str, StrictStr]


class UIComponent(BaseModel):
    model_config = {"extra": "forbid"}

    component_type: Literal[
        "none",
        "operator_handback_card",
        "binding_required_card",
    ]
    action: UIAction | None = None
    target_system: TargetSystem | None = None
    reason_code: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class ConfirmCard(BaseModel):
    model_config = {"extra": "forbid"}

    component_type: Literal["confirm_card"] = "confirm_card"
    action: Literal["confirm"]
    target_system: TargetSystem | None = None
    reason_code: str | None = None
    payload: ConfirmCardPayload


class OperatorHandbackCard(UIComponent):
    component_type: Literal["operator_handback_card"] = "operator_handback_card"
    action: Literal["bind_required", "clarify_scope"]


class BindingRequiredCard(UIComponent):
    component_type: Literal["binding_required_card"] = "binding_required_card"
    action: Literal["bind_required"]


class ResponseEnvelope(BaseModel):
    model_config = {"extra": "forbid"}

    schema_version: str = "phase0.sdui.v1"
    response_id: str
    task_id: str
    session_id: str
    status: ResponseEnvelopeStatus
    message: str
    fallback_text: str
    ui: Annotated[ConfirmCard | UIComponent, Field(discriminator="component_type")]
    data: dict[str, Any] | None = None
    trace_id: str
    trace_summary: str | None = None


class UserAction(BaseModel):
    model_config = {"extra": "forbid"}

    action_type: Literal["confirm"]
    response_id: str
    confirmed: Literal[True]
