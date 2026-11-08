"""Toolkit, control tools and middleware of one M1 turn.

Business tools live in read and write groups the model activates with
``reset_tools``; write tools and the registered browser tool are external
calls the runtime handles outside the framework, never executed here. A D
collection reply uses no toolkit (one JSON extraction). The basic group holds only the
control tools ``report_unsupported`` and ``ask_user``, each of which ends the
turn when accepted. The middleware records what
every model call actually exposed, ties each tool call to that call and ends
the turn without another provider call once a terminal outcome is recorded.
Only public AgentScope interfaces are used; ``builtin_meta_tool`` is replaced
after construction and checked (uv.lock pins the version; re-check on upgrade).

When the turn has a trusted client request id and the job service is configured, a
fixed ``export_oa_pending_report`` control tool accepts the requester's OA pending report
as a background job on the verified bound session; the model sees only fixed text.
A turn that also carries chat attachments gets the fixed ``propose_sheet_fill`` tool: a
valid mapping proposal ends the turn on a confirmation card (no job until the user
confirms); an invalid one returns a fixed, recoverable text (at most three attempts).

An MCP authorization placeholder has no executor. A model response that calls
one is replaced, before any of its tool calls is dispatched, by the terminal
answer: the whole response is dropped and the turn ends on the authorization
outcome, so no business call of that response runs.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

from agentscope.message import TextBlock, ToolCallBlock, ToolResultState
from agentscope.middleware import MiddlewareBase
from agentscope.model import ChatResponse, FinishedReason
from agentscope.permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from agentscope.state import AgentState
from agentscope.tool import (
    FunctionTool,
    RegisteredTool,
    ResetTools,
    ToolBase,
    ToolChunk,
    ToolGroup,
    Toolkit,
)
from jinja2 import Template
from pydantic import ValidationError

from app.background_jobs import (
    OA_PENDING_REPORT_KIND,
    OA_PENDING_REPORT_PARAMS,
    BackgroundJobService,
    oa_pending_report_key,
)
from app.infra.llm.agentscope_chat import (
    ToolErrorKind,
    TurnLimits,
    current_turn_id,
    halt_tool_use,
)
from app.infra.orchestration.agentscope_read_tools import (
    AgentScopeRuntimeDependencies,
    ReadToolScope,
    ToolExecutor,
    session_matches,
)
from app.infra.orchestration.agentscope_runtime.catalog import GROUP_RULES, ToolCatalog
from app.infra.orchestration.agentscope_runtime.ledger import (
    AuthorizationRequired,
    CallSnapshot,
    Clarify,
    ReportJobSettled,
    SheetFillProposed,
    TurnLedger,
    Unsupported,
    current_tool_call_id,
)
from app.knowledge.bounded import KNOWLEDGE_SYSTEM_PROMPT
from app.knowledge.capability_selection import canonical_json
from app.ports.auth import authenticated_session
from app.ports.background_jobs import BackgroundJobError
from app.ports.runtime import chat_attachment_context, chat_client_request_id
from app.sheet_fill import (
    PROPOSAL_INPUT_SCHEMA,
    SheetFillAdmission,
    SheetFillProposal,
    SheetFillRejected,
)
from app.version_binding import prompt_version_binding

if TYPE_CHECKING:
    from agentscope.agent import Agent
    from agentscope.model import ChatModelBase

AGENT_PROMPT_RESOURCE_ID = "runtime.agentscope_agent"
REPORT_UNSUPPORTED = "report_unsupported"
ASK_USER = "ask_user"
EXPORT_OA_PENDING_REPORT = "export_oa_pending_report"
PROPOSE_SHEET_FILL = "propose_sheet_fill"
CONTROL_TOOLS = frozenset(
    {REPORT_UNSUPPORTED, ASK_USER, EXPORT_OA_PENDING_REPORT, PROPOSE_SHEET_FILL}
)
SHEET_FILL_MAX_ATTEMPTS = 3
RESET_TOOLS = "reset_tools"
# Reset results name the activated groups only; usage rules reach the model
# through the system prompt while their group is active.
M1_TEMPLATE = (
    "{% if groups | length == 0 %}已关闭全部工具组。"
    "{% else %}已激活：{{ groups | map(attribute='name') | join('、') }}{% endif %}"
)
# Fixed answer of the synthetic final model response once the turn is terminal;
# it is not model text and never reaches the user. A Clarify terminal answers
# with its question instead, so the session history ends with that question.
TERMINAL_TEXT = "本轮已结束。"
DISCOVERY_CODES = frozenset(
    {
        "tool_not_exposed",
        "reset_unknown_group",
        "reset_too_many_groups",
        "reset_budget_exceeded",
        "reset_limit_reached",
        "reset_invalid_input",
        "control_call_conflict",
        "duplicate_success_call",
        "write_proposal_conflict",
        # A rejected sheet fill proposal: the model may correct it and call again.
        "sheet_fill_proposal_rejected",
    }
)
_RESET_COUNT_KEY = "eternalai_reset_count"
_ALLOW = PermissionDecision(behavior=PermissionBehavior.ALLOW, message="approved read")


def _log_d_input_shape(call: ToolCallBlock) -> None:
    """Fixed structural labels only, before SDK validation; no values or unknown keys."""
    try:
        arguments = json.loads(call.input)
    except ValueError:
        shape = "invalid_json"
    else:
        if not isinstance(arguments, dict):
            shape = "not_object"
        else:
            parts = []
            for field in ("category", "position", "description"):
                value = arguments.get(field)
                kind = (
                    "missing" if field not in arguments else
                    "non_string" if not isinstance(value, str) else
                    "empty" if not value.strip() else
                    "overlong" if len(value) > (100 if field == "category" else 500) else
                    "string"
                )
                parts.append(f"{field}:{kind}")
            parts.append(f"extra:{bool(set(arguments) - {'category', 'position', 'description'})}")
            shape = ",".join(parts)
    logging.getLogger("uvicorn.error.agentscope_runtime").info(
        "agentscope_d_input shape=%s turn=%s", shape, current_turn_id()
    )


@dataclass(slots=True)
class TurnToolkit:
    toolkit: Toolkit
    middleware: M1Middleware
    # Group name of every business tool name.
    group_of: Mapping[str, str]


async def build_turn_toolkit(
    catalog: ToolCatalog,
    *,
    scope: ReadToolScope,
    deps: AgentScopeRuntimeDependencies,
    ledger: TurnLedger,
    limits: TurnLimits,
    model: ChatModelBase,
    prompt_version: str,
    knowledge: Sequence[str] = (),
    knowledge_digest: str | None = None,
    authorization_recheck: Callable[[str], bool] | None = None,
) -> TurnToolkit:
    """Register this turn's catalog as tool groups plus the two control tools.

    ``authorization_recheck`` re-checks, by capability id, that a selected
    placeholder is still hidden only by the missing authorization.
    """

    groups: list[ToolGroup] = []
    for group in catalog.groups:
        tools: list[ToolBase] = []
        for tool in catalog.tools:
            if tool.group != group.name:
                continue
            if tool.kind == "write":
                tools.append(ProposeWrite(tool.name, tool.description, tool.input_schema))
                continue
            if tool.kind == "browser":
                tools.append(BrowserTool(tool.name, tool.description, tool.input_schema))
                continue
            if tool.kind == "authorization":
                # Never a ToolExecutor: selecting it only ends the turn.
                tools.append(
                    AuthorizationPlaceholder(
                        ledger, tool.name, tool.capability_id, tool.description,
                        tool.input_schema,
                    )
                )
                continue
            executor = ToolExecutor(scope, deps, ledger, tool.binding, tool.name, limits)
            tools.append(
                FunctionTool(
                    executor.run,
                    name=tool.name,
                    description=tool.description,
                    input_schema=tool.input_schema,
                    is_concurrency_safe=False,
                    is_read_only=True,
                    is_state_injected=True,
                    permission=_ALLOW,
                )
            )
        groups.append(
            ToolGroup(
                name=group.name,
                description=group.description,
                instructions=group.instructions,
                tools=tools,
            )
        )
    controls: list[ToolBase] = [ReportUnsupported(ledger), AskUser(ledger)]
    client_request_id = chat_client_request_id.get()
    if (
        deps.report_jobs is not None
        and scope.im_source is None
        and client_request_id is not None
    ):
        controls.append(
            ExportOAPendingReport(ledger, scope, deps.report_jobs, client_request_id)
        )
    attachments = chat_attachment_context.get()
    if (
        deps.sheet_fill is not None
        and scope.im_source is None
        and client_request_id is not None
        and attachments
    ):
        controls.append(
            ProposeSheetFill(
                ledger, scope, deps.sheet_fill,
                frozenset(attachment.artifact_id for attachment in attachments),
            )
        )
    toolkit = Toolkit(
        tools=controls,
        tool_groups=groups,
        meta_tool_response_template=M1_TEMPLATE,
    )
    reset = M1ResetTools(
        groups=toolkit.tool_groups,
        response_template=M1_TEMPLATE,
        toolkit=toolkit,
        ledger=ledger,
        limits=limits,
        model=model,
    )
    toolkit.builtin_meta_tool = RegisteredTool(tool=reset)
    if catalog.groups and await toolkit.get_tool(RESET_TOOLS) is not reset:
        raise RuntimeError("reset_tools_binding_invalid")
    group_of = {tool.name: tool.group for tool in catalog.tools}
    middleware = M1Middleware(
        catalog,
        ledger,
        group_of=group_of,
        model_name=model.model,
        prompt_version=prompt_version,
        knowledge=knowledge,
        knowledge_digest=knowledge_digest,
        authorization_recheck=authorization_recheck,
    )
    return TurnToolkit(toolkit=toolkit, middleware=middleware, group_of=group_of)


def _error(text: str, code: str) -> ToolChunk:
    return ToolChunk(
        content=[TextBlock(text=text)],
        state=ToolResultState.ERROR,
        metadata={"error_code": code},
    )


class M1ResetTools(ResetTools):
    """reset_tools with sequential execution and checks before any state change."""

    is_concurrency_safe: bool = False

    def __init__(
        self,
        *,
        groups: list[ToolGroup],
        response_template: str,
        toolkit: Toolkit,
        ledger: TurnLedger,
        limits: TurnLimits,
        model: ChatModelBase,
    ) -> None:
        super().__init__(groups=groups, response_template=response_template)
        self._toolkit = toolkit
        self._ledger = ledger
        self._limits = limits
        self._model = model

    async def call(self, _agent_state: AgentState, **kwargs: Any) -> ToolChunk:
        read_groups = {group.name for group in self.groups if group.name != "basic"}
        async with self._ledger.reset_lock:
            if any(not isinstance(value, bool) for value in kwargs.values()):
                return _error("参数必须是布尔值。", "reset_invalid_input")
            if any(key not in read_groups for key in kwargs):
                return _error("存在未知的工具组。", "reset_unknown_group")
            target = [key for key, value in kwargs.items() if value]
            if len(target) > self._limits.max_groups_per_reset:
                return _error(
                    f"一次最多激活 {self._limits.max_groups_per_reset} 个工具组。",
                    "reset_too_many_groups",
                )
            reply_id = _agent_state.reply_id
            record = _agent_state.middle_context.get(_RESET_COUNT_KEY)
            count = record.get(reply_id, 0) if isinstance(record, dict) else 0
            if count >= self._limits.max_resets:
                return _error("本轮切换工具组的次数已用完。", "reset_limit_reached")
            schemas = await self._toolkit.get_tool_schemas(target)
            if await self._model.count_tokens([], schemas) > self._limits.max_tool_tokens:
                return _error("所选工具组过大，请少选几组。", "reset_budget_exceeded")
            _agent_state.middle_context[_RESET_COUNT_KEY] = {reply_id: count + 1}
            _agent_state.tool_context.activated_groups.clear()
            _agent_state.tool_context.activated_groups.extend(target)
        activated = [group for group in self.groups if group.name in target]
        return ToolChunk(
            content=[TextBlock(text=Template(self.response_template).render(groups=activated))],
            state=ToolResultState.SUCCESS,
        )


def _control_conflict(ledger: TurnLedger, name: str) -> bool:
    """Whether a control call conflicts with the turn or with its own response.

    It conflicts once the turn has a terminal or a failure, or when the
    response that issued it also called a business tool or another control
    tool (a second call of the same control tool included). An unknown call
    id counts as a conflict.
    """

    call_id = current_tool_call_id.get()
    index = ledger.call_of.get(call_id) if call_id is not None else None
    if index is None or ledger.terminal is not None or ledger.failures:
        return True
    siblings = list(ledger.response_tools.get(index, ()))
    if name in siblings:
        siblings.remove(name)
    return any(item in ledger.business_tools or item in CONTROL_TOOLS for item in siblings)


class ProposeWrite(ToolBase):
    """External write proposal: the framework parks the call and never runs it."""

    is_external_tool: bool = True
    is_read_only: bool = False
    is_concurrency_safe: bool = False

    def __init__(self, name: str, description: str, input_schema: dict[str, Any]) -> None:
        super().__init__()
        self.name = name
        self.description = description
        self.input_schema = input_schema

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="external proposal; business confirmation enforced by HumanGate",
        )


class BrowserTool(ToolBase):
    """External browser call: parked by the framework, checked and run by the runtime."""

    is_external_tool: bool = True
    is_read_only: bool = False
    is_concurrency_safe: bool = False

    def __init__(self, name: str, description: str, input_schema: dict[str, Any]) -> None:
        super().__init__()
        self.name = name
        self.description = description
        self.input_schema = input_schema

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="external browser call; admission enforced by the Gateway",
        )


class _ControlTool(ToolBase):
    """Code-owned control tool without side effects: no task, no budget."""

    is_concurrency_safe: bool = False
    is_read_only: bool = True
    is_state_injected: bool = True

    def __init__(self, ledger: TurnLedger) -> None:
        super().__init__()
        self._ledger = ledger

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW, message="control tool without side effects"
        )


class AuthorizationPlaceholder(_ControlTool):
    """MCP capability the current user has not authorized; selected, never run.

    The middleware ends the turn before this is dispatched. Reaching ``call``
    means that interception did not happen: the turn still ends, on the
    denial path, and nothing is executed here.
    """

    def __init__(
        self,
        ledger: TurnLedger,
        name: str,
        capability_id: str,
        description: str,
        input_schema: dict[str, Any],
    ) -> None:
        super().__init__(ledger)
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self._capability_id = capability_id

    async def call(self, _agent_state: AgentState, **kwargs: Any) -> ToolChunk:
        if self._ledger.terminal is None:
            self._ledger.terminal = AuthorizationRequired(self._capability_id, False)
        halt_tool_use(_agent_state, "turn_terminal")
        return _error("需要先完成业务系统授权。", "mcp_authorization_required")


class ReportUnsupported(_ControlTool):
    """Control tool: no visible group can handle this business request."""

    name: str = REPORT_UNSUPPORTED
    description: str = (
        "确认所有可见工具组都不能处理用户的业务请求时调用，表示当前不支持该请求。"
        "闲聊、解释或缺少参数时不要调用；不要和业务工具或 ask_user 同时调用。"
        "身份替换请求不要调用本工具。"
    )
    input_schema: dict[str, Any] = {  # fixed, code-owned schema
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    async def call(self, _agent_state: AgentState, **kwargs: Any) -> ToolChunk:
        # No task, no execution budget, no catalog statistics.
        ledger = self._ledger
        if ledger.executions or _control_conflict(ledger, self.name):
            # Never overrides a recorded result; the model may answer instead.
            return _error("已有业务调用结果，不能再报告不支持。", "control_call_conflict")
        ledger.terminal = Unsupported()
        halt_tool_use(_agent_state, "turn_terminal")
        return ToolChunk(
            content=[TextBlock(text="已记录：当前不支持该请求。")],
            state=ToolResultState.SUCCESS,
        )


class AskUser(_ControlTool):
    """Control tool: end the turn with one explicit question to the user."""

    name: str = ASK_USER
    description: str = (
        "缺少完成请求所必需、对话中也没有有效值的信息时调用：工具必填参数缺失"
        "（用户明确表示还没定、没提供或留空的都算缺失）、需要在几个对象中选定一个、范围不明确。"
        "先对照要调用的工具的参数说明确认缺哪些必填项，把问题写进 question，一次问全，"
        "不要分多次问；不要问可选参数、无关字段或对话中已有的信息；"
        "已有候选对象时列出候选请用户选择。调用后本轮结束等待回答。"
        "没有任何工具能处理的请求用 report_unsupported，不要用本工具询问其他需求；"
        "一般回答、解释、闲聊和拒绝不合规请求都直接用文字，不要调用；"
        "不要和业务工具或 report_unsupported 同时调用。身份替换请求不要调用本工具。"
    )
    input_schema: dict[str, Any] = {  # fixed, code-owned schema
        "type": "object",
        "properties": {"question": {"type": "string", "minLength": 1, "maxLength": 200}},
        "required": ["question"],
        "additionalProperties": False,
    }

    async def call(self, _agent_state: AgentState, **kwargs: Any) -> ToolChunk:
        # Unlike report_unsupported, allowed after completed executions
        # (e.g. the user has to pick one of several results).
        ledger = self._ledger
        if _control_conflict(ledger, self.name):
            return _error("当前不能向用户提问，请直接用文字回复。", "control_call_conflict")
        question = str(kwargs.get("question", "")).strip()
        if not question:
            # The SDK checks minLength before stripping: a blank question is a
            # rejected argument (pre-exec failure), never an empty Clarify.
            call_id = current_tool_call_id.get() or "none"
            ledger.record_pre_exec(call_id, self.name, "tool_input_rejected", "schema_invalid")
            return _error("question 不能为空。", "tool_input_rejected")
        ledger.terminal = Clarify(question)
        halt_tool_use(_agent_state, "turn_terminal")
        return ToolChunk(
            content=[TextBlock(text="已向用户提问，本轮结束。")],
            state=ToolResultState.SUCCESS,
        )


_REPORT_LOGGER = logging.getLogger("uvicorn.error.background_jobs")
_UNKNOWN_OUTCOME = "background_job_accept_outcome_unknown"


async def submit_oa_pending_report(
    scope: ReadToolScope, service: BackgroundJobService, client_request_id: str
) -> ReportJobSettled:
    """Accept the report on the verified bound session; one same-key retry if unknown."""

    session = authenticated_session.get()
    if session is None or not session_matches(scope):
        return ReportJobSettled("refused", "session_invalid")
    principal = session.principal
    key = oa_pending_report_key(principal, scope.session_id, client_request_id)
    for attempt in (1, 2):
        try:
            accepted = await service.submit(
                principal,
                kind=OA_PENDING_REPORT_KIND,
                params=OA_PENDING_REPORT_PARAMS,
                idempotency_key=key,
                session_id=scope.session_id,
            )
        except BackgroundJobError as error:
            if error.code == _UNKNOWN_OUTCOME and attempt == 1:
                continue
            _REPORT_LOGGER.info(
                "report_job stage=failed job=- rows=- elapsed_ms=- code=%s", error.code
            )
            if attempt == 2:
                # The first attempt's outcome is still unknown; a later failure
                # cannot prove it was never committed.
                return ReportJobSettled("unconfirmed", _UNKNOWN_OUTCOME)
            return ReportJobSettled("refused", error.code)
        job_id = str(accepted.job.job_id)
        _REPORT_LOGGER.info(
            "report_job stage=accepted job=%s rows=- elapsed_ms=- code=%s",
            job_id, "deduplicated" if accepted.deduplicated else "accepted",
        )
        return ReportJobSettled("accepted", "accepted", job_id)
    raise AssertionError("unreachable")


class ExportOAPendingReport(_ControlTool):
    """Fixed tool: queue the requester's OA pending report; the turn ends on its outcome."""

    name: str = EXPORT_OA_PENDING_REPORT
    description: str = (
        "用户要求把本人的 OA 待办导出、生成或下载为 Excel、表格或报表文件时调用。"
        "系统会在后台读取本人全部 OA 待办并生成文件，完成后在卡片中提供下载。"
        "直接调用即可，不需要先激活工具组或查询待办；不要和其他工具同时调用；调用后本轮结束。"
        "只是查看、询问或统计待办而没有要文件时不要调用。"
    )
    input_schema: dict[str, Any] = {  # fixed, code-owned schema
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    def __init__(
        self,
        ledger: TurnLedger,
        scope: ReadToolScope,
        service: BackgroundJobService,
        client_request_id: str,
    ) -> None:
        super().__init__(ledger)
        self._scope = scope
        self._service = service
        self._client_request_id = client_request_id

    async def call(self, _agent_state: AgentState, **kwargs: Any) -> ToolChunk:
        ledger = self._ledger
        if ledger.executions or _control_conflict(ledger, self.name):
            return _error("导出报表要单独调用，不能和其他工具一起用。", "control_call_conflict")
        # Interrupted mid-submit (turn deadline): the commit may have landed, so the
        # turn settles as unconfirmed and the original request can recover the job.
        ledger.terminal = ReportJobSettled("unconfirmed", "submit_interrupted")
        ledger.terminal = await submit_oa_pending_report(
            self._scope, self._service, self._client_request_id
        )
        halt_tool_use(_agent_state, "turn_terminal")
        return ToolChunk(
            content=[TextBlock(text="报表请求已处理，本轮结束。")],
            state=ToolResultState.SUCCESS,
        )


_PROPOSAL_FORMAT_TEXT = "提案参数格式不正确，请按工具 schema 重新提交。"
_ATTACHMENT_TEXT = "attachment_id 不是本轮附件，请使用 untrusted_attachments 里的 attachment_id。"
_SESSION_TEXT = "登录状态已变化，本轮不能提交填表提案。"


class ProposeSheetFill(_ControlTool):
    """Fixed tool: propose mapping this turn's xlsx attachment onto approved fields.

    A valid proposal becomes the plan of a confirmation card and ends the turn; the model
    never supplies cell values, and nothing is queued until the user confirms the card.
    """

    name: str = PROPOSE_SHEET_FILL
    description: str = (
        "用户要求把本轮上传的 Excel 表格填好、补全或回填时调用，提交“列 → 字段”的映射提案。"
        "untrusted_attachments 给出 attachment_id、工作表名、行数和第 1 行表头"
        "（列字母 → 列名，第 1 行固定是表头）。"
        "可用来源：oa_pending（本人 OA 待办，key_column 为待办标题所在列；可填 status 状态、"
        "received_at 接收时间、created_at 创建时间、workflow_type_id 流程类型编号、"
        "todo_id 待办编号）；"
        "document（固定文档问答，key_column 为问题所在列；可填 quote 文档原文）。"
        "每个工作表最多一个映射，fills 的列必须是表头非空的空白待填列。"
        "first_row/last_row 可省略（默认第 2 行到最后一行）。"
        "系统校验通过后会给用户一张确认卡，用户确认后才在后台填写，只填空白格；"
        "不要和其他工具同时调用；校验未通过会返回原因，可修正后重试。"
    )
    input_schema: dict[str, Any] = PROPOSAL_INPUT_SCHEMA

    def __init__(
        self,
        ledger: TurnLedger,
        scope: ReadToolScope,
        admission: SheetFillAdmission,
        attachment_ids: frozenset[UUID],
    ) -> None:
        super().__init__(ledger)
        self._scope = scope
        self._admission = admission
        self._attachment_ids = attachment_ids
        self._attempts = 0

    async def call(self, _agent_state: AgentState, **kwargs: Any) -> ToolChunk:
        ledger = self._ledger
        if ledger.executions or _control_conflict(ledger, self.name):
            return _error("填表提案要单独调用，不能和其他工具一起用。", "control_call_conflict")
        self._attempts += 1
        if self._attempts > SHEET_FILL_MAX_ATTEMPTS:
            return _error("本轮填表提案次数已用完，请让用户补充说明后重试。",
                          "sheet_fill_attempts_exhausted")
        session = authenticated_session.get()
        if session is None or not session_matches(self._scope):
            return _error(_SESSION_TEXT, "session_invalid")
        try:
            proposal = SheetFillProposal.model_validate(kwargs)
        except ValidationError:
            return self._rejected("sheet_fill_proposal_invalid", _PROPOSAL_FORMAT_TEXT)
        if proposal.attachment_id not in self._attachment_ids:
            return self._rejected("sheet_fill_proposal_invalid", _ATTACHMENT_TEXT)
        try:
            plan = await self._admission.propose(
                session.principal, self._scope.session_id, proposal
            )
        except SheetFillRejected as rejected:
            return self._rejected(rejected.code, rejected.message)
        _REPORT_LOGGER.info(
            "sheet_fill stage=proposed job=- rows=%s filled=- marked=- elapsed_ms=- code=ok",
            sum(mapping.last_row - mapping.first_row + 1 for mapping in plan.mappings),
        )
        ledger.terminal = SheetFillProposed(plan.model_dump(mode="json"))
        halt_tool_use(_agent_state, "turn_terminal")
        return ToolChunk(
            content=[TextBlock(text="填表提案已生成确认卡，本轮结束。")],
            state=ToolResultState.SUCCESS,
        )

    def _rejected(self, code: str, message: str) -> ToolChunk:
        _REPORT_LOGGER.info(
            "sheet_fill stage=rejected job=- rows=- filled=- marked=- elapsed_ms=- code=%s",
            code,
        )
        return _error(message, "sheet_fill_proposal_rejected")


class M1Middleware(MiddlewareBase):
    """Outermost layer: terminal short-circuit, exposure snapshots, call ids."""

    def __init__(
        self,
        catalog: ToolCatalog,
        ledger: TurnLedger,
        *,
        group_of: Mapping[str, str],
        model_name: str,
        prompt_version: str,
        knowledge: Sequence[str] = (),
        knowledge_digest: str | None = None,
        authorization_recheck: Callable[[str], bool] | None = None,
    ) -> None:
        self._catalog = catalog
        self._authorization_recheck = authorization_recheck
        # Bounded once per turn by the runtime; never part of AgentState.
        self._knowledge = tuple(knowledge)
        self._knowledge_digest = knowledge_digest
        self._ledger = ledger
        self._group_of = group_of
        self._model_name = model_name
        self._prompt_version = prompt_version
        self._fingerprints = {tool.name: tool.binding.fingerprint for tool in catalog.tools}
        self._call_index = 0

    async def on_system_prompt(self, agent: Agent, current_prompt: str) -> str:
        active = agent.state.tool_context.activated_groups
        texts = [
            f'<group name="{group.name}">{group.instructions}</group>'
            for group in self._catalog.groups
            if group.name in active and group.instructions
        ]
        prompt = current_prompt
        if texts:
            block = "\n".join(texts)
            prompt = f"{prompt}\n<tool-instructions>\n{block}\n</tool-instructions>"
        if self._knowledge:
            payload = json.dumps(
                {"semantic_system_knowledge": list(self._knowledge)},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            prompt = f"{prompt}\n<knowledge>\n{KNOWLEDGE_SYSTEM_PROMPT}\n{payload}\n</knowledge>"
        return prompt

    async def on_model_call(
        self,
        agent: Agent,
        input_kwargs: dict[str, Any],
        next_handler: Callable[..., Awaitable[ChatResponse | AsyncGenerator[ChatResponse, None]]],
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        ledger = self._ledger
        if ledger.terminal is not None:
            # Before every guard check, including the SDK's forced summary
            # call at max_iters: no provider call, no budget, no snapshot.
            terminal = ledger.terminal
            text = terminal.question if isinstance(terminal, Clarify) else TERMINAL_TEXT
            return ChatResponse(
                content=[TextBlock(text=text)],
                is_last=True,
                finished_reason=FinishedReason.COMPLETED,
            )
        self._call_index += 1
        index = self._call_index
        tools: list[dict[str, Any]] = input_kwargs.get("tools") or []
        digests: dict[str, str] = {}
        for tool in tools:
            function = tool.get("function")
            name = function.get("name") if isinstance(function, dict) else None
            if isinstance(name, str):
                digests[name] = hashlib.sha256(canonical_json(tool).encode("utf-8")).hexdigest()
        messages = input_kwargs["messages"]
        system_prompt = (messages[0].get_text_content() or "") if messages else ""
        binding = prompt_version_binding(
            resource_id=AGENT_PROMPT_RESOURCE_ID,
            version=self._prompt_version,
            model=self._model_name,
            prompts=(system_prompt,),
            response_schema={
                "tools": [[name, digest] for name, digest in digests.items()],
                "group_rules": GROUP_RULES,
                "knowledge_items_digest": self._knowledge_digest,
            },
        )
        ledger.calls[index] = CallSnapshot(
            call_index=index,
            tool_digests=digests,
            capability_fingerprints={
                name: self._fingerprints[name] for name in digests if name in self._fingerprints
            },
            binding=binding,
        )
        response = await next_handler(**input_kwargs)
        if isinstance(response, ChatResponse):
            names: list[str] = []
            for block in response.content:
                if isinstance(block, ToolCallBlock):
                    ledger.call_of[block.id] = index
                    names.append(block.name)
                    if (
                        block.name == "browser__form__fill_stop"
                        and block.name in ledger.browser_tools
                    ):
                        _log_d_input_shape(block)
            ledger.response_tools[index] = tuple(names)
            selected = next((name for name in names if name in ledger.authorization_tools), None)
            if selected is not None:
                # Whole response: no call of it, business or control, is dispatched.
                capability_id = ledger.authorization_tools[selected]
                recheck = self._authorization_recheck
                passed = recheck is not None and recheck(capability_id)
                ledger.terminal = AuthorizationRequired(capability_id, passed)
                logging.getLogger("uvicorn.error.agentscope_runtime").info(
                    "agentscope_mcp_authorization recheck=%s calls=%s turn=%s",
                    int(passed), len(names), current_turn_id(),
                )
                return ChatResponse(
                    content=[TextBlock(text=TERMINAL_TEXT)],
                    is_last=True,
                    id=response.id,
                    usage=response.usage,
                    finished_reason=FinishedReason.COMPLETED,
                )
        return response

    async def on_acting(
        self,
        agent: Agent,
        input_kwargs: dict[str, Any],
        next_handler: Callable[..., AsyncGenerator[Any, None]],
    ) -> AsyncGenerator[Any, None]:
        # Tools run sequentially inside this iteration, in the same task.
        previous = current_tool_call_id.get()
        current_tool_call_id.set(input_kwargs["tool_call"].id)
        try:
            async for item in next_handler(**input_kwargs):
                yield item
        finally:
            current_tool_call_id.set(previous)

    def classify_tool_error(
        self,
        tool_call_id: str,
        name: str,
        metadata: Mapping[str, Any],
        state: AgentState,
    ) -> ToolErrorKind:
        """Split ERROR results into discovery errors and business errors."""

        ledger = self._ledger
        code = metadata.get("error_code")
        if code in DISCOVERY_CODES or name == RESET_TOOLS:
            return "discovery"
        if code is not None:
            # Executor or Gateway outcome; the executor already recorded it.
            return "business"
        # No metadata: the framework refused the call before the tool ran.
        if name not in ledger.business_tools and name not in CONTROL_TOOLS:
            return "discovery"
        if name in ledger.business_tools:
            snapshot = ledger.snapshot_for(tool_call_id)
            if (
                snapshot is None
                or name not in snapshot.tool_digests
                or self._group_of.get(name) not in state.tool_context.activated_groups
            ):
                return "discovery"
        # An exposed tool whose arguments the SDK rejected.
        logging.getLogger("uvicorn.error.agentscope_runtime").info(
            "agentscope_write_rejected code=schema_invalid stage=framework_validation turn=%s",
            current_turn_id(),
        )
        ledger.record_pre_exec(tool_call_id, name, "tool_input_rejected", "schema_invalid")
        return "business"


__all__ = (
    "AGENT_PROMPT_RESOURCE_ID",
    "ASK_USER",
    "CONTROL_TOOLS",
    "DISCOVERY_CODES",
    "EXPORT_OA_PENDING_REPORT",
    "PROPOSE_SHEET_FILL",
    "M1_TEMPLATE",
    "REPORT_UNSUPPORTED",
    "RESET_TOOLS",
    "TERMINAL_TEXT",
    "M1Middleware",
    "AskUser",
    "AuthorizationPlaceholder",
    "BrowserTool",
    "ExportOAPendingReport",
    "M1ResetTools",
    "ProposeSheetFill",
    "ProposeWrite",
    "ReportUnsupported",
    "TurnToolkit",
    "build_turn_toolkit",
    "submit_oa_pending_report",
)
