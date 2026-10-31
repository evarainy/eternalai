"""Browser ordinary-request and D collection helpers shared by Runtime chat paths.

Moved verbatim from ``RuntimeImpl``; the legacy browser shell (budget, route
ownership, exception branches and document failures) stays in ``RuntimeImpl``.
This module depends only on ports and ``app.runtime.d_form_collection``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

from app.knowledge.capability_selection import capability_fingerprint
from app.memory import SessionMemoryKey
from app.ports.auth import Principal
from app.ports.browser_chat import BrowserOrdinarySubmissionPort
from app.ports.browser_gateway import (
    BrowserGatewayAdmission,
    BrowserGatewayDiagnostic,
    BrowserGatewayInvocation,
    BrowserIntentHandoffPort,
    BrowserRequestScope,
    browser_request_scope,
)
from app.ports.capability_gateway import ExecutionResult
from app.ports.capability_registry import CapabilitySpec
from app.ports.response_envelope import ResponseEnvelope, UIComponent
from app.ports.runtime import DFormCollectionReference, d_form_collection_reference
from app.ports.task_store import (
    DFormCollectionError,
    DFormCollectionRecord,
    DFormCollectionSnapshot,
    DFormCollectionStorePort,
)
from app.ports.trace import TracePort
from app.runtime.chat_stage import _record_chat_stage
from app.runtime.d_form_collection import (
    CAPABILITY_ID as D_FORM_ID,
)
from app.runtime.d_form_collection import (
    COLLECTION_TTL_SECONDS,
    CategoryEvidence,
    collect_arguments,
    missing_fields,
    question,
)
from app.runtime.d_form_collection import (
    request_digest as d_form_request_digest,
)

# The legacy logger name is kept so existing log routing sees the same records.
_LOGGER_NAME = "app.runtime.runtime"


class BrowserRequestHelpers:
    def __init__(
        self,
        *,
        trace_port: TracePort,
        utc_clock: Callable[[], datetime],
        d_form_collection_store: DFormCollectionStorePort | None = None,
        d_form_category_source: Callable[
            [SessionMemoryKey, CapabilitySpec], Awaitable[CategoryEvidence | None]
        ]
        | None = None,
    ) -> None:
        self._trace_port = trace_port
        self._utc_clock = utc_clock
        self.d_form_collection_store = d_form_collection_store
        self._d_form_category_source = d_form_category_source
        self.handoff: BrowserIntentHandoffPort | None = None
        self.submission: BrowserOrdinarySubmissionPort | None = None

    def install(
        self,
        handoff: BrowserIntentHandoffPort,
        submission: BrowserOrdinarySubmissionPort,
    ) -> None:
        self.handoff = handoff
        self.submission = submission

    async def lookup(
        self,
        scope: BrowserRequestScope,
        *,
        allow_collection_resume: bool = False,
    ) -> ResponseEnvelope | None:
        if (
            self.submission is None
            or scope.attempt.message is None
            or scope.attempt.channel is None
        ):
            raise ValueError("browser_request_lookup_invalid")
        response = await self.submission.lookup_ordinary_request(
            principal=scope.verified.principal,
            bound_session=scope.bound_session,
            client_request_id=scope.client_request_id,
            message=scope.attempt.message,
            channel=scope.attempt.channel,
        )
        if response is not None and response.status == "running":
            scope.capture_admission(response)
        if response is None and self.d_form_collection_store is not None:
            principal = scope.verified.principal
            snapshot = await self.d_form_collection_store.find_d_form_collection_request(
                tenant_id=principal.org_ctx.tenant_id,
                ai_user_id=principal.ai_user_id,
                session_id=scope.bound_session,
                request_id=scope.client_request_id,
                request_digest=d_form_request_digest(scope.attempt.message, scope.attempt.channel),
            )
            if snapshot is not None:
                record = snapshot.record
                reference = d_form_collection_reference.get()
                # Only the winning reply can recover its immutable handoff attempt,
                # under the same distributed request lock and the same request key.
                if (
                    allow_collection_resume
                    and record.consumed
                    and snapshot.handoff is None
                    and snapshot.task_status == "running"
                    and reference is not None
                    and reference.task_id == record.task_id
                    and reference.version == record.version - 1
                    and record.request_id == scope.client_request_id
                ):
                    return None
                response = self.collection_response(snapshot, scope.bound_session)
                if snapshot.handoff is not None and response.status == "running":
                    scope.capture_admission(response)
        return response

    def collection_response(
        self,
        snapshot: DFormCollectionSnapshot,
        session_id: str,
        options: tuple[str, ...] | None = None,
    ) -> ResponseEnvelope:
        record, handoff = snapshot.record, snapshot.handoff
        if handoff is not None:
            kind = handoff.get("admission_state")
            data: dict[str, Any] = {"kind": kind, "task_id": handoff.get("canonical_task_id")}
            if kind == "accepted":
                data.update(
                    run_id=handoff.get("canonical_run_id"),
                    state_revision=handoff.get("state_revision"),
                )
            if kind in {"accepted", "pending"}:
                text = "该收集任务已交接；请继续读取原浏览器任务。"
                return ResponseEnvelope(
                    response_id=str(uuid4()),
                    task_id=handoff["canonical_task_id"],
                    session_id=session_id,
                    status="running",
                    message=text,
                    fallback_text=text,
                    ui=UIComponent(component_type="none", action="none"),
                    data=data,
                    trace_id=handoff["canonical_trace_id"],
                )
        expired = not record.consumed and record.expires_at <= self._utc_clock()
        closed = snapshot.task_status not in {"waiting_user", "running"}
        text = (
            "此补参记录已过期，请重新发起报修。"
            if expired
            else "原收集任务已结束，请核对原请求状态。"
            if closed
            else "原参数已交接，结果尚待确认；请保留原请求标识。"
            if record.consumed
            else question(record.arguments, category_source_available=options is not None)
        )
        return ResponseEnvelope(
            response_id=str(uuid4()),
            task_id=record.task_id,
            session_id=session_id,
            status=(
                "failed"
                if closed
                else "blocked"
                if expired
                else "running"
                if record.consumed
                else "waiting_user"
            ),
            message=text,
            fallback_text=text,
            ui=UIComponent(component_type="none", action="none"),
            data={
                "kind": "d_form_collection",
                "outcome": "collection_expired"
                if expired
                else "collection_closed"
                if closed
                else "handoff_pending"
                if record.consumed
                else "waiting_user",
                "d_form_collection": None
                if expired or closed or record.consumed
                else {"task_id": record.task_id, "version": record.version},
                "collected_fields": list(record.arguments),
                "missing_fields": list(missing_fields(record.arguments)),
                "category_source_available": options is not None,
                "category_source_unavailable": (
                    "category" not in record.arguments and options is None
                ),
                "category_options": list(options) if options is not None else [],
                "expires_at": record.expires_at.isoformat(),
            },
            trace_id=record.trace_id,
        )

    async def category_options(
        self,
        owner: SessionMemoryKey,
        capability: CapabilitySpec,
    ) -> tuple[str, ...] | None:
        if self._d_form_category_source is None:
            return None
        try:
            async with asyncio.timeout(5):
                evidence = await self._d_form_category_source(owner, capability)
            return (
                None
                if evidence is None
                else evidence.choices(
                    owner,
                    capability_fingerprint(capability),
                    self._utc_clock(),
                )
            )
        except Exception:
            logging.getLogger(_LOGGER_NAME).warning("d_form_collection_category_source_unavailable")
            return None

    async def recover_browser_request(self, scope: BrowserRequestScope) -> ResponseEnvelope | None:
        if scope.attempt.admission is not None:
            return self.browser_unconfirmed_response(scope, timeout=False)

        async def bounded() -> ResponseEnvelope | None:
            async with asyncio.timeout_at(self.browser_finalization_deadline(scope)):
                return await self.lookup(scope)

        recovery = asyncio.create_task(bounded())
        try:
            return await asyncio.shield(recovery)
        except asyncio.CancelledError:
            recovery.add_done_callback(lambda done: None if done.cancelled() else done.exception())
            raise
        except Exception:
            logging.getLogger(_LOGGER_NAME).warning("browser_request_recovery_unconfirmed")
            return None

    @staticmethod
    def browser_finalization_deadline(scope: BrowserRequestScope) -> float:
        if scope.attempt.finalization_deadline is None:
            scope.attempt.finalization_deadline = asyncio.get_running_loop().time() + 5
        return scope.attempt.finalization_deadline

    @staticmethod
    def browser_unconfirmed_response(
        scope: BrowserRequestScope, *, timeout: bool
    ) -> ResponseEnvelope:
        admission = scope.attempt.admission
        if admission is not None:
            message = "浏览器请求已接纳，交办审计暂未确认；请继续读取原任务。"
            return ResponseEnvelope(
                response_id=str(uuid4()),
                task_id=admission.task_id,
                session_id=scope.bound_session,
                status="running",
                message=message,
                fallback_text=message,
                ui=UIComponent(component_type="none", action="none"),
                data=admission.data(),
                trace_id=admission.trace_id,
                trace_summary="handoff_unavailable",
            )
        diagnostic = BrowserGatewayDiagnostic(
            "admission",
            "admission_timeout" if timeout else "admission_uncertain",
        )
        message = "提交结果尚未确认；重查时请保留原请求。"
        return ResponseEnvelope(
            response_id=str(uuid4()),
            task_id=scope.attempt.task_id or str(uuid4()),
            session_id=scope.bound_session,
            status="running",
            message=message,
            fallback_text=message,
            ui=UIComponent(component_type="none", action="none"),
            data={
                "kind": "unconfirmed",
                "client_request_id": scope.client_request_id,
                "error_code": "adapter_timeout" if timeout else "adapter_error",
                **diagnostic.attributes(),
            },
            trace_id=scope.attempt.trace_id or str(uuid4()),
            trace_summary=diagnostic.reason,
        )

    def start_close_intent(self, scope: BrowserRequestScope) -> asyncio.Task[None]:
        """Start the bounded intent close; callers shield and await the task."""

        async def bounded() -> None:
            async with asyncio.timeout_at(self.browser_finalization_deadline(scope)):
                if self.handoff is None:
                    return
                outcome = await self.handoff.fail_if_open(scope, "adapter_error")
                if outcome not in {"failed", "handed_off"}:
                    if outcome == "absent" and scope.attempt.task_id is not None:
                        logging.getLogger(_LOGGER_NAME).warning(
                            "browser_intent_finalization_unconfirmed"
                        )
                    return
                # The committed DB result controls this; cancellation is not browser-stop proof.
                assert scope.attempt.task_id is not None and scope.attempt.trace_id is not None
                await self._trace_port.finalize_task_trace(
                    scope.attempt.trace_id,
                    scope.attempt.task_id,
                    scope.bound_session,
                    tenant_id=scope.verified.principal.org_ctx.tenant_id,
                    ai_user_id=scope.verified.principal.ai_user_id,
                    status="ok" if outcome == "handed_off" else "failed",
                    error_code=None if outcome == "handed_off" else "adapter_error",
                )

        return asyncio.create_task(bounded())

    async def close_intent(
        self,
        scope: BrowserRequestScope,
        started: Callable[[asyncio.Task[None]], None] | None = None,
    ) -> None:
        """``started`` receives the close task before it is awaited, so an owner can await it."""
        closing = self.start_close_intent(scope)
        if started is not None:
            started(closing)
        try:
            await asyncio.shield(closing)
        except asyncio.CancelledError:
            closing.add_done_callback(lambda done: None if done.cancelled() else done.exception())
            raise
        except Exception:
            logging.getLogger(_LOGGER_NAME).warning("browser_intent_finalization_unconfirmed")

    async def admit_reference(
        self,
        reference: DFormCollectionReference,
        scope: BrowserRequestScope | None,
        principal: Principal,
        session_id: str,
        channel: Literal["web", "cli", "api", "mock"],
    ) -> DFormCollectionSnapshot | ResponseEnvelope:
        """Recheck a collection reply reference before any parsing or Task work.

        Returns the owned snapshot to continue, or the original response to replay.
        """
        if (
            self.d_form_collection_store is None
            or scope is None
            or not scope.matches(principal, session_id)
            or scope.attempt.message is None
        ):
            raise DFormCollectionError("collection_not_found")
        collection = await self.d_form_collection_store.get_d_form_collection(
            tenant_id=principal.org_ctx.tenant_id,
            ai_user_id=principal.ai_user_id,
            session_id=session_id,
            task_id=reference.task_id,
        )
        if collection is None:
            raise DFormCollectionError("collection_not_found")
        record = collection.record
        if collection.handoff is not None or collection.task_status not in {
            "waiting_user",
            "running",
        }:
            return self.collection_response(collection, session_id)
        if record.consumed:
            if (
                collection.task_status != "running"
                or record.request_id != scope.client_request_id
                or record.version != reference.version + 1
                or record.request_digest != d_form_request_digest(scope.attempt.message, channel)
            ):
                return self.collection_response(collection, session_id)
        else:
            if record.expires_at <= self._utc_clock():
                raise DFormCollectionError("collection_expired")
            if record.version != reference.version:
                raise DFormCollectionError("collection_version_conflict")
            if record.version >= 32:
                raise DFormCollectionError("collection_limit_reached")
        return collection

    async def collect(
        self,
        *,
        scope: BrowserRequestScope,
        owner: SessionMemoryKey,
        channel: Literal["web", "cli", "api", "mock"],
        message: str,
        task_id: str,
        trace_id: str,
        capability: CapabilitySpec,
        model_arguments: dict[str, Any],
        collection: DFormCollectionSnapshot | None,
        category_options: tuple[str, ...] | None,
        model_category: bool = False,
        append_description: bool = False,
    ) -> ResponseEnvelope | dict[str, Any]:
        """Merge model arguments into the D record.

        Returns the collection card while fields are missing, otherwise the
        arguments ready for admission. Continuations pass ``category_options``
        already resolved for the recorded capability. With ``model_category``
        the category is the model's choice from the options, and the card
        never lists them: the user is not asked for a category.
        """
        assert self.d_form_collection_store is not None
        assert scope.attempt.message is not None
        if collection is None:
            category_options = await self.category_options(owner, capability)
        previous = collection.record if collection is not None else None
        _record_chat_stage(
            trace_id,
            "d_form_collection",
            "start",
            capability_id=D_FORM_ID,
            reason=(
                "continuation_category_supplied"
                if "category" in model_arguments
                else "continuation_category_missing"
            )
            if previous is not None
            else (
                "initial_category_supplied"
                if "category" in model_arguments
                else "initial_category_missing"
            ),
        )
        arguments = collect_arguments(
            model_arguments,
            message,
            previous,
            category_options,
            model_category=model_category,
            append_description=append_description,
        )
        if "category" not in arguments and category_options is not None:
            matches = tuple(item for item in category_options if item in message)
            if len(matches) == 1:
                arguments["category"] = matches[0]
        _record_chat_stage(
            trace_id,
            "d_form_category",
            "ok" if "category" in arguments else "blocked",
            capability_id=D_FORM_ID,
            reason="category_options_available"
            if category_options is not None
            else "category_options_unavailable",
        )
        if previous is not None and previous.consumed:
            # The sole winning reply replays immutable arguments under its
            # original request lock/key; no new version or admission identity.
            arguments = dict(previous.arguments)
        elif missing_fields(arguments) or previous is not None:
            collected = DFormCollectionRecord(
                task_id=task_id,
                trace_id=trace_id,
                capability_version=capability.version,
                capability_fingerprint=capability_fingerprint(capability),
                version=previous.version + 1 if previous is not None else 1,
                expires_at=previous.expires_at
                if previous is not None
                else self._utc_clock() + timedelta(seconds=COLLECTION_TTL_SECONDS),
                arguments=arguments,
                request_id=scope.client_request_id,
                request_digest=d_form_request_digest(scope.attempt.message, channel),
                consumed=not missing_fields(arguments),
            )
            snapshot = await self.d_form_collection_store.save_d_form_collection(
                collected,
                tenant_id=owner.tenant_id,
                ai_user_id=owner.ai_user_id,
                session_id=owner.session_id,
                expected_version=previous.version if previous is not None else None,
            )
            _record_chat_stage(
                trace_id,
                "d_form_collection",
                "ok" if collected.consumed else "blocked",
                capability_id=D_FORM_ID,
                reason="ready" if collected.consumed else "waiting_user",
            )
            logging.getLogger(_LOGGER_NAME).info(
                "d_form_collection stage=%s version=%s missing=%s",
                "handoff" if collected.consumed else "waiting_user",
                collected.version,
                ",".join(missing_fields(arguments)),
            )
            if not collected.consumed:
                response = self.collection_response(
                    snapshot, owner.session_id, None if model_category else category_options
                )
                if model_category and not {"position", "description"} & arguments.keys():
                    text = "请补充故障位置和发生了什么故障；简短说明即可。"
                    return response.model_copy(update={"message": text, "fallback_text": text})
                return response
        return arguments

    async def retain_unparsed(
        self,
        scope: BrowserRequestScope,
        owner: SessionMemoryKey,
        channel: Literal["web", "cli", "api", "mock"],
        collection: DFormCollectionSnapshot,
        category_options: tuple[str, ...] | None,
    ) -> ResponseEnvelope:
        """Keep an unconsumed record's arguments when a reply cannot be parsed."""
        assert self.d_form_collection_store is not None
        assert scope.attempt.message is not None
        retained_record = collection.record
        retained = retained_record.model_copy(
            update={
                "version": retained_record.version + 1,
                "request_id": scope.client_request_id,
                "request_digest": d_form_request_digest(scope.attempt.message, channel),
            }
        )
        snapshot = await self.d_form_collection_store.save_d_form_collection(
            retained,
            tenant_id=owner.tenant_id,
            ai_user_id=owner.ai_user_id,
            session_id=owner.session_id,
            expected_version=retained_record.version,
        )
        logging.getLogger(_LOGGER_NAME).warning("d_form_collection_reply_unparsed")
        envelope = self.collection_response(snapshot, owner.session_id, category_options)
        assert envelope.data is not None
        envelope.data["input_error"] = "collection_reply_unparsed"
        return envelope

    async def finish_admission(
        self,
        principal: Principal,
        session_id: str,
        task_id: str,
        trace_id: str,
        capability: CapabilitySpec,
        result: ExecutionResult,
        close_started: Callable[[asyncio.Task[None]], None] | None = None,
    ) -> ResponseEnvelope:
        scope, admission = browser_request_scope.get(), result.browser_admission
        if (
            scope is None
            or self.handoff is None
            or type(admission) is not BrowserGatewayAdmission
            or not scope.matches(principal, session_id)
            or admission.owner.tenant_id != principal.org_ctx.tenant_id
            or admission.owner.user_id != principal.ai_user_id
            or admission.owner.session_id != session_id
        ):
            raise ValueError("browser_admission_context_invalid")
        invocation = BrowserGatewayInvocation(
            scope, capability.capability_id, capability.version, task_id, trace_id
        )
        scope.attempt.admission = admission
        diagnostic = None
        try:
            await self.handoff.commit(invocation, admission)
        except Exception as error:
            try:
                exception_type = type(error).__name__
                logging.getLogger(_LOGGER_NAME).warning(
                    "browser_fallback_diagnostic stage=intent_handoff exception_type=%s",
                    exception_type
                    if exception_type.isascii()
                    and exception_type.isidentifier()
                    and len(exception_type) <= 64
                    else "UnknownException",
                )
            except Exception:
                pass  # Diagnostic failure must preserve the original failure path.
            # Known canonical admission survives an unavailable/uncertain I handoff.
            await self.close_intent(scope, close_started)
            return self.browser_unconfirmed_response(scope, timeout=False)
        try:
            await self._trace_port.record_step(
                trace_id,
                task_id,
                session_id,
                tenant_id=admission.owner.tenant_id,
                ai_user_id=admission.owner.user_id,
                event_type="response_envelope_created",
                status="ok",
                capability_id=capability.capability_id,
                attributes={
                    "phase": "admission_only",
                    "canonical_task_id": admission.task_id,
                    "canonical_run_id": admission.run_id,
                    "admission_state": admission.kind,
                },
            )
            await self._trace_port.finalize_task_trace(
                trace_id,
                task_id,
                session_id,
                tenant_id=admission.owner.tenant_id,
                ai_user_id=admission.owner.user_id,
                status="ok",
                capability_id=capability.capability_id,
            )
        except Exception:
            diagnostic = "audit_unavailable"
        message = (
            "浏览器任务已接纳，正在处理。"
            if admission.kind == "accepted"
            else "浏览器请求正在准备。"
        )
        return ResponseEnvelope(
            response_id=str(uuid4()),
            task_id=admission.task_id,
            session_id=session_id,
            status="running",
            message=message,
            fallback_text=message,
            ui=UIComponent(component_type="none", action="none"),
            data=admission.data(),
            trace_id=admission.trace_id,
            trace_summary=diagnostic,
        )
