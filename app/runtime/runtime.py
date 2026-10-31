"""Minimal Runtime main chain implementation."""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from hmac import compare_digest
from threading import Lock
from time import monotonic
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from pydantic import TypeAdapter, ValidationError

from app.contracts.sdui.models import UserAction
from app.evaluator import TerminalBusinessStatus, TerminalEvaluator
from app.evaluator.overview import (
    OverviewPostconditionEvaluator,
    canonical_object,
    evaluate_required_postconditions,
)
from app.evaluator.trace import terminal_evaluation_attributes
from app.infra.sdui.response_envelope_builder import ResponseEnvelopeBuilder
from app.knowledge import BasicKnowledge
from app.knowledge.capability_selection import (
    CandidateBinding,
    CapabilityCandidateSet,
    capability_fingerprint,
    is_safe_capability_id,
)
from app.mcp.models import McpFailure
from app.memory import SessionMemory, SessionMemoryKey
from app.ports.agent_orchestration import (
    AgentOrchestrationPort,
    AgentResponseContext,
    ConfirmationPreview,
    OrchestrationContractError,
)
from app.ports.auth import Principal
from app.ports.browser_chat import BrowserChatError, BrowserOrdinarySubmissionPort
from app.ports.browser_gateway import (
    BrowserGatewayDiagnostic,
    BrowserIntentHandoffPort,
    browser_request_scope,
)
from app.ports.capability_gateway import (
    ErrorCode,
    ExecutionResult,
    ExecutionStatus,
    RequestOrgContext,
)
from app.ports.capability_registry import (
    CapabilityIntentTag,
    CapabilityRegistryPort,
    CapabilitySpec,
)
from app.ports.evaluation import BusinessVerification, EvaluationScope
from app.ports.human_gate import (
    HumanGateConflictError,
    HumanGateDecisionRecord,
    HumanGatePort,
    HumanGateRequest,
    TaskVersionBindingManifest,
    VersionBinding,
    VersionBindingMismatchError,
    build_task_version_binding_manifest,
)
from app.ports.llm_provider import LLMProviderPort
from app.ports.policy_guard import CapabilityCandidatePolicyPort
from app.ports.response_envelope import ResponseEnvelope, UIComponent
from app.ports.response_projection_contract import (
    ApprovedResponseProjectionError,
    ProjectionContractSnapshot,
)
from app.ports.runtime import (
    ConfirmationOwner,
    StagedWaiting,
    UserActionOutcome,
    WaitingProposal,
    d_form_collection_reference,
)
from app.ports.structured_output import StructuredOutputErrorCode, StructuredOutputPort
from app.ports.task_store import (
    DFormCollectionError,
    DFormCollectionSnapshot,
    DFormCollectionStorePort,
    SessionRecord,
    SessionStorePort,
    TaskEventRecord,
    TaskRecord,
    TaskStatus,
    TaskStorePort,
)
from app.ports.trace import TraceEvent, TraceEventStatus, TraceEventType, TracePort
from app.ports.workflow_engine import (
    GovernedConfirmationFailureResult,
    GovernedFinalizationError,
    GovernedTerminalResult,
    WorkflowEnginePort,
)
from app.runtime.browser_request import BrowserRequestHelpers
from app.runtime.chat_stage import _record_chat_stage
from app.runtime.d_form_collection import (
    CAPABILITY_ID as D_FORM_ID,
)
from app.runtime.d_form_collection import (
    CategoryEvidence,
    request_message,
)
from app.runtime.document_qa import DocumentQAResult, DocumentQAService, document_failure
from app.runtime.intent_router import (
    IntentFailureReason,
    IntentRouter,
    record_candidate_coverage,
)
from app.runtime.models import CapabilityRef
from app.version_binding import (
    immutable_request_digest,
    merge_version_bindings,
)


@dataclass(frozen=True)
class _PendingWorkflow:
    task_id: str
    trace_id: str
    response_id: str
    capability_id: str
    projection_snapshot: ProjectionContractSnapshot
    gate_request_id: str | None = None
    action_digest: str | None = None
    request_digest: str | None = None
    binding_manifest_digest: str | None = None
    owner: SessionMemoryKey | None = None
    expires_at: datetime | None = None
    monotonic_deadline: float | None = None
    intent_version_binding: VersionBinding | None = None


@dataclass(frozen=True)
class _StagedWaiting:
    pending_key: tuple[str, str, str]
    expected: _PendingWorkflow | None
    replacement: _PendingWorkflow | None
    response_id: str
    execution: ExecutionResult
    confirmation: ConfirmationPreview | None


_CONFIRMATION_TTL_SECONDS = 600
_CONFIRMATION_RECEIPT_LIMIT = 1024
# Per (tenant, ai_user): one account cannot exhaust the shared table for others.
_CONFIRMATION_RECEIPT_OWNER_LIMIT = 32
_ABANDON_TIMEOUT_SECONDS = 5.0
_PARTIAL_CANDIDATE_NOTICE = (
    "本次仅在相关性最高的部分能力中选择；若目标不符，请补充具体操作。",
    "This selection considered only the most relevant capabilities; specify the "
    "operation if the result does not match your goal.",
)
_CANDIDATE_FAILURE_MESSAGES: dict[ErrorCode, tuple[str, str]] = {
    "capability_candidates_low_confidence": (
        "当前请求尚未定位到足够明确的能力，请补充目标系统和具体操作。",
        "Please specify the target system and operation.",
    ),
    "capability_candidates_ambiguous": (
        "匹配到的能力过于接近，无法确定候选范围，请补充具体操作。",
        "Matching capabilities are too similar; please specify the operation.",
    ),
    "capability_candidates_over_budget": (
        "能力说明超过候选处理范围，请明确具体操作；仍失败时请联系管理员。",
        "Capability descriptions exceed the candidate budget; specify the operation "
        "or contact an administrator.",
    ),
    "capability_catalog_invalid": (
        "能力配置暂不可用，请联系管理员核对。",
        "Capability configuration is temporarily unavailable; contact an administrator.",
    ),
    "capability_candidate_out_of_scope": (
        "本次能力选择无效，请重新描述请求。",
        "The capability selection is invalid; please rephrase the request.",
    ),
    "capability_candidate_stale": (
        "能力配置已变化，请重新发起请求。",
        "Capability configuration changed; please submit the request again.",
    ),
}
_SELECTION_FAILURE_CODES: dict[str, ErrorCode] = {
    "low_confidence": "capability_candidates_low_confidence",
    "ambiguous": "capability_candidates_ambiguous",
    "over_budget": "capability_candidates_over_budget",
    "catalog_invalid": "capability_catalog_invalid",
}
_CATALOG_INVALID_SELECTION = CapabilityCandidateSet(
    outcome="catalog_invalid",
    contracts=(),
    bindings=(),
    visible_count=0,
    selected_count=0,
    omitted_count=0,
    coverage_complete=False,
    truncated_by=(),
    payload_bytes=0,
)
_CONFIRMATION_INVALIDATED_MESSAGE = (
    "此确认已失效，请重新发起。若此前已提交，请先核对业务状态，避免重复操作。"
)


@dataclass
class _ConfirmationClaim:
    tenant_id: str
    response_id: str
    pending: _PendingWorkflow | None
    state: Literal["processing", "completed", "cancelled", "confirmation_invalidated"]
    retain_until: float
    cleanup_complete: bool = False
    error_code: ErrorCode | None = None
    cleanup_in_progress: bool = False
    cleanup_reason: Literal["cancelled", "expired", "exception"] | None = None
    cleanup_governed_only: bool = False
    cleanup_governed: bool | None = None
    governed_terminal: GovernedTerminalResult | None = None
    response_trace_pending: bool = False
    completion_remembered: bool = False
    # Set once a governed card was re-keyed to the current login (M1B §3.8);
    # governed cleanup must then target this generation, not the original.
    governed_rebind: _PendingWorkflow | None = None


@dataclass
class _ConfirmationReceipt:
    """Binds one client_request_id to the first gate and message it was seen with."""

    gate_ref: str | None
    message_digest: bytes
    state: Literal["processing", "done"]
    envelope: ResponseEnvelope | None
    retain_until: float


class _ActionAlreadyClaimedError(RuntimeError):
    pass


class _ActionStaleError(RuntimeError):
    pass


@dataclass
class _DocumentRequest:
    deadline: float | None = None
    context: tuple[str, str, str, str, SessionMemoryKey] | None = None


