"""Canonical first browser Chat admission and authenticated durable result retrieval."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as SchemaValidationError
from referencing import Registry

from app.browser_skill.models import BrowserOwner
from app.browser_skill.run_contracts import (
    BrowserAcceptedView,
    BrowserCancelView,
    BrowserProgressView,
    BrowserResultView,
    BrowserRunView,
    project_accepted,
)
from app.ports.auth import Principal, SessionBindingError, authenticated_session
from app.ports.browser_chat import (
    BrowserChatBindingResolverPort,
    BrowserChatCipherPort,
    BrowserChatError,
    BrowserChatInputIdentity,
    BrowserChatParserPort,
    BrowserRunResponse,
)
from app.ports.browser_publication_store import BrowserPublicationStorePort
from app.ports.browser_run_store import (
    BrowserRunStoreError,
    BrowserRunStorePort,
    CanonicalRequest,
    RunAdmission,
    RunSnapshot,
    checked_run_id,
)
from app.ports.credential_vault import BrowserAuthorizationError
from app.ports.response_envelope import ResponseEnvelope, UIComponent
from app.ports.task_store import SessionRecord, SessionStorePort
from app.ports.trace import TracePort
from app.runtime.response_projection import project_response_data


def _json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"),
    ).encode("ascii")


def _validate_schema(value: object, schema: dict[str, Any]) -> None:
    def local(node: object) -> None:
        if isinstance(node, dict):
            for key in ("$ref", "$dynamicRef"):
                if key in node and (
                    not isinstance(node[key], str) or not node[key].startswith("#/")
                ):
                    raise BrowserChatError("browser_schema_unsupported")
            for child in node.values():
                local(child)
        elif isinstance(node, list):
            for child in node:
                local(child)
    local(schema)
    Draft202012Validator.check_schema(schema)
    # An explicit empty registry has no external retrieval mechanism.
    Draft202012Validator(schema, registry=Registry()).validate(value)


class BrowserChatService:
    def __init__(
        self, store: BrowserRunStorePort, publications: BrowserPublicationStorePort,
        parser: BrowserChatParserPort, bindings: BrowserChatBindingResolverPort,
        cipher: BrowserChatCipherPort, session_binder: Callable[[Principal, str], str],
        trace: TracePort, *, sessions: SessionStorePort, skill_id: str,
        input_digest_key: bytes, enabled: bool = False,
    ) -> None:
        if (
            type(enabled) is not bool or type(input_digest_key) is not bytes
            or len(input_digest_key) != 32
        ):
            raise ValueError("browser_chat_configuration_invalid")
        self._store, self._publications = store, publications
        self._parser, self._bindings, self._cipher = parser, bindings, cipher
        self._binder, self._trace = session_binder, trace
        self._input_key, self._enabled = input_digest_key, enabled
        checked_run_id(skill_id)
        self._sessions, self._skill_id = sessions, skill_id

    @property
    def skill_id(self) -> str:
        return self._skill_id

    def owner_cache_scope(self, principal: Principal) -> tuple[str, str]:
        captured = authenticated_session.get()
        if (
            not self._enabled or captured is None or captured.principal != principal
            or captured.expires_at.tzinfo is None or captured.expires_at <= datetime.now(UTC)
        ):
            raise BrowserChatError("authentication_required", http_status=401)
        tenant = hmac.digest(
            self._input_key,
            _json(["browser-cache-tenant-v1", principal.org_ctx.tenant_id]), hashlib.sha256,
        ).hex()
        user = hmac.digest(
            self._input_key,
            _json(["browser-cache-user-v1", principal.org_ctx.tenant_id, principal.ai_user_id]),
            hashlib.sha256,
        ).hex()
        return "bct1_" + tenant, "bcu1_" + user

    def _owner(self, principal: Principal, bound_session: str) -> BrowserOwner:
        if not self._enabled:
            raise BrowserChatError("browser_chat_unavailable")
        captured = authenticated_session.get()
        if (
            captured is None or captured.principal != principal
            or type(captured.fingerprint) is not bytes or len(captured.fingerprint) != 32
            or captured.expires_at.tzinfo is None or captured.expires_at <= datetime.now(UTC)
        ):
            raise BrowserChatError("authentication_required", http_status=401)
        try:
            if self._binder(principal, bound_session) != bound_session:
                raise SessionBindingError("invalid bound session")
            return BrowserOwner(
                tenant_id=principal.org_ctx.tenant_id, user_id=principal.ai_user_id,
                session_id=bound_session,
            )
        except (SessionBindingError, ValueError):
            raise BrowserChatError("session_not_found", http_status=404) from None
        except Exception:
            raise BrowserChatError("browser_session_unavailable") from None

    async def start(
        self, *, channel: Literal["web", "cli", "api", "mock"], principal: Principal,
        bound_session: str, message: str, client_capabilities: dict[str, Any],
        client_request_id: str, skill_id: str,
    ) -> ResponseEnvelope:
        owner = self._owner(principal, bound_session)
        captured = authenticated_session.get()
        assert captured is not None
        if skill_id != self._skill_id:
            raise BrowserChatError("browser_publication_unavailable")
        if client_capabilities.get("browser_async_v1") is not True:
            raise BrowserChatError("browser_async_required", http_status=422)
        if not client_request_id:
            raise BrowserChatError("browser_request_id_required", http_status=422)
        try:
            checked_run_id(client_request_id)
            checked_run_id(skill_id)
            semantic = _json({
                "schema_version": "browser.request.semantic.v1", "channel": channel,
                "message": message, "client_capabilities": client_capabilities,
                "skill_id": skill_id,
            })
            if not message.strip() or len(semantic) > 1_048_576:
                raise ValueError("invalid semantic input")
        except Exception:
            raise BrowserChatError("browser_request_input_invalid", http_status=422) from None
        # Establish only the exact signed conversation row first; no Task or
        # Trace allocation, parsing or browser work occurs before canonical claim.
        try:
            expected_session = SessionRecord(tenant_id=owner.tenant_id, session_id=owner.session_id)
            persisted_session = await self._sessions.create_session(expected_session)
            if persisted_session != expected_session:
                raise BrowserChatError("session_not_found", http_status=404)
        except BrowserChatError:
            raise
        except Exception:
            raise BrowserChatError("browser_session_unavailable") from None
        # Commit the canonical Task and parsing claim before any parser,
        # binding/source lookup, or publication work.
        try:
            request = await self._store.get_or_create_request(
                owner, client_request_id, semantic, processing_owner=uuid4().hex,
            )
        except BrowserRunStoreError as error:
            raise self._store_error(error) from None
        except Exception:
            raise BrowserChatError("browser_chat_unavailable") from None
        if not request.parse_winner:
            return self._duplicate(request)
        try:
            publication = await self._publications.get_active(owner, skill_id)
            if publication is None:
                raise BrowserChatError("browser_publication_unavailable")
            manifest = publication.manifest
            capability = manifest.capability
            if (
                manifest.skill.skill_id != skill_id or manifest.effect != "read_only"
                or capability.type != "query" or capability.status != "active"
                or capability.target_system != "oa" or not capability.binding_required
                or capability.execution_identity != "user_delegated"
            ):
                raise BrowserChatError("browser_capability_not_readonly", http_status=403)
            arguments = await self._parser.parse(principal, message, capability)
            if type(arguments) is not dict:
                raise BrowserChatError("browser_request_input_invalid", http_status=422)
            _validate_schema(arguments, capability.input_schema)
            binding = await self._bindings.resolve(principal, bound_session, capability)
            if (
                binding.tenant_id != owner.tenant_id or binding.ai_user_id != owner.user_id
                or binding.target_system != capability.target_system
            ):
                raise BrowserChatError("browser_binding_stale", http_status=403)
            await self._publications.assert_current(owner, manifest)
            self._owner(principal, bound_session)
            if authenticated_session.get() != captured:
                raise BrowserChatError("authentication_required", http_status=401)
            run_id = uuid4().hex
            payload: dict[str, object] = {
                "schema_version": "browser.request.input.v2",
                "channel": channel,
                "principal": principal.model_dump(mode="json"),
                "capability_id": capability.capability_id, "arguments": arguments,
            }
            input_digest = hmac.digest(
                self._input_key,
                b"browser-run-input-v1\x00" + run_id.encode("ascii") + b"\x00" + _json(payload),
                hashlib.sha256,
            )
            identity = BrowserChatInputIdentity(
                owner, request.task_id, run_id, binding.target_system, binding.binding_id,
                binding.binding_revision, captured.fingerprint, captured.expires_at,
                bytes.fromhex(manifest.digest), 1, input_digest,
            )
            protected = self._cipher.encrypt_input(identity, payload)
            admission = RunAdmission(
                owner=owner, task_id=request.task_id, run_id=run_id,
                target_system=binding.target_system, binding_id=binding.binding_id,
                binding_revision=binding.binding_revision, auth_fingerprint=captured.fingerprint,
                auth_expires_at=captured.expires_at, publication_digest=identity.publication_digest,
                input_revision=1, input_digest=input_digest, protected_input=protected,
            )
        except Exception as error:
            code = (
                error.code if isinstance(
                    error, (BrowserChatError, BrowserRunStoreError, BrowserAuthorizationError),
                ) else "browser_request_input_invalid" if isinstance(error, SchemaValidationError)
                else "browser_request_admission_failed"
            )
            try:
                await self._store.reject_request(request, code)
            except BrowserRunStoreError as rejected:
                raise self._store_error(rejected) from None
            except Exception:
                raise BrowserChatError("browser_request_rejection_unavailable") from None
            return self._envelope(request, "failed", {"kind": "failed", "error_code": code})
        # Store mints acceptance provenance only after the Run/Task COMMIT.
        # Never reject or rewrite this request if post-commit tracing fails.
        try:
            committed = await self._store.accept(request, admission)
        except BrowserRunStoreError as error:
            raise self._store_error(error) from None
        except Exception:
            # Commit outcome may be uncertain; canonical replay resolves it.
            # Never issue reject_request after attempting the accept transaction.
            raise BrowserChatError("browser_request_admission_unavailable") from None
        accepted = project_accepted(committed, owner)
        diagnostic = None
        try:
            if request.trace_id is None:
                raise BrowserChatError("browser_task_created_trace_unavailable")
            await self._trace.record_step(
                request.trace_id, request.task_id, owner.session_id,
                tenant_id=owner.tenant_id, ai_user_id=owner.user_id,
                event_type="task_created", status="ok", capability_id=capability.capability_id,
            )
        except Exception:
            diagnostic = "browser_task_created_trace_unavailable"
        return self._envelope(request, "running", accepted.model_dump(), diagnostic=diagnostic)

    def _duplicate(self, request: CanonicalRequest) -> ResponseEnvelope:
        if request.run is not None:
            run = request.run
            if run.owner != request.owner or run.task_id != request.task_id:
                raise BrowserChatError("browser_run_projection_invalid")
            return self._envelope(request, "running", BrowserAcceptedView(
                task_id=run.task_id, run_id=run.run_id, state_revision=run.state_revision,
            ).model_dump())
        if request.task_status == "failed":
            return self._envelope(request, "failed", {
                "kind": "failed", "error_code": request.error_code or "browser_request_abandoned",
            })
        return self._envelope(request, "running", {"kind": "pending", "task_id": request.task_id})

    @staticmethod
    def _envelope(
        request: CanonicalRequest, status: Literal["running", "failed"], data: dict[str, Any],
        *, diagnostic: str | None = None,
    ) -> ResponseEnvelope:
        text = (
            "Browser request is being prepared." if data.get("kind") == "pending"
            else "Browser request accepted for processing." if status == "running"
            else "Browser request could not be accepted."
        )
        return ResponseEnvelope(
            response_id=uuid4().hex, task_id=request.task_id, session_id=request.owner.session_id,
            status=status, message=text, fallback_text=text,
            ui=UIComponent(component_type="none", action="none"), data=data,
            trace_id=request.trace_id or "", trace_summary=diagnostic,
        )

    async def get(
        self, principal: Principal, bound_session: str, task_id: str, run_id: str,
    ) -> BrowserRunResponse:
        owner = self._owner(principal, bound_session)
        try:
            run = await self._store.get(owner, task_id, run_id)
            value = None
            if run.status == "completed" and run.verification == "verified":
                publication = await self._publications.get_frozen(
                    owner, run.admission.publication_digest.hex(),
                )
                if publication is None:
                    raise BrowserChatError("browser_publication_unavailable")
                schema = json.loads(publication.manifest.output.output_schema_json)
                value = project_response_data(self._cipher.decrypt_result(run), schema)
                if value is None:
                    raise BrowserChatError("browser_result_projection_invalid")
                _validate_schema(value, schema)
                if len(_json(value)) > publication.manifest.output.maximum_bytes:
                    raise BrowserChatError("browser_result_projection_invalid")
                # The final await repeats live authority before private value
                # delivery; cleanup revision changes do not alter result bytes.
                fresh = await self._store.get(owner, task_id, run_id)
                if (
                    fresh.admission != run.admission or fresh.status != "completed"
                    or fresh.verification != "verified" or fresh.result_digest != run.result_digest
                    or fresh.protected_result != run.protected_result
                ):
                    raise BrowserChatError("browser_target_stale", http_status=409)
                run = fresh
            self._owner(principal, bound_session)
            return BrowserRunResponse(run=self._view(run), value=value)
        except BrowserRunStoreError as error:
            raise self._store_error(error) from None
        except BrowserChatError:
            raise
        except Exception:
            raise BrowserChatError("browser_run_projection_invalid") from None

    async def cancel(
        self, principal: Principal, bound_session: str, task_id: str, run_id: str,
    ) -> BrowserRunResponse:
        owner = self._owner(principal, bound_session)
        try:
            run = await self._store.request_cancel(owner, task_id, run_id)
            self._owner(principal, bound_session)
            # Requesting cancellation is not proof that execution stopped.
            return BrowserRunResponse(run=self._view(run), value=None)
        except BrowserRunStoreError as error:
            raise self._store_error(error) from None
        except BrowserChatError:
            raise
        except Exception:
            raise BrowserChatError("browser_run_projection_invalid") from None

    @staticmethod
    def _view(run: RunSnapshot) -> BrowserRunView:
        terminal = run.status in {"completed", "failed", "cancelled"}
        result = None
        progress = None
        if terminal:
            # Model validation checks completed implies verified, fixed error codes,
            # effect uncertainty, terminal revision and cancellation consistency.
            result = BrowserResultView.model_validate({
                "business": run.status, "effect": run.effect, "verification": run.verification,
                "cleanup": run.cleanup, "error_code": run.error_code,
                "dispatch_failure_code": run.dispatch_failure_code,
                "terminal_revision": run.terminal_revision,
            })
        else:
            progress = BrowserProgressView.model_validate({"phase": run.phase})
        return BrowserRunView(
            task_id=run.task_id, run_id=run.run_id, state_revision=run.state_revision,
            status=run.status, progress=progress, result=result,
            cancel=BrowserCancelView(
                requested=run.cancel_requested, acknowledged=run.cancel_acknowledged,
            ),
        )

    @staticmethod
    def _store_error(error: BrowserRunStoreError) -> BrowserChatError:
        status = 409 if error.code == "request_key_conflict" else (
            404 if error.code in {"browser_run_not_found", "browser_request_not_found"} else 503
        )
        return BrowserChatError(error.code, http_status=status)
