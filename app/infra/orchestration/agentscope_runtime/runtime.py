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
from app.infra.orchestration.agentscope_runtime.toolkit import TurnToolkit, build_turn_toolkit
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

M1_PROMPT_VERSION = "m1a-3"
M1_SYSTEM_PROMPT = (
    # General rules carried over from the M0 prompt.
    "你是企业内部业务助手。只能通过提供的只读工具查询业务数据，不能执行任何写入或提交。"
    "参数不足、对象不唯一或范围不明确时，先向用户提问澄清，不要猜测参数。不要编造工具结果。"
    "向用户追问时，用“请问……？”这样的问句直接点名缺少的信息，句末带问号；"
    "只问缺少的必要信息，不要写成“请提供……”这类没有问号的祈使句。"
    "工具参数被拒绝或报错时，不要自行改成别的值重试，直接告诉用户哪个参数不合法。"
    "工具结果提示本轮已停止工具调用后，不要再调用任何工具，直接用文字回复用户。"
    "OA 待办只能查询当前登录用户本人的；"
    "用户要求以他人身份或查看他人的待办时，直接拒绝，不要改为查询本人。"
    # Arguments and follow-up turns.
    "填写参数时，用户给出的每个值都要按原值填入对应参数，不要遗漏；"
    "用户没有给出的可选参数不要填写，不要拿别的参数值代替，也不要传空字符串。"
    "回答业务数据必须以本轮调用工具得到的结果为准，不能直接复述之前轮次的结果。"
    "用户在后续轮次要求重新获取、再查一次或要最新结果而没有给出新条件时，"
    "这是只读查询：沿用上一次查询的工具和参数重新调用。"
    # Tool-group control rules.
    "业务工具按工具组提供：先看 reset_tools 中全部工具组的说明；"
    "工具组名只能作为 reset_tools 的参数，不能当作工具直接调用。"
    "选组时逐个对照所有组“包含”列出的工具名称，找出含义最贴近请求的那个工具，"
    "用 reset_tools 激活包含它的组（一次最多 3 组），再调用组内工具；"
    "同一类事项的工具可能分在不同组里。激活后组内没有贴切的工具时，"
    "再调用 reset_tools 换组，不要改用含义不同的工具凑数。"
    "缺少必要参数时先向用户追问。"
    "确实没有任何工具组能处理用户的业务请求时，才调用 report_unsupported，且不要同时调用业务工具；"
    "这时必须调用 report_unsupported，不能只用文字说明不支持，也不要反问用户。"
    "激活工具组后发现没有能处理该请求的工具、也没有别的组可换时，同样调用 report_unsupported。"
    "已经调用过业务工具后不要再调用 report_unsupported；结果为空就如实告诉用户没有查到数据。"
    "闲聊或解释能做什么时直接用文字回答，不要调用 report_unsupported。"
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
_IntentReason = Literal["provider_error", "blank_input", "schema_invalid"]


def _intent_failure_message(reason: _IntentReason) -> tuple[str, str]:
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
        """Apply R1-R6 in order; the first matching rule decides the turn."""

        text = _model_text(final) if ledger.terminal is None else None
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
        elif rule in {"r4_low_confidence", "r6_low_confidence"}:
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
    return "provider_error" if code is not None and code not in _SHAPE_ERRORS else "schema_invalid"


def _model_text(final: Msg | None) -> str | None:
    """The model's final text; framework fallback texts never count."""

    if final is None or final.finished_reason == ReplyFinishedReason.EXCEED_MAX_ITERS:
        return None
    text = "".join(
        block.text for block in final.get_content_blocks() if isinstance(block, TextBlock)
    ).strip()
    return text or None


__all__ = ("AGENT_NAME", "M1_PROMPT_VERSION", "M1_SYSTEM_PROMPT", "AgentScopeRuntime")
