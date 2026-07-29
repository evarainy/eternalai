from __future__ import annotations

import asyncio

from pydantic import BaseModel, ConfigDict

from app.infra.llm.json_structured_output import JSONStructuredOutputProvider


class StrictResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability_id: str


def test_json_structured_output_returns_validated_model_and_bounded_metadata() -> None:
    provider = JSONStructuredOutputProvider()

    result = asyncio.run(
        provider.parse_to_schema(
            '{"capability_id":"oa.list_pending_workflows"}',
            StrictResult,
            trace_metadata={"trace_id": "trace-1", "task_id": "task-1"},
        )
    )

    assert result.error is None
    assert result.parsed == StrictResult(capability_id="oa.list_pending_workflows")
    assert result.trace_metadata == {"trace_id": "trace-1", "task_id": "task-1"}
    assert result.raw_response is None


def test_json_structured_output_fails_closed_without_returning_raw_content() -> None:
    provider = JSONStructuredOutputProvider()
    sensitive_raw = '{"capability_id":42,"userpassword":"must-not-escape"}'

    result = asyncio.run(provider.parse_to_schema(sensitive_raw, StrictResult))

    assert result.parsed is None
    assert result.error is not None
    assert result.error.error_code == "validation_error"
    assert result.error.raw_response is None
    assert result.raw_response is None
    assert "must-not-escape" not in repr(result)