class RuntimeImpl:
    def __init__(
        self,
        task_store: TaskStorePort,
        session_store: SessionStorePort,
        capability_registry: CapabilityRegistryPort,
        orchestration: AgentOrchestrationPort,
        trace_port: TracePort,
        llm_provider: LLMProviderPort,
        structured_output: StructuredOutputPort,
        intent_model: str,
        response_builder: ResponseEnvelopeBuilder,
        *,
        candidate_policy: CapabilityCandidatePolicyPort,
        workflow_engine: WorkflowEnginePort | None = None,
        session_memory: SessionMemory | None = None,
        semantic_knowledge: BasicKnowledge | None = None,
        evaluator: TerminalEvaluator | None = None,
        overview_evaluator: OverviewPostconditionEvaluator | None = None,
        human_gate_port: HumanGatePort | None = None,
        utc_clock: Callable[[], datetime] | None = None,
        monotonic_clock: Callable[[], float] = monotonic,
        confirmation_claim_limit: int = 4096,
        d_form_collection_store: DFormCollectionStorePort | None = None,
        d_form_category_source: Callable[
            [SessionMemoryKey, CapabilitySpec], Awaitable[CategoryEvidence | None]
        ]
        | None = None,
        document_qa: DocumentQAService | None = None,
    ) -> None:
        self._task_store = task_store
        self._d_form_collection_store = d_form_collection_store
        self._document_qa = document_qa
        self._browser = BrowserRequestHelpers(
            trace_port=trace_port,
            # Late-bound so a replaced Runtime clock governs collection expiry too.
            utc_clock=lambda: self._utc_clock(),
            d_form_collection_store=d_form_collection_store,
            d_form_category_source=d_form_category_source,
        )
        self._session_store = session_store
        self._capability_registry = capability_registry
        self._orchestration = orchestration
        self._trace_port = trace_port
        self._candidate_policy = candidate_policy
        self._semantic_knowledge = semantic_knowledge or BasicKnowledge()
        self._intent_router = IntentRouter(
            llm_provider=llm_provider,
            structured_output=structured_output,
            model=intent_model,
            semantic_knowledge=self._semantic_knowledge,
        )
        self._response_builder = response_builder
        self._workflow_engine = workflow_engine
        self._session_memory = session_memory or SessionMemory()
        self._evaluator = evaluator or TerminalEvaluator()
        self._overview_evaluator = (
            OverviewPostconditionEvaluator() if overview_evaluator is None else overview_evaluator
        )
        self._human_gate_port = human_gate_port
        self._intent_version_binding = self._intent_router.version_binding()
        self._pending_workflows: dict[tuple[str, str, str], _PendingWorkflow] = {}
        self._pending_confirmation_claim_lock = Lock()
        self._utc_clock = utc_clock or (lambda: datetime.now(UTC))
        self._monotonic_clock = monotonic_clock
        if confirmation_claim_limit < 1:
            raise ValueError("confirmation_claim_limit must be positive")
        self._confirmation_claim_limit = confirmation_claim_limit
        self._claimed_pending_confirmations: dict[
            tuple[str, str, str, str], _ConfirmationClaim
        ] = {}
        self._confirmation_references: dict[
            tuple[str, str, str, str], tuple[str, str, str, str]
        ] = {}
        self._confirmation_receipts: OrderedDict[
            tuple[str, str, str, str], _ConfirmationReceipt
        ] = OrderedDict()

    async def handle_user_message(
        self,
        channel: Literal["web", "cli", "api", "mock"],
        principal: Principal,
        session_id: str,
        message: str,
        client_capabilities: dict[str, Any],
    ) -> ResponseEnvelope:
        scope, handoff = browser_request_scope.get(), self._browser.handoff
        document_request = _DocumentRequest()
        if scope is None or handoff is None:
            try:
                return await self._handle_user_message(
                    channel,
                    principal,
                    session_id,
                    message,
                    client_capabilities,
                    document_request=document_request,
                )
            except asyncio.CancelledError:
                if document_request.context is not None:
                    await self._fail_document_request(document_request, "document_cancelled")
                raise
            except Exception:
                if document_request.context is not None:
                    return await self._fail_document_request(
                        document_request,
                        "document_internal_error",
                    )
                raise
        if not scope.matches(principal, session_id):
            raise ValueError("browser_request_context_mismatch")
        scope.attempt.message = request_message(message, d_form_collection_reference.get())
        scope.attempt.channel = channel
        settled = False
        try:
            async with asyncio.timeout(130) as request_budget:
                document_request.deadline = request_budget.when()
                async with handoff.try_request_route(scope) as acquired:
                    replay = await self._browser.lookup(
                        scope,
                        allow_collection_resume=acquired,
                    )
                    if replay is not None:
                        settled = True
                        return replay
                    if not acquired:
                        # Another app worker owns this first route. A missing
                        # receipt is uncertainty, never permission to reparse.
                        settled = True
                        return self._browser.browser_unconfirmed_response(scope, timeout=False)
                    result = await self._handle_user_message(
                        channel,
                        principal,
                        session_id,
                        message,
                        client_capabilities,
                        document_request=document_request,
                    )
            settled = True
            return result
        except TimeoutError:
            if document_request.context is not None:
                settled = True
                return await self._fail_document_request(document_request, "document_timeout")
            recovered = await self._browser.recover_browser_request(scope)
            if recovered is not None:
                return recovered
            return self._browser.browser_unconfirmed_response(scope, timeout=True)
        except asyncio.CancelledError:
            if document_request.context is not None:
                settled = True
                await self._fail_document_request(document_request, "document_cancelled")
                raise
            await self._browser.recover_browser_request(scope)
            raise
        except DFormCollectionError:
            settled = True  # A rejected reply cannot close another reply's owned Task.
            raise
        except BrowserChatError as error:
            if document_request.context is not None:
                settled = True
                return await self._fail_document_request(
                    document_request,
                    "document_internal_error",
                )
            if error.code == "request_key_conflict":
                message = "原请求标识与本次内容不一致，请使用原内容检查。"
                return ResponseEnvelope(
                    response_id=str(uuid4()),
                    task_id=scope.attempt.task_id or str(uuid4()),
                    session_id=session_id,
                    status="failed",
                    message=message,
                    fallback_text=message,
                    ui=UIComponent(component_type="none", action="none"),
                    data={
                        "error_code": "adapter_payload_invalid",
                        **BrowserGatewayDiagnostic(
                            "admission", "request_key_conflict"
                        ).attributes(),
                    },
                    trace_id=scope.attempt.trace_id or str(uuid4()),
                )
            return self._browser.browser_unconfirmed_response(scope, timeout=False)
        except Exception as error:
            if document_request.context is not None:
                settled = True
                return await self._fail_document_request(
                    document_request,
                    "document_internal_error",
                )
            try:
                exception_type = type(error).__name__
                logging.getLogger(__name__).warning(
                    "browser_fallback_diagnostic stage=ordinary_admission exception_type=%s",
                    exception_type
                    if exception_type.isascii()
                    and exception_type.isidentifier()
                    and len(exception_type) <= 64
                    else "UnknownException",
                )
            except Exception:
                pass  # Diagnostic failure must preserve the original failure path.
            recovered = await self._browser.recover_browser_request(scope)
            if recovered is not None:
                return recovered
            return self._browser.browser_unconfirmed_response(scope, timeout=False)
        finally:
            if not settled and document_request.context is None:
                await self._browser.close_intent(scope)

    def install_browser_handoff(
        self,
        handoff: BrowserIntentHandoffPort,
        submission: BrowserOrdinarySubmissionPort,
        *,
        configured_d_form_model: str | None = None,
    ) -> None:
        if self._browser.handoff is not None:
            raise ValueError("browser_handoff_already_installed")
        self._intent_router.install_browser_checks(configured_d_form_model=configured_d_form_model)
        self._browser.install(handoff, submission)

    async def _handle_user_message(
        self,
        channel: Literal["web", "cli", "api", "mock"],
        principal: Principal,
        session_id: str,
        message: str,
        client_capabilities: dict[str, Any],
        *,
        document_request: _DocumentRequest | None = None,
    ) -> ResponseEnvelope:
        ai_user_id = principal.ai_user_id
        memory_key = SessionMemoryKey(
            tenant_id=principal.org_ctx.tenant_id,
            session_id=session_id,
            ai_user_id=ai_user_id,
        )
        collection: DFormCollectionSnapshot | None = None
        reference = d_form_collection_reference.get()
        scope = browser_request_scope.get()
        if reference is not None:
            admitted = await self._browser.admit_reference(
                reference, scope, principal, session_id, channel
            )
            if isinstance(admitted, ResponseEnvelope):
                return admitted
            collection = admitted
        if reference is None:
            confirmation_envelope = await self._deterministic_confirmation(memory_key, message)
            if confirmation_envelope is not None:
                return confirmation_envelope
        else:
            await self._expire_pending_confirmations(memory_key)
        pending_key = _pending_workflow_key(memory_key.tenant_id, session_id, ai_user_id)
        pending = self._pending_workflows.get(pending_key)

        task_id = collection.record.task_id if collection is not None else str(uuid4())
        trace_id = collection.record.trace_id if collection is not None else str(uuid4())
        response_id = str(uuid4())

        existing = await self._session_store.get_session(session_id, tenant_id=memory_key.tenant_id)
        if existing is None:
            await self._session_store.create_session(
                SessionRecord(tenant_id=memory_key.tenant_id, session_id=session_id)
            )

        scope = browser_request_scope.get()
        if scope is not None and scope.matches(principal, session_id):
            scope.attempt.task_id, scope.attempt.trace_id = task_id, trace_id
        if collection is None:
            await self._task_store.create_task(
                TaskRecord(
                    task_id=task_id,
                    session_id=session_id,
                    ai_user_id=ai_user_id,
                    tenant_id=memory_key.tenant_id,
                    status="running",
                    trace_id=trace_id,
                )
            )
            await self._trace_port.start_task_trace(
                trace_id,
                task_id,
                session_id,
                tenant_id=memory_key.tenant_id,
                ai_user_id=memory_key.ai_user_id,
            )
            await self._trace_port.record_step(
                trace_id,
                task_id,
                session_id,
                tenant_id=memory_key.tenant_id,
                ai_user_id=memory_key.ai_user_id,
                event_type="task_created",
                status="ok",
            )

        # An explicit document question does not enter business intent selection.
        # Existing collection/confirmation state keeps ownership of its replies.
        document_message = message.lstrip()
        if (
            document_message.startswith("请问")
            and collection is None
            and pending is None
            and self._document_qa is not None
            and document_request is not None
            and self._document_qa.permits(
                tenant_id=memory_key.tenant_id,
                ai_user_id=memory_key.ai_user_id,
            )
        ):
            document_request.context = (
                response_id,
                task_id,
                session_id,
                trace_id,
                memory_key,
            )
            _record_chat_stage(trace_id, "document_route", "ok", reason="explicit_prefix")
            result = await self._document_qa.answer(
                question=document_message[2:].lstrip(" ：:，,\t\r\n"),
                tenant_id=memory_key.tenant_id,
                ai_user_id=memory_key.ai_user_id,
                deadline=document_request.deadline,
                trace_id=trace_id,
            )
            return await self._finish_document_qa(document_request, result)

        visible_capabilities: tuple[CapabilitySpec, ...] = ()
        active_count = 0
        if message.strip():
            visibility = await self._visible_capabilities(
                ai_user_id=ai_user_id,
                request_context=RequestOrgContext(
                    request_id=trace_id,
                    channel=channel,
                    tenant_id=memory_key.tenant_id,
                ),
            )
            if visibility is None:
                _record_chat_stage(trace_id, "catalog", "failed", reason="catalog_invalid")
                _record_chat_stage(trace_id, "model", "skipped", reason="catalog_invalid")
                return await self._finish_candidate_failure(
                    response_id,
                    task_id,
                    session_id,
                    trace_id,
                    error_code="capability_catalog_invalid",
                    reason="catalog_invalid",
                    selection=_CATALOG_INVALID_SELECTION,
                    memory_key=memory_key,
                )
            visible_capabilities, active_count = visibility
        _record_chat_stage(
            trace_id,
            "catalog",
            "ok",
            reason=("visible_present" if visible_capabilities else "visible_empty"),
        )
        category_options: tuple[str, ...] | None = None
        collection_parse: dict[str, Any] = {}
        if collection is not None:
            current = tuple(
                item
                for item in visible_capabilities
                if item.capability_id == D_FORM_ID
                and item.version == collection.record.capability_version
                and capability_fingerprint(item) == collection.record.capability_fingerprint
            )
            if len(current) != 1:
                raise DFormCollectionError("collection_version_conflict")
            category_options = await self._browser.category_options(memory_key, current[0])
            collection_parse = {
                "d_form_arguments": dict(collection.record.arguments),
                "d_form_consumed": collection.record.consumed,
                "d_form_category_options": category_options,
            }
        intent_result = await self._intent_router.parse(
            message,
            trace_metadata={
                "trace_id": trace_id,
                "task_id": task_id,
            },
            capabilities=visible_capabilities,
            memory_summaries=self._session_memory.recall(memory_key),
            **collection_parse,
        )
        _record_chat_stage(
            trace_id,
            "intent",
            "failed"
            if intent_result.failure_reason
            else "blocked"
            if intent_result.match is None
            else "ok",
            reason=intent_result.failure_reason
            or (
                "match_capability"
                if intent_result.match == "capability"
                else "match_none"
                if intent_result.match == "none"
                else intent_result.candidate_selection.outcome
                if intent_result.candidate_selection
                else "unparsed"
            ),
            capability_id=(
                intent_result.capability_ref.capability_id if intent_result.capability_ref else None
            ),
        )
        record_candidate_coverage(trace_id, visible_capabilities, intent_result)
        if (
            collection is not None
            and not collection.record.consumed
            and (intent_result.failure_reason is not None or intent_result.match != "capability")
        ):
            # An unparseable reply cannot discard already collected parameters.
            assert self._d_form_collection_store is not None and scope is not None
            return await self._browser.retain_unparsed(
                scope,
                memory_key,
                channel,
                collection,
                category_options,
            )
        selection = intent_result.candidate_selection
        if selection is not None and selection.outcome != "ready":
            if selection.outcome == "empty":
                await self._record_intent_parsed(
                    trace_id,
                    task_id,
                    session_id,
                    memory_key=memory_key,
                    status="ok",
                    attributes={**selection.trace_attributes(), "reason": "empty"},
                )
                return await self._finish_no_capability_found(
                    response_id,
                    task_id,
                    session_id,
                    trace_id,
                    reason=(
                        "no_active_capability_registered"
                        if active_count == 0
                        else "no_visible_capability"
                    ),
                    capabilities=(),
                    memory_key=memory_key,
                )
            return await self._finish_candidate_failure(
                response_id,
                task_id,
                session_id,
                trace_id,
                error_code=_SELECTION_FAILURE_CODES[selection.outcome],
                reason=selection.outcome,
                selection=selection,
                memory_key=memory_key,
            )

        capability_ref = intent_result.capability_ref
        parse_ok = intent_result.failure_reason is None and (
            intent_result.match == "none"
            or (intent_result.match == "capability" and capability_ref is not None)
        )
        selection_attributes = selection.trace_attributes() if selection is not None else {}

        if not parse_ok or selection is None:
            await self._record_intent_parsed(
                trace_id,
                task_id,
                session_id,
                memory_key=memory_key,
                status="failed",
                attributes={
                    **selection_attributes,
                    **_intent_trace_attributes(
                        None,
                        intent_result.failure_reason,
                        intent_result.structured_output_error_code,
                        intent_result.validation_error_path,
                        intent_result.validation_error_type,
                        intent_result.argument_keys,
                    ),
                },
            )
            return await self._finish_intent_failure(
                response_id,
                task_id,
                session_id,
                trace_id,
                reason=intent_result.failure_reason or "schema_invalid",
                memory_key=memory_key,
                error_code=(
                    "adapter_timeout"
                    if intent_result.provider_error_code == "timeout"
                    else "adapter_error"
                    if intent_result.failure_reason == "provider_error"
                    else "adapter_payload_invalid"
                )
                if browser_request_scope.get() is not None and self._browser.handoff is not None
                else "internal_error",
                diagnostic=(
                    BrowserGatewayDiagnostic(
                        "llm" if intent_result.failure_reason == "provider_error" else "input",
                        "llm_timeout"
                        if intent_result.provider_error_code == "timeout"
                        else "model_mismatch"
                        if intent_result.provider_error_code == "model_not_available"
                        else "llm_unavailable"
                        if intent_result.failure_reason == "provider_error"
                        else "input_invalid",
                    )
                    if browser_request_scope.get() is not None and self._browser.handoff is not None
                    else None
                ),
            )

        if intent_result.match == "none" or capability_ref is None:
            if not selection.coverage_complete:
                return await self._finish_candidate_failure(
                    response_id,
                    task_id,
                    session_id,
                    trace_id,
                    error_code="capability_candidates_low_confidence",
                    reason="low_confidence",
                    selection=selection,
                    memory_key=memory_key,
                )
            await self._record_intent_parsed(
                trace_id,
                task_id,
                session_id,
                memory_key=memory_key,
                status="ok",
                attributes={"result": "valid", "match": "none", **selection_attributes},
            )
            return await self._finish_no_capability_found(
                response_id,
                task_id,
                session_id,
                trace_id,
                reason="no_matching_capability",
                capabilities=visible_capabilities,
                memory_key=memory_key,
            )

        valid_intent_attributes = {
            **_intent_trace_attributes(capability_ref, None, None),
            **selection_attributes,
        }
        resolution = _resolve_candidate_binding(selection.bindings, capability_ref)
        if resolution == "no_unique_active_candidate":
            await self._record_intent_parsed(
                trace_id,
                task_id,
                session_id,
                memory_key=memory_key,
                status="ok",
                attributes={**valid_intent_attributes, "reason": resolution},
            )
            return await self._finish_no_capability_found(
                response_id,
                task_id,
                session_id,
                trace_id,
                reason=resolution,
                capabilities=visible_capabilities,
                memory_key=memory_key,
            )
        if resolution is None:
            return await self._finish_candidate_failure(
                response_id,
                task_id,
                session_id,
                trace_id,
                error_code="capability_candidate_out_of_scope",
                reason="candidate_out_of_scope",
                selection=selection,
                memory_key=memory_key,
            )
        candidate_binding, selection_rule = resolution

        intent_selector = capability_ref.capability_id
        try:
            selection_result = await self._orchestration.select_capability(
                capability_id=candidate_binding.capability_id,
                target_system=capability_ref.target_system,
                capability_type=capability_ref.capability_type,
            )
        except ValidationError:
            # A row that became invalid after the prompt is a stale candidate.
            # Never expose repository validation details in the response/trace.
            selection_result = None
        if selection_result is None or not _selection_matches_binding(
            selection_result.capability,
            candidate_binding,
        ):
            return await self._finish_candidate_failure(
                response_id,
                task_id,
                session_id,
                trace_id,
                error_code="capability_candidate_stale",
                reason="candidate_stale",
                selection=selection,
                memory_key=memory_key,
            )
        await self._record_intent_parsed(
            trace_id,
            task_id,
            session_id,
            memory_key=memory_key,
            status="ok",
            attributes=valid_intent_attributes,
        )
        selected_capability = selection_result.capability.model_copy(deep=True)
        projection_snapshot = ProjectionContractSnapshot.from_capability(selected_capability)
        capability_ref = capability_ref.model_copy(
            update={"capability_id": selected_capability.capability_id}
        )

        if collection is not None and (
            selected_capability.capability_id != D_FORM_ID
            or selected_capability.version != collection.record.capability_version
            or capability_fingerprint(selected_capability)
            != collection.record.capability_fingerprint
        ):
            raise DFormCollectionError("collection_version_conflict")
        if (
            selected_capability.capability_id == D_FORM_ID
            and self._d_form_collection_store is not None
            and scope is not None
            and scope.matches(principal, session_id)
            and scope.attempt.message is not None
            and set(capability_ref.arguments) <= {"category", "position", "description"}
        ):
            collected = await self._browser.collect(
                scope=scope,
                owner=memory_key,
                channel=channel,
                message=message,
                task_id=task_id,
                trace_id=trace_id,
                capability=selected_capability,
                model_arguments=capability_ref.arguments,
                collection=collection,
                category_options=category_options,
            )
            if isinstance(collected, ResponseEnvelope):
                return collected
            arguments = collected
            capability_ref = capability_ref.model_copy(update={"arguments": arguments})

        await self._task_store.append_event(
            task_id,
            TaskEventRecord(
                event_id=str(uuid4()),
                task_id=task_id,
                event_type="capability_selected",
                timestamp=datetime.now(UTC),
                payload={
                    "capability_id": selected_capability.capability_id,
                    "selection_rule": selection_rule,
                },
            ),
        )

        await self._trace_port.record_step(
            trace_id,
            task_id,
            session_id,
            tenant_id=memory_key.tenant_id,
            ai_user_id=memory_key.ai_user_id,
            event_type="capability_selected",
            status="ok",
            capability_id=capability_ref.capability_id,
            attributes={
                "intent_fingerprint": _intent_fingerprint(intent_selector),
                "selection_rule": selection_rule,
            },
        )
        binding_manifest: TaskVersionBindingManifest | None = None
        if self._human_gate_port is not None and (
            selected_capability.type != "workflow" or self._workflow_engine is not None
        ):
            try:
                resolved_bindings = await self._orchestration.resolve_task_version_bindings(
                    capability=selected_capability,
                    intent_version_binding=self._intent_version_binding,
                )
                binding_manifest = (
                    await self._human_gate_port.get_task_binding(task_id)
                    if collection is not None and collection.record.consumed
                    else None
                )
                if binding_manifest is not None:
                    await self._human_gate_port.assert_task_bindings(
                        task_id,
                        resolved_bindings.bindings,
                        exact=True,
                    )
                else:
                    binding_manifest = build_task_version_binding_manifest(
                        task_id=task_id,
                        bindings=resolved_bindings.bindings,
                        locked_at=datetime.now(UTC),
                    )
                _assert_manifest_projection_source(
                    binding_manifest,
                    selected_capability,
                    projection_snapshot,
                    resolved_bindings.projection_binding,
                )
                await self._human_gate_port.bind_task(binding_manifest)
            except (HumanGateConflictError, VersionBindingMismatchError, ValueError):
                return await self._finish_version_binding_failure(
                    response_id=response_id,
                    task_id=task_id,
                    session_id=session_id,
                    trace_id=trace_id,
                    capability_id=selected_capability.capability_id,
                    memory_key=memory_key,
                    candidate_selection=selection,
                )
        request_context = RequestOrgContext(
            request_id=trace_id,
            channel=channel,
            tenant_id=memory_key.tenant_id,
            account_set_id=_optional_str_argument(
                capability_ref.arguments,
                "account_set_id",
            ),
            resource_scope=_optional_str_argument(
                capability_ref.arguments,
                "resource_scope",
            ),
            device_domain_id=_optional_str_argument(
                capability_ref.arguments,
                "device_domain_id",
            ),
        )
        try:
            exec_result = await self._orchestration.execute_capability(
                task_id=task_id,
                session_id=session_id,
                ai_user_id=ai_user_id,
                capability=selected_capability,
                arguments=capability_ref.arguments,
                request_context=request_context,
            )
        except VersionBindingMismatchError:
            exec_result = ExecutionResult(
                status="failed",
                error_code="internal_error",
                trace_id=trace_id,
            )
        _record_chat_stage(
            trace_id,
            "execution",
            "ok" if exec_result.status in {"completed", "running"} else "failed",
            reason=exec_result.error_code or exec_result.status,
            capability_id=selected_capability.capability_id,
        )
        if exec_result.status == "running":
            return await self._browser.finish_admission(
                principal,
                session_id,
                task_id,
                trace_id,
                selected_capability,
                exec_result,
            )
        if (
            scope is not None
            and exec_result.browser_diagnostic is not None
            and exec_result.browser_diagnostic.reason
            in {"admission_timeout", "admission_uncertain"}
        ):
            recovered = await self._browser.recover_browser_request(scope)
            if recovered is not None:
                await self._browser.close_intent(scope)
                return recovered
            await self._browser.close_intent(scope)
            return self._browser.browser_unconfirmed_response(
                scope, timeout=exec_result.status == "timeout"
            )
        if exec_result.browser_diagnostic is not None:
            await self._trace_port.record_step(
                trace_id,
                task_id,
                session_id,
                tenant_id=memory_key.tenant_id,
                ai_user_id=memory_key.ai_user_id,
                event_type="gateway_post_recorded",
                status="failed",
                capability_id=selected_capability.capability_id,
                error_code=exec_result.error_code,
                attributes=exec_result.browser_diagnostic.attributes(),
            )
        staged = await self._stage_waiting(
            pending_key=pending_key,
            expected=pending,
            memory_key=memory_key,
            task_id=task_id,
            trace_id=trace_id,
            response_id=response_id,
            capability=selected_capability,
            arguments=capability_ref.arguments,
            exec_result=exec_result,
            binding_manifest=binding_manifest,
            projection_snapshot=projection_snapshot,
        )
        response_id, exec_result = staged.response_id, staged.execution
        confirmation = staged.confirmation
        workflow_waiting = staged.replacement is not None
        if workflow_waiting and not self._publish_staged(staged):
            if self._workflow_engine is not None:
                await self._workflow_engine.discard_checkpoint(task_id)
            exec_result = ExecutionResult(
                status="failed",
                error_code="internal_error",
                trace_id=trace_id,
            )
            workflow_waiting = False
            confirmation = None

        if exec_result.status == "waiting_user" and confirmation is None:
            confirmation = self._orchestration.prepare_confirmation(
                capability_id=selected_capability.capability_id,
                arguments=capability_ref.arguments,
                capability=selected_capability,
            )
        raw_execution = exec_result
        exec_result, business_verification = self._evaluate_required_postconditions(
            exec_result,
            scope=EvaluationScope(
                task_id,
                trace_id,
                session_id,
                memory_key.tenant_id,
                memory_key.ai_user_id,
            ),
            capability_id=selected_capability.capability_id,
            version=selected_capability.version,
        )
        try:
            envelope = self._build_envelope(
                response_id,
                task_id,
                session_id,
                exec_result,
                trace_id,
                capability_ref,
                capability=selected_capability,
                projection_snapshot=projection_snapshot,
                confirmation=confirmation,
                business_verification=(
                    business_verification if business_verification.rule_id else None
                ),
            )
        except ApprovedResponseProjectionError:
            # An approved read's local projection was rejected after the provider
            # returned. raw_execution keeps that fact; the business terminal is a
            # failure with no result data and no success memory.
            exec_result = ExecutionResult(
                status="failed",
                error_code="adapter_payload_invalid",
                trace_id=exec_result.trace_id,
            )
            envelope = self._build_envelope(
                response_id,
                task_id,
                session_id,
                exec_result,
                trace_id,
                capability_ref,
                capability=selected_capability,
                projection_snapshot=projection_snapshot,
            )
        if exec_result.browser_diagnostic is not None:
            envelope = envelope.model_copy(
                update={
                    "data": {
                        "error_code": exec_result.error_code,
                        **exec_result.browser_diagnostic.attributes(),
                    }
                }
            )
            if exec_result.browser_diagnostic.reason in {
                "admission_timeout",
                "admission_uncertain",
            }:
                message = "提交结果尚未确认；重查时请保留原请求。"
                envelope = envelope.model_copy(
                    update={"message": message, "fallback_text": message}
                )
        envelope = _append_partial_candidate_notice(envelope, selection)
        await self._trace_port.record_step(
            trace_id,
            task_id,
            session_id,
            tenant_id=memory_key.tenant_id,
            ai_user_id=memory_key.ai_user_id,
            event_type="response_envelope_created",
            status="ok",
        )
        final_task_status = _map_exec_to_task_status(exec_result.status)
        await self._task_store.update_status(
            task_id,
            final_task_status,
            exec_result.error_code,
        )
        terminal_event = _terminal_event_for_exec_status(exec_result.status)
        if terminal_event is not None:
            await self._trace_port.record_step(
                trace_id,
                task_id,
                session_id,
                tenant_id=memory_key.tenant_id,
                ai_user_id=memory_key.ai_user_id,
                event_type=terminal_event,
                status="ok" if terminal_event == "task_completed" else "failed",
                capability_id=capability_ref.capability_id,
                error_code=exec_result.error_code,
            )

        if not workflow_waiting:
            if exec_result.status != "waiting_user":
                await self._record_terminal_evaluation(
                    trace_id=trace_id,
                    task_id=task_id,
                    session_id=session_id,
                    business_status=cast(TerminalBusinessStatus, exec_result.status),
                    error_code=exec_result.error_code,
                    raw_execution=raw_execution,
                    business_verification=business_verification,
                    capability_id=capability_ref.capability_id,
                    memory_key=memory_key,
                )
            finalize_status = _map_task_to_finalize_status(final_task_status)
            await self._trace_port.finalize_task_trace(
                trace_id,
                task_id,
                session_id,
                tenant_id=memory_key.tenant_id,
                ai_user_id=memory_key.ai_user_id,
                status=finalize_status,
                capability_id=capability_ref.capability_id,
                error_code=exec_result.error_code,
            )
        if exec_result.status == "completed":
            self._session_memory.remember_completed(
                memory_key,
                capability_id=capability_ref.capability_id,
            )
        return envelope

    async def _stage_waiting(
        self,
        *,
        pending_key: tuple[str, str, str],
        expected: _PendingWorkflow | None,
        memory_key: SessionMemoryKey,
        task_id: str,
        trace_id: str,
        response_id: str,
        capability: CapabilitySpec,
        arguments: dict[str, Any],
        exec_result: ExecutionResult,
        binding_manifest: TaskVersionBindingManifest | None,
        projection_snapshot: ProjectionContractSnapshot,
        intent_version_binding: VersionBinding | None = None,
    ) -> _StagedWaiting:
        """Prepare a waiting confirmation without publishing it as claimable."""
        confirmation: ConfirmationPreview | None = None
        gate_request_id: str | None = None
        action_digest: str | None = None
        request_digest: str | None = None
        gate_manifest_digest: str | None = None
        workflow_waiting = (
            capability.type == "workflow" and exec_result.status == "waiting_user"
        )
        requested_at = self._utc_clock()
        gate_expires_at = requested_at + timedelta(seconds=_CONFIRMATION_TTL_SECONDS)
        monotonic_deadline = self._monotonic_clock() + _CONFIRMATION_TTL_SECONDS
        if workflow_waiting and self._human_gate_port is not None:
            try:
                if binding_manifest is None:
                    raise VersionBindingMismatchError(
                        "Waiting Workflow has no Task version binding"
                    )
                gate_request_id = response_id
                gate_manifest_digest = binding_manifest.manifest_digest
                if self._workflow_engine is None:
                    raise VersionBindingMismatchError(
                        "Waiting Workflow has no immutable action digest"
                    )
                action_digest = await self._workflow_engine.pending_confirmation_action_digest(
                    task_id
                )
                confirmation = self._orchestration.prepare_confirmation(
                    capability_id=capability.capability_id,
                    arguments=arguments,
                    capability=capability,
                )
                request_digest = immutable_request_digest(
                    task_id=task_id,
                    action_digest=action_digest,
                    preview=confirmation.to_payload(),
                    binding_manifest_digest=gate_manifest_digest,
                )
                persisted_gate = await self._workflow_engine.governed_confirmation(task_id)
                if persisted_gate is not None:
                    gate_request_id = persisted_gate.request_id
                    response_id = persisted_gate.request_id
                    action_digest = persisted_gate.action_digest
                    request_digest = persisted_gate.request_digest
                    gate_manifest_digest = persisted_gate.binding_manifest_digest
                    gate_expires_at = persisted_gate.expires_at
                    monotonic_deadline = self._monotonic_clock() + max(
                        0,
                        (gate_expires_at - self._utc_clock()).total_seconds(),
                    )
                else:
                    await self._human_gate_port.create_request(
                        HumanGateRequest(
                            request_id=gate_request_id,
                            task_id=task_id,
                            requested_for_ai_user_id=memory_key.ai_user_id,
                            requested_session_id=memory_key.session_id,
                            requested_tenant_id=memory_key.tenant_id,
                            action_digest=action_digest,
                            request_digest=request_digest,
                            binding_manifest_digest=gate_manifest_digest,
                            requested_at=requested_at,
                            expires_at=requested_at + timedelta(seconds=_CONFIRMATION_TTL_SECONDS),
                        )
                    )
            except (HumanGateConflictError, VersionBindingMismatchError):
                if self._workflow_engine is not None:
                    await self._workflow_engine.discard_checkpoint(task_id)
                exec_result = ExecutionResult(
                    status="failed",
                    error_code="internal_error",
                    trace_id=trace_id,
                )
                workflow_waiting = False
                confirmation = None
        replacement: _PendingWorkflow | None = None
        if workflow_waiting:
            replacement = _PendingWorkflow(
                task_id=task_id,
                trace_id=trace_id,
                response_id=response_id,
                capability_id=capability.capability_id,
                projection_snapshot=projection_snapshot,
                gate_request_id=gate_request_id,
                action_digest=action_digest,
                request_digest=request_digest,
                binding_manifest_digest=gate_manifest_digest,
                owner=memory_key,
                expires_at=gate_expires_at,
                monotonic_deadline=monotonic_deadline,
                intent_version_binding=intent_version_binding,
            )
            await self._expire_pending_confirmations(memory_key)
            if expected is not None and self._pending_workflows.get(pending_key) is None:
                expected = None
        return _StagedWaiting(
            pending_key=pending_key,
            expected=expected,
            replacement=replacement,
            response_id=response_id,
            execution=exec_result,
            confirmation=confirmation,
        )

    def _publish_staged(self, staged: _StagedWaiting) -> bool:
        """Synchronous CAS publication; no await between check and swap."""
        assert staged.replacement is not None
        return self._publish_pending_workflow(
            staged.pending_key,
            expected=staged.expected,
            replacement=staged.replacement,
        )

    async def handle_confirmation_message(
        self,
        owner: ConfirmationOwner,
        message: str,
        *,
        client_request_id: str | None,
    ) -> ResponseEnvelope | None:
        return await self._deterministic_confirmation(
            _owner_key(owner), message, client_request_id=client_request_id
        )

    async def handle_confirmation_action(
        self,
        owner: ConfirmationOwner,
        action: UserAction,
    ) -> ResponseEnvelope:
        return await self._confirmation_action(_owner_key(owner), action)

    def has_pending(self, owner: ConfirmationOwner) -> bool:
        memory_key = _owner_key(owner)
        pending_key = _pending_workflow_key(
            memory_key.tenant_id, memory_key.session_id, memory_key.ai_user_id
        )
        return self._pending_workflows.get(pending_key) is not None

    async def stage_waiting(self, proposal: WaitingProposal) -> StagedWaiting:
        if proposal.intent_version_binding is None:
            raise VersionBindingMismatchError("Waiting proposal has no intent version binding")
        memory_key = _owner_key(proposal.owner)
        pending_key = _pending_workflow_key(
            memory_key.tenant_id, memory_key.session_id, memory_key.ai_user_id
        )
        staged = await self._stage_waiting(
            pending_key=pending_key,
            expected=self._pending_workflows.get(pending_key),
            memory_key=memory_key,
            task_id=proposal.task_id,
            trace_id=proposal.trace_id,
            response_id=proposal.response_id,
            capability=proposal.capability,
            arguments=dict(proposal.arguments),
            exec_result=proposal.execution,
            binding_manifest=proposal.binding_manifest,
            projection_snapshot=proposal.projection,
            intent_version_binding=proposal.intent_version_binding,
        )
        return StagedWaiting(
            response_id=staged.response_id,
            execution=staged.execution,
            confirmation=staged.confirmation,
            token=staged,
        )

    def publish_waiting(self, staged: StagedWaiting) -> bool:
        token = staged.token
        if not isinstance(token, _StagedWaiting):
            raise TypeError("Staged waiting token was not issued by this coordinator")
        return token.replacement is not None and self._publish_staged(token)

    async def abandon_waiting(self, staged: StagedWaiting) -> None:
        """Clean up a staged item that was never published; bounded."""
        token = staged.token
        if not isinstance(token, _StagedWaiting):
            raise TypeError("Staged waiting token was not issued by this coordinator")
        pending = token.replacement
        if pending is None or self._workflow_engine is None:
            return
        async with asyncio.timeout(_ABANDON_TIMEOUT_SECONDS):
            governed = await self._workflow_engine.retire_owned_confirmation(
                pending.task_id,
                pending.action_digest or "",
                pending.gate_request_id or "",
                "exception",
            )
            if not governed:
                await self._workflow_engine.discard_checkpoint(pending.task_id)

    async def _deterministic_confirmation(
        self,
        memory_key: SessionMemoryKey,
        message: str,
        *,
        client_request_id: str | None = None,
    ) -> ResponseEnvelope | None:
        """Text confirmation and stale card references, before any ordinary turn."""
        await self._expire_pending_confirmations(memory_key)
        pending_key = _pending_workflow_key(
            memory_key.tenant_id, memory_key.session_id, memory_key.ai_user_id
        )
        pending = self._pending_workflows.get(pending_key)
        owned = pending if pending is not None and pending.owner == memory_key else None
        explicit = owned is not None and _is_explicit_workflow_confirmation(message, owned)
        claim_outcome: UserActionOutcome | None = None
        receipt: _ConfirmationReceipt | None = None
        if client_request_id is not None and (
            explicit or _is_confirmation_text(message)
        ):
            # The receipt and the claim share one critical section: a replayed
            # request can never claim a gate other than the one it first saw.
            receipt_key = (*pending_key, client_request_id)
            digest = sha256(_normalized_confirmation_text(message).encode("utf-8")).digest()
            with self._pending_confirmation_claim_lock:
                hit, receipt = self._admit_confirmation_receipt(receipt_key, digest, owned)
                if hit == "new" and explicit:
                    assert owned is not None
                    claim_outcome = self._claim_confirmation_locked(
                        pending_key, owned, memory_key
                    )
            if hit != "new":
                logging.getLogger(__name__).info(
                    "agentscope_confirmation_receipt hit=%s gate_bound=%s",
                    hit,
                    receipt is not None and receipt.gate_ref is not None,
                )
                if hit == "done":
                    assert receipt is not None
                    return receipt.envelope
                return await self._build_stale_confirmation_response(
                    pending=owned,
                    session_id=memory_key.session_id,
                    memory_key=memory_key,
                    receipt_full=hit == "full",
                )
        elif explicit:
            assert owned is not None
            claim_outcome = self._claim_confirmation(pending_key, owned, memory_key)
        envelope = await self._settle_confirmation_message(
            pending_key, owned, explicit, claim_outcome, memory_key, message
        )
        if receipt is not None:
            with self._pending_confirmation_claim_lock:
                receipt.state, receipt.envelope = "done", envelope
        return envelope

    async def _settle_confirmation_message(
        self,
        pending_key: tuple[str, str, str],
        pending: _PendingWorkflow | None,
        explicit: bool,
        claim_outcome: UserActionOutcome | None,
        memory_key: SessionMemoryKey,
        message: str,
    ) -> ResponseEnvelope | None:
        session_id = memory_key.session_id
        if pending is not None:
            if explicit:
                if claim_outcome is not None:
                    return await self._build_stale_confirmation_response(
                        pending=pending,
                        session_id=session_id,
                        memory_key=memory_key,
                    )
                envelope, outcome = await self._process_pending_confirmation(
                    pending_key=pending_key,
                    pending=pending,
                    session_id=session_id,
                    memory_key=memory_key,
                    action_type="confirm",
                )
                if outcome in {"action_version_conflict", "action_already_claimed"}:
                    claim = self._claimed_pending_confirmations[
                        _pending_confirmation_claim_key(pending_key, pending)
                    ]
                    if claim.cleanup_governed is False:
                        return await self._finish_version_binding_failure(
                            response_id=str(uuid4()),
                            task_id=pending.task_id,
                            session_id=session_id,
                            trace_id=pending.trace_id,
                            capability_id=pending.capability_id,
                            memory_key=memory_key,
                        )
                return envelope
            if _is_stale_workflow_confirmation_message(message):
                return await self._build_stale_confirmation_response(
                    pending=pending,
                    session_id=session_id,
                    memory_key=memory_key,
                )

        if _is_stale_workflow_confirmation_message(message):
            confirmation_reference = message.strip().split()[-1]
            cached = self._lookup_confirmation_outcome(memory_key, confirmation_reference)
            if cached in {"cancelled", "confirmation_invalidated"}:
                action_trace_id, action_task_id = str(uuid4()), str(uuid4())
                await self._trace_port.record_step(
                    action_trace_id,
                    action_task_id,
                    session_id,
                    tenant_id=memory_key.tenant_id,
                    ai_user_id=memory_key.ai_user_id,
                    event_type="user_action",
                    status="ok",
                    attributes={"phase": "inbound", "action_type": "confirm"},
                )
                return await self._finish_user_action_attempt(
                    action_trace_id=action_trace_id,
                    action_task_id=action_task_id,
                    session_id=session_id,
                    memory_key=memory_key,
                    outcome=cached or "confirmation_invalidated",
                )
        return None

    async def handle_user_action(
        self,
        channel: Literal["web", "cli", "api", "mock"],
        principal: Principal,
        session_id: str,
        action: UserAction,
    ) -> ResponseEnvelope:
        del channel
        return await self._confirmation_action(
            SessionMemoryKey(
                tenant_id=principal.org_ctx.tenant_id,
                session_id=session_id,
                ai_user_id=principal.ai_user_id,
            ),
            action,
        )

    async def _confirmation_action(
        self,
        memory_key: SessionMemoryKey,
        action: UserAction,
    ) -> ResponseEnvelope:
        session_id = memory_key.session_id
        action_trace_id = str(uuid4())
        action_task_id = str(uuid4())
        await self._trace_port.record_step(
            action_trace_id,
            action_task_id,
            session_id,
            tenant_id=memory_key.tenant_id,
            ai_user_id=memory_key.ai_user_id,
            event_type="user_action",
            status="ok",
            attributes={"phase": "inbound", "action_type": action.action_type},
        )

        pending_key = _pending_workflow_key(memory_key.tenant_id, session_id, memory_key.ai_user_id)
        if self._human_gate_port is None:
            return await self._finish_user_action_attempt(
                action_trace_id=action_trace_id,
                action_task_id=action_task_id,
                session_id=session_id,
                memory_key=memory_key,
                outcome="action_gate_unavailable",
            )

        await self._expire_pending_confirmations(memory_key)
        cached = self._lookup_confirmation_outcome(memory_key, action.response_id)
        pending = self._pending_workflows.get(pending_key)
        outcome: UserActionOutcome | None = cached
        if outcome is None:
            if pending is None or pending.owner != memory_key:
                outcome = "confirmation_invalidated"
            elif action.response_id != (pending.gate_request_id or pending.response_id):
                outcome = "action_reference_mismatch"
            elif (
                pending.action_digest is None
                or pending.request_digest is None
                or pending.binding_manifest_digest is None
            ):
                outcome = "action_binding_incomplete"
            else:
                outcome = self._claim_confirmation(pending_key, pending, memory_key)
        if outcome is not None:
            return await self._finish_user_action_attempt(
                action_trace_id=action_trace_id,
                action_task_id=action_task_id,
                session_id=session_id,
                memory_key=memory_key,
                outcome=outcome,
                error_code=self._confirmation_error_code(memory_key, action.response_id),
            )
        assert pending is not None
        envelope, outcome = await self._process_pending_confirmation(
            pending_key=pending_key,
            pending=pending,
            session_id=session_id,
            memory_key=memory_key,
            action_type=action.action_type,
        )
        return await self._finish_user_action_attempt(
            action_trace_id=action_trace_id,
            action_task_id=action_task_id,
            session_id=session_id,
            memory_key=memory_key,
            outcome=outcome,
            envelope=envelope,
            error_code=self._confirmation_error_code(memory_key, action.response_id),
        )

    async def _finish_user_action_attempt(
        self,
        *,
        action_trace_id: str,
        action_task_id: str,
        session_id: str,
        memory_key: SessionMemoryKey,
        outcome: UserActionOutcome,
        envelope: ResponseEnvelope | None = None,
        error_code: ErrorCode | None = None,
    ) -> ResponseEnvelope:
        if envelope is None and outcome in {"cancelled", "confirmation_invalidated"}:
            envelope = self._confirmation_terminal_envelope(
                task_id=action_task_id,
                trace_id=action_trace_id,
                session_id=session_id,
                status="cancelled" if outcome == "cancelled" else "confirmation_invalidated",
            )
        elif envelope is None:
            envelope = self._response_builder.build_failed(
                str(uuid4()),
                action_task_id,
                session_id,
                "此确认已处理，本次没有再次执行。"
                if outcome == "action_already_claimed"
                else "结构化操作未被受理，本次未执行。",
                "This confirmation was already handled; no additional execution was started."
                if outcome == "action_already_claimed"
                else "The structured action was not accepted; nothing was executed.",
                action_trace_id,
                data={"action_outcome": outcome, "result": None},
            )
        else:
            envelope = envelope.model_copy(
                update={
                    "data": {
                        "action_outcome": outcome,
                        "result": None
                        if outcome in {"cancelled", "confirmation_invalidated"}
                        else envelope.data,
                    }
                }
            )
        await self._trace_port.record_step(
            action_trace_id,
            action_task_id,
            session_id,
            tenant_id=memory_key.tenant_id,
            ai_user_id=memory_key.ai_user_id,
            event_type="user_action",
            status="ok" if outcome == "accepted" else "blocked",
            error_code=error_code,
            attributes={"phase": "outcome", "action_outcome": outcome},
        )
        return envelope

    def _prune_confirmation_claims(self, *, make_room: bool = False) -> None:
        """Called only under the claim lock; unfinished cleanup cannot be evicted."""
        now = self._monotonic_clock()
        for key, claim in list(self._claimed_pending_confirmations.items()):
            if claim.cleanup_complete and claim.retain_until < now:
                self._forget_confirmation_claim(key, claim)
        if make_room and len(self._claimed_pending_confirmations) >= self._confirmation_claim_limit:
            eligible = [
                (claim.retain_until, key, claim)
                for key, claim in self._claimed_pending_confirmations.items()
                if claim.cleanup_complete
            ]
            if eligible:
                _, key, claim = min(eligible, key=lambda item: item[0])
                self._forget_confirmation_claim(key, claim)

    def _forget_confirmation_claim(
        self,
        key: tuple[str, str, str, str],
        claim: _ConfirmationClaim,
    ) -> None:
        del self._claimed_pending_confirmations[key]
        self._confirmation_references.pop(
            (*key[:3], claim.response_id),
            None,
        )

    def _lookup_confirmation_outcome(
        self,
        owner: SessionMemoryKey,
        reference: str,
    ) -> UserActionOutcome | None:
        with self._pending_confirmation_claim_lock:
            self._prune_confirmation_claims()
            key = self._confirmation_references.get(
                (owner.tenant_id, owner.session_id, owner.ai_user_id, reference),
            )
            claim = self._claimed_pending_confirmations.get(key) if key is not None else None
            if claim is None or claim.tenant_id != owner.tenant_id:
                return None
            if claim.state in {"cancelled", "confirmation_invalidated"}:
                if not claim.cleanup_complete:
                    raise RuntimeError("Confirmation cleanup is incomplete")
                return "cancelled" if claim.state == "cancelled" else "confirmation_invalidated"
            return "action_already_claimed"

    def _confirmation_error_code(
        self,
        owner: SessionMemoryKey,
        reference: str,
    ) -> ErrorCode | None:
        with self._pending_confirmation_claim_lock:
            key = self._confirmation_references.get(
                (owner.tenant_id, owner.session_id, owner.ai_user_id, reference)
            )
            claim = self._claimed_pending_confirmations.get(key) if key is not None else None
            return (
                claim.error_code
                if claim is not None and claim.tenant_id == owner.tenant_id
                else None
            )

    def _confirmation_expired(self, pending: _PendingWorkflow) -> bool:
        return (
            pending.expires_at is None
            or pending.monotonic_deadline is None
            or self._utc_clock() > pending.expires_at
            or self._monotonic_clock() > pending.monotonic_deadline
        )

    def _new_confirmation_claim(
        self,
        key: tuple[str, str, str],
        pending: _PendingWorkflow,
        owner: SessionMemoryKey,
    ) -> _ConfirmationClaim | None:
        self._prune_confirmation_claims(make_room=True)
        if len(self._claimed_pending_confirmations) >= self._confirmation_claim_limit:
            return None
        claim_key = _pending_confirmation_claim_key(key, pending)
        claim = _ConfirmationClaim(
            tenant_id=owner.tenant_id,
            response_id=pending.gate_request_id or pending.response_id,
            pending=pending,
            state="processing",
            retain_until=self._monotonic_clock() + _CONFIRMATION_TTL_SECONDS,
        )
        self._claimed_pending_confirmations[claim_key] = claim
        self._confirmation_references[(*key, claim.response_id)] = claim_key
        return claim

    def _claim_confirmation(
        self,
        key: tuple[str, str, str],
        pending: _PendingWorkflow,
        owner: SessionMemoryKey,
    ) -> UserActionOutcome | None:
        with self._pending_confirmation_claim_lock:
            return self._claim_confirmation_locked(key, pending, owner)

    def _claim_confirmation_locked(
        self,
        key: tuple[str, str, str],
        pending: _PendingWorkflow,
        owner: SessionMemoryKey,
    ) -> UserActionOutcome | None:
        """Called only under the claim lock."""
        if self._pending_workflows.get(key) is not pending:
            return "action_pending_changed"
        claim = self._claimed_pending_confirmations.get(
            _pending_confirmation_claim_key(key, pending),
        )
        if claim is not None:
            if claim.tenant_id == owner.tenant_id and claim.state in {
                "cancelled",
                "confirmation_invalidated",
            }:
                if not claim.cleanup_complete:
                    raise RuntimeError("Confirmation cleanup is incomplete")
                return "cancelled" if claim.state == "cancelled" else "confirmation_invalidated"
            return "action_already_claimed"
        if self._confirmation_expired(pending):
            return "confirmation_invalidated"
        if self._new_confirmation_claim(key, pending, owner) is None:
            return "action_gate_unavailable"
        return None

    def _admit_confirmation_receipt(
        self,
        key: tuple[str, str, str, str],
        digest: bytes,
        pending: _PendingWorkflow | None,
    ) -> tuple[Literal["new", "done", "stale", "full"], _ConfirmationReceipt | None]:
        """Called only under the claim lock; bounded and fail-closed when full."""
        now = self._monotonic_clock()
        receipts = self._confirmation_receipts
        while receipts and next(iter(receipts.values())).retain_until < now:
            receipts.popitem(last=False)
        receipt = receipts.get(key)
        if receipt is not None:
            if receipt.message_digest != digest or receipt.state == "processing":
                return "stale", receipt
            return "done", receipt
        if len(receipts) >= _CONFIRMATION_RECEIPT_LIMIT or sum(
            1 for held in receipts if held[0] == key[0] and held[2] == key[2]
        ) >= _CONFIRMATION_RECEIPT_OWNER_LIMIT:
            return "full", None
        receipt = _ConfirmationReceipt(
            gate_ref=(pending.gate_request_id or pending.response_id)
            if pending is not None
            else None,
            message_digest=digest,
            state="processing",
            envelope=None,
            retain_until=now + 2 * _CONFIRMATION_TTL_SECONDS,
        )
        receipts[key] = receipt
        return "new", receipt

    def _confirmation_terminal_envelope(
        self,
        *,
        task_id: str,
        trace_id: str,
        session_id: str,
        status: Literal["cancelled", "confirmation_invalidated"],
    ) -> ResponseEnvelope:
        return self._response_builder.build_message(
            str(uuid4()),
            task_id,
            session_id,
            "已取消这项操作，本次未执行。"
            if status == "cancelled"
            else _CONFIRMATION_INVALIDATED_MESSAGE,
            "This operation was cancelled and was not executed."
            if status == "cancelled"
            else (
                "This confirmation is no longer valid. Start again after verifying "
                "the business state to avoid duplicate operations."
            ),
            trace_id,
            status=status,
            data={"action_outcome": status, "result": None},
        )

    async def _retire_pending_confirmation(
        self,
        *,
        pending_key: tuple[str, str, str],
        pending: _PendingWorkflow,
        status: Literal["cancelled", "confirmation_invalidated"],
        reason: Literal["cancelled", "expired", "exception"],
        error_code: ErrorCode | None,
        governed_only: bool = False,
    ) -> ResponseEnvelope:
        owner = pending.owner
        if owner is None:
            raise RuntimeError("Pending confirmation has no trusted owner")
        claim_key = _pending_confirmation_claim_key(pending_key, pending)
        with self._pending_confirmation_claim_lock:
            claim = self._claimed_pending_confirmations.get(claim_key)
            if claim is None:
                claim = self._new_confirmation_claim(pending_key, pending, owner)
            if claim is None:
                raise RuntimeError("Confirmation cleanup capacity exhausted")
            if claim.state != "processing":
                return self._confirmation_terminal_envelope(
                    task_id=pending.task_id,
                    trace_id=pending.trace_id,
                    session_id=owner.session_id,
                    status=status,
                )
            claim.state = status
            claim.error_code = error_code
            claim.cleanup_reason = reason
            claim.cleanup_governed_only = governed_only
            claim.pending = pending
            claim.retain_until = self._monotonic_clock() + _CONFIRMATION_TTL_SECONDS
            owns_pending = self._pending_workflows.get(pending_key) is pending
            if not owns_pending:
                # A CAS loser owns no cleanup work; the winner must stay untouched.
                if governed_only:
                    claim.state = "completed"
                claim.cleanup_complete = True
        envelope = self._confirmation_terminal_envelope(
            task_id=pending.task_id,
            trace_id=pending.trace_id,
            session_id=owner.session_id,
            status=status,
        )
        if not owns_pending:
            return envelope
        await self._retry_confirmation_cleanup(pending_key, claim)
        return envelope

    async def _retry_confirmation_cleanup(
        self,
        pending_key: tuple[str, str, str],
        claim: _ConfirmationClaim,
    ) -> None:
        with self._pending_confirmation_claim_lock:
            if claim.cleanup_complete or claim.cleanup_in_progress:
                return
            pending = claim.pending
            if pending is None or claim.cleanup_reason is None:
                return
            claim.cleanup_in_progress = True
        try:
            assert pending.owner is not None
            target = claim.governed_rebind or pending
            governed = False
            if self._workflow_engine is not None:
                task = await self._task_store.get_task(pending.task_id)
                if task is None or (task.tenant_id, task.ai_user_id, task.session_id) != (
                    pending.owner.tenant_id,
                    pending.owner.ai_user_id,
                    pending.owner.session_id,
                ):
                    raise RuntimeError("Confirmation cleanup owner mismatch")
                governed = await self._workflow_engine.retire_owned_confirmation(
                    pending.task_id,
                    target.action_digest or "",
                    target.gate_request_id or "",
                    claim.cleanup_reason,
                )
                if not governed:
                    await self._workflow_engine.discard_checkpoint(pending.task_id)
                elif claim.cleanup_governed_only:
                    # A duplicate decision can collide with a still-active Apps
                    # sender. Retirement only proves cleanup ownership; its bool
                    # must not overwrite the sender's subsequently durable result.
                    action = await self._workflow_engine.pending_confirmation_action_digest(
                        pending.task_id
                    )
                    if action == target.action_digest:
                        result = await self._workflow_engine.finalize_governed_task(
                            task_id=pending.task_id
                        )
                        current_action = (
                            await self._workflow_engine.pending_confirmation_action_digest(
                                pending.task_id
                            )
                        )
                        if (
                            isinstance(result, GovernedTerminalResult)
                            and current_action == target.action_digest
                        ):
                            self._retain_governed_terminal(pending_key, pending, result)
            if not governed and not claim.cleanup_governed_only:
                await self._finish_confirmation_terminal(
                    pending=pending,
                    status="cancelled"
                    if claim.state == "cancelled"
                    else "confirmation_invalidated",
                    reason=claim.cleanup_reason,
                    error_code=claim.error_code,
                )
            if claim.response_trace_pending:
                await self._record_governed_response(pending)
                claim.response_trace_pending = False
            with self._pending_confirmation_claim_lock:
                claim.cleanup_governed = governed
                if not governed and claim.cleanup_governed_only:
                    claim.state = "completed"
                if self._pending_workflows.get(pending_key) is pending:
                    del self._pending_workflows[pending_key]
                claim.cleanup_complete = True
                claim.pending = None
        finally:
            with self._pending_confirmation_claim_lock:
                claim.cleanup_in_progress = False

    async def _finish_confirmation_terminal(
        self,
        *,
        pending: _PendingWorkflow,
        status: Literal["cancelled", "confirmation_invalidated"],
        reason: Literal["cancelled", "expired", "exception"],
        error_code: ErrorCode | None,
    ) -> None:
        owner = pending.owner
        assert owner is not None
        record = await self._task_store.get_task(pending.task_id)
        if (
            record is not None
            and record.status != status
            and record.status
            in {
                "completed",
                "failed",
                "no_capability_found",
                "cancelled",
                "confirmation_invalidated",
            }
        ):
            return
        await self._task_store.update_status(pending.task_id, status, error_code)
        trace_status: TraceEventStatus = "blocked" if status == "cancelled" else "failed"
        await self._trace_port.record_step(
            pending.trace_id,
            pending.task_id,
            owner.session_id,
            tenant_id=owner.tenant_id,
            ai_user_id=owner.ai_user_id,
            event_type="response_envelope_created",
            status="ok",
        )
        await self._trace_port.record_step(
            pending.trace_id,
            pending.task_id,
            owner.session_id,
            tenant_id=owner.tenant_id,
            ai_user_id=owner.ai_user_id,
            event_type="task_cancelled"
            if status == "cancelled"
            else "task_confirmation_invalidated",
            status=trace_status,
            capability_id=pending.capability_id,
            error_code=error_code,
            attributes={"reason": reason},
        )
        await self._record_terminal_evaluation(
            trace_id=pending.trace_id,
            task_id=pending.task_id,
            session_id=owner.session_id,
            business_status=status,
            error_code=error_code,
            capability_id=pending.capability_id,
            memory_key=owner,
        )
        await self._trace_port.finalize_task_trace(
            pending.trace_id,
            pending.task_id,
            owner.session_id,
            tenant_id=owner.tenant_id,
            ai_user_id=owner.ai_user_id,
            status=trace_status,
            capability_id=pending.capability_id,
            error_code=error_code,
        )

    async def _repair_owner_pending(self, owner: SessionMemoryKey) -> None:
        for claim_key, cleanup_claim in list(self._claimed_pending_confirmations.items()):
            if (
                cleanup_claim.pending is not None
                and cleanup_claim.pending.owner == owner
                and cleanup_claim.state
                in {
                    "cancelled",
                    "confirmation_invalidated",
                }
            ):
                await self._retry_confirmation_cleanup(claim_key[:3], cleanup_claim)
        if self._workflow_engine is None:
            return
        for key, pending in list(self._pending_workflows.items()):
            if pending.owner != owner:
                continue
            with self._pending_confirmation_claim_lock:
                claim = self._claimed_pending_confirmations.get(
                    _pending_confirmation_claim_key(key, pending),
                )
                if claim is not None and claim.state == "processing":
                    continue
            result = await self._workflow_engine.finalize_governed_task(task_id=pending.task_id)
            if result is None:
                continue
            if claim is not None and claim.response_trace_pending:
                await self._record_governed_response(pending)
                claim.response_trace_pending = False
            with self._pending_confirmation_claim_lock:
                if self._pending_workflows.get(key) is not pending:
                    continue
                if claim is None:
                    claim = self._new_confirmation_claim(key, pending, owner)
                if claim is None:
                    raise RuntimeError("Confirmation cleanup capacity exhausted")
            if isinstance(result, GovernedTerminalResult):
                self._retain_governed_terminal(key, pending, result)
            else:
                claim.state = "completed"
            with self._pending_confirmation_claim_lock:
                claim.pending = None
                claim.cleanup_complete = True
                if self._pending_workflows.get(key) is pending:
                    del self._pending_workflows[key]

    async def _expire_pending_confirmations(self, owner: SessionMemoryKey) -> None:
        await self._repair_owner_pending(owner)
        for key, pending in list(self._pending_workflows.items()):
            with self._pending_confirmation_claim_lock:
                if (
                    pending.owner != owner
                    or not self._confirmation_expired(pending)
                    or self._pending_workflows.get(key) is not pending
                    or _pending_confirmation_claim_key(key, pending)
                    in self._claimed_pending_confirmations
                ):
                    continue
                if pending.owner is None:
                    continue
                if self._new_confirmation_claim(key, pending, pending.owner) is None:
                    continue
            await self._retire_pending_confirmation(
                pending_key=key,
                pending=pending,
                status="confirmation_invalidated",
                reason="expired",
                error_code="confirm_required",
            )

    async def _process_pending_confirmation(
        self,
        *,
        pending_key: tuple[str, str, str],
        pending: _PendingWorkflow,
        session_id: str,
        memory_key: SessionMemoryKey,
        action_type: Literal["confirm", "reject", "cancel"],
    ) -> tuple[ResponseEnvelope, UserActionOutcome]:
        try:
            try:
                effective = await self._rebind_governed_pending(pending_key, pending, memory_key)
            except McpFailure as exc:
                claim = self._claimed_pending_confirmations[
                    _pending_confirmation_claim_key(pending_key, pending)
                ]
                if action_type != "confirm" or claim.governed_rebind is not None:
                    raise
                # Nothing was decided or re-keyed: keep the card claimable and
                # never fall back to the original login's authorization.
                envelope = self._response_builder.build_failed(
                    str(uuid4()),
                    pending.task_id,
                    session_id,
                    "当前登录尚未完成该业务系统授权或授权已失效，请重新授权后再点确认；本次未执行。",
                    "The current login has no valid authorization for this business "
                    "system; authorize again, then confirm. Nothing was executed.",
                    pending.trace_id,
                    data={"error_code": _mcp_route_error(exc.code)},
                )
                self._release_unstarted_claim(pending_key, pending)
                return envelope, "action_gate_unavailable"
            if action_type != "confirm":
                await self._record_confirmation_decision(effective, memory_key, "rejected")
                return await self._retire_pending_confirmation(
                    pending_key=pending_key,
                    pending=pending,
                    status="cancelled",
                    reason="cancelled",
                    error_code=None,
                ), "cancelled"
            envelope = await self._resume_pending_workflow(
                pending_key=pending_key,
                pending=pending,
                effective=effective,
                session_id=session_id,
                memory_key=memory_key,
            )
        except asyncio.CancelledError:
            claim = self._claimed_pending_confirmations[
                _pending_confirmation_claim_key(pending_key, pending)
            ]
            if claim.governed_terminal is not None:
                self._retain_governed_terminal(pending_key, pending, claim.governed_terminal)
                raise
            await self._retire_pending_confirmation(
                pending_key=pending_key,
                pending=pending,
                status="confirmation_invalidated",
                reason="exception",
                error_code="internal_error",
            )
            raise
        except (_ActionAlreadyClaimedError, VersionBindingMismatchError) as exc:
            outcome: UserActionOutcome = (
                "action_already_claimed"
                if isinstance(exc, _ActionAlreadyClaimedError)
                else "action_version_conflict"
            )
            await self._retire_pending_confirmation(
                pending_key=pending_key,
                pending=pending,
                status="cancelled",
                reason="cancelled",
                error_code=None,
                governed_only=True,
            )
            claim = self._claimed_pending_confirmations[
                _pending_confirmation_claim_key(pending_key, pending)
            ]
            handled = outcome == "action_already_claimed" or (
                claim.governed_terminal is not None and claim.state == "completed"
            )
            return self._response_builder.build_failed(
                str(uuid4()),
                pending.task_id,
                session_id,
                "此确认已处理，本次没有再次执行。"
                if handled
                else "结构化操作未被受理，本次未执行。",
                "This confirmation was already handled; no additional execution was started."
                if handled
                else "The structured action was not accepted; nothing was executed.",
                pending.trace_id,
            ), outcome
        except OrchestrationContractError:
            raise
        except GovernedFinalizationError as exc:
            if not isinstance(exc.result, GovernedTerminalResult):
                raise
            self._retain_governed_terminal(pending_key, pending, exc.result)
            if isinstance(exc.__cause__, asyncio.CancelledError):
                raise exc.__cause__
            raise
        except Exception as exc:
            claim = self._claimed_pending_confirmations[
                _pending_confirmation_claim_key(pending_key, pending)
            ]
            if claim.state != "processing":
                # A cleanup write failed after the irreversible retirement marker.
                # Preserve the failure and record; never retry or claim audit success.
                raise
            envelope = await self._retire_pending_confirmation(
                pending_key=pending_key,
                pending=pending,
                status="confirmation_invalidated",
                reason="expired" if isinstance(exc, _ActionStaleError) else "exception",
                error_code="confirm_required"
                if isinstance(exc, _ActionStaleError)
                else "internal_error",
            )
            return envelope, "confirmation_invalidated"
        self._archive_confirmation(pending_key, pending, cleanup_complete=True)
        claim = self._claimed_pending_confirmations[
            _pending_confirmation_claim_key(pending_key, pending)
        ]
        if claim.state == "cancelled":
            return envelope, "cancelled"
        if claim.state == "confirmation_invalidated":
            return envelope, "confirmation_invalidated"
        return envelope, "accepted"

    async def _rebind_governed_pending(
        self,
        pending_key: tuple[str, str, str],
        pending: _PendingWorkflow,
        owner: SessionMemoryKey,
    ) -> _PendingWorkflow:
        """Re-key a claimed governed card to the current login before any decision."""
        if (
            self._workflow_engine is None
            or self._human_gate_port is None
            or pending.gate_request_id is None
            or pending.action_digest is None
        ):
            return pending
        request = await self._workflow_engine.rebind_governed_confirmation(
            pending.task_id,
            expected_action_digest=pending.action_digest,
            expected_gate_request_id=pending.gate_request_id,
        )
        if request is None:
            return pending
        effective = replace(
            pending,
            gate_request_id=request.request_id,
            action_digest=request.action_digest,
            request_digest=request.request_digest,
        )
        self._claimed_pending_confirmations[
            _pending_confirmation_claim_key(pending_key, pending)
        ].governed_rebind = effective
        if (
            request.task_id != pending.task_id
            or request.requested_for_ai_user_id != owner.ai_user_id
            or request.requested_session_id != owner.session_id
            or request.requested_tenant_id != owner.tenant_id
            or request.binding_manifest_digest != pending.binding_manifest_digest
        ):
            raise VersionBindingMismatchError("Rebound governed gate does not match the card")
        return effective

    def _release_unstarted_claim(
        self,
        pending_key: tuple[str, str, str],
        pending: _PendingWorkflow,
    ) -> None:
        """Drop a claim that made no decision, so the same card stays claimable."""
        claim_key = _pending_confirmation_claim_key(pending_key, pending)
        with self._pending_confirmation_claim_lock:
            claim = self._claimed_pending_confirmations.get(claim_key)
            if (
                claim is not None
                and claim.state == "processing"
                and claim.governed_rebind is None
                and self._pending_workflows.get(pending_key) is pending
            ):
                self._forget_confirmation_claim(claim_key, claim)

    def _retain_governed_terminal(
        self,
        key: tuple[str, str, str],
        pending: _PendingWorkflow,
        result: GovernedTerminalResult,
    ) -> None:
        """Keep all four durable outcomes repairable; never replay their send."""
        with self._pending_confirmation_claim_lock:
            claim = self._claimed_pending_confirmations[
                _pending_confirmation_claim_key(key, pending)
            ]
            state = result.output["state"]
            presend_failure = isinstance(result, GovernedConfirmationFailureResult)
            claim.state = (
                "cancelled"
                if state == "CANCELLED"
                else "confirmation_invalidated"
                if state == "EXPIRED" or presend_failure
                else "completed"
            )
            claim.error_code = (
                None
                if state == "CANCELLED"
                else "confirm_required"
                if state == "EXPIRED"
                else result.error_code
            )
            claim.governed_terminal = result
            if state in {"CANCELLED", "EXPIRED"}:
                claim.cleanup_reason = "cancelled" if state == "CANCELLED" else "expired"
            elif presend_failure:
                claim.cleanup_reason = "exception"
            claim.cleanup_complete = False
            claim.pending = pending
            remember = state == "VERIFIED_SUCCESS" and not claim.completion_remembered
        if remember:
            assert pending.owner is not None
            self._session_memory.remember_completed(
                pending.owner, capability_id=pending.capability_id
            )
            claim.completion_remembered = True

    async def _record_governed_response(self, pending: _PendingWorkflow) -> None:
        assert pending.owner is not None
        event = TraceEvent(
            trace_id=pending.trace_id,
            task_id=pending.task_id,
            session_id=pending.owner.session_id,
            tenant_id=pending.owner.tenant_id,
            ai_user_id=pending.owner.ai_user_id,
            event_type="response_envelope_created",
            status="ok",
        )
        key = (
            "mcp-response:"
            + sha256(
                canonical_object(
                    {
                        "owner": [
                            pending.owner.tenant_id,
                            pending.owner.ai_user_id,
                            pending.owner.session_id,
                        ],
                        "task": pending.task_id,
                        "trace": pending.trace_id,
                        "action": pending.action_digest,
                        "gate": pending.gate_request_id,
                    }
                ).encode("utf-8")
            ).hexdigest()
        )
        await self._trace_port.record_event_once(event, key)

    def _archive_confirmation(
        self,
        key: tuple[str, str, str],
        pending: _PendingWorkflow,
        *,
        cleanup_complete: bool,
    ) -> None:
        with self._pending_confirmation_claim_lock:
            claim = self._claimed_pending_confirmations[
                _pending_confirmation_claim_key(key, pending)
            ]
            if claim.state == "processing":
                claim.state = "completed"
            claim.pending = None
            claim.cleanup_complete = cleanup_complete
            claim.retain_until = self._monotonic_clock() + _CONFIRMATION_TTL_SECONDS

    def _publish_pending_workflow(
        self,
        pending_key: tuple[str, str, str],
        *,
        expected: _PendingWorkflow | None,
        replacement: _PendingWorkflow,
    ) -> bool:
        with self._pending_confirmation_claim_lock:
            if self._pending_workflows.get(pending_key) is not expected:
                return False
            if (
                expected is not None
                and _pending_confirmation_claim_key(pending_key, expected)
                in self._claimed_pending_confirmations
            ):
                return False
            self._pending_workflows[pending_key] = replacement
            return True

    def _compare_and_swap_pending_workflow(
        self,
        pending_key: tuple[str, str, str],
        *,
        expected: _PendingWorkflow,
        replacement: _PendingWorkflow | None,
    ) -> bool:
        with self._pending_confirmation_claim_lock:
            if self._pending_workflows.get(pending_key) is not expected:
                return False
            if replacement is None:
                self._pending_workflows.pop(pending_key, None)
            else:
                self._pending_workflows[pending_key] = replacement
            return True

    async def _build_stale_confirmation_response(
        self,
        *,
        pending: _PendingWorkflow | None,
        session_id: str,
        memory_key: SessionMemoryKey,
        receipt_full: bool = False,
    ) -> ResponseEnvelope:
        task_id = pending.task_id if pending is not None else str(uuid4())
        trace_id = pending.trace_id if pending is not None else str(uuid4())
        envelope = self._response_builder.build_message(
            str(uuid4()),
            task_id,
            session_id,
            "确认请求暂无法受理，请使用卡片上的确认按钮；本次未执行。"
            if receipt_full
            else "确认请求已失效或与当前待确认动作不匹配，本次未执行。",
            "The confirmation request cannot be accepted now; use the card confirm "
            "button. Nothing was executed."
            if receipt_full
            else "The confirmation request is stale or does not match the "
            "current pending action; nothing was executed.",
            trace_id,
            status="waiting_user" if pending is not None else "failed",
        )
        await self._trace_port.record_step(
            trace_id,
            task_id,
            session_id,
            tenant_id=memory_key.tenant_id,
            ai_user_id=memory_key.ai_user_id,
            event_type="response_envelope_created",
            status="ok",
            attributes={"confirmation_status": "stale"},
        )
        return envelope

    async def _record_confirmation_decision(
        self,
        pending: _PendingWorkflow,
        memory_key: SessionMemoryKey,
        decision: Literal["confirmed", "rejected"],
    ) -> None:
        if self._human_gate_port is None or self._workflow_engine is None:
            raise RuntimeError("Confirmation dependencies are unavailable")
        if (
            pending.gate_request_id is None
            or pending.action_digest is None
            or pending.request_digest is None
            or pending.binding_manifest_digest is None
        ):
            raise VersionBindingMismatchError(
                "Pending Workflow has no immutable human gate request"
            )
        resume_bindings = merge_version_bindings(
            (
                pending.intent_version_binding
                if pending.intent_version_binding is not None
                else self._intent_version_binding,
            ),
            await self._workflow_engine.resume_version_bindings(task_id=pending.task_id),
        )
        await self._human_gate_port.assert_task_bindings(
            pending.task_id,
            resume_bindings,
            exact=True,
        )
        current_action_digest = await self._workflow_engine.pending_confirmation_action_digest(
            pending.task_id
        )
        if not compare_digest(
            pending.action_digest,
            current_action_digest,
        ):
            raise VersionBindingMismatchError("Pending Workflow action changed after preview")
        try:
            await self._human_gate_port.record_decision(
                HumanGateDecisionRecord(
                    request_id=pending.gate_request_id,
                    task_id=pending.task_id,
                    decided_by_ai_user_id=memory_key.ai_user_id,
                    decided_session_id=memory_key.session_id,
                    decided_tenant_id=memory_key.tenant_id,
                    decision=decision,
                    request_digest=pending.request_digest,
                    binding_manifest_digest=pending.binding_manifest_digest,
                    decided_at=self._utc_clock(),
                )
            )
        except HumanGateConflictError:
            existing_decision = await self._human_gate_port.get_decision(pending.gate_request_id)
            if existing_decision is None:
                raise _ActionStaleError from None
            raise _ActionAlreadyClaimedError from None

    async def _resume_pending_workflow(
        self,
        *,
        pending_key: tuple[str, str, str],
        pending: _PendingWorkflow,
        effective: _PendingWorkflow,
        session_id: str,
        memory_key: SessionMemoryKey,
    ) -> ResponseEnvelope:
        if self._workflow_engine is None:
            raise RuntimeError("pending Workflow has no configured engine")

        response_id = str(uuid4())
        if self._human_gate_port is not None:
            await self._record_confirmation_decision(effective, memory_key, "confirmed")
        if self._human_gate_port is None:
            exec_result = await self._orchestration.resume_capability(
                task_id=pending.task_id,
                confirmed=True,
            )
        else:
            exec_result = await self._orchestration.resume_capability(
                task_id=pending.task_id,
                confirmed=True,
                expected_action_digest=effective.action_digest,
            )
        terminal = exec_result._governed_terminal
        if isinstance(terminal, GovernedTerminalResult):
            self._claimed_pending_confirmations[
                _pending_confirmation_claim_key(pending_key, pending)
            ].governed_terminal = terminal
        try:
            return await self._complete_resumed_workflow(
                pending_key=pending_key,
                pending=pending,
                session_id=session_id,
                memory_key=memory_key,
                response_id=response_id,
                exec_result=exec_result,
            )
        except GovernedFinalizationError:
            raise
        except Exception as exc:
            if isinstance(terminal, GovernedTerminalResult):
                raise GovernedFinalizationError(terminal) from exc
            raise

    async def _complete_resumed_workflow(
        self,
        *,
        pending_key: tuple[str, str, str],
        pending: _PendingWorkflow,
        session_id: str,
        memory_key: SessionMemoryKey,
        response_id: str,
        exec_result: ExecutionResult,
    ) -> ResponseEnvelope:
        assert self._workflow_engine is not None
        confirmation: ConfirmationPreview | None = None
        requested_at = self._utc_clock()
        gate_expires_at = requested_at + timedelta(seconds=_CONFIRMATION_TTL_SECONDS)
        monotonic_deadline = self._monotonic_clock() + _CONFIRMATION_TTL_SECONDS
        next_gate_request_id = pending.gate_request_id
        next_action_digest = pending.action_digest
        next_request_digest = pending.request_digest
        if exec_result.status == "waiting_user" and self._human_gate_port is not None:
            try:
                if pending.binding_manifest_digest is None:
                    raise VersionBindingMismatchError(
                        "Resumed Workflow has no immutable human gate request"
                    )
                next_gate_request_id = response_id
                next_action_digest = await self._workflow_engine.pending_confirmation_action_digest(
                    pending.task_id
                )
                confirmation = self._orchestration.prepare_confirmation(
                    capability_id=pending.capability_id,
                    arguments={},
                    capability=None,
                )
                next_request_digest = immutable_request_digest(
                    task_id=pending.task_id,
                    action_digest=next_action_digest,
                    preview=confirmation.to_payload(),
                    binding_manifest_digest=pending.binding_manifest_digest,
                )
                persisted_gate = await self._workflow_engine.governed_confirmation(pending.task_id)
                if persisted_gate is not None:
                    next_gate_request_id = persisted_gate.request_id
                    response_id = persisted_gate.request_id
                    next_action_digest = persisted_gate.action_digest
                    next_request_digest = persisted_gate.request_digest
                    gate_expires_at = persisted_gate.expires_at
                    monotonic_deadline = self._monotonic_clock() + max(
                        0,
                        (gate_expires_at - self._utc_clock()).total_seconds(),
                    )
                else:
                    await self._human_gate_port.create_request(
                        HumanGateRequest(
                            request_id=next_gate_request_id,
                            task_id=pending.task_id,
                            requested_for_ai_user_id=memory_key.ai_user_id,
                            requested_session_id=session_id,
                            requested_tenant_id=memory_key.tenant_id,
                            action_digest=next_action_digest,
                            request_digest=next_request_digest,
                            binding_manifest_digest=pending.binding_manifest_digest,
                            requested_at=requested_at,
                            expires_at=requested_at + timedelta(seconds=_CONFIRMATION_TTL_SECONDS),
                        )
                    )
            except (HumanGateConflictError, VersionBindingMismatchError):
                if not self._compare_and_swap_pending_workflow(
                    pending_key,
                    expected=pending,
                    replacement=None,
                ):
                    return self._response_builder.build_failed(
                        response_id,
                        pending.task_id,
                        session_id,
                        "确认状态已变更，本次未继续执行。",
                        "The confirmation state changed; execution did not continue.",
                        pending.trace_id,
                    )
                await self._workflow_engine.discard_checkpoint(pending.task_id)
                return await self._finish_version_binding_failure(
                    response_id=response_id,
                    task_id=pending.task_id,
                    session_id=session_id,
                    trace_id=pending.trace_id,
                    capability_id=pending.capability_id,
                    memory_key=memory_key,
                )
        capability_ref = CapabilityRef(
            capability_id=pending.capability_id,
            capability_type="workflow",
        )
        if exec_result.status == "waiting_user" and confirmation is None:
            confirmation = self._orchestration.prepare_confirmation(
                capability_id=pending.capability_id,
                arguments={},
                capability=None,
            )
        raw_execution = exec_result
        exec_result, business_verification = self._evaluate_required_postconditions(
            exec_result,
            scope=EvaluationScope(
                pending.task_id,
                pending.trace_id,
                session_id,
                memory_key.tenant_id,
                memory_key.ai_user_id,
            ),
            capability_id=pending.capability_id,
            version=(
                pending.projection_snapshot.capability_version
                if pending.projection_snapshot is not None
                else ""
            ),
        )
        envelope = self._build_envelope(
            response_id,
            pending.task_id,
            session_id,
            exec_result,
            pending.trace_id,
            capability_ref,
            projection_snapshot=pending.projection_snapshot,
            confirmation=confirmation,
            business_verification=(
                business_verification if business_verification.rule_id else None
            ),
        )
        claim = self._claimed_pending_confirmations[
            _pending_confirmation_claim_key(pending_key, pending)
        ]
        if claim.governed_terminal is not None:
            claim.response_trace_pending = True
        if exec_result.status == "waiting_user":
            next_pending = _PendingWorkflow(
                task_id=pending.task_id,
                trace_id=pending.trace_id,
                response_id=response_id,
                capability_id=pending.capability_id,
                projection_snapshot=pending.projection_snapshot,
                gate_request_id=next_gate_request_id,
                action_digest=next_action_digest,
                request_digest=next_request_digest,
                binding_manifest_digest=pending.binding_manifest_digest,
                owner=memory_key,
                expires_at=gate_expires_at,
                monotonic_deadline=monotonic_deadline,
                intent_version_binding=pending.intent_version_binding,
            )
            # The processing claim still pins the old pending: write the waiting
            # marks first, then make the next confirmation claimable.
            await self._trace_port.record_step(
                pending.trace_id,
                pending.task_id,
                session_id,
                tenant_id=memory_key.tenant_id,
                ai_user_id=memory_key.ai_user_id,
                event_type="response_envelope_created",
                status="ok",
            )
            await self._task_store.update_status(
                pending.task_id,
                _map_exec_to_task_status(exec_result.status),
                exec_result.error_code,
            )
            self._compare_and_swap_pending_workflow(
                pending_key,
                expected=pending,
                replacement=next_pending,
            )
            return envelope
        governed_result = await self._workflow_engine.finalize_governed_task(
            task_id=pending.task_id,
        )
        if governed_result is None and claim.governed_terminal is not None:
            # A replaced generation is repaired using the captured gate;
            # it cannot fall through to the ordinary Workflow finalizer.
            raise GovernedFinalizationError(claim.governed_terminal)
        owns_pending = (
            self._pending_workflows.get(pending_key) is pending
            if isinstance(governed_result, GovernedTerminalResult)
            else self._compare_and_swap_pending_workflow(
                pending_key,
                expected=pending,
                replacement=None,
            )
        )
        if not owns_pending:
            return envelope
        if isinstance(governed_result, GovernedTerminalResult):
            await self._record_governed_response(pending)
            claim.response_trace_pending = False
            self._retain_governed_terminal(pending_key, pending, governed_result)
            if claim.state in {"cancelled", "confirmation_invalidated"}:
                envelope = self._confirmation_terminal_envelope(
                    task_id=pending.task_id,
                    trace_id=pending.trace_id,
                    session_id=session_id,
                    status=(
                        "cancelled" if claim.state == "cancelled" else "confirmation_invalidated"
                    ),
                )
            self._compare_and_swap_pending_workflow(pending_key, expected=pending, replacement=None)
            return envelope
        await self._trace_port.record_step(
            pending.trace_id,
            pending.task_id,
            session_id,
            tenant_id=memory_key.tenant_id,
            ai_user_id=memory_key.ai_user_id,
            event_type="response_envelope_created",
            status="ok",
        )
        if governed_result is not None:
            if governed_result.status == "completed":
                self._session_memory.remember_completed(
                    memory_key,
                    capability_id=pending.capability_id,
                )
            return envelope
        final_task_status = _map_exec_to_task_status(exec_result.status)
        await self._task_store.update_status(
            pending.task_id,
            final_task_status,
            exec_result.error_code,
        )
        terminal_event = _terminal_event_for_exec_status(exec_result.status)
        if terminal_event is not None:
            await self._trace_port.record_step(
                pending.trace_id,
                pending.task_id,
                session_id,
                tenant_id=memory_key.tenant_id,
                ai_user_id=memory_key.ai_user_id,
                event_type=terminal_event,
                status="ok" if terminal_event == "task_completed" else "failed",
                capability_id=pending.capability_id,
                error_code=exec_result.error_code,
            )

        await self._record_terminal_evaluation(
            trace_id=pending.trace_id,
            task_id=pending.task_id,
            session_id=session_id,
            business_status=cast(TerminalBusinessStatus, exec_result.status),
            error_code=exec_result.error_code,
            raw_execution=raw_execution,
            business_verification=business_verification,
            capability_id=pending.capability_id,
            memory_key=memory_key,
        )
        await self._trace_port.finalize_task_trace(
            pending.trace_id,
            pending.task_id,
            session_id,
            tenant_id=memory_key.tenant_id,
            ai_user_id=memory_key.ai_user_id,
            status=_map_task_to_finalize_status(final_task_status),
            capability_id=pending.capability_id,
            error_code=exec_result.error_code,
        )
        if exec_result.status == "completed":
            self._session_memory.remember_completed(
                memory_key,
                capability_id=pending.capability_id,
            )
        return envelope

    async def _visible_capabilities(
        self,
        *,
        ai_user_id: str,
        request_context: RequestOrgContext,
    ) -> tuple[tuple[CapabilitySpec, ...], int] | None:
        """Return this request's policy-previewed active snapshot, or None if invalid."""
        try:
            snapshot = await self._capability_registry.list(status="active")
        except ValidationError:
            return None
        active = [item for item in snapshot if item.status == "active"]
        if any(not is_safe_capability_id(item.capability_id) for item in active):
            return None
        visible: list[CapabilitySpec] = []
        for item in sorted(active, key=lambda capability: capability.capability_id):
            try:
                visibility = await self._candidate_policy.preview_capability(
                    ai_user_id=ai_user_id,
                    capability_id=item.capability_id,
                    request_context=request_context.model_copy(deep=True),
                )
            except Exception:
                return None
            if visibility == "exclude":
                continue
            if visibility != "defer":
                return None
            visible.append(item.model_copy(deep=True))
        return tuple(visible), len(active)

    async def _record_intent_parsed(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
        *,
        memory_key: SessionMemoryKey,
        status: TraceEventStatus,
        attributes: dict[str, Any],
        error_code: ErrorCode | None = None,
    ) -> None:
        await self._trace_port.record_step(
            trace_id,
            task_id,
            session_id,
            tenant_id=memory_key.tenant_id,
            ai_user_id=memory_key.ai_user_id,
            event_type="intent_parsed",
            status=status,
            error_code=error_code,
            attributes=attributes,
        )

    async def _finish_candidate_failure(
        self,
        response_id: str,
        task_id: str,
        session_id: str,
        trace_id: str,
        *,
        error_code: ErrorCode,
        reason: str,
        selection: CapabilityCandidateSet,
        memory_key: SessionMemoryKey,
    ) -> ResponseEnvelope:
        _record_chat_stage(trace_id, "fixed_reply", "blocked", reason=reason)
        await self._record_intent_parsed(
            trace_id,
            task_id,
            session_id,
            memory_key=memory_key,
            status="failed",
            error_code=error_code,
            attributes={**selection.trace_attributes(), "reason": reason},
        )
        message, fallback_text = _CANDIDATE_FAILURE_MESSAGES[error_code]
        await self._task_store.update_status(task_id, "failed", error_code)
        envelope = self._response_builder.build_message(
            response_id,
            task_id,
            session_id,
            message,
            fallback_text,
            trace_id,
            status="failed",
        )
        await self._trace_port.record_step(
            trace_id,
            task_id,
            session_id,
            tenant_id=memory_key.tenant_id,
            ai_user_id=memory_key.ai_user_id,
            event_type="response_envelope_created",
            status="ok",
        )
        await self._trace_port.record_step(
            trace_id,
            task_id,
            session_id,
            tenant_id=memory_key.tenant_id,
            ai_user_id=memory_key.ai_user_id,
            event_type="task_failed",
            status="failed",
            error_code=error_code,
        )
        await self._record_terminal_evaluation(
            trace_id=trace_id,
            task_id=task_id,
            session_id=session_id,
            business_status="failed",
            error_code=error_code,
            memory_key=memory_key,
        )
        await self._trace_port.finalize_task_trace(
            trace_id,
            task_id,
            session_id,
            tenant_id=memory_key.tenant_id,
            ai_user_id=memory_key.ai_user_id,
            status="failed",
            error_code=error_code,
        )
        return envelope

    async def _finish_version_binding_failure(
        self,
        *,
        response_id: str,
        task_id: str,
        session_id: str,
        trace_id: str,
        capability_id: str,
        memory_key: SessionMemoryKey,
        candidate_selection: CapabilityCandidateSet | None = None,
    ) -> ResponseEnvelope:
        error_code: ErrorCode = "internal_error"
        await self._task_store.update_status(task_id, "failed", error_code)
        envelope = self._response_builder.build_message(
            response_id,
            task_id,
            session_id,
            "任务绑定的执行版本已不可用，本次未执行。",
            "The Task's locked execution version is unavailable; nothing was executed.",
            trace_id,
            status="failed",
        )
        envelope = _append_partial_candidate_notice(envelope, candidate_selection)
        await self._trace_port.record_step(
            trace_id,
            task_id,
            session_id,
            tenant_id=memory_key.tenant_id,
            ai_user_id=memory_key.ai_user_id,
            event_type="response_envelope_created",
            status="ok",
        )
        await self._trace_port.record_step(
            trace_id,
            task_id,
            session_id,
            tenant_id=memory_key.tenant_id,
            ai_user_id=memory_key.ai_user_id,
            event_type="task_failed",
            status="failed",
            capability_id=capability_id,
            error_code=error_code,
        )
        await self._record_terminal_evaluation(
            trace_id=trace_id,
            task_id=task_id,
            session_id=session_id,
            business_status="failed",
            error_code=error_code,
            capability_id=capability_id,
            memory_key=memory_key,
        )
        await self._trace_port.finalize_task_trace(
            trace_id,
            task_id,
            session_id,
            tenant_id=memory_key.tenant_id,
            ai_user_id=memory_key.ai_user_id,
            status="failed",
            capability_id=capability_id,
            error_code=error_code,
        )
        return envelope

    async def _finish_intent_failure(
        self,
        response_id: str,
        task_id: str,
        session_id: str,
        trace_id: str,
        *,
        reason: IntentFailureReason,
        memory_key: SessionMemoryKey,
        error_code: ErrorCode = "internal_error",
        diagnostic: BrowserGatewayDiagnostic | None = None,
    ) -> ResponseEnvelope:
        _record_chat_stage(trace_id, "fixed_reply", "blocked", reason=reason)
        message, fallback_text = _intent_failure_message(reason)
        await self._task_store.update_status(task_id, "failed", error_code)
        envelope = self._response_builder.build_message(
            response_id,
            task_id,
            session_id,
            message,
            fallback_text,
            trace_id,
            status="failed",
        )
        if diagnostic is not None:
            envelope = envelope.model_copy(
                update={
                    "data": {
                        "error_code": error_code,
                        **diagnostic.attributes(),
                    }
                }
            )
        await self._trace_port.record_step(
            trace_id,
            task_id,
            session_id,
            tenant_id=memory_key.tenant_id,
            ai_user_id=memory_key.ai_user_id,
            event_type="response_envelope_created",
            status="ok",
        )
        await self._trace_port.record_step(
            trace_id,
            task_id,
            session_id,
            tenant_id=memory_key.tenant_id,
            ai_user_id=memory_key.ai_user_id,
            event_type="task_failed",
            status="failed",
            error_code=error_code,
            attributes=diagnostic.attributes() if diagnostic is not None else None,
        )
        await self._record_terminal_evaluation(
            trace_id=trace_id,
            task_id=task_id,
            session_id=session_id,
            business_status="failed",
            error_code=error_code,
            memory_key=memory_key,
        )
        await self._trace_port.finalize_task_trace(
            trace_id,
            task_id,
            session_id,
            tenant_id=memory_key.tenant_id,
            ai_user_id=memory_key.ai_user_id,
            status="failed",
            error_code=error_code,
        )
        return envelope

    async def _fail_document_request(
        self,
        request: _DocumentRequest,
        reason: str,
    ) -> ResponseEnvelope:
        result = document_failure(
            reason,
            "adapter_timeout" if reason == "document_timeout" else "internal_error",
        )
        try:
            async with asyncio.timeout(5):
                return await self._finish_document_qa(request, result)
        except Exception:
            logging.getLogger("eternalai.document_qa").warning(
                "document_qa stage=finalize outcome=failed reason=store_unavailable",
            )
        assert request.context is not None
        response_id, task_id, session_id, trace_id, _ = request.context
        return self._response_builder.build_message(
            response_id,
            task_id,
            session_id,
            result.message,
            result.message,
            trace_id,
            status="failed",
            data={"error_code": result.error_code},
        )

    async def _finish_document_qa(
        self,
        request: _DocumentRequest,
        result: DocumentQAResult,
    ) -> ResponseEnvelope:
        assert request.context is not None
        response_id, task_id, session_id, trace_id, memory_key = request.context
        envelope = self._response_builder.build_message(
            response_id,
            task_id,
            session_id,
            result.message,
            result.message,
            trace_id,
            status="failed" if result.outcome == "failed" else "completed",
            data={
                "kind": "fixed_document_qa",
                "outcome": result.outcome,
                "error_code": result.error_code,
            },
        )
        if result.outcome != "failed" and (
            envelope.message != result.message or envelope.status != "completed"
        ):
            result = document_failure("document_answer_invalid", "adapter_payload_invalid")
            envelope = self._response_builder.build_message(
                response_id,
                task_id,
                session_id,
                result.message,
                result.message,
                trace_id,
                status="failed",
                data={"error_code": result.error_code},
            )
        failed = result.outcome == "failed"
        await self._task_store.update_status(
            task_id,
            "failed" if failed else "completed",
            result.error_code,
        )
        await self._trace_port.record_step(
            trace_id,
            task_id,
            session_id,
            tenant_id=memory_key.tenant_id,
            ai_user_id=memory_key.ai_user_id,
            event_type="response_envelope_created",
            status="ok",
            attributes={
                "kind": "fixed_document_qa",
                "outcome": result.outcome,
                "reason": result.reason,
                "document_version": result.version,
            },
        )
        await self._trace_port.record_step(
            trace_id,
            task_id,
            session_id,
            tenant_id=memory_key.tenant_id,
            ai_user_id=memory_key.ai_user_id,
            event_type="task_failed" if failed else "task_completed",
            status="failed" if failed else "ok",
            error_code=result.error_code,
        )
        await self._record_terminal_evaluation(
            trace_id=trace_id,
            task_id=task_id,
            session_id=session_id,
            memory_key=memory_key,
            business_status="failed" if failed else "completed",
            error_code=result.error_code,
        )
        await self._trace_port.finalize_task_trace(
            trace_id,
            task_id,
            session_id,
            tenant_id=memory_key.tenant_id,
            ai_user_id=memory_key.ai_user_id,
            status="failed" if failed else "ok",
            error_code=result.error_code,
        )
        return envelope

    async def _finish_no_capability_found(
        self,
        response_id: str,
        task_id: str,
        session_id: str,
        trace_id: str,
        reason: str,
        *,
        capabilities: Sequence[CapabilitySpec],
        memory_key: SessionMemoryKey,
    ) -> ResponseEnvelope:
        _record_chat_stage(trace_id, "fixed_reply", "blocked", reason=reason)
        message, fallback_text = self._semantic_knowledge.no_capability_guidance(capabilities)
        await self._task_store.update_status(
            task_id,
            "no_capability_found",
            "capability_not_found",
        )
        await self._trace_port.record_step(
            trace_id,
            task_id,
            session_id,
            tenant_id=memory_key.tenant_id,
            ai_user_id=memory_key.ai_user_id,
            event_type="no_capability_found",
            status="blocked",
            error_code="capability_not_found",
            attributes={"reason": reason},
        )
        envelope = self._response_builder.build_no_capability_found(
            response_id,
            task_id,
            session_id,
            message,
            fallback_text,
            trace_id,
        )
        await self._trace_port.record_step(
            trace_id,
            task_id,
            session_id,
            tenant_id=memory_key.tenant_id,
            ai_user_id=memory_key.ai_user_id,
            event_type="response_envelope_created",
            status="ok",
        )
        await self._trace_port.record_step(
            trace_id,
            task_id,
            session_id,
            tenant_id=memory_key.tenant_id,
            ai_user_id=memory_key.ai_user_id,
            event_type="task_failed",
            status="failed",
            error_code="capability_not_found",
        )
        await self._record_terminal_evaluation(
            trace_id=trace_id,
            task_id=task_id,
            session_id=session_id,
            business_status="no_capability_found",
            error_code="capability_not_found",
            memory_key=memory_key,
        )
        await self._trace_port.finalize_task_trace(
            trace_id,
            task_id,
            session_id,
            tenant_id=memory_key.tenant_id,
            ai_user_id=memory_key.ai_user_id,
            status="blocked",
            error_code="capability_not_found",
        )
        return envelope

    def _evaluate_required_postconditions(
        self,
        execution: ExecutionResult,
        *,
        scope: EvaluationScope,
        capability_id: str,
        version: str,
    ) -> tuple[ExecutionResult, BusinessVerification]:
        return evaluate_required_postconditions(
            execution,
            scope=scope,
            capability_id=capability_id,
            version=version,
            evaluator=self._overview_evaluator,
        )

    async def _record_terminal_evaluation(
        self,
        *,
        trace_id: str,
        task_id: str,
        session_id: str,
        memory_key: SessionMemoryKey,
        business_status: TerminalBusinessStatus,
        error_code: ErrorCode | None,
        capability_id: str | None = None,
        raw_execution: ExecutionResult | None = None,
        business_verification: BusinessVerification | None = None,
    ) -> None:
        status, attributes = terminal_evaluation_attributes(
            self._evaluator,
            business_status=business_status,
            error_code=error_code,
            capability_id=capability_id,
            raw_execution=raw_execution,
            business_verification=business_verification,
        )
        await self._trace_port.record_step(
            trace_id,
            task_id,
            session_id,
            tenant_id=memory_key.tenant_id,
            ai_user_id=memory_key.ai_user_id,
            event_type="evaluation_recorded",
            status=status,
            capability_id=capability_id,
            error_code=error_code,
            attributes=attributes,
        )

    def _build_envelope(
        self,
        response_id: str,
        task_id: str,
        session_id: str,
        exec_result: ExecutionResult,
        trace_id: str,
        capability_ref: CapabilityRef,
        *,
        capability: CapabilitySpec | None = None,
        projection_snapshot: ProjectionContractSnapshot | None = None,
        confirmation: ConfirmationPreview | None = None,
        business_verification: BusinessVerification | None = None,
    ) -> ResponseEnvelope:
        return self._orchestration.build_response(
            context=AgentResponseContext(
                response_id=response_id,
                task_id=task_id,
                session_id=session_id,
                trace_id=trace_id,
                capability_id=capability_ref.capability_id,
            ),
            execution=exec_result,
            projection=projection_snapshot,
            confirmation=confirmation,
            business_verification=business_verification,
        )


