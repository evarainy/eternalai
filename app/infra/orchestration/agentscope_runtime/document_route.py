"""Fixed-document questions on the AgentScope path (M2 §4.2).

An explicit "请问" question runs the document service directly: no agent, no
AgentScope model call, no AgentState. The turn task is closed once by the
turn's ``close_once``; this module only builds the envelope and the close,
with the same trace attributes as the legacy document route.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.infra.orchestration.agentscope_read_tools import ReadToolScope, TaskClose
from app.infra.sdui.response_envelope_builder import ResponseEnvelopeBuilder
from app.ports.capability_gateway import ExecutionResult
from app.ports.error_codes import ErrorCode
from app.ports.response_envelope import ResponseEnvelope
from app.ports.runtime import ConfirmationCoordinatorPort, ConfirmationOwner
from app.runtime.document_qa import DocumentQAResult, DocumentQAService, document_failure

_DOCUMENT_PREFIX = "请问"
_QUESTION_LEAD = " ：:，,\t\r\n"


@dataclass(frozen=True, slots=True)
class DocumentTurn:
    envelope: ResponseEnvelope
    close: TaskClose
    outcome: str


def document_question(
    service: DocumentQAService | None,
    confirmations: ConfirmationCoordinatorPort,
    owner: ConfirmationOwner,
    scope: ReadToolScope,
    message: str,
) -> str | None:
    """The question of an explicit document request, or None for the ordinary route.

    Pending confirmations keep ownership of their replies; an account the
    document is not configured for never reaches the document or its model.
    """

    text = message.lstrip()
    if (
        service is None
        or not text.startswith(_DOCUMENT_PREFIX)
        or confirmations.has_pending(owner)
        or not service.permits(tenant_id=scope.tenant_id, ai_user_id=scope.ai_user_id)
    ):
        return None
    return text[len(_DOCUMENT_PREFIX):].lstrip(_QUESTION_LEAD)


def failure_reason(error: BaseException) -> str:
    """Document failure reason of a turn that ended by cancellation or a defect."""

    if isinstance(error, TimeoutError):
        return "document_timeout"
    if isinstance(error, Exception):
        return "document_internal_error"
    return "document_cancelled"


def failure_close(task_id: str, trace_id: str, reason: str) -> TaskClose:
    return _close(task_id, trace_id, _failure(reason))


async def answer_document(
    service: DocumentQAService,
    builder: ResponseEnvelopeBuilder,
    scope: ReadToolScope,
    *,
    response_id: str,
    task_id: str,
    trace_id: str,
    question: str,
    deadline: float,
) -> DocumentTurn:
    result = await service.answer(
        question=question, tenant_id=scope.tenant_id, ai_user_id=scope.ai_user_id,
        deadline=deadline, trace_id=trace_id,
    )
    return document_turn(builder, scope, response_id, task_id, trace_id, result)


def document_turn(
    builder: ResponseEnvelopeBuilder,
    scope: ReadToolScope,
    response_id: str,
    task_id: str,
    trace_id: str,
    result: DocumentQAResult,
) -> DocumentTurn:
    """Envelope and close of one result; an answer the builder alters is invalid."""

    def build(item: DocumentQAResult, data: dict[str, object]) -> ResponseEnvelope:
        return builder.build_message(
            response_id, task_id, scope.session_id, item.message, item.message, trace_id,
            status="failed" if item.outcome == "failed" else "completed", data=data,
        )

    envelope = build(result, {
        "kind": "fixed_document_qa", "outcome": result.outcome, "error_code": result.error_code,
    })
    if result.outcome != "failed" and (
        envelope.message != result.message or envelope.status != "completed"
    ):
        result = _failure("document_answer_invalid", "adapter_payload_invalid")
        envelope = build(result, {"error_code": result.error_code})
    return DocumentTurn(envelope, _close(task_id, trace_id, result), result.outcome)


def timeout_turn(
    builder: ResponseEnvelopeBuilder,
    scope: ReadToolScope,
    response_id: str,
    task_id: str,
    trace_id: str,
) -> DocumentTurn:
    return document_turn(
        builder, scope, response_id, task_id, trace_id, _failure("document_timeout")
    )


def _failure(reason: str, error_code: ErrorCode | None = None) -> DocumentQAResult:
    # Same code mapping as the legacy _fail_document_request.
    if error_code is None:
        error_code = "adapter_timeout" if reason == "document_timeout" else "internal_error"
    return document_failure(reason, error_code)


def _close(task_id: str, trace_id: str, result: DocumentQAResult) -> TaskClose:
    execution = ExecutionResult(
        status="failed" if result.outcome == "failed" else "completed",
        error_code=result.error_code, trace_id=trace_id,
    )
    return TaskClose(
        task_id, trace_id, None, execution, raw_execution=execution, envelope_created=True,
        response_attributes={
            "kind": "fixed_document_qa", "outcome": result.outcome,
            "reason": result.reason, "document_version": result.version,
        },
    )


__all__ = (
    "DocumentTurn",
    "answer_document",
    "document_question",
    "document_turn",
    "failure_close",
    "failure_reason",
    "timeout_turn",
)
