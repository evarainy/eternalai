"""Contract tests for fail-closed Work Object handling projection."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.ports.capability_registry import (
    CapabilityAutomationLevel,
    CapabilitySpec,
)
from app.ports.work_object_handling import (
    WorkObjectHandlingSelector,
    project_handling_action,
)
from tests.runtime.registry_fakes import active_capability


def _capability(automation_level: CapabilityAutomationLevel) -> CapabilitySpec:
    return active_capability("test.handle").model_copy(
        update={"automation_level": automation_level}
    )


def test_selector_requires_exact_strict_three_tuple() -> None:
    selector = WorkObjectHandlingSelector(
        source_system="oa",
        source_kind="pending_workflow",
        source_workflow_type_id=None,
    )

    assert selector.model_dump() == {
        "source_system": "oa",
        "source_kind": "pending_workflow",
        "source_workflow_type_id": None,
    }
    with pytest.raises(ValidationError):
        WorkObjectHandlingSelector.model_validate(
            {
                "source_system": "oa",
                "source_kind": "pending_workflow",
                "source_workflow_type_id": None,
                "priority": 1,
            }
        )


@pytest.mark.parametrize(
    ("state_authority", "source_system", "handling_mark", "capability", "expected"),
    [
        ("external_snapshot", "oa", "handled_elsewhere", _capability("full"), "view_only"),
        ("external_snapshot", "oa", None, _capability("full"), "ai_draft"),
        ("external_snapshot", "oa", None, _capability("assisted"), "self_serve"),
        ("external_snapshot", "oa", None, None, "go_source_system"),
        ("external_snapshot", "unregistered", None, None, "view_only"),
        ("internal", "oa", None, None, "view_only"),
    ],
)
def test_projection_follows_the_five_branch_order(
    state_authority: str,
    source_system: str,
    handling_mark: str | None,
    capability: CapabilitySpec | None,
    expected: str,
) -> None:
    assert (
        project_handling_action(
            state_authority=state_authority,
            source_system=source_system,
            handling_mark=handling_mark,
            capability=capability,
        )
        == expected
    )


def test_pending_sync_confirmation_does_not_suppress_capability_action() -> None:
    assert (
        project_handling_action(
            state_authority="external_snapshot",
            source_system="oa",
            handling_mark="pending_sync_confirmation",
            capability=_capability("full"),
        )
        == "ai_draft"
    )