def _assert_manifest_projection_source(
    manifest: TaskVersionBindingManifest,
    capability: CapabilitySpec,
    snapshot: ProjectionContractSnapshot,
    expected_binding: VersionBinding,
) -> None:
    if not snapshot.matches(capability):
        raise VersionBindingMismatchError(
            "Manifest projection source differs from the selected capability snapshot"
        )
    resource_type = "workflow" if capability.type == "workflow" else "tool"
    if (
        expected_binding.resource_type != resource_type
        or expected_binding.resource_id != capability.capability_id
        or expected_binding.version != capability.version
    ):
        raise VersionBindingMismatchError(
            "Expected projection binding differs from the selected capability"
        )
    matching = tuple(
        binding
        for binding in manifest.bindings
        if binding.resource_type == resource_type
        and binding.resource_id == capability.capability_id
    )
    if len(matching) != 1 or matching[0] != expected_binding:
        raise VersionBindingMismatchError(
            "Projection contract differs from the Task version binding"
        )


def _map_exec_to_task_status(status: ExecutionStatus) -> TaskStatus:
    if status == "completed":
        return "completed"
    if status == "no_capability_found":
        return "no_capability_found"
    if status == "waiting_user":
        return "waiting_user"
    return "failed"


def _is_explicit_workflow_confirmation(
    message: str,
    pending: _PendingWorkflow,
) -> bool:
    normalized = _normalized_confirmation_text(message)
    if normalized in {"确认", "confirm"}:
        return True
    identifiers: tuple[str, ...]
    if pending.gate_request_id is not None:
        identifiers = (pending.gate_request_id.casefold(),)
    else:
        identifiers = (pending.task_id.casefold(), pending.response_id.casefold())
    return any(
        normalized
        in {
            f"确认 {identifier}",
            f"confirm {identifier}",
            f"用户确认 {identifier}",
        }
        for identifier in identifiers
    )


