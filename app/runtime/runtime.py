"""Minimal Runtime main chain implementation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from hmac import compare_digest
from threading import Lock
from time import monotonic
from typing import Any, Literal
from uuid import UUID, uuid4

from app.contracts.sdui.models import UserAction
from app.evaluator import (
    EvaluationConclusion,
    TerminalBusinessStatus,
    TerminalEvaluator,
)
from app.infra.sdui.response_envelope_builder import ResponseEnvelopeBuilder
from app.knowledge import BasicKnowledge
from app.memory import SessionMemory, SessionMemoryKey
from app.ports.agent_orchestration import (
    AgentOrchestrationPort,
    AgentResponseContext,
    ConfirmationPreview,
    OrchestrationContractError,
)
from app.ports.auth import Principal
from app.ports.capability_gateway import (
    ErrorCode,
    ExecutionResult,
    ExecutionStatus,
    RequestOrgContext,
)
from app.ports.capability_registry import CapabilityRegistryPort, CapabilitySpec
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
from app.ports.response_envelope import ResponseEnvelope
from app.ports.response_projection_contract import ProjectionContractSnapshot
from app.ports.runtime import UserActionOutcome
from app.ports.structured_output import StructuredOutputErrorCode, StructuredOutputPort
from app.ports.task_store import (
    SessionRecord,
    SessionStorePort,
    TaskEventRecord,
    TaskRecord,
    TaskStatus,
    TaskStorePort,
)
from app.ports.trace import TraceEventStatus, TraceEventType, TracePort
from app.ports.workflow_engine import WorkflowEnginePort
from app.runtime.intent_router import IntentFailureReason, IntentRouter
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


_CONFIRMATION_TTL_SECONDS = 600
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


class _ActionAlreadyClaimedError(RuntimeError):
    pass


class _ActionStaleError(RuntimeError):
    pass


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
        workflow_engine: WorkflowEnginePort | None = None,
        session_memory: SessionMemory | None = None,
        semantic_knowledge: BasicKnowledge | None = None,
        evaluator: TerminalEvaluator | None = None,
        human_gate_port: HumanGatePort | None = None,
        utc_clock: Callable[[], datetime] | None = None,
        monotonic_clock: Callable[[], float] = monotonic,
        confirmation_claim_limit: int = 4096,
    ) -> None:
        self._task_store = task_store
        self._session_store = session_store
        self._capability_registry = capability_registry
        self._orchestration = orchestration
        self._trace_port = trace_port
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
        self._human_gate_port = human_gate_port
        self._intent_version_binding = self._intent_router.version_binding()
        self._pending_workflows: dict[tuple[str, str], _PendingWorkflow] = {}
        self._pending_confirmation_claim_lock = Lock()
        self._utc_clock = utc_clock or (lambda: datetime.now(UTC))
        self._monotonic_clock = monotonic_clock
        if confirmation_claim_limit < 1:
            raise ValueError("confirmation_claim_limit must be positive")
        self._confirmation_claim_limit = confirmation_claim_limit
        self._claimed_pending_confirmations: dict[tuple[str, str, str], _ConfirmationClaim] = {}
        self._confirmation_references: dict[tuple[str, str, str, str], tuple[str, str, str]] = {}

    async def handle_user_message(
        self,
        channel: Literal["web", "cli", "api", "mock"],
        principal: Principal,
        session_id: str,
        message: str,
        client_capabilities: dict[str, Any],
    ) -> ResponseEnvelope:
        ai_user_id = principal.ai_user_id
        memory_key = SessionMemoryKey(
            tenant_id=principal.org_ctx.tenant_id,
            session_id=session_id,
            ai_user_id=ai_user_id,
        )
        await self._expire_pending_confirmations()
        pending_key = _pending_workflow_key(session_id, ai_user_id)
        pending = self._pending_workflows.get(pending_key)
        if pending is not None and pending.owner == memory_key:
            if _is_explicit_workflow_confirmation(message, pending):
                outcome = self._claim_confirmation(pending_key, pending, memory_key)
                if outcome is not None:
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
            reference = message.strip().split()[-1]
            cached = self._lookup_confirmation_outcome(memory_key, reference)
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

        task_id = str(uuid4())
        trace_id = str(uuid4())
        response_id = str(uuid4())

        existing = await self._session_store.get_session(session_id)
        if existing is None:
            await self._session_store.create_session(SessionRecord(session_id=session_id))

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

        capability_snapshot = await self._capability_registry.list(status="active")
        intent_result = await self._intent_router.parse(
            message,
            trace_metadata={
                "trace_id": trace_id,
                "task_id": task_id,
            },
            capabilities=tuple(capability_snapshot),
            memory_summaries=self._session_memory.recall(memory_key),
        )
        capability_ref = intent_result.capability_ref
        parse_ok = intent_result.failure_reason is None and (
            intent_result.match == "none"
            or (intent_result.match == "capability" and capability_ref is not None)
        )

        await self._trace_port.record_step(
            trace_id,
            task_id,
            session_id,
            tenant_id=memory_key.tenant_id,
            ai_user_id=memory_key.ai_user_id,
            event_type="intent_parsed",
            status="ok" if parse_ok else "failed",
            attributes={"result": "valid", "match": "none"}
            if parse_ok and intent_result.match == "none"
            else _intent_trace_attributes(
                capability_ref,
                intent_result.failure_reason,
                intent_result.structured_output_error_code,
                intent_result.validation_error_path,
                intent_result.validation_error_type,
                intent_result.argument_keys,
            ),
        )

        if parse_ok and intent_result.match == "none":
            return await self._finish_no_capability_found(
                response_id,
                task_id,
                session_id,
                trace_id,
                reason="no_matching_capability",
                memory_key=memory_key,
            )

        if not parse_ok or capability_ref is None:
            # With no active capability at all, the honest answer is that the
            # function is not integrated yet — not that the parse blew up. Only
            # a parse failure against a non-empty catalogue is an internal fault.
            if not capability_snapshot:
                return await self._finish_no_capability_found(
                    response_id,
                    task_id,
                    session_id,
                    trace_id,
                    reason="no_active_capability_registered",
                    memory_key=memory_key,
                )
            return await self._finish_intent_failure(
                response_id,
                task_id,
                session_id,
                trace_id,
                reason=intent_result.failure_reason or "schema_invalid",
                memory_key=memory_key,
            )

        intent_selector = capability_ref.capability_id
        selection = await self._orchestration.select_capability(
            capability_id=capability_ref.capability_id,
            target_system=capability_ref.target_system,
            capability_type=capability_ref.capability_type,
        )
        if selection is None:
            return await self._finish_no_capability_found(
                response_id,
                task_id,
                session_id,
                trace_id,
                reason="no_unique_active_candidate",
                memory_key=memory_key,
            )
        selected_capability = selection.capability.model_copy(deep=True)
        projection_snapshot = ProjectionContractSnapshot.from_capability(selected_capability)
        capability_ref = capability_ref.model_copy(
            update={"capability_id": selected_capability.capability_id}
        )

        await self._task_store.append_event(
            task_id,
            TaskEventRecord(
                event_id=str(uuid4()),
                task_id=task_id,
                event_type="capability_selected",
                timestamp=datetime.now(UTC),
                payload={
                    "capability_id": selected_capability.capability_id,
                    "selection_rule": selection.rule,
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
                "selection_rule": selection.rule,
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
        confirmation: ConfirmationPreview | None = None
        gate_request_id: str | None = None
        action_digest: str | None = None
        request_digest: str | None = None
        gate_manifest_digest: str | None = None
        workflow_waiting = (
            selected_capability.type == "workflow" and exec_result.status == "waiting_user"
        )
        requested_at = self._utc_clock()
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
                action_digest = self._workflow_engine.pending_confirmation_action_digest(task_id)
                confirmation = self._orchestration.prepare_confirmation(
                    capability_id=selected_capability.capability_id,
                    arguments=capability_ref.arguments,
                    capability=selected_capability,
                )
                request_digest = immutable_request_digest(
                    task_id=task_id,
                    action_digest=action_digest,
                    preview=confirmation.to_payload(),
                    binding_manifest_digest=gate_manifest_digest,
                )
                await self._human_gate_port.create_request(
                    HumanGateRequest(
                        request_id=gate_request_id,
                        task_id=task_id,
                        requested_for_ai_user_id=ai_user_id,
                        requested_session_id=session_id,
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
                    self._workflow_engine.discard_checkpoint(task_id)
                exec_result = ExecutionResult(
                    status="failed",
                    error_code="internal_error",
                    trace_id=trace_id,
                )
                workflow_waiting = False
                confirmation = None
        if workflow_waiting:
            replacement = _PendingWorkflow(
                task_id=task_id,
                trace_id=trace_id,
                response_id=response_id,
                capability_id=capability_ref.capability_id,
                projection_snapshot=projection_snapshot,
                gate_request_id=gate_request_id,
                action_digest=action_digest,
                request_digest=request_digest,
                binding_manifest_digest=gate_manifest_digest,
                owner=memory_key,
                expires_at=requested_at + timedelta(seconds=_CONFIRMATION_TTL_SECONDS),
                monotonic_deadline=monotonic_deadline,
            )
            await self._expire_pending_confirmations()
            if pending is not None and self._pending_workflows.get(pending_key) is None:
                pending = None
            if not self._publish_pending_workflow(
                pending_key,
                expected=pending,
                replacement=replacement,
            ):
                if self._workflow_engine is not None:
                    self._workflow_engine.discard_checkpoint(task_id)
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
                    business_status=exec_result.status,
                    error_code=exec_result.error_code,
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

    async def handle_user_action(
        self,
        channel: Literal["web", "cli", "api", "mock"],
        principal: Principal,
        session_id: str,
        action: UserAction,
    ) -> ResponseEnvelope:
        del channel
        action_trace_id = str(uuid4())
        action_task_id = str(uuid4())
        memory_key = SessionMemoryKey(
            tenant_id=principal.org_ctx.tenant_id,
            session_id=session_id,
            ai_user_id=principal.ai_user_id,
        )
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

        pending_key = _pending_workflow_key(session_id, principal.ai_user_id)
        if self._human_gate_port is None:
            return await self._finish_user_action_attempt(
                action_trace_id=action_trace_id,
                action_task_id=action_task_id,
                session_id=session_id,
                memory_key=memory_key,
                outcome="action_gate_unavailable",
            )

        await self._expire_pending_confirmations()
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
                "结构化操作未被受理，本次未执行。",
                "The structured action was not accepted; nothing was executed.",
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
        key: tuple[str, str, str],
        claim: _ConfirmationClaim,
    ) -> None:
        del self._claimed_pending_confirmations[key]
        self._confirmation_references.pop(
            (claim.tenant_id, key[0], key[1], claim.response_id),
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
        key: tuple[str, str],
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
        self._confirmation_references[(owner.tenant_id, key[0], key[1], claim.response_id)] = (
            claim_key
        )
        return claim

    def _claim_confirmation(
        self,
        key: tuple[str, str],
        pending: _PendingWorkflow,
        owner: SessionMemoryKey,
    ) -> UserActionOutcome | None:
        with self._pending_confirmation_claim_lock:
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
        pending_key: tuple[str, str],
        pending: _PendingWorkflow,
        status: Literal["cancelled", "confirmation_invalidated"],
        reason: Literal["cancelled", "expired", "exception"],
        error_code: ErrorCode | None,
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
                if not claim.cleanup_complete:
                    raise RuntimeError("Confirmation cleanup is incomplete")
                return self._confirmation_terminal_envelope(
                    task_id=pending.task_id,
                    trace_id=pending.trace_id,
                    session_id=owner.session_id,
                    status=status,
                )
            claim.state = status
            claim.error_code = error_code
            claim.pending = None
            claim.retain_until = self._monotonic_clock() + _CONFIRMATION_TTL_SECONDS
            owns_pending = self._pending_workflows.get(pending_key) is pending
            if owns_pending:
                del self._pending_workflows[pending_key]
            else:
                # A CAS loser owns no cleanup work; the winner must stay untouched.
                claim.cleanup_complete = True
        envelope = self._confirmation_terminal_envelope(
            task_id=pending.task_id,
            trace_id=pending.trace_id,
            session_id=owner.session_id,
            status=status,
        )
        if not owns_pending:
            return envelope
        # No await between CAS removal and discarding this captured checkpoint.
        if self._workflow_engine is not None:
            self._workflow_engine.discard_checkpoint(pending.task_id)
        await self._finish_confirmation_terminal(
            pending=pending,
            status=status,
            reason=reason,
            error_code=error_code,
        )
        with self._pending_confirmation_claim_lock:
            claim.cleanup_complete = True
        return envelope

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
        if record is not None and record.status in {
            "completed",
            "failed",
            "no_capability_found",
            "cancelled",
            "confirmation_invalidated",
        }:
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

    async def _expire_pending_confirmations(self) -> None:
        for key, pending in list(self._pending_workflows.items()):
            with self._pending_confirmation_claim_lock:
                if (
                    not self._confirmation_expired(pending)
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
        pending_key: tuple[str, str],
        pending: _PendingWorkflow,
        session_id: str,
        memory_key: SessionMemoryKey,
        action_type: Literal["confirm", "reject", "cancel"],
    ) -> tuple[ResponseEnvelope, UserActionOutcome]:
        try:
            if action_type != "confirm":
                await self._record_confirmation_decision(pending, memory_key, "rejected")
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
                session_id=session_id,
                memory_key=memory_key,
            )
        except asyncio.CancelledError:
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
            if self._compare_and_swap_pending_workflow(
                pending_key, expected=pending, replacement=None
            ):
                if self._workflow_engine is not None:
                    self._workflow_engine.discard_checkpoint(pending.task_id)
            self._archive_confirmation(pending_key, pending, cleanup_complete=False)
            return self._response_builder.build_failed(
                str(uuid4()),
                pending.task_id,
                session_id,
                "结构化操作未被受理，本次未执行。",
                "The structured action was not accepted; nothing was executed.",
                pending.trace_id,
            ), outcome
        except OrchestrationContractError:
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
        return envelope, "accepted"

    def _archive_confirmation(
        self,
        key: tuple[str, str],
        pending: _PendingWorkflow,
        *,
        cleanup_complete: bool,
    ) -> None:
        with self._pending_confirmation_claim_lock:
            claim = self._claimed_pending_confirmations[
                _pending_confirmation_claim_key(key, pending)
            ]
            claim.state = "completed"
            claim.pending = None
            claim.cleanup_complete = cleanup_complete
            claim.retain_until = self._monotonic_clock() + _CONFIRMATION_TTL_SECONDS

    def _publish_pending_workflow(
        self,
        pending_key: tuple[str, str],
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
        pending_key: tuple[str, str],
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
        pending: _PendingWorkflow,
        session_id: str,
        memory_key: SessionMemoryKey,
    ) -> ResponseEnvelope:
        envelope = self._response_builder.build_message(
            str(uuid4()),
            pending.task_id,
            session_id,
            "确认请求已失效或与当前待确认动作不匹配，本次未执行。",
            "The confirmation request is stale or does not match the "
            "current pending action; nothing was executed.",
            pending.trace_id,
            status="waiting_user",
        )
        await self._trace_port.record_step(
            pending.trace_id,
            pending.task_id,
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
            (self._intent_version_binding,),
            await self._workflow_engine.resume_version_bindings(task_id=pending.task_id),
        )
        await self._human_gate_port.assert_task_bindings(
            pending.task_id,
            resume_bindings,
            exact=True,
        )
        current_action_digest = self._workflow_engine.pending_confirmation_action_digest(
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
        pending_key: tuple[str, str],
        pending: _PendingWorkflow,
        session_id: str,
        memory_key: SessionMemoryKey,
    ) -> ResponseEnvelope:
        if self._workflow_engine is None:
            raise RuntimeError("pending Workflow has no configured engine")

        response_id = str(uuid4())
        if self._human_gate_port is not None:
            await self._record_confirmation_decision(pending, memory_key, "confirmed")
        if self._human_gate_port is None:
            exec_result = await self._orchestration.resume_capability(
                task_id=pending.task_id,
                confirmed=True,
            )
        else:
            exec_result = await self._orchestration.resume_capability(
                task_id=pending.task_id,
                confirmed=True,
                expected_action_digest=pending.action_digest,
            )
        confirmation: ConfirmationPreview | None = None
        requested_at = self._utc_clock()
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
                next_action_digest = self._workflow_engine.pending_confirmation_action_digest(
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
                self._workflow_engine.discard_checkpoint(pending.task_id)
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
        envelope = self._build_envelope(
            response_id,
            pending.task_id,
            session_id,
            exec_result,
            pending.trace_id,
            capability_ref,
            projection_snapshot=pending.projection_snapshot,
            confirmation=confirmation,
        )
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
                expires_at=requested_at + timedelta(seconds=_CONFIRMATION_TTL_SECONDS),
                monotonic_deadline=monotonic_deadline,
            )
            owns_pending = self._compare_and_swap_pending_workflow(
                pending_key,
                expected=pending,
                replacement=next_pending,
            )
        else:
            owns_pending = self._compare_and_swap_pending_workflow(
                pending_key,
                expected=pending,
                replacement=None,
            )
        if not owns_pending:
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

        if exec_result.status != "waiting_user":
            await self._record_terminal_evaluation(
                trace_id=pending.trace_id,
                task_id=pending.task_id,
                session_id=session_id,
                business_status=exec_result.status,
                error_code=exec_result.error_code,
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


    async def _finish_version_binding_failure(
        self,
        *,
        response_id: str,
        task_id: str,
        session_id: str,
        trace_id: str,
        capability_id: str,
        memory_key: SessionMemoryKey,
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
    ) -> ResponseEnvelope:
        message, fallback_text = _intent_failure_message(reason)
        await self._task_store.update_status(task_id, "failed", "internal_error")
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
            error_code="internal_error",
        )
        await self._record_terminal_evaluation(
            trace_id=trace_id,
            task_id=task_id,
            session_id=session_id,
            business_status="failed",
            error_code="internal_error",
            memory_key=memory_key,
        )
        await self._trace_port.finalize_task_trace(
            trace_id,
            task_id,
            session_id,
            tenant_id=memory_key.tenant_id,
            ai_user_id=memory_key.ai_user_id,
            status="failed",
            error_code="internal_error",
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
        memory_key: SessionMemoryKey,
    ) -> ResponseEnvelope:
        active_capabilities = await self._capability_registry.list(status="active")
        message, fallback_text = self._semantic_knowledge.no_capability_guidance(
            active_capabilities
        )
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
    ) -> None:
        try:
            conclusion = self._evaluator.evaluate(business_status, error_code)
        except Exception:
            conclusion = EvaluationConclusion(
                business_status=business_status,
                business_error_code=error_code,
                evaluation_result="error",
                reason="evaluator_error",
            )
        await self._trace_port.record_step(
            trace_id,
            task_id,
            session_id,
            tenant_id=memory_key.tenant_id,
            ai_user_id=memory_key.ai_user_id,
            event_type="evaluation_recorded",
            status="ok" if conclusion.evaluation_result == "passed" else "failed",
            capability_id=capability_id,
            error_code=error_code,
            attributes=conclusion.trace_attributes(),
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
    normalized = " ".join(message.strip().casefold().split())
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


def _is_stale_workflow_confirmation_message(message: str) -> bool:
    normalized = " ".join(message.strip().casefold().split())
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


def _pending_workflow_key(session_id: str, ai_user_id: str) -> tuple[str, str]:
    return session_id, ai_user_id


def _pending_confirmation_claim_key(
    pending_key: tuple[str, str],
    pending: _PendingWorkflow,
) -> tuple[str, str, str]:
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
