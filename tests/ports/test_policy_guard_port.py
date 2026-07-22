"""Contract tests for PolicyGuardPort policy boundary models."""

from __future__ import annotations

import inspect
from typing import Any, get_args, get_type_hints

import pytest
from pydantic import ValidationError

from app.ports.policy_guard import (
    ManagementPlanePolicyContext,
    PolicyDecision,
    PolicyDecisionValue,
    PolicyGuardPort,
    PolicyRequestContext,
    PolicyRequiredAction,
)
from app.ports.request_context import RequestOrgContext

EXPECTED_POLICY_DECISION_FIELDS = {
    "decision",
    "reason_code",
    "required_action",
}

EXPECTED_POLICY_DECISION_VALUES = ("allow", "deny", "confirm")

EXPECTED_POLICY_REQUIRED_ACTION_VALUES = ("confirm", "none")

REQUIRED_REASON_CODES = (
    "role_not_allowed",
    "policy_denied",
    "high_risk_action_requires_confirm",
)


def test_policy_decision_field_set_matches_contract() -> None:
    assert set(PolicyDecision.model_fields.keys()) == EXPECTED_POLICY_DECISION_FIELDS


def test_policy_decision_literal_values_match_contract() -> None:
    assert get_args(PolicyDecisionValue) == EXPECTED_POLICY_DECISION_VALUES


def test_policy_decision_accepts_all_decision_values() -> None:
    for value in ("allow", "deny", "confirm"):
        assert PolicyDecision(decision=value).decision == value


def test_policy_decision_rejects_decision_outside_contract() -> None:
    with pytest.raises(ValidationError) as exc_info:
        PolicyDecision(decision="escalate")

    assert "Input should be" in str(exc_info.value)


def test_policy_required_action_literal_values_match_contract() -> None:
    assert get_args(PolicyRequiredAction) == EXPECTED_POLICY_REQUIRED_ACTION_VALUES


def test_policy_decision_accepts_all_required_action_values() -> None:
    for value in ("confirm", "none"):
        assert (
            PolicyDecision(decision="confirm", required_action=value).required_action == value
        )
    assert PolicyDecision(decision="confirm", required_action=None).required_action is None


def test_policy_decision_rejects_required_action_outside_contract() -> None:
    with pytest.raises(ValidationError) as exc_info:
        PolicyDecision(decision="confirm", required_action="approve")

    assert "Input should be" in str(exc_info.value)


def test_policy_decision_defaults_reason_code_and_required_action_to_none() -> None:
    decision = PolicyDecision(decision="allow")

    assert decision.reason_code is None
    assert decision.required_action is None


def test_policy_decision_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError) as exc_info:
        PolicyDecision(decision="deny", policy_name="restricted-role")

    assert "Extra inputs are not permitted" in str(exc_info.value)


def test_policy_decision_reason_codes_are_representable() -> None:
    for reason_code in REQUIRED_REASON_CODES:
        decision = PolicyDecision(decision="deny", reason_code=reason_code)

        assert decision.reason_code == reason_code


def test_policy_decision_accepts_arbitrary_reason_code() -> None:
    decision = PolicyDecision(decision="deny", reason_code="some_other_reason_code")

    assert decision.reason_code == "some_other_reason_code"


class TestPolicyGuardPortProtocol:
    def test_protocol_is_not_runtime_checkable(self) -> None:
        assert hasattr(PolicyGuardPort, "__protocol_attrs__")
        assert not getattr(PolicyGuardPort, "_is_runtime_protocol", False)

    def test_protocol_defines_only_decide(self) -> None:
        assert set(PolicyGuardPort.__protocol_attrs__) == {"decide"}

    def test_decide_signature_matches_contract(self) -> None:
        hints = get_type_hints(PolicyGuardPort.decide)
        signature = inspect.signature(PolicyGuardPort.decide)

        assert PolicyGuardPort.decide.__name__ == "decide"
        assert list(signature.parameters) == [
            "self",
            "ai_user_id",
            "capability_id",
            "arguments",
            "request_context",
        ]
        assert hints["ai_user_id"] is str
        assert hints["capability_id"] is str
        assert hints["arguments"] == dict[str, Any]
        assert hints["request_context"] == PolicyRequestContext
        assert hints["return"] is PolicyDecision
        assert inspect.iscoroutinefunction(PolicyGuardPort.decide)


def test_policy_request_context_keeps_business_and_management_planes_distinct() -> None:
    business = RequestOrgContext(request_id="runtime-request", roles=["admin"])
    management = ManagementPlanePolicyContext(
        request_id="admin-request",
        roles=["admin"],
    )

    assert type(business) is RequestOrgContext
    assert type(management) is ManagementPlanePolicyContext
    assert not isinstance(management, RequestOrgContext)