def _normalized_confirmation_text(message: str) -> str:
    return " ".join(message.strip().casefold().split())


def _is_confirmation_text(message: str) -> bool:
    """Bare confirmation or a card reference, with or without a pending item."""
    return _normalized_confirmation_text(message) in {
        "确认",
        "confirm",
    } or _is_stale_workflow_confirmation_message(message)


def _is_stale_workflow_confirmation_message(message: str) -> bool:
    normalized = _normalized_confirmation_text(message)
    for prefix in ("确认 ", "confirm ", "用户确认 "):
        if not normalized.startswith(prefix):
            continue
        identifier = normalized.removeprefix(prefix)
        try:
            UUID(identifier)
        except ValueError:
            return False
        return True
    return False


def _pending_workflow_key(tenant_id: str, session_id: str, ai_user_id: str) -> tuple[str, str, str]:
    return tenant_id, session_id, ai_user_id


def _mcp_route_error(code: str) -> ErrorCode:
    """Same mapping as the Gateway's governed route failures."""
    identity_codes: dict[str, ErrorCode] = {
        "mcp_identity_unbound": "identity_unbound",
        "mcp_identity_pending": "identity_unbound",
        "mcp_identity_expired": "identity_expired",
        "mcp_identity_revoked": "identity_revoked",
    }
    return identity_codes.get(code, "policy_denied")


