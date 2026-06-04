"""Tests for the minimal Phase 0 policy guard skeleton."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import ValidationError

from app.infra.policy.minimal_policy_guard import MinimalPolicyGuard
from app.ports.capability_gateway import RequestOrgContext
from app.ports.policy_guard import PolicyDecision


def _request_context() -> RequestOrgContext:
    return RequestOrgContext(request_id="policy-test-request")


def _decide(
    *,
    capability_id: str,
    arguments: dict[str, Any] | None = None,
) -> PolicyDecision:
    return asyncio.run(
        MinimalPolicyGuard().decide(
            ai_user_id="policy-test-user",
            capability_id=capability_id,
            arguments=arguments,  # type: ignore[arg-type]
            request_context=_request_context(),
        )
    )


def test_none_arguments_denies_policy_denied() -> None:
    result = _decide(capability_id="safe_capability", arguments=None)

    assert isinstance(result, PolicyDecision)
    assert result.decision == "deny"
    assert result.reason_code == "policy_denied"
    assert result.reason_code


def test_admin_capability_denies_role_not_allowed() -> None:
    result = _decide(capability_id="admin_delete_user", arguments={})

    assert result == PolicyDecision(
        decision="deny",
        reason_code="role_not_allowed",
    )


def test_confirm_capability_returns_confirm_decision() -> None:
    result = _decide(capability_id="payroll_update_confirm", arguments={})

    assert result == PolicyDecision(
        decision="confirm",
        reason_code="high_risk_action_requires_confirm",
        required_action="confirm",
    )


def test_safe_capability_returns_allow() -> None:
    result = _decide(capability_id="oa_create_task", arguments={})

    assert isinstance(result, PolicyDecision)
    assert result.decision == "allow"


def test_rule_order_none_before_admin_confirm() -> None:
    result = _decide(capability_id="admin_delete_confirm", arguments=None)

    assert result == PolicyDecision(
        decision="deny",
        reason_code="policy_denied",
    )


@pytest.mark.parametrize(
    ("capability_id", "arguments"),
    [
        ("oa_create_task", {}),
        ("admin_delete_user", {}),
        ("payroll_update_confirm", {}),
        ("admin_delete_confirm", None),
    ],
)
def test_decide_returns_policy_decision_for_all_paths(
    capability_id: str,
    arguments: dict[str, Any] | None,
) -> None:
    result = _decide(capability_id=capability_id, arguments=arguments)

    assert isinstance(result, PolicyDecision)


def test_all_valid_decision_values() -> None:
    assert PolicyDecision(decision="allow").decision == "allow"
    assert PolicyDecision(decision="deny", reason_code="x").decision == "deny"
    assert (
        PolicyDecision(
            decision="confirm",
            reason_code="x",
            required_action="confirm",
        ).decision
        == "confirm"
    )


def test_invalid_decision_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        PolicyDecision(decision="invalid", reason_code="x")


def test_extra_field_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        PolicyDecision(decision="allow", bogus=1)


def test_required_action_none_valid() -> None:
    result = PolicyDecision(decision="allow", required_action="none")

    assert result.required_action == "none"


def test_invalid_required_action_raises() -> None:
    with pytest.raises(ValidationError):
        PolicyDecision(decision="allow", required_action="invalid_value")


def test_arbitrary_reason_code_open_str() -> None:
    result = PolicyDecision(
        decision="deny",
        reason_code="some_arbitrary_value_xyz",
    )

    assert result.reason_code == "some_arbitrary_value_xyz"


def test_decide_return_isinstance() -> None:
    result = _decide(capability_id="oa_create_task", arguments={})

    assert isinstance(result, PolicyDecision)
