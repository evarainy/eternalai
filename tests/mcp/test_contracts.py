from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.mcp.contracts import INPUT_SCHEMAS, WRITE_TOOLS, OutputContract, validate_input
from app.mcp.models import McpFailure

NOW = datetime(2026, 9, 30, tzinfo=UTC)
VALID = {
    "business_context_get": {},
    "person_find": {"query": "Synthetic"},
    "talk_context_get": {"personId": "p1", "purpose": "synthetic", "scenario": "synthetic"},
    "clothing_options_get": {"jyId": "j1"},
    "clothing_plan_preview": {
        "jyId": "j1",
        "templateId": "t1",
        "warehouseId": "w1",
        "year": 2026,
        "batchMonth": 9,
        "distDate": "2026-09-29",
    },
    "clothing_plan_submit": {"artifactId": "a" * 32, "payloadHash": "b" * 64},
    "clothing_result_get": {"artifactId": "a" * 32},
    "talk_preparation_save": {
        "personId": "p1",
        "purpose": "synthetic",
        "scenario": "synthetic",
        "outline": "synthetic preparation, not actual minutes",
    },
    "talk_record_draft_save": {
        "personId": "p1",
        "occurredAt": "2026-09-29T10:00:00+08:00",
        "locationCode": "1",
        "officerIds": ["1"],
        "talkTypeCode": "1",
        "effectCode": "1",
        "content": "synthetic",
        "actualNotes": "synthetic",
    },
    "talk_record_submit": {"artifactId": "a" * 32, "payloadHash": "b" * 64},
    "talk_record_get": {"recordId": "r1"},
    "talk_tasks_list": {},
    "talk_task_claim": {"taskId": "a" * 32, "version": 1},
}


def test_full_contract_set_keeps_seven_reads_and_six_writes() -> None:
    assert set(VALID) == set(INPUT_SCHEMAS)
    assert len(VALID) == 13
    assert len(WRITE_TOOLS) == 6
    assert len(set(VALID) - WRITE_TOOLS) == 7


@pytest.mark.parametrize("tool", list(VALID))
def test_each_exact_input_accepts_valid_and_rejects_unknown_and_null(tool: str) -> None:
    args = VALID[tool]
    validate_input(tool, args, now=NOW)
    with pytest.raises(McpFailure, match="mcp_input_invalid"):
        validate_input(tool, {**args, "unapproved": None}, now=NOW)
    for name in INPUT_SCHEMAS[tool]["properties"]:
        with pytest.raises(McpFailure, match="mcp_input_invalid"):
            validate_input(tool, {**args, name: None}, now=NOW)


@pytest.mark.parametrize(
    "tool,key,maximum",
    [
        ("person_find", "page", 1000),
        ("person_find", "pageSize", 20),
        ("talk_tasks_list", "page", 100000),
        ("talk_tasks_list", "pageSize", 100),
        ("talk_context_get", "limit", 10),
    ],
)
def test_pagination_exact_upper_bound_and_strict_integer(tool: str, key: str, maximum: int) -> None:
    validate_input(tool, {**VALID[tool], key: maximum}, now=NOW)
    for bad in (0, maximum + 1, str(maximum), True, 1.0):
        with pytest.raises(McpFailure, match="mcp_input_invalid"):
            validate_input(tool, {**VALID[tool], key: bad}, now=NOW)


@pytest.mark.parametrize(
    "patch",
    [
        {"officerIds": ["1", "1"]},
        {"officerIds": ["1" * 19, "2" * 19, "3" * 19]},
        {"content": "中" * 667},
        {"followUp": "中" * 667},
        {"locationCode": "中" * 17},
        {"talkTypeCode": "11"},
        {"mandatoryTopicCodes": ["a", "a"]},
        {"mandatoryTopicCodes": ["a" * 20, "b" * 20, "c" * 20]},
        {"occurredAt": "2026-10-01T00:00:00+08:00"},
    ],
)
def test_record_semantic_boundaries(patch: dict[str, object]) -> None:
    with pytest.raises(McpFailure, match="mcp_input_invalid"):
        validate_input(
            "talk_record_draft_save", {**VALID["talk_record_draft_save"], **patch}, now=NOW
        )


def test_clothing_date_and_duplicates() -> None:
    for patch in ({"distDate": "2026-10-01"}, {"personIds": ["p1", "p1"]}):
        with pytest.raises(McpFailure, match="mcp_input_invalid"):
            validate_input(
                "clothing_plan_preview", {**VALID["clothing_plan_preview"], **patch}, now=NOW
            )


def test_unknown_outputs_are_not_default_approved_and_projections_are_distinct() -> None:
    policy = OutputContract(
        "synthetic-1",
        {
            "type": "object",
            "required": ["model", "ui"],
            "properties": {"model": {"type": "integer"}, "ui": {"type": "integer"}},
            "additionalProperties": False,
        },
        ("model",),
        ("ui",),
        (),
        "synthetic, unconfirmed",
    )
    with pytest.raises(McpFailure, match="mcp_output_contract_unapproved"):
        policy.require_production()
    data = policy.validate(
        {
            "structuredContent": {"model": 1, "ui": 2},
            "content": [{"type": "text", "text": '{"model":1,"ui":2}'}],
        }
    )
    assert policy.project(data, "model") == {"model": 1}
    assert policy.project(data, "ui") == {"ui": 2}
    assert policy.project(data, "persistence") == {}
    assert policy.postcondition(data) is False
    with pytest.raises(McpFailure, match="mcp_output_invalid"):
        policy.validate(
            {
                "structuredContent": {"model": 1, "ui": 2},
                "content": [{"type": "text", "text": "{}"}],
            }
        )