def _owner_key(owner: ConfirmationOwner) -> SessionMemoryKey:
    return SessionMemoryKey(
        tenant_id=owner.tenant_id,
        session_id=owner.session_id,
        ai_user_id=owner.ai_user_id,
    )


def _pending_confirmation_claim_key(
    pending_key: tuple[str, str, str],
    pending: _PendingWorkflow,
) -> tuple[str, str, str, str]:
    return (*pending_key, pending.gate_request_id or pending.response_id)


def _terminal_event_for_exec_status(status: ExecutionStatus) -> TraceEventType | None:
    if status == "completed":
        return "task_completed"
    if status == "waiting_user":
        return None
    return "task_failed"


def _map_task_to_finalize_status(status: TaskStatus) -> TraceEventStatus:
    if status in {"completed", "waiting_user"}:
        return "ok"
    if status in {"no_capability_found", "cancelled"}:
        return "blocked"
    return "failed"


def _optional_str_argument(arguments: dict[str, Any], key: str) -> str | None:
    value = arguments.get(key)
    if value is None:
        return None
    return str(value)


def _append_partial_candidate_notice(
    envelope: ResponseEnvelope, selection: CapabilityCandidateSet | None
) -> ResponseEnvelope:
    if selection is None or selection.omitted_count == 0:
        return envelope
    return envelope.model_copy(
        update={
            "message": f"{envelope.message}\n{_PARTIAL_CANDIDATE_NOTICE[0]}",
            "fallback_text": f"{envelope.fallback_text}\n{_PARTIAL_CANDIDATE_NOTICE[1]}",
        }
    )


