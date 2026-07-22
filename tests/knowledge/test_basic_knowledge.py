"""Deterministic boundaries for the small global knowledge component."""

from __future__ import annotations

from app.knowledge import BasicKnowledge
from app.ports.capability_registry import CapabilitySpec, CapabilityStatus


def _capability(
    capability_id: str,
    *,
    status: CapabilityStatus = "active",
    description: str | None = None,
    owner: str = "knowledge-test-owner",
) -> CapabilitySpec:
    return CapabilitySpec(
        capability_id=capability_id,
        name=capability_id,
        type="query",
        intent_tags=[],
        input_schema_digest=f"input-{capability_id}",
        output_schema_digest=f"output-{capability_id}",
        risk_level="low",
        owner=owner,
        version="1.0.0",
        status=status,
        short_description=description or capability_id,
        target_system="oa",
        execution_identity="user_delegated",
        binding_required=False,
    )


def test_static_content_covers_required_categories_and_keyword_boundaries() -> None:
    knowledge = BasicKnowledge()

    matched = knowledge.context_items("报销单在哪里审批", ())
    unmatched = knowledge.context_items("完全无关的问题", ())

    assert {item.category for item in knowledge.static_items} == {
        "enterprise_term",
        "mock_system",
        "policy_template",
    }
    assert any("企业术语：报销单" in item for item in matched)
    assert any("制度模板：费用报销" in item for item in matched)
    assert all("设备巡检" not in item for item in matched)
    assert unmatched == ()
    assert knowledge.context_items("roadmap automation", ()) == ()
    assert any(
        "Mock 系统说明" in item
        for item in knowledge.context_items("查询 OA 待办", ())
    )


def test_capability_descriptions_follow_each_registry_snapshot() -> None:
    knowledge = BasicKnowledge()
    first = _capability("oa.expense.first", description="first description")
    second = _capability("oa.expense.second", description="second description")

    first_context = knowledge.context_items("unmatched", (first,))
    second_context = knowledge.context_items("unmatched", (second,))

    assert len(first_context) == len(second_context) == 1
    assert "oa.expense.first" in first_context[0]
    assert "first description" in first_context[0]
    assert "oa.expense.first" not in second_context[0]
    assert "first description" not in second_context[0]
    assert "oa.expense.second" in second_context[0]
    assert "second description" in second_context[0]


def test_registry_sensitive_values_are_removed_before_knowledge_exists() -> None:
    credential_key = "access_" + "token"
    credential_value = "synthetic-credential-value"
    private_address = "http://10.20.30.40/internal"
    personal_name = "张三"
    owner_marker = "synthetic-owner-marker"
    quoted_credential = "synthetic alpha beta marker"
    capability = _capability(
        "oa.safe.query",
        description=(
            f"{credential_key}={credential_value} endpoint={private_address} "
            f"联系人:{personal_name} password=\"{quoted_credential}\""
        ),
        owner=owner_marker,
    )

    context = BasicKnowledge().context_items("unmatched", (capability,))
    serialized = repr(context)

    for sensitive in (
        credential_value,
        private_address,
        personal_name,
        owner_marker,
        quoted_credential,
    ):
        assert sensitive not in serialized
    assert serialized.count("[REDACTED]") >= 4


def test_no_capability_guidance_filters_to_active_registry_entries() -> None:
    active = _capability("oa.active.query", description="active description")
    disabled = _capability(
        "oa.disabled.query",
        status="disabled",
        description="disabled description",
    )

    message, fallback = BasicKnowledge().no_capability_guidance((disabled, active))

    assert "oa.active.query" in message
    assert "active description" in message
    assert "oa.disabled.query" not in message
    assert "disabled description" not in message
    assert "Admin Lite > Registry" in message
    assert "will not create or execute" in fallback
