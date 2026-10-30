"""AgentScope runtime browser requests (M2 S3): tool exposure, admission and close ownership.

The model is scripted; the handoff, submission and browser orchestration are
recording fakes. Covered boundaries: the browser tool exists only for a
trusted request of the same user and session; the barrier-free orchestration
runs only the accepted browser call; the chat intent is closed exactly once,
by the turn before a browser persistent operation and by the shell after it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import pytest

from app.browser_skill.models import BrowserOwner
from app.infra.browser.d_form_manifest import build_fill_stop_manifest
from app.infra.orchestration.agentscope_runtime.browser_handoff import (
    BROWSER_BINDING_RESOURCE_ID,
)
from app.infra.persistence.task_store.postgresql import d_form_continuation_conflict
from app.ports.agent_orchestration import AgentTaskVersionBindings
from app.ports.auth import authenticated_session
from app.ports.browser_gateway import (
    BrowserGatewayAdmission,
    BrowserGatewayDiagnostic,
    BrowserGatewayRegistration,
    BrowserRequestScope,
    browser_request_scope,
)
from app.ports.capability_gateway import ExecutionResult, RequestOrgContext
from app.ports.capability_registry import CapabilitySpec
from app.ports.human_gate import VersionBinding
from app.ports.response_envelope import ResponseEnvelope
from app.ports.runtime import DFormCollectionReference, d_form_collection_reference
from app.ports.task_store import (
    DFormCollectionError,
    DFormCollectionRecord,
    DFormCollectionSnapshot,
)
from app.runtime.d_form_collection import collect_arguments
from app.version_binding import merge_version_bindings
from tests.infra.orchestration.agentscope_runtime.test_proposal import (
    SESSION_ID,
    Harness,
    Orchestration,
    _calls,
    _query,
    _settle,
    _text,
)

BROWSER_ID = "browser.oa.read"
BROWSER_TOOL = "browser__oa__read"
BROWSER_GROUP = "oa_browser"
READ_TOOL = "oa__leave_balance"
D_FORM_ID = "browser.form.fill_stop"
D_FORM_TOOL = "browser__form__fill_stop"


def _browser_capability() -> CapabilitySpec:
    return _query().model_copy(
        update={
            "capability_id": BROWSER_ID,
            "name": "浏览器读取",
            "input_schema": {
                "type": "object",
                "properties": {"keyword": {"type": "string", "description": "关键词"}},
                "required": ["keyword"],
                "additionalProperties": False,
            },
            "input_schema_digest": f"input-{BROWSER_ID}",
            "short_description": "由浏览器读取 OA 页面。",
            "binding_required": True,
        }
    )


def _d_form_capability() -> CapabilitySpec:
    # Use the real publication schema, including required and minLength. A
    # type-only fake missed the empty-placeholder failure seen at this boundary.
    manifest = build_fill_stop_manifest(origin="https://oa.test")
    schema = json.loads(manifest.capability_snapshot_json)["input_schema"]
    return _browser_capability().model_copy(
        update={
            "capability_id": D_FORM_ID,
            "name": "报修填写",
            "type": "action",
            "input_schema": schema,
            "input_schema_digest": f"input-{D_FORM_ID}",
            "short_description": "填写 OA 报修表单后停止。",
        }
    )


class CollectionStore:
    """In-memory D records; the task status follows the record as in PostgreSQL.

    Saves apply PostgreSQL's own continuation predicate, so a record that
    rewrites collected words is rejected here as it is in production.
    """

    def __init__(self) -> None:
        self.snapshots: dict[str, DFormCollectionSnapshot] = {}
        self.requests: dict[str, tuple[str, str]] = {}

    async def get_d_form_collection(
        self, *, task_id: str, **kwargs: Any
    ) -> DFormCollectionSnapshot | None:
        return self.snapshots.get(task_id)

    async def find_d_form_collection_request(
        self, *, request_id: str, request_digest: str, **kwargs: Any
    ) -> DFormCollectionSnapshot | None:
        if request_id not in self.requests:
            return None
        task_id, digest = self.requests[request_id]
        if digest != request_digest:
            raise DFormCollectionError("collection_request_conflict")
        return self.snapshots.get(task_id)

    async def save_d_form_collection(
        self, record: DFormCollectionRecord, *, expected_version: int | None, **kwargs: Any
    ) -> DFormCollectionSnapshot:
        current = self.snapshots.get(record.task_id)
        if current is None:
            if expected_version is not None or record.version != 1 or record.consumed:
                raise DFormCollectionError("collection_version_conflict")
        elif current.record.consumed:
            raise DFormCollectionError("collection_consumed")
        elif d_form_continuation_conflict(
            current.record, record, expected_version=expected_version,
            task_status=current.task_status,
        ):
            raise DFormCollectionError("collection_version_conflict")
        snapshot = DFormCollectionSnapshot(
            record=record, task_status="running" if record.consumed else "waiting_user"
        )
        self.snapshots[record.task_id] = snapshot
        self.requests[record.request_id] = (record.task_id, record.request_digest)
        return snapshot

    def hand_off(self, task_id: str, admission: BrowserGatewayAdmission) -> None:
        """As handoff.commit: the chat task completes and the handoff is linked."""
        snapshot = self.snapshots.get(task_id)
        if snapshot is None or not snapshot.record.consumed:
            return
        self.snapshots[task_id] = snapshot.model_copy(update={
            "task_status": "completed",
            "handoff": {
                "admission_state": admission.kind,
                "canonical_task_id": admission.task_id,
                "canonical_trace_id": admission.trace_id,
                "canonical_run_id": admission.run_id,
                "state_revision": admission.state_revision,
            },
        })


class ToolBindings(Orchestration):
    """Production selection and responses; bindings by capability type, execution scripted."""

    async def resolve_task_version_bindings(
        self, *, capability: CapabilitySpec, intent_version_binding: VersionBinding
    ) -> AgentTaskVersionBindings:
        tool = VersionBinding(
            resource_type="workflow" if capability.type == "workflow" else "tool",
            resource_id=capability.capability_id,
            version=capability.version,
            digest=hashlib.sha256(capability.model_dump_json().encode()).hexdigest(),
        )
        return AgentTaskVersionBindings(
            bindings=merge_version_bindings((intent_version_binding,), (tool,)),
            projection_binding=tool,
        )

    async def execute_capability(
        self, *, request_context: RequestOrgContext, **kwargs: Any
    ) -> ExecutionResult:
        self.executions += 1
        self.executed_ids.append(kwargs["capability"].capability_id)
        self.executed_arguments.append(dict(kwargs["arguments"]))
        return await self._execute()

    executed_ids: list[str]
    executed_arguments: list[dict[str, Any]]


class Handoff:
    def __init__(self, store: CollectionStore | None = None) -> None:
        self.commits = 0
        self.fail_calls = 0
        self.store = store

    @asynccontextmanager
    async def try_request_route(self, scope: BrowserRequestScope) -> AsyncIterator[bool]:
        yield True

    async def commit(self, invocation: Any, admission: Any) -> None:
        self.commits += 1
        if self.store is not None:
            self.store.hand_off(invocation.task_id, admission)

    async def fail_if_open(self, scope: BrowserRequestScope, error_code: str) -> str:
        self.fail_calls += 1
        return "failed"


class Submission:
    async def lookup_ordinary_request(self, **kwargs: Any) -> ResponseEnvelope | None:
        return None


def _orchestration(
    harness: Harness, execute: Callable[[], Awaitable[ExecutionResult]]
) -> ToolBindings:
    orchestration = ToolBindings(harness.registry, execute)
    orchestration.executed_ids = []
    orchestration.executed_arguments = []
    return orchestration


def _completed() -> Callable[[], Awaitable[ExecutionResult]]:
    async def execute() -> ExecutionResult:
        return ExecutionResult(status="completed", trace_id="t", data={"items": []})

    return execute


class BrowserHarness:
    def __init__(
        self,
        steps: list[Any],
        *,
        user_id: str = "ai-user-1",
        browser_execute: Callable[[], Awaitable[ExecutionResult]] | None = None,
        fill_stop: bool = False,
        category_options: tuple[str, ...] | None = None,
        store: CollectionStore | None = None,
    ) -> None:
        self.base = Harness(steps)
        base = self.base
        browser = _d_form_capability() if fill_stop else _browser_capability()
        base.registry.items = {item.capability_id: item for item in (_query(), browser)}
        base.orchestration = _orchestration(base, _completed())
        self.browser = _orchestration(base, browser_execute or self._admitted)
        deps = base.runtime._deps
        object.__setattr__(deps, "orchestration", base.orchestration)
        object.__setattr__(deps, "browser_orchestration", self.browser)
        # A shared store across harnesses is PostgreSQL surviving a restart.
        self.store = store or CollectionStore()
        if fill_stop:
            object.__setattr__(deps, "d_form_collection_store", self.store)
            base.runtime._browser.d_form_collection_store = self.store  # type: ignore[assignment]
        self.handoff = Handoff(self.store)
        base.runtime.install_browser(
            self.handoff,  # type: ignore[arg-type]
            Submission(),  # type: ignore[arg-type]
            registration=BrowserGatewayRegistration(
                tenant_id="tenant-test",
                user_id=user_id,
                capability_id=browser.capability_id,
                capability_version="1.0.0",
                skill_id="skill.read",
                skill_version="1",
                source_kind="registered_site" if fill_stop else "registered_synthetic_fixture",
                effect="fill_stop" if fill_stop else "read_only",
            ),
            configured_d_form_model="scripted",
            d_form_category_options=category_options,
        )

    async def _admitted(self) -> ExecutionResult:
        scope = browser_request_scope.get()
        assert scope is not None and scope.attempt.task_id and scope.attempt.trace_id
        owner = BrowserOwner(tenant_id="tenant-test", user_id="ai-user-1", session_id=SESSION_ID)
        return ExecutionResult(
            status="running",
            trace_id=scope.attempt.trace_id,
            browser_admission=BrowserGatewayAdmission(
                owner, "canonical-task", "canonical-trace", "accepted", "run-1", 3
            ),
        )

    def open_scope(
        self, *, session_id: str = SESSION_ID, request_id: str | None = None,
    ) -> BrowserRequestScope:
        context = authenticated_session.get()
        assert context is not None
        scope = BrowserRequestScope(context, session_id, request_id or str(uuid4()))
        browser_request_scope.set(scope)
        return scope

    def tools_seen(self) -> str:
        return " ".join(tools for _, tools in self.base.model.seen)

    def statuses(self) -> list[str]:
        return [item for item in self.base.timeline.entries if item.startswith("status:")]


def _browser_steps(arguments: dict[str, Any]) -> list[Any]:
    return [_calls(("reset_tools", {BROWSER_GROUP: True})), _calls((BROWSER_TOOL, arguments))]


def test_browser_tool_only_for_a_trusted_request_of_the_same_user() -> None:
    async def exposure(*, scoped: bool, user_id: str = "ai-user-1") -> BrowserHarness:
        harness = BrowserHarness([_text("你好")], user_id=user_id)
        harness.base.login()
        if scoped:
            harness.open_scope()
        envelope = await harness.base.send("你好")
        assert envelope.status == "completed"
        return harness

    trusted = asyncio.run(exposure(scoped=True))
    assert BROWSER_GROUP in trusted.tools_seen()
    unscoped = asyncio.run(exposure(scoped=False))
    assert BROWSER_GROUP not in unscoped.tools_seen()
    assert READ_TOOL.split("__")[0] + "_query" in unscoped.tools_seen()
    other_user = asyncio.run(exposure(scoped=True, user_id="ai-user-2"))
    assert BROWSER_GROUP not in other_user.tools_seen()


def test_browser_request_of_another_session_never_reaches_a_turn() -> None:
    async def exercise() -> BrowserHarness:
        harness = BrowserHarness([_text("你好")])
        harness.base.login()
        harness.open_scope(session_id="session-other")
        with pytest.raises(ValueError, match="browser_request_context_mismatch"):
            await harness.base.send("你好")
        return harness

    harness = asyncio.run(exercise())
    assert harness.base.model.calls == 0 and harness.base.tasks.records == {}


def test_barrier_free_orchestration_runs_only_the_accepted_browser_call() -> None:
    async def exercise(steps: list[Any]) -> tuple[BrowserHarness, ResponseEnvelope]:
        harness = BrowserHarness(steps)
        harness.base.login()
        harness.open_scope()
        envelope = await harness.base.send("查一下")
        await _settle()
        return harness, envelope

    read, _ = asyncio.run(
        exercise(
            [
                _calls(("reset_tools", {"oa_query": True})),
                _calls((READ_TOOL, {"days": 1})),
                _text("查到了"),
            ]
        )
    )
    assert read.base.orchestration.executed_ids == ["oa.leave_balance"]  # type: ignore[attr-defined]
    assert read.browser.executions == 0
    # An argument outside the registered properties is rejected before any execution.
    extra, envelope = asyncio.run(exercise(_browser_steps({"keyword": "a", "user_id": "x"})))
    assert envelope.status == "failed"
    assert extra.browser.executions == 0 and extra.base.orchestration.executions == 0
    assert extra.handoff.commits == 0 and extra.handoff.fail_calls == 0


def test_admission_hands_off_once_and_freezes_the_browser_binding() -> None:
    async def exercise() -> tuple[BrowserHarness, ResponseEnvelope, Any]:
        harness = BrowserHarness(_browser_steps({"keyword": "报修"}))
        harness.base.login()
        scope = harness.open_scope()
        envelope = await harness.base.send("帮我读一下报修单")
        await _settle()
        assert scope.attempt.task_id is not None
        manifest = await harness.base.runtime._deps.human_gate.get_task_binding(
            scope.attempt.task_id
        )
        return harness, envelope, manifest

    harness, envelope, manifest = asyncio.run(exercise())
    assert envelope.status == "running" and envelope.data == {
        "kind": "accepted",
        "task_id": "canonical-task",
        "run_id": "run-1",
        "state_revision": 3,
    }
    assert harness.browser.executed_ids == [BROWSER_ID]
    assert harness.base.orchestration.executions == 0
    # The handoff closed the chat task: no close by the turn and no fail_if_open.
    assert harness.handoff.commits == 1 and harness.handoff.fail_calls == 0
    assert harness.statuses() == []
    assert any(item.resource_id == BROWSER_BINDING_RESOURCE_ID for item in manifest.bindings)
    _, entry = harness.base.state()
    assert not entry.busy


def test_failure_after_admission_started_is_closed_once_by_the_shell() -> None:
    async def broken() -> ExecutionResult:
        raise RuntimeError("admission transport failed")

    async def exercise() -> tuple[BrowserHarness, ResponseEnvelope]:
        harness = BrowserHarness(_browser_steps({"keyword": "报修"}), browser_execute=broken)
        harness.base.login()
        harness.open_scope()
        envelope = await harness.base.send("帮我读一下报修单")
        await _settle()
        return harness, envelope

    harness, envelope = asyncio.run(exercise())
    assert envelope.status == "running" and envelope.data is not None
    assert envelope.data["kind"] == "unconfirmed"
    assert harness.handoff.fail_calls == 1 and harness.handoff.commits == 0
    # The turn never closed the task itself.
    assert harness.statuses() == []
    _, entry = harness.base.state()
    assert not entry.busy


def test_cancellation_during_the_turn_close_of_a_browser_result_closes_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> tuple[BrowserHarness, bool, bool]:
        harness = BrowserHarness(_browser_steps({"keyword": "报修"}), browser_execute=_completed())
        tasks = harness.base.tasks
        original = tasks.update_status
        closing, gate = asyncio.Event(), asyncio.Event()

        async def stalled(task_id: str, status: str, error_code: str | None = None) -> Any:
            closing.set()
            await gate.wait()
            return await original(task_id, status, error_code)

        monkeypatch.setattr(tasks, "update_status", stalled)
        harness.base.login()
        harness.open_scope()
        request = asyncio.ensure_future(harness.base.send("帮我读一下报修单"))
        await closing.wait()
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        await _settle()
        _, entry = harness.base.state()
        busy_while_closing = entry.busy
        gate.set()
        await _settle()
        return harness, busy_while_closing, entry.busy

    harness, busy_while_closing, busy_after = asyncio.run(exercise())
    assert harness.browser.executed_ids == [BROWSER_ID]
    # The turn's close of the settled result is the only close: no fail_if_open.
    assert harness.handoff.fail_calls == 0 and harness.handoff.commits == 0
    assert harness.statuses() == ["status:completed"]
    assert harness.base.timeline.entries.count("finalize") == 1
    # The session stays busy until that close ends, then it is free.
    assert busy_while_closing and not busy_after


@pytest.mark.parametrize("case", ["admission_timeout", "admission_uncertain", "handoff_failed"])
def test_cancellation_during_a_diagnostic_close_closes_once_and_keeps_the_session_busy(
    case: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def uncertain() -> ExecutionResult:
        timeout = case == "admission_timeout"
        return ExecutionResult(
            status="timeout" if timeout else "failed",
            trace_id="t",
            error_code="adapter_timeout" if timeout else "adapter_error",
            browser_diagnostic=BrowserGatewayDiagnostic("admission", case),
        )

    async def exercise() -> tuple[BrowserHarness, bool, bool]:
        harness = BrowserHarness(
            _browser_steps({"keyword": "报修"}),
            browser_execute=None if case == "handoff_failed" else uncertain,
        )
        handoff = harness.handoff
        closing, gate = asyncio.Event(), asyncio.Event()

        async def stalled(scope: BrowserRequestScope, error_code: str) -> str:
            handoff.fail_calls += 1
            closing.set()
            await gate.wait()
            return "failed"

        async def unavailable(invocation: Any, admission: Any) -> None:
            raise RuntimeError("handoff store unavailable")

        monkeypatch.setattr(handoff, "fail_if_open", stalled)
        if case == "handoff_failed":
            monkeypatch.setattr(handoff, "commit", unavailable)
        harness.base.login()
        harness.open_scope()
        request = asyncio.ensure_future(harness.base.send("帮我读一下报修单"))
        await closing.wait()
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        await _settle()
        _, entry = harness.base.state()
        busy_while_closing = entry.busy
        gate.set()
        await _settle()
        return harness, busy_while_closing, entry.busy

    harness, busy_while_closing, busy_after = asyncio.run(exercise())
    assert harness.browser.executed_ids == [BROWSER_ID]
    # The close the turn started is the only close: the shell starts no second one.
    assert harness.handoff.fail_calls == 1 and harness.handoff.commits == 0
    assert harness.statuses() == []
    assert harness.base.timeline.entries.count("finalize") == 1
    # The session stays busy until that close ends, then it is free.
    assert busy_while_closing and not busy_after


def test_failure_before_any_browser_operation_is_closed_once_by_the_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unavailable(*args: Any, **kwargs: Any) -> AgentTaskVersionBindings:
        raise RuntimeError("binding store unavailable")

    async def exercise() -> tuple[BrowserHarness, ResponseEnvelope]:
        harness = BrowserHarness(_browser_steps({"keyword": "报修"}))
        monkeypatch.setattr(harness.browser, "resolve_task_version_bindings", unavailable)
        harness.base.login()
        harness.open_scope()
        envelope = await harness.base.send("帮我读一下报修单")
        await _settle()
        return harness, envelope

    harness, envelope = asyncio.run(exercise())
    assert envelope.data is not None and envelope.data["kind"] == "unconfirmed"
    assert harness.handoff.fail_calls == 0 and harness.browser.executions == 0
    assert harness.statuses() == ["status:failed"]
    _, entry = harness.base.state()
    assert not entry.busy


def test_collection_reply_never_opens_or_closes_a_task() -> None:
    async def exercise() -> tuple[BrowserHarness, ResponseEnvelope, ResponseEnvelope]:
        harness = BrowserHarness(
            [
                _calls(("reset_tools", {BROWSER_GROUP: True})),
                _calls((D_FORM_TOOL, {"position": "三楼东侧", "description": "监控坏了"})),
                _text('{"position":"三楼东侧"}'),
                _text("好的"),
            ],
            fill_stop=True,
        )
        harness.base.login()
        harness.open_scope()
        card = await harness.base.send("三楼东侧监控坏了")
        await _settle()
        assert card.data is not None and card.data["d_form_collection"] is not None
        reference = card.data["d_form_collection"]
        d_form_collection_reference.set(DFormCollectionReference(**reference))
        harness.open_scope()
        reply = await harness.base.send("先这样")
        await _settle()
        return harness, card, reply

    harness, card, reply = asyncio.run(exercise())
    assert card.status == "waiting_user" and card.data is not None
    assert card.data["missing_fields"] == ["category"]
    assert reply.status == "waiting_user" and reply.data is not None
    assert reply.data["input_error"] == "collection_reply_unparsed"
    assert reply.task_id == card.task_id
    # One turn task only; the store owns its status, neither turn nor shell closes it.
    assert list(harness.base.tasks.records) == [card.task_id]
    assert harness.statuses() == [] and harness.handoff.fail_calls == 0
    assert harness.store.snapshots[card.task_id].record.version == 2
    assert harness.browser.executions == 0
    # The reply is one JSON-schema extraction: no tools, as on the legacy path.
    assert harness.base.model.seen[-1][1] == "null"
    assert harness.base.model.formats[-1]["type"] == "json_schema"
    _, entry = harness.base.state()
    assert not entry.busy


# User decision 2026-10-29: with configured category candidates the model picks
# the category (JSON schema enum, server-side enum check); the user is only
# asked for a missing position or description.
CATEGORIES = ("监控系统", "门禁系统", "广播对讲", "基础网络", "其他类别")


def _d_steps(
    arguments: dict[str, Any], *json_outputs: str, position_output: str | None = None,
) -> list[Any]:
    return [
        _calls(("reset_tools", {BROWSER_GROUP: True})),
        _calls((D_FORM_TOOL, arguments)),
        *([_text(position_output if position_output is not None else json.dumps(
            {"position": arguments["position"]}, ensure_ascii=False,
        ))] if arguments.get("position") else []),
        *(_text(output) for output in json_outputs),
    ]


async def _reply(
    harness: BrowserHarness, card: ResponseEnvelope, text: str, *, request_id: str | None = None,
) -> ResponseEnvelope:
    assert card.data is not None and card.data["d_form_collection"] is not None
    d_form_collection_reference.set(DFormCollectionReference(**card.data["d_form_collection"]))
    harness.open_scope(request_id=request_id)
    envelope = await harness.base.send(text)
    await _settle()
    return envelope


@pytest.mark.parametrize("description", ["投影仪没有画面", "三楼会议室投影仪没有画面"])
def test_complete_report_gets_a_model_chosen_category_and_one_admission(description: str) -> None:
    async def exercise() -> tuple[BrowserHarness, ResponseEnvelope]:
        harness = BrowserHarness(
            _d_steps(
                {"position": "三楼会议室", "description": description},
                '{"category":"其他类别"}',
            ),
            fill_stop=True,
            category_options=CATEGORIES,
        )
        harness.base.login()
        harness.open_scope()
        envelope = await harness.base.send("三楼会议室投影仪没有画面")
        await _settle()
        return harness, envelope

    harness, envelope = asyncio.run(exercise())
    assert envelope.status == "running" and envelope.data is not None
    assert envelope.data["kind"] == "accepted"
    assert harness.browser.executions == 1 and harness.handoff.commits == 1
    assert harness.browser.executed_arguments == [
        {"position": "三楼会议室", "description": description, "category": "其他类别"}
    ]
    # No collection card: nothing was saved for a reply.
    assert harness.store.snapshots == {}
    # The category came from one enum-constrained JSON call over the candidates.
    selection = harness.base.model.formats[-1]
    schema = selection["json_schema"]["schema"]
    assert schema["properties"]["category"]["enum"] == list(CATEGORIES)
    assert harness.base.model.seen[-1][1] == "null"


@pytest.mark.parametrize(
    ("first", "message", "reply", "extracted", "category", "question"),
    [
        (
            {"position": "三楼会议室"},
            "三楼会议室的设备",
            "投影仪没有画面",
            '{"description":"投影仪没有画面"}',
            "其他类别",
            "请补充发生了什么故障",
        ),
        (
            {"description": "门禁刷卡没反应"},
            "门禁刷卡没反应",
            "一楼东门",
            '{"position":"一楼东门"}',
            "门禁系统",
            "请补充故障位置",
        ),
    ],
)
def test_only_the_missing_field_is_asked_and_the_reply_is_admitted(
    first: dict[str, str],
    message: str,
    reply: str,
    extracted: str,
    category: str,
    question: str,
) -> None:
    async def exercise() -> tuple[BrowserHarness, ResponseEnvelope, ResponseEnvelope]:
        harness = BrowserHarness(
            [*_d_steps(first), _text(extracted), _text(f'{{"category":"{category}"}}')],
            fill_stop=True,
            category_options=CATEGORIES,
        )
        harness.base.login()
        harness.open_scope()
        card = await harness.base.send(message)
        await _settle()
        return harness, card, await _reply(harness, card, reply)

    harness, card, admitted = asyncio.run(exercise())
    assert card.status == "waiting_user" and card.data is not None
    assert question in card.message and "类别" not in card.message
    # The card never lists candidates for the user to choose from.
    assert card.data["category_options"] == []
    assert admitted.status == "running" and admitted.data is not None
    assert admitted.data["kind"] == "accepted"
    assert harness.browser.executions == 1
    assert harness.browser.executed_arguments == [
        {**first, **json.loads(extracted), "category": category}
    ]


def test_an_object_offered_as_the_place_is_not_a_place_and_only_the_place_is_asked() -> None:
    async def exercise() -> tuple[BrowserHarness, ResponseEnvelope, int, ResponseEnvelope]:
        harness = BrowserHarness(
            [
                *_d_steps({"position": "监控", "description": "监控坏了"}, position_output="{}"),
                _text('{"position":"三楼东侧"}'),
                _text('{"category":"其他类别"}'),
            ],
            fill_stop=True,
            category_options=CATEGORIES,
        )
        harness.base.login()
        harness.open_scope()
        card = await harness.base.send("监控坏了")
        await _settle()
        executions_before_reply = harness.browser.executions
        return harness, card, executions_before_reply, await _reply(harness, card, "三楼东侧")

    harness, card, executions_before_reply, admitted = asyncio.run(exercise())
    assert card.status == "waiting_user" and card.data is not None
    assert executions_before_reply == 0
    assert "请补充故障位置" in card.message and "类别" not in card.message
    assert admitted.status == "running" and admitted.data is not None
    assert harness.browser.executions == 1
    assert harness.browser.executed_arguments == [
        {"position": "三楼东侧", "description": "监控坏了", "category": "其他类别"}
    ]


def test_a_category_outside_the_candidates_is_rejected_without_admission() -> None:
    async def exercise() -> tuple[BrowserHarness, ResponseEnvelope]:
        harness = BrowserHarness(
            _d_steps(
                {"position": "三楼会议室", "description": "投影仪坏了", "category": "多媒体"},
                '{"category":"多媒体"}',
            ),
            fill_stop=True,
            category_options=CATEGORIES,
        )
        harness.base.login()
        harness.open_scope()
        envelope = await harness.base.send("三楼会议室投影仪坏了，类别：多媒体")
        await _settle()
        return harness, envelope

    harness, envelope = asyncio.run(exercise())
    assert envelope.status == "failed" and "未能从报修类别候选中确定类别" in envelope.message
    assert harness.browser.executions == 0 and harness.handoff.commits == 0
    assert harness.store.snapshots == {}
    # The turn owned the task and closed it as failed, once.
    assert harness.statuses() == ["status:failed"] and harness.handoff.fail_calls == 0


def test_without_candidates_an_explicit_category_reply_is_extracted_as_on_the_legacy_path() -> None:
    # R7: the reply 类别：多媒体 used to end as collection_reply_unparsed when
    # glm answered with text instead of a tool call; it is now a JSON extraction.
    async def exercise() -> tuple[BrowserHarness, ResponseEnvelope, ResponseEnvelope]:
        harness = BrowserHarness(
            [
                *_d_steps({"position": "三楼会议室", "description": "投影仪坏了"}),
                _text('{"category":"多媒体"}'),
            ],
            fill_stop=True,
        )
        harness.base.login()
        harness.open_scope()
        card = await harness.base.send("三楼会议室投影仪坏了")
        await _settle()
        return harness, card, await _reply(harness, card, "类别：多媒体")

    harness, card, admitted = asyncio.run(exercise())
    assert card.data is not None and card.data["missing_fields"] == ["category"]
    assert admitted.status == "running" and admitted.data is not None
    assert admitted.data["kind"] == "accepted"
    assert harness.browser.executed_arguments == [
        {"position": "三楼会议室", "description": "投影仪坏了", "category": "多媒体"}
    ]
    schema = harness.base.model.formats[-1]["json_schema"]["schema"]
    assert set(schema["properties"]) == {"position", "description", "category"}


def _registered_d_form_text(harness: BrowserHarness) -> None:
    # The registered manifest's own wording and required list (d_form_manifest.py).
    harness.base.registry.items[D_FORM_ID] = _d_form_capability().model_copy(
        update={
            "name": "填写表单后停止",
            "short_description": (
                "仅在已打开且获准的表单填写类别、故障位置和故障描述，随后停止；不保存或提交。"
            ),
            "input_schema": {
                **_d_form_capability().input_schema,
                "required": ["category", "position", "description"],
            },
        }
    )


@pytest.mark.parametrize("detail", ["黑屏无法查看", "黑屏无法查看，在1楼走廊东侧"])
def test_an_object_only_repair_report_is_routed_to_d_and_keeps_the_object(detail: str) -> None:
    # 2026-10-30 acceptance: glm answered 监控坏了 in free text, asking for the
    # category, location and urgency, and then picked the OA pending list.
    async def exercise() -> tuple[BrowserHarness, ResponseEnvelope, ResponseEnvelope]:
        harness = BrowserHarness(
            [
                *_d_steps({"description": "监控坏了"}),
                _text(json.dumps({"position": "1楼走廊东侧", "description": detail})),
                _text('{"category":"监控系统"}'),
            ],
            fill_stop=True,
            category_options=CATEGORIES,
        )
        _registered_d_form_text(harness)
        harness.base.login()
        harness.open_scope()
        card = await harness.base.send("监控坏了")
        await _settle()
        return harness, card, await _reply(harness, card, "黑屏无法查看，在1楼走廊东侧")

    harness, card, admitted = asyncio.run(exercise())
    messages, tools = harness.base.model.seen[0]
    # Before any group is active the model sees the repair rule and a D group
    # that takes repair reports; the activated D tool no longer sends missing
    # fields to ask_user.
    assert "OA 报修：用户说设备或设施坏了" in messages and "不要询问类别" in messages
    assert "OA 报修：设备或设施坏了、出故障、需要维修时用这一组" in tools
    _, d_tools = harness.base.model.seen[1]
    assert "OA 报修工具：用户说设备或设施坏了" in d_tools
    assert "缺少时调用 ask_user" not in d_tools
    assert card.status == "waiting_user" and card.data is not None
    assert "请补充故障位置" in card.message and "类别" not in card.message
    assert admitted.status == "running" and admitted.data is not None
    assert admitted.data["kind"] == "accepted"
    assert harness.browser.executed_arguments == [
        {"position": "1楼走廊东侧", "description": f"监控坏了；{detail}", "category": "监控系统"}
    ]
    assert "已有 description 时也提取" in harness.base.model.seen[2][0]
    assert f"监控坏了；{detail}" in harness.base.model.seen[3][0]


_SUPPLEMENT = "黑屏无法查看，在1楼走廊东侧"
_SUPPLEMENT_STEPS = [
    _text(json.dumps({"position": "1楼走廊东侧", "description": "黑屏无法查看"})),
    _text('{"category":"监控系统"}'),
]


@pytest.mark.parametrize("restart", [False, True])
def test_a_supplemented_description_is_admitted_once_and_its_retry_never_admits_again(
    restart: bool,
) -> None:
    # 2026-10-30 acceptance: this reply got 409, as the store rejected the
    # appended description. A restart is a new runtime over the same records.
    async def exercise() -> tuple[BrowserHarness, list[ResponseEnvelope], int]:
        first = BrowserHarness(
            [*_d_steps({"description": "监控坏了"}), *([] if restart else _SUPPLEMENT_STEPS)],
            fill_stop=True, category_options=CATEGORIES,
        )
        first.base.login()
        first.open_scope()
        card = await first.base.send("监控坏了")
        await _settle()
        harness = first
        if restart:
            harness = BrowserHarness(
                _SUPPLEMENT_STEPS, fill_stop=True, category_options=CATEGORIES,
                store=first.store,
            )
            harness.base.login()
        request_id = str(uuid4())
        admitted = await _reply(harness, card, _SUPPLEMENT, request_id=request_id)
        calls = len(harness.base.model.seen)
        retried = await _reply(harness, card, _SUPPLEMENT, request_id=request_id)
        return harness, [card, admitted, retried], calls

    harness, (card, admitted, retried), calls = asyncio.run(exercise())
    assert card.status == "waiting_user"
    record = harness.store.snapshots[card.task_id].record
    assert record.version == 2 and record.consumed
    assert record.arguments["description"] == "监控坏了；黑屏无法查看"
    assert admitted.status == "running" and admitted.data is not None
    assert admitted.data["kind"] == "accepted"
    assert harness.browser.executed_arguments == [
        {"position": "1楼走廊东侧", "description": "监控坏了；黑屏无法查看", "category": "监控系统"}
    ]
    # The browser retry button resends the same request: the original run, no model call.
    assert retried.status == "running" and retried.data is not None
    assert retried.task_id == admitted.task_id == "canonical-task"
    assert retried.data["run_id"] == "run-1"
    assert len(harness.base.model.seen) == calls
    assert harness.browser.executions == 1 and harness.handoff.commits == 1


def test_a_stale_card_after_a_supplement_is_a_version_conflict() -> None:
    async def exercise() -> tuple[BrowserHarness, ResponseEnvelope, ResponseEnvelope, str]:
        harness = BrowserHarness(
            [
                *_d_steps({"description": "监控坏了"}),
                _text('{"description":"黑屏无法查看"}'),
                _text('{"position":"1楼走廊东侧"}'),
                _text('{"category":"监控系统"}'),
            ],
            fill_stop=True, category_options=CATEGORIES,
        )
        harness.base.login()
        harness.open_scope()
        card = await harness.base.send("监控坏了")
        await _settle()
        supplemented = await _reply(harness, card, "黑屏无法查看")
        with pytest.raises(DFormCollectionError) as stale:
            await _reply(harness, card, "在1楼走廊东侧")
        admitted = await _reply(harness, supplemented, "在1楼走廊东侧")
        return harness, supplemented, admitted, stale.value.code

    harness, supplemented, admitted, code = asyncio.run(exercise())
    # Still missing the place, the supplement is a new version on the same card.
    assert supplemented.status == "waiting_user" and supplemented.data is not None
    assert supplemented.data["d_form_collection"]["version"] == 2
    assert "请补充故障位置" in supplemented.message
    assert code == "collection_version_conflict"
    assert admitted.status == "running"
    assert harness.browser.executed_arguments == [
        {"position": "1楼走廊东侧", "description": "监控坏了；黑屏无法查看", "category": "监控系统"}
    ]
    assert harness.browser.executions == 1 and harness.handoff.commits == 1


@pytest.mark.parametrize(
    ("arguments", "collected", "missing"),
    [
        ({"description": "监控坏了"}, ["description"], ["category", "position"]),
        ({"position": "一楼"}, ["position"], ["category", "description"]),
        ({}, [], ["category", "position", "description"]),
        ({"description": "监控坏了", "position": "", "category": ""},
         ["description"], ["category", "position"]),
        ({"position": "", "description": "", "category": ""},
         [], ["category", "position", "description"]),
    ],
)
def test_published_d_schema_accepts_partial_collection(
    arguments: dict[str, str], collected: list[str], missing: list[str],
) -> None:
    async def exercise() -> tuple[BrowserHarness, ResponseEnvelope]:
        harness = BrowserHarness(
            _d_steps(arguments), fill_stop=True, category_options=CATEGORIES,
        )
        harness.base.login()
        harness.open_scope()
        card = await harness.base.send("一楼监控坏了")
        await _settle()
        return harness, card

    harness, card = asyncio.run(exercise())
    assert card.status == "waiting_user" and card.data is not None
    assert card.data["collected_fields"] == collected
    assert card.data["missing_fields"] == missing
    assert card.data["category_options"] == [] and "类别" not in card.message
    if not collected:
        assert "故障位置和发生了什么故障" in card.message
    assert harness.browser.executions == 0 and harness.handoff.commits == 0
    # Projection is a collection contract; the publication itself is unchanged.
    original = harness.base.registry.items[D_FORM_ID].input_schema
    assert original["required"] == ["category", "position", "description"]
    assert original["properties"]["position"]["minLength"] == 1


@pytest.mark.parametrize(
    ("arguments", "reason"),
    [({"position": None}, "framework_validation"), ({"position": 1}, "type"),
     ({"position": "x" * 501}, "framework_validation"),
     ({"tenant_id": "private-marker"}, "browser_keys_or_types")],
)
def test_collection_schema_still_rejects_invalid_arguments(
    arguments: dict[str, Any], reason: str, caplog: pytest.LogCaptureFixture,
) -> None:
    async def exercise() -> tuple[BrowserHarness, ResponseEnvelope]:
        harness = BrowserHarness(
            [*_d_steps(arguments), _text("不能处理")],
            fill_stop=True, category_options=CATEGORIES,
        )
        harness.base.login()
        harness.open_scope()
        card = await harness.base.send("监控坏了")
        await _settle()
        return harness, card

    with caplog.at_level("INFO"):
        harness, card = asyncio.run(exercise())
    assert card.status == "failed"
    assert harness.browser.executions == 0 and harness.store.snapshots == {}
    assert f"code=schema_invalid stage={reason}" in caplog.text
    assert "private-marker" not in caplog.text
    assert "agentscope_d_input shape=" in caplog.text


@pytest.mark.parametrize(
    ("enabled", "consumed", "value", "message", "expected"),
    [
        (False, False, "黑屏", "黑屏", "监控坏了"),  # Legacy default.
        (True, True, "黑屏", "黑屏", "监控坏了"),  # Consumed replay is immutable.
        (True, False, "监控坏了", "监控坏了", "监控坏了"),  # No duplicate.
        (True, False, "黑屏", "请快点处理", "监控坏了"),  # No invented detail.
        (True, False, "黑屏", "黑屏", "监控坏了；黑屏"),
        # Beyond the form limit the recorded words are kept, never replaced.
        (True, False, "黑" * 495, "黑" * 495, "监控坏了；" + "黑" * 495),
        (True, False, "黑" * 496, "黑" * 496, "监控坏了"),
    ],
)
def test_description_supplement_preserves_collection_boundaries(
    enabled: bool, consumed: bool, value: str, message: str, expected: str,
) -> None:
    async def first_record() -> DFormCollectionRecord:
        harness = BrowserHarness(
            _d_steps({"description": "监控坏了"}),
            fill_stop=True, category_options=CATEGORIES,
        )
        harness.base.login()
        harness.open_scope()
        card = await harness.base.send("监控坏了")
        await _settle()
        return harness.store.snapshots[card.task_id].record

    previous = asyncio.run(first_record()).model_copy(update={"consumed": consumed})
    supplied = {"description": value}
    result = (
        collect_arguments(supplied, message, previous, append_description=True)
        if enabled else collect_arguments(supplied, message, previous)
    )
    assert result == {"description": expected}
    assert previous.arguments == {"description": "监控坏了"}


def test_a_pending_query_still_reads_while_the_repair_tool_is_exposed() -> None:
    async def exercise(*, scoped: bool) -> BrowserHarness:
        harness = BrowserHarness(
            [
                _calls(("reset_tools", {"oa_query": True})),
                _calls((READ_TOOL, {"days": 1})),
                _text("查到了"),
            ],
            fill_stop=True,
            category_options=CATEGORIES,
        )
        harness.base.login()
        if scoped:
            harness.open_scope()
        envelope = await harness.base.send("我有哪些待办")
        await _settle()
        assert envelope.status == "completed"
        return harness

    read = asyncio.run(exercise(scoped=True))
    assert read.base.orchestration.executed_ids == ["oa.leave_balance"]  # type: ignore[attr-defined]
    assert read.browser.executions == 0 and read.store.snapshots == {}
    # The repair rule is part of the prompt only while the D tool is exposed.
    assert "OA 报修：用户说设备或设施坏了" in read.base.model.seen[0][0]
    unscoped = asyncio.run(exercise(scoped=False))
    assert "OA 报修" not in unscoped.base.model.seen[0][0]
