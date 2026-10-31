"""AgentScope M1 runtime: one bounded agent reply per user turn.

Text confirmations, stale card references and ``/action`` go to the shared
confirmation coordinator before the busy check; they never create a turn or
touch AgentState. Every accepted turn has one turn task created at turn start.
Read tools run through the M1 executor; write tools only propose, and a
proposal waiting for confirmation is published to the coordinator, which
alone closes its task from then on. Otherwise the turn outcome is summarized
once from the turn ledger by fixed rules (a published proposal first, then
terminal, any business failure fails the turn, all executions completed
completes it), and only this module closes the turn task. The session stays
busy until every close, interrupt and abandon of the turn has ended.

A trusted browser request (M2) runs inside the browser request shell: replay
lookup and route ownership come before the busy check, the registered
browser tool appears only for that request, and a D collection reply runs a
JSON-extraction continuation on the record's task. Once a browser persistent operation
has started the shell alone closes the chat intent, and the session is
released after that close.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
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
    bounded_turn_scope,
    create_bounded_agent,
    expect_model,
    log_turn_end,
    model_call_count,
    new_turn_deadline,
    reset_expected_model,
    reset_turn_id,
    start_interrupt,
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
from app.infra.orchestration.agentscope_runtime.browser_handoff import (
    BrowserHandoffCoordinator,
    BrowserRequestShell,
    BrowserTurnOwnership,
    allowed_browser_keys,
    expose_browser_tool,
    extract_reply_fields,
)
from app.infra.orchestration.agentscope_runtime.catalog import (
    BROWSER_GROUP,
    TOOL_GROUP_PROTOCOL_VERSION,
    CatalogTool,
    ToolCatalog,
    VisibleCatalog,
    build_tool_catalog,
    visible_capabilities,
)
from app.infra.orchestration.agentscope_runtime.document_route import (
    answer_document,
    document_question,
    failure_close,
    failure_reason,
    timeout_turn,
)
from app.infra.orchestration.agentscope_runtime.ledger import (
    BindingCard,
    BrowserSettled,
    Clarify,
    LedgerExecution,
    Proposal,
    TurnLedger,
    Unsupported,
)
from app.infra.orchestration.agentscope_runtime.proposal import (
    ProposalCoordinator,
    start_abandon,
)
from app.infra.orchestration.agentscope_runtime.session import (
    SessionCoordinator,
    SessionKey,
    TurnSnapshot,
    prepare_turn_state,
    rollback_turn_state,
    start_parked_interrupt,
)
from app.infra.orchestration.agentscope_runtime.toolkit import (
    CONTROL_TOOLS,
    RESET_TOOLS,
    build_turn_toolkit,
)
from app.knowledge import BasicKnowledge
from app.knowledge.bounded import bound_generated_knowledge
from app.knowledge.capability_selection import canonical_json, capability_fingerprint
from app.memory import SessionMemoryKey
from app.ports.auth import AuthenticatedSessionContext, Principal, authenticated_session
from app.ports.browser_chat import BrowserChatError, BrowserOrdinarySubmissionPort
from app.ports.browser_gateway import (
    BrowserGatewayDiagnostic,
    BrowserGatewayRegistration,
    BrowserIntentHandoffPort,
    BrowserRequestScope,
    browser_request_scope,
)
from app.ports.capability_gateway import ExecutionResult, ExecutionStatus
from app.ports.capability_registry import CapabilitySpec
from app.ports.error_codes import ErrorCode
from app.ports.response_envelope import ResponseEnvelope
from app.ports.runtime import (
    ConfirmationOwner,
    DFormCollectionReference,
    chat_client_request_id,
    d_form_collection_reference,
)
from app.ports.task_store import (
    DFormCollectionError,
    DFormCollectionSnapshot,
    SessionRecord,
    TaskRecord,
)
from app.ports.trace import TraceEventStatus
from app.runtime.browser_request import BrowserRequestHelpers
from app.runtime.d_form_collection import CAPABILITY_ID as D_FORM_ID
from app.runtime.d_form_collection import CategoryEvidence

if TYPE_CHECKING:
    from agentscope.agent import Agent

# m2-2: the repair rule while the D tool is exposed (user decision 2026-10-30).
M1_PROMPT_VERSION = "m2-2"
M1_SYSTEM_PROMPT = (
    # General rules carried over from the M0 prompt.
    "你是企业内部业务助手。查询用只读工具；写入类工具只会生成确认卡片，由用户确认后才执行，"
    "不要声称已提交。"
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
    "这类拒绝不属于能力缺失，回复中要明确写出：不能代他人查询，也不能以他人身份操作；"
    "不要说成暂不支持或尚未接入，也不要列出可用能力。"
    "按姓名查询业务系统人员属于正常业务，不在此列。"
    # Task completion boundary.
    "本轮工具结果已经覆盖用户当前这条消息要的内容时，立即用文字回答，"
    "不要为补充、换参数、换 view 或查看详情再调用工具。"
    "只有用户明确要了多项内容，或结果提示还有下一页而用户要的是全部时，才继续调用。"
    "用户在新一轮要求重新查询时照常调用。"
    # Arguments and follow-up turns.
    "填写参数时，用户给出的每个值都要按原值填入对应参数，不要遗漏；"
    "用户没有给出的可选参数不要填写，不要拿别的参数值代替，也不要传空字符串。"
    "用户的新消息是新的查询、要求刷新、给出新条件或要看还没读过的页时，"
    "必须在本轮调用工具，用本轮结果回答，不能拿之前轮次的结果代替；"
    "只是要求复述或解释之前已查到的结果时，可以直接根据已有结果回答。"
    "用户在后续轮次要求重新获取、再查一次或要最新结果而没有给出新条件时，"
    "这是只读查询：沿用上一次查询的工具和参数重新调用。"
    # Tool-group control rules.
    "业务工具按工具组提供：先看 reset_tools 中全部工具组的说明；"
    "工具组名只能作为 reset_tools 的参数，不能当作工具直接调用。"
    "选组时逐个对照所有组“包含”列出的工具名称，找出含义最贴近请求的那个工具，"
    "用 reset_tools 激活包含它的组（一次最多 3 组），再调用组内工具；"
    "同一类事项的工具可能分在不同组里。激活后组内没有贴切的工具时，"
    "再调用 reset_tools 换组，不要改用含义不同的工具凑数。"
    "处理业务请求按以下顺序决定：第一，确定能处理请求的工具；"
    "确实没有任何工具组能处理时调用 report_unsupported，且不要同时调用业务工具，"
    "不能只用文字说明不支持，也不要调用 ask_user 询问用户别的需求。"
    "第二，对照该工具的参数说明逐项确认必填信息：用户明确表示还没定、没提供或留空的内容不是有效值，"
    "不能填入参数，也不能用空字符串或自拟的值代替。"
    "第三，缺少必填信息或需要在几个对象中选定时调用 ask_user，在一次提问里问全所有缺项；"
    "只问完成请求必需的信息，不要索取可选参数或无关字段；"
    "已有候选对象时列出候选请用户选择，不要改为让用户提供 ID。"
    "第四，信息齐全时直接调用工具，不要再追问对话中已有的信息。"
    "激活工具组后发现没有能处理该请求的工具、也没有别的组可换时，同样调用 report_unsupported。"
    "已经调用过业务工具后不要再调用 report_unsupported；结果为空就如实告诉用户没有查到数据。"
    "闲聊或解释能做什么时直接用文字回答，不要调用 report_unsupported 或 ask_user。"
)
# Appended only while this turn exposes the registered D tool.
M1_REPAIR_PROMPT = (
    "OA 报修：用户说设备或设施坏了、出故障、不能用或要报修、维修时（例如“监控坏了”"
    "“投影仪没有画面”），这就是报修请求：用 reset_tools 激活 "
    f"{BROWSER_GROUP} 组并调用其中的报修工具，即使用户只说了哪样东西坏了也要调用。"
    "报修请求不适用上面的第三条：缺少的故障位置或故障情况由系统追问，"
    "不要用文字或 ask_user 追问；报修类别由系统从候选中选择，不要询问类别，"
    "也不要询问姓名、电话、紧急程度等报修表单之外的信息。"
    "报修请求不要调用查询待办、消息等查询工具，也不要调用 report_unsupported。"
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


_SESSION_INVALID = ("当前登录会话无效，请重新登录。", "The login session is invalid.")
# Model failures caused by response shape or budgets, as opposed to the provider.
_SHAPE_ERRORS = frozenset(
    {"forbidden_request_shape", "tool_call_budget_exceeded", "model_call_budget_exceeded"}
)
_ERROR_CODES: frozenset[str] = frozenset(get_args(ErrorCode))
_RUNTIME_LOGGER = logging.getLogger("uvicorn.error.agentscope_runtime")

# M3 §8.3: whole-message phrases after trimming whitespace and sentence-final marks.
_HANDOFF_PHRASES = frozenset(
    {
        "我已提交",
        "已提交",
        "提交了",
        "我提交了",
        "我已经提交了",
        "已经提交了",
        "查询结果",
        "查结果",
        "检查结果",
        "核实结果",
        "查一下结果",
    }
)
_SENTENCE_END = "。．.！!？?～~…，,；;、"
# M3 §9.2 fixed replies, verbatim; business wording only (no codes or internals).
_HANDOFF_SUBMITTED = (
    "目前不能自动确认提交结果。请到 OA 核对这张报修单：已提交的请勿重复提交；"
    '确认未提交且仍需报修时再提交。处理完后可在报修协助卡片上点"结束协助"关闭窗口。'
)
_HANDOFF_MANY = "您有多张报修单的协助尚未结束，请在对应的报修协助卡片上操作。"
_HANDOFF_ENDED = (
    "这张报修单的协助已经结束。提交状态请到 OA 中核对：已提交的请勿重复提交；"
    "确认未提交且仍需报修时再重新发起。"
)
_HANDOFF_CONTINUE_REJECTED = (
    "您还有一张报修单的协助尚未结束。请先到 OA 核对那张单（已提交的请勿重复提交），"
    '处理完后在卡片上点"结束协助"，再继续这次报修。'
)
# A check refused for these reasons means this handoff is over (deadline or terminal).
_HANDOFF_OVER = frozenset(
    {"browser_handoff_expired", "browser_handoff_not_waiting", "browser_run_not_found"}
)


def _handoff_phrase(message: str) -> bool:
    text = message.strip()
    while text and (text[-1] in _SENTENCE_END or text[-1].isspace()):
        text = text[:-1]
    return text in _HANDOFF_PHRASES


def _error_code(code: str) -> ErrorCode:
    # Ledger codes outside the shared vocabulary close tasks as internal_error;
    # the symbolic log keeps the original code.
    return code if code in _ERROR_CODES else "internal_error"  # type: ignore[return-value]


@dataclass(slots=True)
class _BrowserTurn:
    """The trusted browser request a turn runs in, and its D record when replying."""

    scope: BrowserRequestScope
    ownership: BrowserTurnOwnership
    collection: DFormCollectionSnapshot | None = None
    # The registered capability exposed as this turn's browser tool, if any.
    spec: CapabilitySpec | None = None
    # M3 §8.4: this bound session has an active D handoff; no D tool this turn.
    handoff_active: bool = False


@dataclass(slots=True)
class _Turn:
    """Ids and scope of one accepted turn."""

    scope: ReadToolScope
    owner: ConfirmationOwner
    response_id: str
    task_id: str
    trace_id: str
    turn_id: str
    deadline: float
    browser: _BrowserTurn | None = None
    # Question of a fixed-document turn (M2 §4.2); None for every other turn.
    document: str | None = None


@dataclass(slots=True)
class _Progress:
    snapshot: TurnSnapshot | None = None
    agent: Agent | None = None
    agent_ran: bool = False


@dataclass(slots=True)
class _Outcome:
    rule: str
    envelope: ResponseEnvelope
    # None once a published proposal handed the turn task to the coordinator.
    close: TaskClose | None
    rollback: bool = False


class AgentScopeRuntime:
    """RuntimePort implementation over AgentScope tool groups (reads and write proposals)."""

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
        self._browser_registration: BrowserGatewayRegistration | None = None
        self._d_form_category_options: tuple[str, ...] | None = None
        self._browser = BrowserRequestHelpers(
            trace_port=deps.trace_port,
            utc_clock=lambda: datetime.now(UTC),
            d_form_collection_store=deps.d_form_collection_store,
            d_form_category_source=self._d_form_category_evidence,
        )
        self._browser_shell: BrowserRequestShell | None = None
        self._knowledge_digest = _knowledge_items_digest(deps.knowledge)

    def install_browser(
        self,
        handoff: BrowserIntentHandoffPort,
        submission: BrowserOrdinarySubmissionPort,
        *,
        registration: BrowserGatewayRegistration,
        configured_d_form_model: str | None = None,
        d_form_category_options: tuple[str, ...] | None = None,
    ) -> None:
        """Install the browser handoff once; trusted browser requests use it.

        ``d_form_category_options`` are the D installation's configured category
        candidates: the model chooses among them and the user is never asked.
        """

        if self._browser_shell is not None:
            raise ValueError("browser_handoff_already_installed")
        # Same startup check as the legacy IntentRouter.install_browser_checks.
        expected = "glm-4.7" if configured_d_form_model is None else configured_d_form_model.strip()
        if not expected or self._deps.model_name != expected:
            raise ValueError("browser_intent_model_invalid")
        self._browser.install(handoff, submission)
        self._browser_registration = registration
        self._d_form_category_options = d_form_category_options
        self._browser_shell = BrowserRequestShell(self._browser, handoff)

    async def _d_form_category_evidence(
        self, owner: SessionMemoryKey, capability: CapabilitySpec
    ) -> CategoryEvidence | None:
        """Category evidence from the trusted installation configuration, if any."""

        options, registration = self._d_form_category_options, self._browser_registration
        if options is None or registration is None:
            return None
        return CategoryEvidence(
            owner=owner,
            capability_fingerprint=capability_fingerprint(capability),
            target_form_ref=registration.skill_id,
            source_version=registration.skill_version,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            options=options,
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
        context = _verified_session(principal)
        if context is None:
            return self._session_invalid(session_id)
        reference = d_form_collection_reference.get()
        browser_scope, shell = browser_request_scope.get(), self._browser_shell
        if reference is not None and (browser_scope is None or shell is None):
            # A reply needs a trusted browser request; never handed to the legacy path.
            raise DFormCollectionError("collection_not_found")
        if browser_scope is not None and shell is not None and not browser_scope.matches(
            principal, session_id
        ):
            # Same order as the legacy path: before confirmations and the document route.
            raise ValueError("browser_request_context_mismatch")
        owner = _owner(principal, session_id)
        if reference is None:
            # Deterministic confirmations before the busy check: no turn, no AgentState.
            confirmed = await self._deps.confirmations.handle_confirmation_message(
                owner, message, client_request_id=chat_client_request_id.get()
            )
            if confirmed is not None:
                return confirmed
            # M3 §8.3: before the document route and the busy check; no turn or task.
            answered = await self._handoff_prelude(principal, session_id, message)
            if answered is not None:
                return answered
        scope = ReadToolScope.from_session(context, bound_session_id=session_id, channel=channel)
        question = (
            document_question(
                self._deps.document_qa, self._deps.confirmations, owner, scope, message
            )
            if reference is None else None
        )
        if question is not None:
            # Outside the browser shell: busy check as usual, then the document service.
            return await self._accept(scope, context, owner, message, None, question)
        if browser_scope is None or shell is None:
            return await self._accept(scope, context, owner, message, None)
        # Model calls of a browser request must be answered by the configured model.
        expected = expect_model(self._deps.model_name)
        try:
            return await shell.handle(
                browser_scope, channel, message, reference,
                partial(self._accept_browser, scope, context, owner, message, reference),
            )
        finally:
            reset_expected_model(expected)

    async def _accept_browser(
        self,
        scope: ReadToolScope,
        context: AuthenticatedSessionContext,
        owner: ConfirmationOwner,
        message: str,
        reference: DFormCollectionReference | None,
        ownership: BrowserTurnOwnership,
    ) -> ResponseEnvelope:
        """Inside the shell: recheck a D reference before the busy check, then the turn."""

        browser_scope = browser_request_scope.get()
        assert browser_scope is not None
        handoff_active = False
        if self._registration().effect == "fill_stop":
            # M3 §8.4, same trusted bound session; a failed lookup is never "none".
            submission = self._browser.submission
            assert submission is not None
            handoff_active = bool(
                await submission.find_active_handoffs(
                    principal=context.principal, bound_session=scope.session_id
                )
            )
            if handoff_active and reference is not None:
                # Before admit_reference: no collection, no turn, no task.
                return self._handoff_reply(
                    str(uuid4()), scope.session_id, _HANDOFF_CONTINUE_REJECTED, status="failed"
                )
        collection: DFormCollectionSnapshot | None = None
        if reference is not None:
            admitted = await self._browser.admit_reference(
                reference, browser_scope, context.principal, scope.session_id, scope.channel,
            )
            if isinstance(admitted, ResponseEnvelope):
                return admitted
            collection = admitted
        return await self._accept(
            scope,
            context,
            owner,
            message,
            _BrowserTurn(browser_scope, ownership, collection, handoff_active=handoff_active),
        )

    async def _handoff_prelude(
        self, principal: Principal, session_id: str, message: str
    ) -> ResponseEnvelope | None:
        """M3 §8.3: fixed reply when this bound session has active D handoffs.

        Only the trusted bound session is searched, never client task fields.
        None (ordinary routing) for other messages, zero handoffs or a failed
        lookup; the browser path rechecks before exposing D and submission
        keeps its own guard.
        """

        submission, registration = self._browser.submission, self._browser_registration
        if (
            submission is None
            or registration is None
            or registration.effect != "fill_stop"
            or not _handoff_phrase(message)
        ):
            return None
        try:
            found = await submission.find_active_handoffs(
                principal=principal, bound_session=session_id
            )
        except BrowserChatError as error:
            _RUNTIME_LOGGER.info(
                "agentscope_handoff_prelude result=skipped reason=%s", _error_code(error.code)
            )
            return None
        if not found:
            return None
        if len(found) > 1:
            return self._handoff_reply(str(uuid4()), session_id, _HANDOFF_MANY)
        task_id, run_id = found[0]
        text = _HANDOFF_SUBMITTED
        try:
            # Production verification is unsupported this phase (Q4): the reply
            # never claims a result; the worker settles the requested check.
            await submission.check_handoff(principal, session_id, task_id, run_id)
        except BrowserChatError as error:
            if error.code in _HANDOFF_OVER:
                text = _HANDOFF_ENDED
        return self._handoff_reply(task_id, session_id, text)

    def _handoff_reply(
        self,
        task_id: str,
        session_id: str,
        text: str,
        *,
        status: Literal["completed", "failed"] = "completed",
    ) -> ResponseEnvelope:
        _RUNTIME_LOGGER.info("agentscope_handoff_reply status=%s", status)
        return self._deps.response_builder.build_message(
            str(uuid4()), task_id, session_id, text, text, str(uuid4()), status=status
        )

    async def _accept(
        self,
        scope: ReadToolScope,
        context: AuthenticatedSessionContext,
        owner: ConfirmationOwner,
        message: str,
        browser: _BrowserTurn | None,
        document: str | None = None,
    ) -> ResponseEnvelope:
        key = self._sessions.key_for(
            tenant_id=scope.tenant_id,
            ai_user_id=scope.ai_user_id,
            fingerprint=context.fingerprint,
            session_id=scope.session_id,
        )
        # Busy check before any task or event exists.
        # Cache bound to the login instance; each turn is still gated by _verified_session.
        state = self._sessions.acquire(key, expires_at=context.instance_expires_at)
        record = browser.collection.record if browser and browser.collection else None
        turn_id, turn_token = bind_turn_id()
        try:
            turn = _Turn(
                scope=scope,
                owner=owner,
                response_id=str(uuid4()),
                # A continuation runs on the record's task and trace (no task_created).
                task_id=record.task_id if record is not None else str(uuid4()),
                trace_id=record.trace_id if record is not None else str(uuid4()),
                turn_id=turn_id,
                deadline=new_turn_deadline(self._limits),
                browser=browser,
                document=document,
            )
            if browser is not None and record is not None:
                # The record's task is never this turn's to close: the shell
                # alone settles it, as after any browser persistent operation.
                browser.ownership.browser_owned = True
                browser.scope.attempt.task_id = turn.task_id
                browser.scope.attempt.trace_id = turn.trace_id
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
        # The card carries its own reference: never busy, never a turn.
        del channel
        if _verified_session(principal) is None:
            return self._session_invalid(session_id)
        return await self._deps.confirmations.handle_confirmation_action(
            _owner(principal, session_id), action
        )

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
            document_route=turn.document is not None,
        )
        progress = _Progress()
        rollback = False
        rule = "internal_error"
        try:
            outcome = await self._turn_body(turn, state, message, ledger, progress)
            rule, rollback = outcome.rule, outcome.rollback
            envelope = outcome.envelope
            if outcome.close is None:
                return envelope
            closing = close_once(ledger, deps, scope, outcome.close)
            if turn.browser is not None:
                # Cancelled or timed out while this close runs: the shell must not close again.
                turn.browser.ownership.turn_close = closing.future
            remaining = max(0.0, turn.deadline - asyncio.get_running_loop().time())
            if not await await_close(closing, remaining):
                # A failed terminal write is reported as a failure, never success.
                ledger.unclosed.append(turn.task_id)
                _RUNTIME_LOGGER.info(
                    "agentscope_turn_unclosed task=%s turn=%s", turn.task_id, turn.turn_id
                )
                envelope = self._build_failed(turn, *_intent_failure_message("schema_invalid"))
                rule = "turn_unclosed"
            return envelope
        except asyncio.CancelledError as error:
            rollback, rule = True, "cancelled"
            self._fail_unpublished(turn, ledger, error)
            raise
        except Exception as error:
            # Unexpected defect: close the turn task as failed, then surface it.
            rollback, rule = True, "internal_error"
            self._fail_unpublished(turn, ledger, error)
            raise
        finally:
            agent = progress.agent
            if (
                agent is not None
                and (ledger.interrupt is None or ledger.interrupt.done())
                and state.has_awaiting_tool_calls(AGENT_NAME)
            ):
                # A reply left parked is closed before rollback and release.
                ledger.interrupt = start_interrupt(agent, state.reply_id)
            futures: list[asyncio.Future[Any]] = [item.future for item in ledger.closes.values()]
            futures += [item for item in (ledger.interrupt, ledger.abandon) if item is not None]
            # Rollback waits for every interrupt and abandon, then busy clears.
            before_release = (
                partial(rollback_turn_state, state, progress.snapshot)
                if rollback and progress.snapshot is not None
                else None
            )
            ownership = turn.browser.ownership if turn.browser is not None else None
            if ownership is not None and ownership.browser_owned:
                # Released by the shell, after its chat-intent close if it starts one.
                ownership.defer_release(
                    lambda extra: self._sessions.release_after(
                        key, [*futures, *extra], before_release=before_release
                    )
                )
            else:
                self._sessions.release_after(key, futures, before_release=before_release)
            ran = progress.agent_ran
            log_turn_end(
                rule, started,
                model_calls=model_call_count(state) if ran else 0,
                tool_calls=len(ledger.call_of),
                tool_executions=tool_execution_count(state) if ran else 0,
                halt_reason=tool_halt_reason(state) if ran else None,
            )

    def _fail_unpublished(self, turn: _Turn, ledger: TurnLedger, error: BaseException) -> None:
        """Cancellation or a defect before publication; after it the coordinator owns the task.

        Once a browser persistent operation started, the browser shell closes instead.
        A document turn closes with its own failure reason, through the same single close.
        """

        if ledger.handoff or (turn.browser is not None and turn.browser.ownership.browser_owned):
            return
        self._abandon_unpublished(ledger)
        close = (
            failure_close(turn.task_id, turn.trace_id, failure_reason(error))
            if ledger.document_route
            else TaskClose(turn.task_id, turn.trace_id, None, self._failed_execution(turn))
        )
        close_once(ledger, self._deps, turn.scope, close)

    def _abandon_unpublished(self, ledger: TurnLedger) -> None:
        if ledger.staged is not None and not ledger.handoff and ledger.abandon is None:
            ledger.abandon = start_abandon(self._deps.confirmations, ledger.staged)

    def _publish(self, turn: _Turn, agent: Agent, ledger: TurnLedger) -> None:
        """Publish a staged proposal right after its waiting marks, or abandon it.

        Synchronous: no await between the end of the waiting marks and the CAS.
        """

        staged = ledger.staged
        assert staged is not None
        if (
            ledger.model_error is None
            and isinstance(ledger.terminal, Proposal)
            and self._deps.confirmations.publish_waiting(staged)
        ):
            ledger.handoff = True
            ledger.interrupt = start_interrupt(agent, agent.state.reply_id)
            _RUNTIME_LOGGER.info(
                "agentscope_proposal published=1 task=%s turn=%s", turn.task_id, turn.turn_id
            )
            return
        _RUNTIME_LOGGER.info(
            "agentscope_proposal published=0 task=%s turn=%s", turn.task_id, turn.turn_id
        )
        self._abandon_unpublished(ledger)

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
        if turn.document is not None:
            return await self._document_body(turn)
        if turn.browser is not None and turn.browser.collection is not None:
            return await self._continuation_body(turn, state, message, ledger, progress)
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
        catalog, visible, agent = prepared
        browser = self._browser_coordinator(turn, ledger, catalog, message)
        proposals = ProposalCoordinator.from_catalog(
            catalog, scope=turn.scope, deps=self._deps, ledger=ledger, limits=self._limits,
            owner=turn.owner, browser=browser,
        )
        final = await self._drive(turn, agent, proposals, message, ledger, progress)
        if ledger.staged is not None:
            self._publish(turn, agent, ledger)
        outcome = self._summarize(turn, ledger, catalog, visible, final)
        return await self._finish_intent(turn, ledger, catalog, outcome)

    async def _document_body(self, turn: _Turn) -> _Outcome:
        """Open the turn task and answer from the fixed document; no agent, no AgentState."""

        service = self._deps.document_qa
        assert service is not None and turn.document is not None
        builder, scope = self._deps.response_builder, turn.scope
        try:
            async with asyncio.timeout_at(turn.deadline):
                await self._open_turn_task(turn)
                answered = await answer_document(
                    service, builder, scope, response_id=turn.response_id,
                    task_id=turn.task_id, trace_id=turn.trace_id, question=turn.document,
                    deadline=turn.deadline,
                )
        except TimeoutError:
            answered = timeout_turn(
                builder, scope, turn.response_id, turn.task_id, turn.trace_id
            )
        return _Outcome(f"document_{answered.outcome}", answered.envelope, answered.close)

    async def _drive(
        self,
        turn: _Turn,
        agent: Agent,
        proposals: ProposalCoordinator,
        message: str,
        ledger: TurnLedger,
        progress: _Progress,
    ) -> Msg | None:
        progress.agent_ran = True
        try:
            # One absolute deadline covers the reply, every parked-call check,
            # every ERROR continuation and the proposal core with its marks.
            async with bounded_turn_scope(turn.deadline):
                final = await agent.reply(UserMsg(name="user", content=message))
                return await proposals.drive(agent, final)
        except AgentScopeModelError as exc:
            ledger.model_error = exc.code
            browser = turn.browser
            if (
                browser is not None
                and browser.ownership.browser_owned
                and not isinstance(ledger.terminal, BrowserSettled)
                and (browser.collection is None or ledger.turn_task_claimed)
            ):
                # Stopped inside a browser persistent operation: the shell
                # recovers and closes the chat intent; the turn never does.
                if exc.code in {"turn_timeout", "timeout"}:
                    raise TimeoutError from None
                raise RuntimeError("browser_operation_unsettled") from None
            return None

    async def _continuation_body(
        self,
        turn: _Turn,
        state: AgentState,
        message: str,
        ledger: TurnLedger,
        progress: _Progress,
    ) -> _Outcome:
        """A D collection reply: one JSON extraction on the record's task, no agent.

        Never opens a task and never touches AgentState. A reply whose JSON
        output is unusable keeps the collected fields (collection_reply_unparsed);
        a consumed record replays its arguments without any model call.
        """

        del state, progress
        deps, scope = self._deps, turn.scope
        browser = turn.browser
        assert browser is not None and browser.collection is not None
        record = browser.collection.record
        owner_key = _memory_key(scope)
        async with asyncio.timeout_at(turn.deadline):
            visible = await visible_capabilities(
                deps.capability_registry, deps.candidate_policy,
                ai_user_id=scope.ai_user_id, tenant_id=scope.tenant_id,
                channel=scope.channel, request_id=turn.trace_id,
                browser_capability_id=D_FORM_ID,
            )
            spec = self._browser_spec(turn, visible)
            if (
                spec is None
                or spec.capability_id != D_FORM_ID
                or spec.version != record.capability_version
                or capability_fingerprint(spec) != record.capability_fingerprint
            ):
                raise DFormCollectionError("collection_version_conflict")
            category_options = await self._browser.category_options(owner_key, spec)
            catalog, tool = expose_browser_tool(build_tool_catalog(()), spec, self._registration())
            if tool is None:
                raise DFormCollectionError("collection_version_conflict")
            ledger.business_tools = {tool.name: tool.capability_id}
            ledger.browser_tools = {tool.name: tool.capability_id}
            ledger.catalog_attributes = catalog.trace_attributes()
            coordinator = self._new_coordinator(
                turn, ledger, tool, spec, message, category_options
            )
            if record.consumed:
                # The sole winning reply replays the immutable arguments.
                ledger.turn_task_claimed = True
                await coordinator.run({})
            else:
                fields = await extract_reply_fields(
                    deps.model_factory(), message, dict(record.arguments),
                    explicit_category=category_options is None,
                )
                if fields is not None:
                    ledger.turn_task_claimed = True
                    await coordinator.run(fields)
        terminal = ledger.terminal
        if isinstance(terminal, BrowserSettled):
            return _Outcome("browser_settled", terminal.envelope, terminal.close)
        if record.consumed:
            raise RuntimeError("browser_replay_unsettled")
        # The card never lists configured candidates: categories are not asked.
        envelope = await self._browser.retain_unparsed(
            browser.scope, owner_key, scope.channel, browser.collection, None,
        )
        return _Outcome("collection_unparsed", envelope, None)

    def _registration(self) -> BrowserGatewayRegistration:
        assert self._browser_registration is not None
        return self._browser_registration

    def _browser_spec(self, turn: _Turn, visible: VisibleCatalog | None) -> CapabilitySpec | None:
        """The registered browser capability when this request may expose it (§3.3)."""

        browser, registration = turn.browser, self._browser_registration
        deps, scope = self._deps, turn.scope
        if browser is None or registration is None or visible is None or browser.handoff_active:
            return None
        spec = visible.browser
        if (
            spec is None
            or deps.browser_orchestration is None
            or not browser.scope.matches(browser.scope.verified.principal, scope.session_id)
            or browser.scope.verified.principal.ai_user_id != scope.ai_user_id
            or registration.tenant_id != scope.tenant_id
            or registration.user_id != scope.ai_user_id
            or spec.capability_id != registration.capability_id
            or spec.status != "active"
            or spec.version != registration.capability_version
            or spec.type != ("action" if registration.effect == "fill_stop" else "query")
            or (registration.effect == "fill_stop" and deps.d_form_collection_store is None)
        ):
            return None
        return spec

    def _new_coordinator(
        self,
        turn: _Turn,
        ledger: TurnLedger,
        tool: CatalogTool,
        spec: CapabilitySpec,
        message: str,
        category_options: tuple[str, ...] | None = None,
    ) -> BrowserHandoffCoordinator:
        browser, registration = turn.browser, self._registration()
        orchestration = self._deps.browser_orchestration
        assert browser is not None and orchestration is not None
        return BrowserHandoffCoordinator(
            deps=self._deps,
            orchestration=orchestration,
            helpers=self._browser,
            registration=registration,
            tool=tool,
            allowed_keys=allowed_browser_keys(registration, spec),
            scope=turn.scope,
            browser_scope=browser.scope,
            ownership=browser.ownership,
            ledger=ledger,
            message=message,
            collection=browser.collection,
            category_options=category_options,
        )

    def _browser_coordinator(
        self, turn: _Turn, ledger: TurnLedger, catalog: ToolCatalog, message: str
    ) -> BrowserHandoffCoordinator | None:
        tool = next((item for item in catalog.tools if item.kind == "browser"), None)
        if tool is None or turn.browser is None:
            return None
        spec = turn.browser.spec
        assert spec is not None
        return self._new_coordinator(turn, ledger, tool, spec, message)

    async def _prepare(
        self,
        turn: _Turn,
        state: AgentState,
        message: str,
        ledger: TurnLedger,
        progress: _Progress,
    ) -> _Outcome | tuple[ToolCatalog, tuple[CapabilitySpec, ...], Agent]:
        """Open the turn task and build this turn's agent, or short-circuit."""

        deps, scope = self._deps, turn.scope
        await self._open_turn_task(turn)
        browser = turn.browser
        if browser is not None:
            # The Gateway admits the browser capability only on this turn's ids.
            browser.scope.attempt.task_id = turn.task_id
            browser.scope.attempt.trace_id = turn.trace_id
        if not message.strip():
            await self._intent(turn, "failed", {"result": "invalid", "reason": "blank_input"})
            return self._intent_failure(turn, "blank_input")
        visible = await visible_capabilities(
            deps.capability_registry, deps.candidate_policy,
            ai_user_id=scope.ai_user_id, tenant_id=scope.tenant_id,
            channel=scope.channel, request_id=turn.trace_id,
            browser_capability_id=(
                self._browser_registration.capability_id
                if browser is not None and self._browser_registration is not None
                else None
            ),
        )
        if visible is None:
            return await self._catalog_invalid(turn, None)
        spec = self._browser_spec(turn, visible)
        if visible.scope_excluded:
            # Out of this path's scope (browser.*); not a catalog gap.
            _RUNTIME_LOGGER.info(
                "agentscope_catalog scope_excluded=%s turn=%s",
                visible.scope_excluded, turn.turn_id,
            )
        if not visible.capabilities and spec is None:
            # No agent is built and no model is called.
            return await self._empty_catalog(turn, visible.active_count)
        catalog = build_tool_catalog(visible.capabilities)
        if spec is not None:
            # Only for this trusted browser request; never carried to another turn.
            assert browser is not None
            catalog, tool = expose_browser_tool(catalog, spec, self._registration())
            browser.spec = spec if tool is not None else None
        if not catalog.tools:
            return await self._catalog_invalid(turn, catalog)
        if catalog.summary_omitted:
            # Listed by name only in their group description; not a catalog gap.
            _RUNTIME_LOGGER.info(
                "agentscope_catalog summary_omitted=%s turn=%s",
                catalog.summary_omitted, turn.turn_id,
            )
        ledger.business_tools = {tool.name: tool.capability_id for tool in catalog.tools}
        ledger.write_tools = {
            tool.name: tool.capability_id for tool in catalog.tools if tool.kind == "write"
        }
        ledger.browser_tools = {
            tool.name: tool.capability_id for tool in catalog.tools if tool.kind == "browser"
        }
        ledger.catalog_attributes = catalog.trace_attributes()
        model = deps.model_factory()
        # Once per ordinary turn; every model call of the turn sees the same items.
        knowledge = bound_generated_knowledge(
            deps.knowledge.context_items(message, visible.capabilities)
        )
        _RUNTIME_LOGGER.info(
            "agentscope_knowledge items=%s turn=%s", len(knowledge), turn.turn_id
        )
        kit = await build_turn_toolkit(
            catalog, scope=scope, deps=deps, ledger=ledger, limits=self._limits,
            model=model, prompt_version=M1_PROMPT_VERSION,
            knowledge=knowledge, knowledge_digest=self._knowledge_digest,
        )
        agent = create_bounded_agent(
            name=AGENT_NAME,
            system_prompt=M1_SYSTEM_PROMPT + (
                M1_REPAIR_PROMPT
                if browser is not None
                and browser.spec is not None
                and self._registration().effect == "fill_stop"
                else ""
            ),
            model=model,
            toolkit=kit.toolkit,
            state=state,
            limits=self._limits,
            extra_middlewares=[kit.middleware],
            classify_tool_error=kit.middleware.classify_tool_error,
        )
        progress.agent = agent
        # A reply parked by an earlier proposal is interrupted first; the
        # future is registered before the wait so release always waits for it.
        interrupt = start_parked_interrupt(agent, state)
        if interrupt is not None:
            ledger.interrupt = interrupt
            await asyncio.shield(interrupt)
        # Groups are kind-homogeneous; only read groups carry over a turn.
        progress.snapshot = await prepare_turn_state(
            state,
            read_groups={tool.group for tool in catalog.tools if tool.kind == "read"},
            model=model,
        )
        return catalog, visible.capabilities, agent

    # --- summarization -------------------------------------------------------

    def _summarize(
        self,
        turn: _Turn,
        ledger: TurnLedger,
        catalog: ToolCatalog | None,
        visible: tuple[CapabilitySpec, ...] | None,
        final: Msg | None,
    ) -> _Outcome:
        """Apply R0-R6 (R4c after R4) in order; the first matching rule decides the turn."""

        terminal = ledger.terminal
        if isinstance(terminal, BrowserSettled):
            # The coordinator's card or admission, never model text (§3.5 C').
            return _Outcome("browser_settled", terminal.envelope, terminal.close)
        if isinstance(terminal, Proposal) and ledger.handoff:  # R0
            # The published card is the builder envelope; never model text.
            item = ledger.executions[terminal.execution_index]
            return _Outcome("r0_proposal", item.envelope, None)
        if ledger.staged is not None:
            # Staged but never published (CAS lost, timeout, failed post-check):
            # abandoned; the waiting marks may already exist.
            failed = self._close(turn, None, "failed", "internal_error")
            return _Outcome(
                "r0_abandoned",
                self._build_failed(turn, *_intent_failure_message("schema_invalid")),
                TaskClose(
                    failed.task_id, failed.trace_id, None, failed.execution,
                    raw_execution=failed.raw_execution,
                ),
                rollback=True,
            )
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
            envelope = self._build_failed(turn, *_intent_failure_message(_model_reason(ledger)))
            error_code: ErrorCode = "internal_error"
            if turn.browser is not None:
                # Browser-request mapping of the legacy path (M2 §3.6).
                error_code, diagnostic = _browser_model_failure(ledger)
                envelope = envelope.model_copy(
                    update={"data": {"error_code": error_code, **diagnostic.attributes()}}
                )
            return _Outcome(
                "r5_model_error", envelope,
                self._close(turn, None, "failed", error_code),
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


def _knowledge_items_digest(knowledge: BasicKnowledge) -> str:
    """Canonical digest of the whole static item set, taken once at build time."""

    items = [
        {"category": item.category, "keywords": list(item.keywords), "content": item.content}
        for item in knowledge.static_items
    ]
    return hashlib.sha256(canonical_json(items).encode("utf-8")).hexdigest()


def _verified_session(principal: Principal) -> AuthenticatedSessionContext | None:
    """The request's verified login when it belongs to ``principal`` and is unexpired."""

    context = authenticated_session.get()
    if (
        context is None
        or context.principal != principal
        or context.expires_at <= datetime.now(UTC)
    ):
        return None
    return context


def _owner(principal: Principal, session_id: str) -> ConfirmationOwner:
    # Account identity plus bound session; never the login token fingerprint.
    return ConfirmationOwner(
        tenant_id=principal.org_ctx.tenant_id,
        session_id=session_id,
        ai_user_id=principal.ai_user_id,
    )


def _memory_key(scope: ReadToolScope) -> SessionMemoryKey:
    return SessionMemoryKey(
        tenant_id=scope.tenant_id, session_id=scope.session_id, ai_user_id=scope.ai_user_id
    )


def _browser_model_failure(ledger: TurnLedger) -> tuple[ErrorCode, BrowserGatewayDiagnostic]:
    """Legacy browser-scope mapping of a model failure; model_not_available is a mismatch."""

    code = ledger.model_error
    if _model_reason(ledger) != "provider_error":
        return "adapter_payload_invalid", BrowserGatewayDiagnostic("input", "input_invalid")
    if code in {"timeout", "turn_timeout"}:
        return "adapter_timeout", BrowserGatewayDiagnostic("llm", "llm_timeout")
    if code == "model_not_available":
        return "adapter_error", BrowserGatewayDiagnostic("llm", "model_mismatch")
    return "adapter_error", BrowserGatewayDiagnostic("llm", "llm_unavailable")


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


__all__ = (
    "AGENT_NAME",
    "M1_PROMPT_VERSION",
    "M1_REPAIR_PROMPT",
    "M1_SYSTEM_PROMPT",
    "AgentScopeRuntime",
)