_INTENT_TAG_ADAPTER = TypeAdapter(CapabilityIntentTag)


def _resolve_candidate_binding(
    bindings: tuple[CandidateBinding, ...],
    intent: CapabilityRef,
) -> (
    tuple[CandidateBinding, Literal["exact_id", "unique_intent_tag"]]
    | Literal["no_unique_active_candidate"]
    | None
):
    """Bind a model selector to this request's admitted candidates only.

    Exact IDs win; a tag must map uniquely inside the admitted bindings. A tag
    collision retains the frozen no-match outcome; outside references and
    type/target contradictions never get rescued from the full Registry.
    """
    exact = [item for item in bindings if item.capability_id == intent.capability_id]
    if exact:
        binding: CandidateBinding = exact[0]
        rule: Literal["exact_id", "unique_intent_tag"] = "exact_id"
    else:
        try:
            selector = _INTENT_TAG_ADAPTER.validate_python(intent.capability_id)
        except ValidationError:
            return None
        tagged = [item for item in bindings if selector and selector in item.intent_tags]
        if len(tagged) > 1:
            return "no_unique_active_candidate"
        if len(tagged) != 1:
            return None
        binding, rule = tagged[0], "unique_intent_tag"
    if intent.target_system is not None and binding.target_system != intent.target_system:
        return None
    if intent.capability_type is not None and binding.capability_type != intent.capability_type:
        return None
    return binding, rule


