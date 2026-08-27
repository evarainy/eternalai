"""Fail-closed Work Object handling projection contract."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol, TypeAlias

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from app.ports.capability_registry import CapabilitySpec


class WorkObjectHandlingSelector(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    source_system: str
    source_kind: str
    source_workflow_type_id: str | None


WorkObjectHandlingAction: TypeAlias = Literal[
    "ai_draft",
    "self_serve",
    "go_source_system",
    "view_only",
]


class WorkObjectHandlingResolverPort(Protocol):
    async def resolve(
        self,
        *,
        source_system: str,
        source_kind: str,
        source_workflow_type_id: str | None,
    ) -> CapabilitySpec | None: ...


_SOURCE_SYSTEM_HANDLING_LABELS: dict[str, str] = {"oa": "去 OA 办"}


def project_handling_action(
    *,
    state_authority: str,
    source_system: str,
    handling_mark: str | None,
    capability: CapabilitySpec | None,
) -> WorkObjectHandlingAction:
    """Project exactly one safe handling action using the governed rule order."""

    if handling_mark == "handled_elsewhere":
        return "view_only"
    if capability is not None and capability.automation_level == "full":
        return "ai_draft"
    if capability is not None and capability.automation_level == "assisted":
        return "self_serve"
    if (
        state_authority == "external_snapshot"
        and source_system in _SOURCE_SYSTEM_HANDLING_LABELS
    ):
        return "go_source_system"
    return "view_only"


__all__ = (
    "WorkObjectHandlingAction",
    "WorkObjectHandlingResolverPort",
    "WorkObjectHandlingSelector",
    "project_handling_action",
)
