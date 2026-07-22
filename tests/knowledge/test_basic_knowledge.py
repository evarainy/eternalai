"""Deterministic boundaries for the small global knowledge component."""

from __future__ import annotations

from app.knowledge import BasicKnowledge, sanitize_knowledge_text
from app.ports.capability_registry import CapabilitySpec, CapabilityStatus


def _capability(
    capability_id: str,
    *,
    status: CapabilityStatus = "active",
    description: str | None = None,
    owner: str = "knowledge-test-owner",
    name: str | None = None,
    intent_tags: list[str] | None = None,
) -> CapabilitySpec:
    return CapabilitySpec(
        capability_id=capability_id,
        name=name or capability_id,
        type="query",
        intent_tags=intent_tags or [],
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
    first = _capability("oa.expense.first", status="active")
    second = _capability("oa.expense.second", status="active")
    inactive = tuple(
        _capability(f"oa.expense.{status}", status=status)
        for status in ("draft", "disabled", "deprecated")
    )

    first_context = knowledge.context_items("unmatched", (first,))
    second_context = knowledge.context_items("unmatched", (second,))
    inactive_context = knowledge.context_items("unmatched", inactive)

    assert len(first_context) == len(second_context) == 1
    assert "oa.expense.first" in first_context[0]
    assert "type=query" in first_context[0]
    assert "target_system=oa" in first_context[0]
    assert "status=active" in first_context[0]
    assert "oa.expense.first" not in second_context[0]
    assert "oa.expense.second" in second_context[0]
    assert "status=active" in second_context[0]
    assert inactive_context == ()


def test_registry_free_text_is_never_read_into_capability_knowledge() -> None:
    description_marker = "unique-description-marker-7f3a"
    name_marker = "unique-name-marker-8b4c"
    owner_marker = "unique-owner-marker-9d5e"
    intent_marker = "unique-intent-marker-0a6f"
    capability = _capability(
        "oa.safe.query",
        description=description_marker,
        owner=owner_marker,
        name=name_marker,
        intent_tags=[intent_marker],
    )

    context = BasicKnowledge().context_items("unmatched", (capability,))
    serialized = repr(context)

    for free_text in (
        description_marker,
        name_marker,
        owner_marker,
        intent_marker,
    ):
        assert free_text not in serialized
    assert "oa.safe.query" in serialized
    assert "type=query" in serialized


def test_sensitive_items_and_unsafe_capability_ids_fail_closed_as_whole_values() -> None:
    sensitive_items = (
        "token=synthetic-token-value",
        "credential=synthetic-credential-value",
        '"access_token": "synthetic-json-value"',
        'Bearer "synthetic bearer value"',
        '联系人="张三 李四"',
        "endpoint=https://10.20.30.40/internal",
        "mail=person@example.internal",
        "host=192.168.10.20",
    )

    assert [sanitize_knowledge_text(item) for item in sensitive_items] == [
        "[REDACTED]"
    ] * len(sensitive_items)
    context = BasicKnowledge().context_items(
        "unmatched",
        (_capability("token=synthetic-unsafe-id"),),
    )
    assert len(context) == 1
    assert "id=[REDACTED]" in context[0]
    assert "synthetic-unsafe-id" not in context[0]


def test_no_capability_guidance_filters_to_active_registry_entries() -> None:
    active = _capability("oa.active.query")
    disabled = _capability(
        "oa.disabled.query",
        status="disabled",
    )

    message, fallback = BasicKnowledge().no_capability_guidance((disabled, active))

    assert "oa.active.query" in message
    assert "query/oa/active" in message
    assert "oa.disabled.query" not in message
    assert "oa.active.query" in fallback
    assert "query/oa/active" in fallback
    assert "oa.disabled.query" not in fallback
    assert "Admin Lite > Registry" in message
    assert "will not create or execute" in fallback