def _selection_matches_binding(capability: CapabilitySpec, binding: CandidateBinding) -> bool:
    return (
        capability.status == "active"
        and capability.capability_id == binding.capability_id
        and capability.version == binding.version
        and capability_fingerprint(capability) == binding.fingerprint
    )


def _intent_trace_attributes(
    intent: CapabilityRef | None,
    failure_reason: IntentFailureReason | None,
    structured_output_error_code: StructuredOutputErrorCode | None,
    validation_error_path: str | None = None,
    validation_error_type: str | None = None,
    argument_keys: tuple[str, ...] = (),
) -> dict[str, Any]:
    if intent is None:
        attributes: dict[str, Any] = {
            "result": "invalid",
            "reason": failure_reason or "schema_invalid",
        }
        if structured_output_error_code is not None:
            attributes["structured_output_error_code"] = structured_output_error_code
        if validation_error_path is not None:
            attributes["error_path"] = validation_error_path
        if validation_error_type is not None:
            attributes["error_type"] = validation_error_type
        if validation_error_path is not None or validation_error_type is not None:
            attributes["argument_keys"] = list(argument_keys)
        return attributes
    attributes = {
        "result": "valid",
        "intent_fingerprint": _intent_fingerprint(intent.capability_id),
    }
    if intent.target_system is not None:
        attributes["target_system"] = intent.target_system
    if intent.capability_type is not None:
        attributes["capability_type"] = intent.capability_type
    return attributes


def _intent_failure_message(reason: IntentFailureReason) -> tuple[str, str]:
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


def _intent_fingerprint(selector: str) -> str:
    return sha256(selector.encode("utf-8")).hexdigest()


__all__ = ("RuntimeImpl",)
