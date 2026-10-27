"""AgentScope M1 runtime: one bounded agent reply per user turn.

Every accepted turn has one turn task created at turn start. Business tools run
through the M1 executor; the turn outcome is summarized once from the turn
ledger by fixed rules (terminal first, any business failure fails the turn,
all executions completed completes it), and only this module closes the turn
task. The session stays busy until every close of the turn has ended.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import TYPE_CHECKING, Any, Literal, get_args
from uuid import uuid4

from agentscope.event import ReplyFinishedReason
from agentscope.message import Msg, TextBlock, UserMsg
from agentscope.state import AgentState

from app.contracts.sdui.models import UserAction
from app.infra.llm.agentscope_chat import (
    M1_TURN_LIMITS,
    AgentScopeModelError,
    TurnLimits,
    bind_turn_id,
    create_bounded_agent,
    log_turn_end,
    model_call_count,
    new_turn_deadline,
    reset_turn_id,
    run_bounded_turn,
    tool_execution_count,
    tool_halt_reason,
)
from app.infra.orchestration.agentscope_read_tools import (
    AgentScopeRuntimeDependencies,
    ReadToolScope,
    TaskClose,
    await_close,
    close_once,
)
from app.infra.orchestration.agentscope_runtime.catalog import (
    TOOL_GROUP_PROTOCOL_VERSION,
    ToolCatalog,
    build_tool_catalog,
    visible_capabilities,
)
from app.infra.orchestration.agentscope_runtime.ledger import (
    BindingCard,
    Clarify,
    LedgerExecution,
    TurnLedger,
    Unsupported,
)
from app.infra.orchestration.agentscope_runtime.session import (
    SessionCoordinator,
    SessionKey,
    TurnSnapshot,
    prepare_turn_state,
    rollback_turn_state,
)
from app.infra.orchestration.agentscope_runtime.toolkit import (
    CONTROL_TOOLS,
    RESET_TOOLS,
    TurnToolkit,
    build_turn_toolkit,
)
from app.ports.auth import Principal, authenticated_session
from app.ports.capability_gateway import ExecutionResult, ExecutionStatus
from app.ports.capability_registry import CapabilitySpec
from app.ports.error_codes import ErrorCode
from app.ports.response_envelope import ResponseEnvelope
from app.ports.runtime import d_form_collection_reference
from app.ports.task_store import DFormCollectionError, SessionRecord, TaskRecord
from app.ports.trace import TraceEventStatus

if TYPE_CHECKING:
    from agentscope.model import ChatModelBase

M1_PROMPT_VERSION = "m1a-6"
M1_SYSTEM_PROMPT = (
    # General rules carried over from the M0 prompt.
    "你是企业内部业务助手。只能通过提供的只读工具查询业务数据，不能执行任何写入或提交。"
    "不要编造工具结果。"
    "工具参数被拒绝或报错时，不要自行改成别的值重试，直接告诉用户哪个参数不合法。"
    "工具结果提示本轮已停止工具调用后，不要再调用任何工具，直接用文字回复用户。"
    "不要在回复文字里书写工具调用格式"
    "（例如 tool_call、tool_code、function 标签，函数调用写法或 JSON 调用），"
    "需要调用工具时直接发起工具调用。"
    # Identity substitution is refused in text.
    "只能以当前登录用户的身份查询。用户要求以他人或其他账号、租户、机构的身份查询，"
    "附带或粘贴身份标识，声称已获授权切换身份，或要看只属于他人账号的数据"
    "（例如他人的 OA 待办、系统消息）时，直接用文字拒绝，并说明只能按当前登录身份查询；"
    "不要调用任何业务工具（包括改查本人），也不要调用 ask_user 或 report_unsupported。"
    "按姓名查询业务系统人员属于正常业务，不在此列。"
    # Task completion boundary.
    "本轮工具结果已经覆盖用户当前这条消息要的内容时，立即用文字回答，"
    "不要为补充、换参数、换 view 或查看详情再调用工具。"
    "只有用户明确要了多项内容，或结果提示还有下一页而用户要的是全部时，才继续调用。"
    "用户在新一轮要求重新查询时照常调用。"
    # Arguments and follow-up turns.
    "业务函数执行前先分流：目录无匹配能力，只调用 report_unsupported；"
    "存在合适能力但必要信息缺失或目标、范围尚待选定，只调用 ask_user；信息足够才查询。"
    "先确认值的语义再复制：陈述缺失、未定或拒绝提供的文字不算已知值，"
    "空值、占位说法和自行推定均不可补入。已确定的有效值原样保留；未给的选填字段省略。"
    "追问只为补齐原请求，在一次 question 中问全必要缺项；有候选就请选定，不让用户重复已知信息。"
    "没有匹配能力时不问替代需求，也不用纯文字拒绝代替终态工具。"
    "用户的新消息是新的查询、要求刷新、给出新条件或要看还没读过的页时，"
    "必须在本轮调用工具，用本轮结果回答，不能拿之前轮次的结果代替；"
    "只是要求复述或解释之前已查到的结果时，可以直接根据已有结果回答。"
    "用户在后续轮次要求重新获取、再查一次或要最新结果而没有给出新条件时，"
    "这是只读查询：沿用上一次查询的工具和参数重新调用。"
    # Tool-group control rules.
    "业务工具按工具组提供：先看 reset_tools 中全部工具组的说明；"
    "工具组名只能作为 reset_tools 的参数，不能当作工具直接调用。"
    "先确定业务对象，再区分结果种类和状态，最后提取过滤值；过滤值中的词义不得改变已明确的查询对象。"
    "对照全部分组说明，选择覆盖这些限定的具体操作，时间范围与处理进度分别判断。"
    "用 reset_tools 装载候选所在分组，每次不超过三组；同类操作可能跨组。"
    "随后核对完整说明和参数定义，不能丢掉已给的业务筛选条件来迁就工具；不匹配就换组。"
    "激活工具组后发现没有能处理该请求的工具、也没有别的组可换时，同样调用 report_unsupported。"
    "已经调用过业务工具后不要再调用 report_unsupported；结果为空就如实告诉用户没有查到数据。"
    "闲聊或解释能做什么时直接用文字回答，不要调用 report_unsupported 或 ask_user。"
)
AGENT_NAME = "eternalai_m1"

# Copied verbatim from app/runtime/runtime.py::_CANDIDATE_FAILURE_MESSAGES@6ca76bd
_CANDIDATE_FAILURE_MESSAGES: dict[ErrorCode, tuple[str, str]] = {
    "capability_candidates_low_confidence": (
        "当前请求尚未定位到足够明确的能力，请补充目标系统和具体操作。",
        "Please specify the target system and operation.",
    ),
    "capability_catalog_invalid": (
        "能力配置暂不可用，请联系管理员核对。",
        "Capability configuration is temporarily unavailable; contact an administrator.",
    ),
}
_IntentReason = Literal["provider_error", "blank_input", "schema_invalid", "protocol_error"]


def _intent_failure_message(reason: _IntentReason) -> tuple[str, str]:
    # protocol_error falls through to the schema_invalid wording.
    # Copied verbatim from app/runtime/runtime.py::_intent_failure_message@6ca76bd
    if reason == "provider_error":
        return (
            "模型服务暂时无法连接或响应，请稍后重试。",
            "The model service is temporarily unavailable. Please retry later.",
        )
    if reason == "blank_input":
        return (
            "没有收到可处理的查询内容，请重新输入。",
            "No query content was received. Please enter it again.",
        )
    return (
        "模型返回的查询结果暂时无法识别，请重新提交一次。",
        "The model response could not be recognized. Please submit the query again.",
    )


# Copied verbatim from app/runtime/runtime.py::_finish_user_action_attempt@6ca76bd
_ACTION_REJECTED = (
    "结构化操作未被受理，本次未执行。",
    "The structured action was not accepted; nothing was executed.",
)
_SESSION_INVALID = ("当前登录会话无效，请重新登录。", "The login session is invalid.")
# Model failures caused by response shape or budgets, as opposed to the provider.
_SHAPE_ERRORS = frozenset(
    {"forbidden_request_shape", "tool_call_budget_exceeded", "model_call_budget_exceeded"}
)
_ERROR_CODES: frozenset[str] = frozenset(get_args(ErrorCode))
_RUNTIME_LOGGER = logging.getLogger("uvicorn.error.agentscope_runtime")


def _error_code(code: str) -> ErrorCode:
    # Ledger codes outside the shared vocabulary close tasks as internal_error;
    # the symbolic log keeps the original code.
    return code if code in _ERROR_CODES else "internal_error"  # type: ignore[return-value]


@dataclass(slots=True)
class _Turn:
    """Ids and scope of one accepted turn."""

    scope: ReadToolScope
    response_id: str
    task_id: str
    trace_id: str
    turn_id: str
    deadline: float


@dataclass(slots=True)
class _Progress:
    snapshot: TurnSnapshot | None = None
    agent_ran: bool = False


@dataclass(slots=True)
class _Outcome:
    rule: str
    envelope: ResponseEnvelope
    close: TaskClose
    rollback: bool = False


class AgentScopeRuntime:
    """RuntimePort implementation over AgentScope tool groups (M1A, reads only)."""

    def __init__(
        self,
        deps: AgentScopeRuntimeDependencies,
        *,
        limits: TurnLimits = M1_TURN_LIMITS,
        sessions: SessionCoordinator | None = None,
    ) -> None:
        self._deps = deps
        self._limits = limits
        self._sessions = sessions or SessionCoordinator(
            idle_ttl_seconds=deps.session_idle_ttl_seconds
        )

    async def handle_user_message(
        self,
        channel: Literal["web", "cli", "api", "mock"],
        principal: Principal,
        session_id: str,
        message: str,
        client_capabilities: dict[str, Any],
    ) -> ResponseEnvelope:
        del client_capabilities
        if d_form_collection_reference.get() is not None:
            # Not supported on this path and never handed to the legacy one.
            raise DFormCollectionError("collection_not_found")
        context = authenticated_session.get()
        if (
            context is None
            or context.principal != principal
            or context.expires_at <= datetime.now(UTC)
        ):
            return self._session_invalid(session_id)
        scope = ReadToolScope.from_session(context, bound_session_id=session_id, channel=channel)
        key = self._sessions.key_for(
            tenant_id=scope.tenant_id,
            ai_user_id=scope.ai_user_id,
            fingerprint=context.fingerprint,
            session_id=session_id,
        )
        # Busy check before any task or event exists.
        state = self._sessions.acquire(key, expires_at=context.expires_at)
        turn_id, turn_token = bind_turn_id()
        try:
            turn = _Turn(
                scope=scope,
                response_id=str(uuid4()),
                task_id=str(uuid4()),
                trace_id=str(uuid4()),
                turn_id=turn_id,
                deadline=new_turn_deadline(self._limits),
            )
            return await self._run_turn(turn, key, state, message)
        finally:
            reset_turn_id(turn_token)

    async def handle_user_action(
        self,
        channel: Literal["web", "cli", "api", "mock"],
        principal: Principal,
        session_id: str,
        action: UserAction,
    ) -> ResponseEnvelope:
        # M1A never creates a pending confirmation, so every action is refused.
        del channel
        deps = self._deps
        trace_id, task_id = str(uuid4()), str(uuid4())
        tenant_id, ai_user_id = principal.org_ctx.tenant_id, principal.ai_user_id
        await deps.trace_port.record_step(
            trace_id, task_id, session_id, tenant_id=tenant_id, ai_user_id=ai_user_id,
            event_type="user_action", status="ok",
            attributes={"phase": "inbound", "action_type": action.action_type},
        )
        envelope = deps.response_builder.build_failed(
            str(uuid4()), task_id, session_id, *_ACTION_REJECTED, trace_id,
            data={"action_outcome": "no_pending_action", "result": None},
        )
        await deps.trace_port.record_step(
            trace_id, task_id, session_id, tenant_id=tenant_id, ai_user_id=ai_user_id,
            event_type="user_action", status="blocked",
            attributes={"phase": "outcome", "action_outcome": "no_pending_action"},
        )
        return envelope

    def _session_invalid(self, session_id: str) -> ResponseEnvelope:
        _RUNTIME_LOGGER.info("agentscope_turn result=rejected reason=session_invalid")
        return self._deps.response_builder.build_failed(
            str(uuid4()), str(uuid4()), session_id, *_SESSION_INVALID, str(uuid4())
        )

    async def _run_turn(
        self, turn: _Turn, key: SessionKey, state: AgentState, message: str
    ) -> ResponseEnvelope:
        deps, scope = self._deps, turn.scope
        started = monotonic()
        ledger = TurnLedger(
            response_id=turn.response_id,
            task_id=turn.task_id,
            trace_id=turn.trace_id,
            turn_id=turn.turn_id,
            business_tools={},
            catalog_attributes={},
        )
        progress = _Progress()
        rollback = False
        rule = "internal_error"
        try:
            outcome = await self._turn_body(turn, state, message, ledger, progress)
            rule, rollback = outcome.rule, outcome.rollback
            closing = close_once(ledger, deps, scope, outcome.close)
            remaining = max(0.0, turn.deadline - asyncio.get_running_loop().time())
            envelope = outcome.envelope
            if not await await_close(closing, remaining):
                # A failed terminal write is reported as a failure, never success.
                ledger.unclosed.append(turn.task_id)
                _RUNTIME_LOGGER.info(
                    "agentscope_turn_unclosed task=%s turn=%s", turn.task_id, turn.turn_id
                )
                envelope = self._build_failed(turn, *_intent_failure_message("schema_invalid"))
                rule = "turn_unclosed"
            return envelope
        except asyncio.CancelledError:
            rollback, rule = True, "cancelled"
            close_once(
                ledger, deps, scope,
                TaskClose(turn.task_id, turn.trace_id, None, self._failed_execution(turn)),
            )
            raise
        except Exception:
            # Unexpected defect: close the turn task as failed, then surface it.
            rollback, rule = True, "internal_error"
            close_once(
                ledger, deps, scope,
                TaskClose(turn.task_id, turn.trace_id, None, self._failed_execution(turn)),
            )
            raise
        finally:
            # State changes are synchronous and done before busy can clear.
            if rollback and progress.snapshot is not None:
                rollback_turn_state(state, progress.snapshot)
            self._sessions.release_after(key, [item.future for item in ledger.closes.values()])
            ran = progress.agent_ran
            log_turn_end(
                rule, started,
                model_calls=model_call_count(state) if ran else 0,
                tool_calls=len(ledger.call_of),
                tool_executions=tool_execution_count(state) if ran else 0,
                halt_reason=tool_halt_reason(state) if ran else None,
            )

    async def _open_turn_task(self, turn: _Turn) -> None:
        deps, scope = self._deps, turn.scope
        existing = await deps.session_store.get_session(scope.session_id, tenant_id=scope.tenant_id)
        if existing is None:
            await deps.session_store.create_session(
                SessionRecord(tenant_id=scope.tenant_id, session_id=scope.session_id)
            )
        await deps.task_store.create_task(
            TaskRecord(
                task_id=turn.task_id,
                session_id=scope.session_id,
                ai_user_id=scope.ai_user_id,
                tenant_id=scope.tenant_id,
                status="running",
                trace_id=turn.trace_id,
            )
        )
        await deps.trace_port.start_task_trace(
            turn.trace_id, turn.task_id, scope.session_id,
            tenant_id=scope.tenant_id, ai_user_id=scope.ai_user_id,
        )
        await self._step(turn, "task_created", "ok")

    async def _turn_body(
        self,
        turn: _Turn,
        state: AgentState,
        message: str,
        ledger: TurnLedger,
        progress: _Progress,
    ) -> _Outcome:
        # Preparation counts against the same absolute turn deadline.
        try:
            async with asyncio.timeout_at(turn.deadline):
                prepared = await self._prepare(turn, state, message, ledger, progress)
        except TimeoutError:
            ledger.model_error = "turn_timeout"
            outcome = self._summarize(turn, ledger, None, None, None)
            return await self._finish_intent(turn, ledger, None, outcome)
        if isinstance(prepared, _Outcome):
            return prepared
        catalog, visible, kit, model = prepared
        agent = create_bounded_agent(
            name=AGENT_NAME,
            system_prompt=M1_SYSTEM_PROMPT,
            model=model,
            toolkit=kit.toolkit,
            state=state,
            limits=self._limits,
            extra_middlewares=[kit.middleware],
            classify_tool_error=kit.middleware.classify_tool_error,
        )
        final: Msg | None = None
        progress.agent_ran = True
        try:
            final = await run_bounded_turn(
                agent, UserMsg(name="user", content=message), deadline=turn.deadline
            )
        except AgentScopeModelError as exc:
            ledger.model_error = exc.code
        outcome = self._summarize(turn, ledger, catalog, visible, final)
        return await self._finish_intent(turn, ledger, catalog, outcome)

    async def _prepare(
        self,
        turn: _Turn,
        state: AgentState,
        message: str,
        ledger: TurnLedger,
        progress: _Progress,
    ) -> _Outcome | tuple[ToolCatalog, tuple[CapabilitySpec, ...], TurnToolkit, ChatModelBase]:
        """Open the turn task and build this turn's tools, or short-circuit."""

        deps, scope = self._deps, turn.scope
        await self._open_turn_task(turn)
        if not message.strip():
            await self._intent(turn, "failed", {"result": "invalid", "reason": "blank_input"})
            return self._intent_failure(turn, "blank_input")
        visible = await visible_capabilities(
            deps.capability_registry, deps.candidate_policy,
            ai_user_id=scope.ai_user_id, tenant_id=scope.tenant_id,
            channel=scope.channel, request_id=turn.trace_id,
        )
        if visible is None:
            return await self._catalog_invalid(turn, None)
        if not visible.capabilities:
            # No agent is built and no model is called.
            return await self._empty_catalog(turn, visible.active_count)
        catalog = build_tool_catalog(visible.capabilities)
        if not catalog.tools:
            return await self._catalog_invalid(turn, catalog)
        if catalog.summary_omitted:
            # Listed by name only in their group description; not a catalog gap.
            _RUNTIME_LOGGER.info(
                "agentscope_catalog summary_omitted=%s turn=%s",
                catalog.summary_omitted, turn.turn_id,
            )
        ledger.business_tools = {tool.name: tool.capability_id for tool in catalog.tools}
        ledger.catalog_attributes = catalog.trace_attributes()
        model = deps.model_factory()
        kit = await build_turn_toolkit(
            catalog, scope=scope, deps=deps, ledger=ledger, limits=self._limits,
            model=model, prompt_version=M1_PROMPT_VERSION,
        )
        progress.snapshot = await prepare_turn_state(
            state, read_groups=[group.name for group in catalog.groups], model=model
        )
        return catalog, visible.capabilities, kit, model

    # --- summarization -------------------------------------------------------

    def _summarize(
        self,
        turn: _Turn,
        ledger: TurnLedger,
        catalog: ToolCatalog | None,
        visible: tuple[CapabilitySpec, ...] | None,
        final: Msg | None,
    ) -> _Outcome:
        """Apply R1-R6 (R4c after R4) in order; the first matching rule decides the turn."""

        terminal = ledger.terminal
        if isinstance(terminal, Clarify):
            text: str | None = terminal.question
        elif terminal is None:
            text = self._checked_text(turn, ledger, _model_text(final))
        else:
            text = None
        attach = next((item for item in ledger.executions if item.mode == "attach"), None)
        capability_id = attach.capability_id if attach is not None else None
        if isinstance(ledger.terminal, BindingCard):  # R1
            item = ledger.executions[ledger.terminal.execution_index]
            envelope = self._rebind(turn, item.envelope, None)
            return self._business_outcome(turn, "r1_binding", envelope, item, attach)
        if ledger.failures:  # R2
            first = ledger.failures[0]
            if first.kind == "execution" and first.execution_index is not None:
                item = ledger.executions[first.execution_index]
                envelope = self._rebind(turn, item.envelope, text)
                return self._business_outcome(turn, "r2_failed", envelope, item, attach)
            if first.kind == "pre_exec" and not ledger.turn_task_claimed:
                return _Outcome("r2_rejected", self._build_failed(
                    turn, *_intent_failure_message("schema_invalid")
                ), self._close(turn, None, "failed", _error_code(first.error_code)),
                    rollback=True)
            rejected = _intent_failure_message("schema_invalid")
            envelope = self._build_failed(turn, text or rejected[0], text or rejected[1])
            return _Outcome(
                "r2_failed", envelope,
                self._close(turn, capability_id, "failed", _error_code(first.error_code)),
            )
        if ledger.executions:  # R3: every execution completed
            if isinstance(terminal, Clarify):
                # Case 17b: the question replaces the model text.
                _RUNTIME_LOGGER.info(
                    "agentscope_turn clarify_after_execution turn=%s", turn.turn_id
                )
            if len(ledger.executions) == 1:
                item = ledger.executions[0]
                envelope = self._rebind(turn, item.envelope, text)
                return self._business_outcome(turn, "r3_completed", envelope, item, attach)
            joined = "\n".join(item.envelope.message for item in ledger.executions)
            envelope = self._deps.response_builder.build_message(
                turn.response_id, turn.task_id, turn.scope.session_id,
                text or joined, text or joined, turn.trace_id, status="completed",
            )
            if envelope.status != "completed" or attach is None:
                return self._internal_failure(turn, capability_id)
            close = TaskClose(
                turn.task_id, turn.trace_id, attach.capability_id, attach.execution,
                raw_execution=attach.raw_execution, verification=attach.verification,
                envelope_created=True,
            )
            return _Outcome("r3_completed", envelope, close)
        if isinstance(ledger.terminal, Unsupported):  # R4
            assert catalog is not None and visible is not None
            if catalog.incomplete:
                return self._low_confidence(turn, "r4_low_confidence")
            message, fallback = self._deps.knowledge.no_capability_guidance(visible)
            envelope = self._deps.response_builder.build_no_capability_found(
                turn.response_id, turn.task_id, turn.scope.session_id,
                message, fallback, turn.trace_id,
            )
            return _Outcome(
                "r4_unsupported", envelope,
                self._close(turn, None, "no_capability_found", "capability_not_found"),
            )
        if isinstance(terminal, Clarify):  # R4c: an explicit question, nothing executed
            assert catalog is not None
            if catalog.incomplete:
                return self._low_confidence(turn, "r4c_low_confidence")
            envelope = self._deps.response_builder.build_message(
                turn.response_id, turn.task_id, turn.scope.session_id,
                terminal.question, terminal.question, turn.trace_id, status="completed",
            )
            if envelope.status != "completed":
                return self._internal_failure(turn, None)
            return _Outcome("r4c_clarify", envelope, self._close(turn, None, "completed", None))
        if ledger.model_error is not None or not text or catalog is None:
            # R5; an empty final reply is treated as a malformed response.
            return _Outcome(
                "r5_model_error",
                self._build_failed(turn, *_intent_failure_message(_model_reason(ledger))),
                self._close(turn, None, "failed", "internal_error"),
                rollback=True,
            )
        # R6: a plain answer, follow-up question or chat.
        if catalog.incomplete:
            _RUNTIME_LOGGER.info(
                "agentscope_turn answer_hidden reason=catalog_incomplete turn=%s", turn.turn_id
            )
            return self._low_confidence(turn, "r6_low_confidence")
        envelope = self._deps.response_builder.build_message(
            turn.response_id, turn.task_id, turn.scope.session_id, text, text, turn.trace_id,
            status="completed",
        )
        if envelope.status != "completed":
            return self._internal_failure(turn, None)
        return _Outcome("r6_answer", envelope, self._close(turn, None, "completed", None))

    def _checked_text(self, turn: _Turn, ledger: TurnLedger, text: str | None) -> str | None:
        """Drop final text that carries standalone tool-call blocks; never parse them.

        Without any execution, failure or terminal the turn fails as
        protocol_error (R5); otherwise the business result stands and the
        text is discarded. Content is never logged.
        """

        if text is None:
            return None
        shells = _protocol_shells(text, _final_call_names(ledger))
        if not shells:
            return text
        rule = "discarded" if ledger.executions or ledger.failures else "r5"
        if rule == "r5":
            ledger.model_error = "tool_protocol_text"
        _RUNTIME_LOGGER.info(
            "agentscope_protocol_error code=tool_protocol_text rule=%s shells=%s turn=%s",
            rule, shells, turn.turn_id,
        )
        return None

    async def _finish_intent(
        self, turn: _Turn, ledger: TurnLedger, catalog: ToolCatalog | None, outcome: _Outcome
    ) -> _Outcome:
        """Write the one intent_parsed of turns whose turn task ran no execution."""

        stats: dict[str, Any] = (
            catalog.trace_attributes() if catalog is not None
            else {"protocol_version": TOOL_GROUP_PROTOCOL_VERSION}
        )
        rule = outcome.rule
        if rule == "r6_answer":
            await self._intent(turn, "ok", {"result": "valid", "match": "answer", **stats})
        elif rule == "r4c_clarify":
            await self._intent(turn, "ok", {"result": "valid", "match": "clarify", **stats})
        elif rule in {"r4_low_confidence", "r4c_low_confidence", "r6_low_confidence"}:
            await self._intent(
                turn, "failed", {**stats, "reason": "low_confidence"},
                error_code="capability_candidates_low_confidence",
            )
        elif rule == "r4_unsupported":
            await self._intent(turn, "ok", {"result": "valid", "match": "none", **stats})
            await self._step(
                turn, "no_capability_found", "blocked", error_code="capability_not_found",
                attributes={"reason": "no_matching_capability"},
            )
        elif rule == "r2_rejected":
            first = ledger.failures[0]
            reason = first.pre_exec_reason or "execution_rejected"
            await self._intent(
                turn, "failed",
                {**stats, "result": "invalid", "reason": reason,
                 "rejected_code": first.error_code},
                error_code=_error_code(first.error_code),
            )
        elif rule == "r5_model_error":
            await self._intent(
                turn, "failed",
                {**stats, "result": "invalid", "reason": _model_reason(ledger),
                 "model_error": ledger.model_error or "empty_reply"},
            )
        return outcome

    def _business_outcome(
        self,
        turn: _Turn,
        rule: str,
        envelope: ResponseEnvelope,
        item: LedgerExecution,
        attach: LedgerExecution | None,
    ) -> _Outcome:
        if envelope.status != item.envelope.status:
            return self._internal_failure(turn, item.capability_id)
        if item.mode == "attach":
            close = TaskClose(
                turn.task_id, turn.trace_id, item.capability_id, item.execution,
                raw_execution=item.raw_execution, verification=item.verification,
                envelope_created=True,
            )
        else:
            # An own task's result ends the turn task with the same status/code.
            close = self._close(
                turn, attach.capability_id if attach else None,
                item.execution.status, item.execution.error_code,
            )
        return _Outcome(rule, envelope, close)

    def _rebind(
        self, turn: _Turn, envelope: ResponseEnvelope, message: str | None
    ) -> ResponseEnvelope:
        # Model text reaches the envelope only through the builder. A rebuild
        # that changes the status is retried without the text; the caller
        # turns a second change into internal_error.
        builder = self._deps.response_builder
        rebound = builder.rebind(
            envelope, response_id=turn.response_id, task_id=turn.task_id,
            trace_id=turn.trace_id, message=message,
        )
        if rebound.status != envelope.status and message is not None:
            rebound = builder.rebind(
                envelope, response_id=turn.response_id, task_id=turn.task_id,
                trace_id=turn.trace_id,
            )
        return rebound

    def _internal_failure(self, turn: _Turn, capability_id: str | None) -> _Outcome:
        envelope = self._build_failed(turn, *_intent_failure_message("schema_invalid"))
        return _Outcome(
            "envelope_invalid", envelope,
            self._close(turn, capability_id, "failed", "internal_error"),
        )

    def _low_confidence(self, turn: _Turn, rule: str) -> _Outcome:
        message, fallback = _CANDIDATE_FAILURE_MESSAGES["capability_candidates_low_confidence"]
        return _Outcome(
            rule, self._build_failed(turn, message, fallback),
            self._close(turn, None, "failed", "capability_candidates_low_confidence"),
        )

    def _intent_failure(self, turn: _Turn, reason: _IntentReason) -> _Outcome:
        return _Outcome(
            f"intent_{reason}",
            self._build_failed(turn, *_intent_failure_message(reason)),
            self._close(turn, None, "failed", "internal_error"),
        )

    # --- catalog short-circuits (no agent, no model call) ----------------------

    async def _catalog_invalid(self, turn: _Turn, catalog: ToolCatalog | None) -> _Outcome:
        stats = (
            catalog.trace_attributes() if catalog is not None
            else {"protocol_version": TOOL_GROUP_PROTOCOL_VERSION}
        )
        await self._intent(
            turn, "failed", {**stats, "reason": "catalog_invalid"},
            error_code="capability_catalog_invalid",
        )
        message, fallback = _CANDIDATE_FAILURE_MESSAGES["capability_catalog_invalid"]
        return _Outcome(
            "catalog_invalid", self._build_failed(turn, message, fallback),
            self._close(turn, None, "failed", "capability_catalog_invalid"),
        )

    async def _empty_catalog(self, turn: _Turn, active_count: int) -> _Outcome:
        stats = build_tool_catalog(()).trace_attributes()
        await self._intent(turn, "ok", {**stats, "reason": "empty"})
        reason = "no_active_capability_registered" if active_count == 0 else "no_visible_capability"
        await self._step(
            turn, "no_capability_found", "blocked", error_code="capability_not_found",
            attributes={"reason": reason},
        )
        message, fallback = self._deps.knowledge.no_capability_guidance(())
        envelope = self._deps.response_builder.build_no_capability_found(
            turn.response_id, turn.task_id, turn.scope.session_id, message, fallback,
            turn.trace_id,
        )
        return _Outcome(
            "empty_catalog", envelope,
            self._close(turn, None, "no_capability_found", "capability_not_found"),
        )

    # --- small helpers ----------------------------------------------------------

    def _build_failed(self, turn: _Turn, message: str, fallback: str) -> ResponseEnvelope:
        return self._deps.response_builder.build_message(
            turn.response_id, turn.task_id, turn.scope.session_id, message, fallback,
            turn.trace_id, status="failed",
        )

    def _close(
        self,
        turn: _Turn,
        capability_id: str | None,
        status: ExecutionStatus,
        error_code: ErrorCode | None,
    ) -> TaskClose:
        execution = ExecutionResult(status=status, error_code=error_code, trace_id=turn.trace_id)
        return TaskClose(
            turn.task_id, turn.trace_id, capability_id, execution,
            raw_execution=execution, envelope_created=True,
        )

    def _failed_execution(self, turn: _Turn) -> ExecutionResult:
        return ExecutionResult(status="failed", error_code="internal_error", trace_id=turn.trace_id)

    async def _intent(
        self,
        turn: _Turn,
        status: TraceEventStatus,
        attributes: dict[str, Any],
        *,
        error_code: ErrorCode | None = None,
    ) -> None:
        await self._step(turn, "intent_parsed", status, error_code=error_code,
                         attributes=attributes)

    async def _step(
        self,
        turn: _Turn,
        event_type: Literal["task_created", "intent_parsed", "no_capability_found"],
        status: TraceEventStatus,
        *,
        error_code: ErrorCode | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        scope = turn.scope
        await self._deps.trace_port.record_step(
            turn.trace_id, turn.task_id, scope.session_id,
            tenant_id=scope.tenant_id, ai_user_id=scope.ai_user_id,
            event_type=event_type, status=status, error_code=error_code, attributes=attributes,
        )


def _model_reason(ledger: TurnLedger) -> _IntentReason:
    code = ledger.model_error
    if code == "tool_protocol_text":
        return "protocol_error"
    return "provider_error" if code is not None and code not in _SHAPE_ERRORS else "schema_invalid"


_FENCE = re.compile(r" {0,3}(```|~~~)")
_QUOTE = re.compile(r" {0,3}>")
_INDENTED = re.compile(r"(?: {4}|\t)")
_INLINE_CODE = re.compile(r"(`+).*?\1")
_CALL_OPEN = re.compile(r" {0,3}<tool_call>")
_CALL_CLOSE = "</tool_call>"
_CALL_NAME = re.compile(
    r'<function=([A-Za-z0-9_.\-]+)>?|\{\s*"name"\s*:\s*"([A-Za-z0-9_.\-]+)"'
)
_FUNCTION_NAME = re.compile(r" {0,3}<function=([A-Za-z0-9_.\-]+)")
_FUNCTION_CLOSE = "</function>"
_CODE_OPEN = re.compile(r" {0,3}<?tool_code>")
_CODE_NAME = re.compile(r"(?:print\()?([A-Za-z0-9_.\-]+)\s*\(")
_CODE_CLOSE = "</tool_code>"


def _final_call_names(ledger: TurnLedger) -> frozenset[str]:
    """Tool names exposed to the model call that produced the final text.

    Business tools add their capability id's last segment; reset_tools and
    the control tools count only when that call exposed them too.
    """

    if not ledger.calls:
        return frozenset()
    names: set[str] = set()
    for name in ledger.calls[max(ledger.calls)].tool_digests:
        capability_id = ledger.business_tools.get(name)
        if capability_id is not None:
            names.update((name, capability_id.rsplit(".", 1)[-1]))
        elif name == RESET_TOOLS or name in CONTROL_TOOLS:
            names.add(name)
    return frozenset(names)


def _protocol_shells(text: str, names: frozenset[str]) -> int:
    """Count standalone, unquoted tool-call shells naming an exposed tool.

    Fenced blocks (an unclosed fence runs to the end), indented code lines,
    Markdown quote lines and inline code spans are removed first. A shell
    starts on a line whose text (after at most 3 spaces) begins with its
    opening token: ``<tool_call>`` (a closed block, or a block truncated at
    the end of the text), ``<function=NAME`` or ``<tool_code>``/``tool_code>``
    followed by ``NAME(``. A shell without a recognisable name never counts,
    and text after a complete shell on the same line marks it as an
    explanation rather than a call. Arguments are never parsed.
    """

    lines: list[str] = []
    fence: str | None = None
    for line in text.splitlines():
        opened = _FENCE.match(line)
        if fence is not None:
            if opened is not None and opened.group(1) == fence:
                fence = None
            lines.append("")
        elif opened is not None:
            fence = opened.group(1)
            lines.append("")
        elif _QUOTE.match(line) or _INDENTED.match(line):
            lines.append("")
        else:
            # A placeholder keeps removed inline code counting as text before a tag.
            lines.append(_INLINE_CODE.sub("\x1a", line))
    count = 0
    index = 0
    while index < len(lines):
        line = lines[index]
        if (start := _CALL_OPEN.match(line)) is not None:
            found, index = _call_block(lines, index, start.end(), names)
        elif (function := _FUNCTION_NAME.match(line)) is not None:
            found, index = _function_block(lines, index, function, names)
        elif (code := _CODE_OPEN.match(line)) is not None:
            found, index = _code_block(lines, index, code.end(), names)
        else:
            found, index = False, index + 1
        count += found
    return count


def _call_block(
    lines: list[str], index: int, offset: int, names: frozenset[str]
) -> tuple[bool, int]:
    """A ``<tool_call>`` block: closed (A) or running to the end of the text (B)."""

    parts = [lines[index][offset:]]
    end = index
    while _CALL_CLOSE not in parts[-1] and end + 1 < len(lines):
        end += 1
        parts.append(lines[end])
    body = "\n".join(parts)
    close = body.find(_CALL_CLOSE)
    if close >= 0:
        if body[close + len(_CALL_CLOSE):].strip():
            return False, end + 1
        named = _CALL_NAME.match(body[:close].strip())
        return named is not None and (named.group(1) or named.group(2)) in names, end + 1
    content = body.strip()
    named = _CALL_NAME.match(content)
    if named is None or (named.group(1) or named.group(2)) not in names:
        return False, end + 1
    if named.group(1) is not None and not _header_ok(content, named.end(1)):
        return False, end + 1
    return True, end + 1


def _function_block(
    lines: list[str], index: int, function: re.Match[str], names: frozenset[str]
) -> tuple[bool, int]:
    """A bare ``<function=NAME`` block, closed by ``</function>`` or truncated."""

    line = lines[index]
    end = index
    close = line.find(_FUNCTION_CLOSE, function.end())
    while close < 0 and end + 1 < len(lines):
        end += 1
        close = lines[end].find(_FUNCTION_CLOSE)
    found = (
        function.group(1) in names
        and _header_ok(line, function.end(1))
        and (close < 0 or not lines[end][close + len(_FUNCTION_CLOSE):].strip())
    )
    return found, end + 1


def _code_block(
    lines: list[str], index: int, offset: int, names: frozenset[str]
) -> tuple[bool, int]:
    """A ``tool_code>`` opener whose first content is ``NAME(``."""

    segment = lines[index][offset:]
    end = index
    if not segment.strip():
        end = next((i for i in range(index + 1, len(lines)) if lines[i].strip()), index)
        if end == index:
            return False, index + 1
        segment = lines[end]
    call = segment.strip()
    named = _CODE_NAME.match(call)
    if named is None:
        return False, index + 1
    name = named.group(1)
    if name not in names and name.rsplit(".", 1)[-1] not in names:
        return False, end + 1
    return _call_tail_ok(call[call.index("("):]), end + 1


def _header_ok(text: str, name_end: int) -> bool:
    """After ``<function=NAME`` only an optional ``>`` and a tag, JSON or nothing."""

    rest = text[name_end:].split("\n", 1)[0]
    rest = rest.removeprefix(">").strip()
    return not rest or rest.startswith(("<", "{"))


def _call_tail_ok(call: str) -> bool:
    """A call left open is truncated; a closed call may only be followed by ``</tool_code>``."""

    depth = 0
    quote: str | None = None
    escaped = False
    for position, char in enumerate(call):
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return not call[position + 1:].strip().removeprefix(_CODE_CLOSE).strip()
    return True


def _model_text(final: Msg | None) -> str | None:
    """The model's final text; framework fallback texts never count."""

    if final is None or final.finished_reason == ReplyFinishedReason.EXCEED_MAX_ITERS:
        return None
    text = "".join(
        block.text for block in final.get_content_blocks() if isinstance(block, TextBlock)
    ).strip()
    return text or None


__all__ = ("AGENT_NAME", "M1_PROMPT_VERSION", "M1_SYSTEM_PROMPT", "AgentScopeRuntime")
