"""AgentScope runtime fixed-document route (M2 S4): no agent, one close, document attributes.

The document service is a scripted fake; the model is never called by a document turn.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import pytest

from app.runtime.document_qa import DocumentQAResult
from tests.infra.orchestration.agentscope_runtime.test_proposal import (
    Harness,
    _settle,
    _text,
)

_ANSWERED = DocumentQAResult("answered", "年假五天。", version="doc-v1")


class FakeDocument:
    def __init__(
        self, result: DocumentQAResult | None = None, *, block: bool = False, permitted: bool = True
    ) -> None:
        self.result = result or _ANSWERED
        self.block = block
        self.permitted = permitted
        self.questions: list[str] = []
        self.started = asyncio.Event()

    def permits(self, *, tenant_id: str, ai_user_id: str) -> bool:
        return self.permitted

    async def answer(
        self,
        *,
        question: str,
        tenant_id: str,
        ai_user_id: str,
        deadline: float | None = None,
        trace_id: str = "",
    ) -> DocumentQAResult:
        self.questions.append(question)
        self.started.set()
        if self.block:
            await asyncio.Event().wait()
        return self.result


def _harness(
    document: FakeDocument, steps: list[Any] | None = None, **options: Any
) -> tuple[Harness, list[tuple[str, dict[str, Any] | None]]]:
    harness = Harness(steps or [], **options)
    harness.runtime._deps = replace(harness.runtime._deps, document_qa=document)  # type: ignore[arg-type]
    events: list[tuple[str, dict[str, Any] | None]] = []
    record = harness.trace.record_step

    async def recording(
        trace_id: str, task_id: str, session_id: str, *, event_type: str, status: str, **kwargs: Any
    ) -> None:
        events.append((event_type, kwargs.get("attributes")))
        await record(trace_id, task_id, session_id, event_type=event_type, status=status, **kwargs)

    harness.trace.record_step = recording  # type: ignore[method-assign]
    harness.login()
    return harness, events


def _envelope_attributes(events: list[tuple[str, dict[str, Any] | None]]) -> dict[str, Any] | None:
    (attributes,) = [item for kind, item in events if kind == "response_envelope_created"]
    return attributes


def test_document_answer_closes_once_with_document_attributes() -> None:
    async def exercise() -> tuple[Harness, FakeDocument, Any, list[Any]]:
        document = FakeDocument()
        harness, events = _harness(document)
        envelope = await harness.send("  请问：年假几天")
        await _settle()
        return harness, document, envelope, events

    harness, document, envelope, events = asyncio.run(exercise())
    assert document.questions == ["年假几天"]
    assert envelope.status == "completed" and envelope.message == "年假五天。"
    assert envelope.data == {"kind": "fixed_document_qa", "outcome": "answered", "error_code": None}
    assert harness.model.calls == 0
    assert [kind for kind, _ in events] == [
        "task_created",
        "response_envelope_created",
        "task_completed",
        "evaluation_recorded",
    ]
    assert _envelope_attributes(events) == {
        "kind": "fixed_document_qa",
        "outcome": "answered",
        "reason": "",
        "document_version": "doc-v1",
    }
    entries = harness.timeline.entries
    assert entries.count("finalize") == 1 and entries.count("status:completed") == 1
    (record,) = harness.tasks.records.values()
    assert record.status == "completed"
    _, entry = harness.state()
    assert not entry.busy and not entry.state.has_awaiting_tool_calls("eternalai_m1")


def test_document_failure_result_fails_the_task_with_its_reason() -> None:
    async def exercise() -> tuple[Harness, Any, list[Any]]:
        document = FakeDocument(
            DocumentQAResult("failed", "文档问答超时", "adapter_timeout", "document_timeout")
        )
        harness, events = _harness(document)
        envelope = await harness.send("请问年假几天")
        await _settle()
        return harness, envelope, events

    harness, envelope, events = asyncio.run(exercise())
    assert envelope.status == "failed"
    attributes = _envelope_attributes(events)
    assert attributes is not None
    assert attributes["outcome"] == "failed" and attributes["reason"] == "document_timeout"
    (record,) = harness.tasks.records.values()
    assert record.status == "failed" and harness.timeline.entries.count("finalize") == 1


@pytest.mark.parametrize("mode", ["cancelled", "timeout"])
def test_cancelled_or_timed_out_document_turn_closes_once(mode: str) -> None:
    async def exercise() -> tuple[Harness, Any, list[Any]]:
        document = FakeDocument(block=True)
        harness, events = _harness(document, turn_timeout=0.3)
        envelope = None
        if mode == "cancelled":
            turn = asyncio.ensure_future(harness.send("请问年假几天"))
            await asyncio.wait_for(document.started.wait(), 2)
            turn.cancel()
            with pytest.raises(asyncio.CancelledError):
                await turn
        else:
            envelope = await asyncio.wait_for(harness.send("请问年假几天"), 5)
        await _settle()
        return harness, envelope, events

    harness, envelope, events = asyncio.run(exercise())
    reason = "document_cancelled" if mode == "cancelled" else "document_timeout"
    attributes = _envelope_attributes(events)
    assert attributes is not None and attributes["reason"] == reason
    if envelope is not None:
        assert envelope.status == "failed"
    entries = harness.timeline.entries
    assert entries.count("finalize") == 1 and entries.count("status:failed") == 1
    assert [kind for kind, _ in events].count("task_failed") == 1
    assert harness.model.calls == 0
    _, entry = harness.state()
    assert not entry.busy


@pytest.mark.parametrize("blocker", ["pending", "not_permitted"])
def test_document_route_yields_to_pending_confirmation_and_unpermitted_accounts(
    blocker: str,
) -> None:
    async def exercise() -> tuple[Harness, FakeDocument]:
        document = FakeDocument(permitted=blocker != "not_permitted")
        harness, _ = _harness(document, [_text("你好")])
        harness.coordinator.pending = blocker == "pending"
        await harness.send("请问年假几天")
        return harness, document

    harness, document = asyncio.run(exercise())
    assert document.questions == [] and harness.model.calls == 1
