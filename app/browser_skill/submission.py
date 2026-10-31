"""Controlled browser submission and Run access, independent of Chat parsing.

Only an authenticated user's own session is supported by the current durable
authority. A service or delegated identity needs a separate approved contract.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Never
from uuid import uuid4

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as SchemaValidationError
from referencing import Registry

from app.browser_skill.compiler import (
    DurableTeachingCompilerAuthority,
    DurableTeachingDraft,
    compile_authorized_run,
)
from app.browser_skill.models import (
    BrowserOwner,
    ConfirmedBusinessKey,
    ScopeBinding,
    TeachingPlan,
)
from app.browser_skill.publication_contracts import (
    BrowserPublicationManifest,
    BrowserPublicationRecord,
)
from app.browser_skill.run_contracts import (
    BrowserAcceptedView,
    BrowserArtifactView,
    BrowserCancelView,
    BrowserProgressView,
    BrowserResultView,
    BrowserRunView,
    project_accepted,
)
from app.browser_skill.trajectory import (
    ParameterApproval,
    RunTeachingAuthority,
    RunTeachingSource,
    capture_authorized_run,
)
from app.browser_skill.write_workflow import BrowserWritePreparation, prepare_browser_write
from app.ports.auth import Principal, SessionBindingError, authenticated_session
from app.ports.browser_chat import (
    BrowserChatBindingResolverPort,
    BrowserChatCipherPort,
    BrowserChatError,
    BrowserChatInputIdentity,
    BrowserHandoffFields,
    BrowserHandoffView,
    BrowserRunResponse,
    BrowserWritePreview,
)
from app.ports.browser_publication_store import BrowserPublicationError, BrowserPublicationStorePort
from app.ports.browser_run_store import (
    BrowserRunStoreError,
    BrowserRunStorePort,
    CanonicalRequest,
    RunAdmission,
    RunSnapshot,
    checked_run_id,
)
from app.ports.capability_registry import CapabilitySpec
from app.ports.credential_vault import BrowserAuthorizationError
from app.ports.human_gate import (
    HumanGateConflictError,
    HumanGateDecisionRecord,
    HumanGatePort,
    HumanGateRequest,
    VersionBinding,
    build_task_version_binding_manifest,
)
from app.ports.response_envelope import ResponseEnvelope, UIComponent
from app.ports.task_store import SessionRecord, SessionStorePort
from app.ports.trace import TracePort
from app.runtime.response_projection import project_response_data


def _json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def _raise_teaching_source_unavailable() -> Never:
    raise BrowserChatError("browser_teaching_capture_unavailable")


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


@dataclass(frozen=True, slots=True, repr=False)
class _Invocation:
    """Derived only from the request's verified authentication context."""

    principal: Principal = field(repr=False)
    owner: BrowserOwner
    fingerprint: bytes = field(repr=False)
    expires_at: datetime


class BrowserSubmissionService:
    def __init__(
        self,
        store: BrowserRunStorePort,
        publications: BrowserPublicationStorePort,
        bindings: BrowserChatBindingResolverPort,
        cipher: BrowserChatCipherPort,
        session_binder: Callable[[Principal, str], str],
        trace: TracePort,
        *,
        sessions: SessionStorePort,
        skill_id: str,
        input_digest_key: bytes,
        enabled: bool = False,
        write_gate: HumanGatePort | None = None,
        confirmed_key_digest: Callable[[bytes, str], str] | None = None,
        fill_stop_reports: bool = False,
        list_artifacts: Callable[[RunSnapshot], Awaitable[tuple[BrowserArtifactView, ...]]]
        | None = None,
        read_artifact: Callable[[RunSnapshot, str], Awaitable[tuple[str, bytes]]] | None = None,
        teaching_base_digest: str | None = None,
        teaching_candidate_digest: str | None = None,
        teaching_candidate_version: str | None = None,
        teaching_publication: Callable[
            [BrowserOwner, str, str], Awaitable[BrowserPublicationRecord | None]
        ]
        | None = None,
        teaching_plan: Callable[[RunAdmission, BrowserPublicationManifest], Awaitable[TeachingPlan]]
        | None = None,
        teaching_capture_grant: Callable[[BrowserOwner, str], Awaitable[bool]] | None = None,
        teaching_capture_stores: Callable[
            [BrowserOwner, str, str],
            Awaitable[tuple[BrowserRunStorePort, BrowserPublicationStorePort]],
        ]
        | None = None,
        teaching_published_routes: Callable[
            [BrowserOwner, str], Awaitable[frozenset[tuple[str, ...]]]
        ]
        | None = None,
        teaching_review_request: Callable[
            [BrowserOwner, BrowserPublicationManifest, str, str, str],
            Awaitable[HumanGateRequest],
        ]
        | None = None,
        teaching_review_decide: Callable[
            [
                BrowserOwner,
                BrowserPublicationManifest,
                str,
                str,
                str,
                Literal["confirmed", "rejected"],
            ],
            Awaitable[HumanGateDecisionRecord],
        ]
        | None = None,
        initial_review_request: Callable[
            [BrowserOwner, BrowserPublicationManifest, str, str, str],
            Awaitable[HumanGateRequest],
        ]
        | None = None,
        initial_review_decide: Callable[
            [
                BrowserOwner,
                BrowserPublicationManifest,
                str,
                str,
                str,
                Literal["confirmed", "rejected"],
            ],
            Awaitable[HumanGateDecisionRecord],
        ]
        | None = None,
    ) -> None:
        if (
            type(enabled) is not bool
            or type(input_digest_key) is not bytes
            or len(input_digest_key) != 32
        ):
            raise ValueError("browser_chat_configuration_invalid")
        self._store, self._publications = store, publications
        self._bindings, self._cipher = bindings, cipher
        self._binder, self._trace = session_binder, trace
        self._input_key, self._enabled = input_digest_key, enabled
        self._fill_stop_reports = fill_stop_reports
        checked_run_id(skill_id)
        self._sessions, self._skill_id = sessions, skill_id
        self._write_gate = write_gate
        self._confirmed_key_digest = confirmed_key_digest
        self._list_artifacts = list_artifacts
        self._read_artifact = read_artifact
        self._teaching_base_digest = teaching_base_digest
        self._teaching_candidate_digest = teaching_candidate_digest
        self._teaching_candidate_version = teaching_candidate_version
        self._teaching_publication = teaching_publication
        self._teaching_plan = teaching_plan
        self._teaching_capture_grant = teaching_capture_grant
        self._teaching_capture_stores = teaching_capture_stores
        self._teaching_published_routes = teaching_published_routes
        self._teaching_review_request = teaching_review_request
        self._teaching_review_decide = teaching_review_decide
        self._initial_review_request = initial_review_request
        self._initial_review_decide = initial_review_decide

    @property
    def skill_id(self) -> str:
        return self._skill_id

    def owner_cache_scope(self, principal: Principal) -> tuple[str, str]:
        captured = authenticated_session.get()
        if (
            not self._enabled
            or captured is None
            or captured.principal != principal
            or captured.expires_at.tzinfo is None
            or captured.expires_at <= datetime.now(UTC)
        ):
            raise BrowserChatError("authentication_required", http_status=401)
        tenant = hmac.digest(
            self._input_key,
            _json(["browser-cache-tenant-v1", principal.org_ctx.tenant_id]),
            hashlib.sha256,
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
            captured is None
            or captured.principal != principal
            or type(captured.fingerprint) is not bytes
            or len(captured.fingerprint) != 32
            or captured.expires_at.tzinfo is None
            or captured.expires_at <= datetime.now(UTC)
        ):
            raise BrowserChatError("authentication_required", http_status=401)
        try:
            if self._binder(principal, bound_session) != bound_session:
                raise SessionBindingError("invalid bound session")
            return BrowserOwner(
                tenant_id=principal.org_ctx.tenant_id,
                user_id=principal.ai_user_id,
                session_id=bound_session,
            )
        except (SessionBindingError, ValueError):
            raise BrowserChatError("session_not_found", http_status=404) from None
        except Exception:
            raise BrowserChatError("browser_session_unavailable") from None

    def _invocation(self, principal: Principal, bound_session: str) -> _Invocation:
        owner = self._owner(principal, bound_session)
        captured = authenticated_session.get()
        if captured is None:
            raise BrowserChatError("authentication_required", http_status=401)
        return _Invocation(principal, owner, captured.fingerprint, captured.expires_at)

    def _recheck(self, invocation: _Invocation) -> None:
        self._owner(invocation.principal, invocation.owner.session_id)
        current = authenticated_session.get()
        if (
            current is None
            or current.principal != invocation.principal
            or current.fingerprint != invocation.fingerprint
            or current.expires_at != invocation.expires_at
        ):
            raise BrowserChatError("authentication_required", http_status=401)

    @staticmethod
    def _ordinary_semantic(message: str, channel: str) -> bytes:
        if (
            type(message) is not str
            or not message.strip()
            or channel not in {"web", "cli", "api", "mock"}
        ):
            raise BrowserChatError("browser_request_input_invalid", http_status=422)
        semantic = _json(
            {
                "schema_version": "browser.request.semantic.ordinary.v1",
                "channel": channel,
                "message": message,
            }
        )
        if len(semantic) > 1_048_576:
            raise BrowserChatError("browser_request_input_invalid", http_status=422)
        return semantic

    async def lookup_ordinary_request(
        self,
        *,
        principal: Principal,
        bound_session: str,
        client_request_id: str,
        message: str,
        channel: Literal["web", "cli", "api", "mock"],
    ) -> ResponseEnvelope | None:
        invocation = self._invocation(principal, bound_session)
        semantic = self._ordinary_semantic(message, channel)
        try:
            request = await self._store.lookup_request(
                invocation.owner,
                client_request_id,
                semantic,
                settle_expired=True,
            )
        except BrowserRunStoreError as error:
            raise self._store_error(error) from None
        except BrowserAuthorizationError as error:
            raise BrowserChatError(error.code, http_status=403) from None
        except Exception:
            raise BrowserChatError("browser_request_lookup_unavailable") from None
        self._recheck(invocation)
        if request is None:
            return None
        if self._fill_stop_reports:
            return await self._replay_canonical(
                invocation,
                request,
                semantic,
                structured=False,
                ordinary=True,
            )
        return self._duplicate(request)

    async def submit(
        self,
        *,
        principal: Principal,
        bound_session: str,
        skill_id: str,
        skill_version: str,
        arguments: dict[str, Any],
        client_request_id: str,
        request_message: str | None = None,
        request_channel: Literal["web", "cli", "api", "mock"] | None = None,
        on_admitted: Callable[[ResponseEnvelope], None] | None = None,
    ) -> ResponseEnvelope:
        """Submit structured input for the verified session's own subject.

        The protected input records a fixed first-party caller and same-subject
        operator. An external adapter must authenticate before invoking this;
        API is legacy Policy metadata, never a caller or delegation grant.
        """
        self._invocation(principal, bound_session)
        if type(arguments) is not dict:
            raise BrowserChatError("browser_request_input_invalid", http_status=422)
        ordinary = request_message is not None or request_channel is not None
        try:
            semantic = (
                self._ordinary_semantic(request_message, request_channel)
                if request_message is not None and request_channel is not None
                else _json(
                    {
                        "schema_version": "browser.request.semantic.v2",
                        "skill_id": skill_id,
                        "skill_version": skill_version,
                        "arguments": arguments,
                    }
                )
            )
        except (TypeError, ValueError):
            raise BrowserChatError("browser_request_input_invalid", http_status=422) from None
        if (
            ordinary
            and (request_message is None or request_channel is None)
            or len(semantic) > 1_048_576
            or len(_json(arguments)) > 16_384
        ):
            raise BrowserChatError("browser_request_input_invalid", http_status=422)

        async def supplied(_capability: CapabilitySpec) -> dict[str, Any]:
            return arguments

        return await self._submit(
            principal=principal,
            bound_session=bound_session,
            skill_id=skill_id,
            skill_version=skill_version,
            semantic=semantic,
            arguments_for=supplied,
            client_request_id=client_request_id,
            channel=request_channel or "api",
            entrypoint="structured_internal",
            ordinary=ordinary,
            on_admitted=on_admitted,
        )

    async def submit_teaching(
        self,
        *,
        principal: Principal,
        bound_session: str,
        skill_id: str,
        skill_version: str,
        arguments: dict[str, Any],
        client_request_id: str,
        replay_source: tuple[str, str, str] | None = None,
    ) -> ResponseEnvelope:
        """Internal prepared-candidate admission; one Run and MAC plan commit."""
        self._invocation(principal, bound_session)
        if (
            type(arguments) is not dict
            or self._teaching_base_digest is None
            or self._teaching_candidate_digest is None
            or self._teaching_publication is None
            or self._teaching_plan is None
        ):
            raise BrowserChatError("browser_teaching_unavailable")
        if replay_source is not None:
            try:
                if type(replay_source) is not tuple or len(replay_source) != 3:
                    raise ValueError("invalid replay source")
                checked_run_id(replay_source[0])
                checked_run_id(replay_source[1])
                if (
                    type(replay_source[2]) is not str
                    or len(replay_source[2]) != 64
                    or any(c not in "0123456789abcdef" for c in replay_source[2])
                ):
                    raise ValueError("invalid replay digest")
            except Exception:
                raise BrowserChatError("browser_teaching_replay_source_invalid") from None
        try:
            semantic_payload: dict[str, object] = {
                "schema_version": "browser.request.semantic.teaching.v1",
                "skill_id": skill_id,
                "skill_version": skill_version,
                "candidate_digest": self._teaching_candidate_digest,
                "source_publication_digest": self._teaching_base_digest,
                "arguments": arguments,
            }
            if replay_source is not None:
                semantic_payload["replay_source"] = replay_source
            semantic = _json(semantic_payload)
        except (TypeError, ValueError):
            raise BrowserChatError("browser_request_input_invalid", http_status=422) from None
        if len(semantic) > 1_048_576 or len(_json(arguments)) > 16_384:
            raise BrowserChatError("browser_request_input_invalid", http_status=422)

        async def supplied(_capability: CapabilitySpec) -> dict[str, Any]:
            return arguments

        return await self._submit(
            principal=principal,
            bound_session=bound_session,
            skill_id=skill_id,
            skill_version=skill_version,
            semantic=semantic,
            arguments_for=supplied,
            client_request_id=client_request_id,
            channel="api",
            entrypoint="structured_internal",
            teaching=True,
            replay_source=replay_source,
        )

    async def submit_initial_validation(
        self,
        *,
        principal: Principal,
        bound_session: str,
        skill_id: str,
        skill_version: str,
        arguments: dict[str, Any],
        client_request_id: str,
    ) -> ResponseEnvelope:
        """Internal prepared first-version Run; verified result is not a replay."""
        self._invocation(principal, bound_session)
        if (
            type(arguments) is not dict
            or self._teaching_base_digest is not None
            or self._teaching_candidate_digest is None
            or self._teaching_publication is None
            or self._teaching_plan is None
        ):
            raise BrowserChatError("browser_initial_validation_unavailable")
        try:
            semantic = _json(
                {
                    "schema_version": "browser.request.semantic.initial_validation.v1",
                    "skill_id": skill_id,
                    "skill_version": skill_version,
                    "publication_digest": self._teaching_candidate_digest,
                    "arguments": arguments,
                }
            )
        except (TypeError, ValueError):
            raise BrowserChatError("browser_request_input_invalid", http_status=422) from None
        if len(semantic) > 1_048_576 or len(_json(arguments)) > 16_384:
            raise BrowserChatError("browser_request_input_invalid", http_status=422)

        async def supplied(_capability: CapabilitySpec) -> dict[str, Any]:
            return arguments

        return await self._submit(
            principal=principal,
            bound_session=bound_session,
            skill_id=skill_id,
            skill_version=skill_version,
            semantic=semantic,
            arguments_for=supplied,
            client_request_id=client_request_id,
            channel="api",
            entrypoint="structured_internal",
            teaching=True,
        )

    async def submit_chat(
        self,
        *,
        principal: Principal,
        bound_session: str,
        skill_id: str,
        message: str,
        client_capabilities: dict[str, Any],
        client_request_id: str,
        channel: Literal["web", "cli", "api", "mock"],
        arguments_for: Callable[[CapabilitySpec], Awaitable[dict[str, Any]]],
    ) -> ResponseEnvelope:
        """Legacy adapter: canonical claim precedes model parsing and other IO."""
        self._invocation(principal, bound_session)
        if client_capabilities.get("browser_async_v1") is not True:
            raise BrowserChatError("browser_async_required", http_status=422)
        try:
            semantic = _json(
                {
                    "schema_version": "browser.request.semantic.v1",
                    "channel": channel,
                    "message": message,
                    "client_capabilities": client_capabilities,
                    "skill_id": skill_id,
                }
            )
            if not message.strip() or len(semantic) > 1_048_576:
                raise ValueError("invalid semantic input")
        except Exception:
            raise BrowserChatError("browser_request_input_invalid", http_status=422) from None
        return await self._submit(
            principal=principal,
            bound_session=bound_session,
            skill_id=skill_id,
            skill_version=None,
            semantic=semantic,
            arguments_for=arguments_for,
            client_request_id=client_request_id,
            channel=channel,
            entrypoint="runtime_chat",
        )

    async def _submit(
        self,
        *,
        principal: Principal,
        bound_session: str,
        skill_id: str,
        skill_version: str | None,
        semantic: bytes,
        arguments_for: Callable[[CapabilitySpec], Awaitable[dict[str, Any]]],
        client_request_id: str,
        channel: Literal["web", "cli", "api", "mock"],
        entrypoint: Literal["runtime_chat", "structured_internal"],
        teaching: bool = False,
        replay_source: tuple[str, str, str] | None = None,
        ordinary: bool = False,
        on_admitted: Callable[[ResponseEnvelope], None] | None = None,
    ) -> ResponseEnvelope:
        invocation = self._invocation(principal, bound_session)
        owner = invocation.owner
        if skill_id != self._skill_id:
            raise BrowserChatError("browser_publication_unavailable")
        if not client_request_id:
            raise BrowserChatError("browser_request_id_required", http_status=422)
        try:
            checked_run_id(client_request_id)
            checked_run_id(skill_id)
            if skill_version is not None:
                checked_run_id(skill_version)
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
                owner,
                client_request_id,
                semantic,
                processing_owner=uuid4().hex,
            )
        except BrowserRunStoreError as error:
            raise self._store_error(error) from None
        except Exception:
            raise BrowserChatError("browser_chat_unavailable") from None
        if not request.parse_winner:
            if ordinary:
                response = (
                    await self._replay_canonical(
                        invocation,
                        request,
                        semantic,
                        structured=False,
                        ordinary=True,
                    )
                    if self._fill_stop_reports
                    else self._duplicate(request)
                )
                if on_admitted is not None and response.status == "running":
                    on_admitted(response)
                return response
            return await self._replay_canonical(
                invocation,
                request,
                semantic,
                structured=entrypoint == "structured_internal",
            )
        if self._fill_stop_reports:
            # One D handoff per session: a new request (never a replay above)
            # cannot admit a second Run while the user still holds the window.
            try:
                active = await self._store.find_active_handoffs(owner)
            except Exception:
                active = None
            if active != ():
                code = "browser_handoff_active" if active else "browser_handoff_check_unavailable"
                try:
                    await self._store.reject_request(request, code)
                except BrowserRunStoreError as rejected:
                    raise self._store_error(rejected) from None
                except Exception:
                    raise BrowserChatError("browser_request_rejection_unavailable") from None
                raise BrowserChatError(code, http_status=409 if active else 503)
        admission_stage: Literal[
            "publication",
            "capability",
            "arguments",
            "schema",
            "binding",
            "publication_current",
            "authorization",
            "input",
            "encryption",
            "run_admission",
            "teaching_plan",
        ] = "publication"
        try:
            publication = (
                await self._teaching_publication(owner, skill_id, skill_version)
                if teaching and self._teaching_publication is not None and skill_version is not None
                else await self._publications.get_active(owner, skill_id)
            )
            if publication is None or (
                skill_version is not None and publication.manifest.skill.version != skill_version
            ):
                raise BrowserChatError("browser_publication_unavailable")
            manifest = publication.manifest
            capability = manifest.capability
            registered_target = (
                manifest.source_kind == "registered_site"
                and capability.target_system in {"oa", "u8", "hikvision_ivms"}
            )
            synthetic_oa = (
                manifest.source_kind == "registered_synthetic_fixture"
                and capability.target_system == "oa"
            )
            controlled_write = manifest.effect == "controlled_write"
            admission_stage = "capability"
            if (
                manifest.skill.skill_id != skill_id
                or manifest.effect not in {"read_only", "controlled_write"}
                or capability.type != ("action" if controlled_write else "query")
                or capability.status != "active"
                or not (registered_target or synthetic_oa)
                or not capability.binding_required
                or capability.execution_identity != "user_delegated"
                or (
                    ordinary
                    and manifest.effect != "read_only"
                    and manifest.execution_mode != "d_form_fill_stop_v1"
                )
                or (
                    controlled_write
                    and (
                        manifest.source_kind != "registered_site"
                        or (
                            entrypoint != "structured_internal"
                            and not (ordinary and manifest.execution_mode == "d_form_fill_stop_v1")
                        )
                        or self._write_gate is None
                        or self._confirmed_key_digest is None
                    )
                )
                or (
                    teaching
                    and (
                        manifest.source_kind != "registered_site"
                        or (
                            manifest.effect != "read_only"
                            and self._teaching_base_digest is not None
                        )
                        or manifest.digest != self._teaching_candidate_digest
                        or self._teaching_plan is None
                    )
                )
            ):
                raise BrowserChatError("browser_capability_unavailable", http_status=403)
            admission_stage = "arguments"
            arguments = await arguments_for(capability)
            if type(arguments) is not dict:
                raise BrowserChatError("browser_request_input_invalid", http_status=422)
            admission_stage = "schema"
            _validate_schema(arguments, capability.input_schema)
            admission_stage = "binding"
            binding = await self._bindings.resolve(principal, bound_session, capability)
            if (
                binding.tenant_id != owner.tenant_id
                or binding.ai_user_id != owner.user_id
                or binding.target_system != capability.target_system
            ):
                raise BrowserChatError("browser_binding_stale", http_status=403)
            admission_stage = "publication_current"
            if not teaching:
                await self._publications.assert_current(owner, manifest)
            admission_stage = "authorization"
            self._recheck(invocation)
            admission_stage = "input"
            run_id = uuid4().hex
            payload: dict[str, object] = {
                "schema_version": "browser.request.input.v2",
                "channel": channel,
                "principal": principal.model_dump(mode="json"),
                "capability_id": capability.capability_id,
                "arguments": arguments,
                "invocation": {
                    "caller": "authenticated_session_v1",
                    "entrypoint": entrypoint,
                    "operator_user_id": owner.user_id,
                    "represented_user_id": owner.user_id,
                },
            }
            if replay_source is not None:
                if not teaching:
                    raise BrowserChatError("browser_teaching_replay_source_invalid")
                payload["replay_source"] = {
                    "task_id": replay_source[0],
                    "run_id": replay_source[1],
                    "draft_digest": replay_source[2],
                }
            input_digest = hmac.digest(
                self._input_key,
                b"browser-run-input-v1\x00" + run_id.encode("ascii") + b"\x00" + _json(payload),
                hashlib.sha256,
            )
            identity = BrowserChatInputIdentity(
                owner,
                request.task_id,
                run_id,
                binding.target_system,
                binding.binding_id,
                binding.binding_revision,
                invocation.fingerprint,
                invocation.expires_at,
                bytes.fromhex(manifest.digest),
                1,
                input_digest,
            )
            admission_stage = "encryption"
            protected = self._cipher.encrypt_input(identity, payload)
            admission_stage = "run_admission"
            admission = RunAdmission(
                owner=owner,
                task_id=request.task_id,
                run_id=run_id,
                target_system=binding.target_system,
                binding_id=binding.binding_id,
                binding_revision=binding.binding_revision,
                auth_fingerprint=invocation.fingerprint,
                auth_expires_at=invocation.expires_at,
                publication_digest=identity.publication_digest,
                input_revision=1,
                input_digest=input_digest,
                protected_input=protected,
            )
            admission_stage = "teaching_plan"
            plan = (
                await self._teaching_plan(admission, manifest)
                if teaching and self._teaching_plan is not None
                else None
            )
        except Exception as error:
            code = (
                error.code
                if isinstance(
                    error,
                    (BrowserChatError, BrowserRunStoreError, BrowserAuthorizationError),
                )
                else "browser_request_input_invalid"
                if isinstance(error, SchemaValidationError)
                else "browser_request_admission_failed"
            )
            # Metadata only: never pass the exception, traceback or protected input to logging.
            exception_type = type(error).__name__
            safe_code = (
                code
                if isinstance(code, str)
                and re.fullmatch(
                    r"(?:browser|request|session|authentication)_[a-z0-9_]{1,80}",
                    code,
                )
                else None
            )
            publication_code = None
            publication_cause = None
            if type(error) is BrowserPublicationError and len(error.args) == 1:
                publication_argument = error.args[0]
                if type(publication_argument) is str and publication_argument in {
                    "browser_initial_validation_unavailable",
                    "browser_teaching_candidate_denied",
                    "browser_initial_publication_exists",
                    "browser_teaching_primitive_denied",
                    "browser_teaching_grant_denied",
                    "browser_publication_capability_changed",
                    "browser_publication_denied",
                    "browser_publication_source_denied",
                    "browser_publication_capability_unavailable",
                    "browser_publication_registry_unavailable",
                    "browser_publication_reference_invalid",
                    "browser_publication_storage_invalid",
                    "browser_publication_manifest_invalid",
                    "browser_publication_store_unavailable",
                }:
                    publication_code = publication_argument
                cause = error.__context__
                if (
                    type(cause) is BrowserAuthorizationError
                    and cause.code == "browser_binding_stale"
                ):
                    publication_cause = "browser_binding_stale"
            admission_diagnostic = {
                "stage": admission_stage,
                "exception_type": exception_type
                if re.fullmatch(
                    r"[A-Za-z][A-Za-z0-9_]{0,63}",
                    exception_type,
                )
                else "UnknownException",
                "trusted_error_code": safe_code
                if type(error)
                in (
                    BrowserChatError,
                    BrowserRunStoreError,
                    BrowserAuthorizationError,
                    SchemaValidationError,
                )
                else publication_code,
                "trusted_cause_code": publication_cause,
                "aggregate_code": safe_code,
                "task_id": request.task_id
                if isinstance(request.task_id, str)
                and re.fullmatch(
                    r"[A-Za-z0-9_-]{1,96}",
                    request.task_id,
                )
                else None,
            }
            # Put JSON in the message so the existing default formatter retains every field.
            logging.getLogger(__name__).error(
                "browser_request_admission_diagnostic %s",
                json.dumps(
                    admission_diagnostic,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
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
            committed = (
                await self._store.accept_teaching(request, admission, plan)
                if teaching and plan is not None
                else await self._store.accept(request, admission)
            )
        except BrowserRunStoreError as error:
            raise self._store_error(error) from None
        except Exception:
            # Commit outcome may be uncertain; canonical replay resolves it.
            # Never issue reject_request after attempting the accept transaction.
            raise BrowserChatError("browser_request_admission_unavailable") from None
        accepted = project_accepted(committed, owner)
        if on_admitted is not None:
            # Synchronous publication after the actual accept COMMIT, before
            # write preparation, Trace or any subsequent cancellation boundary.
            on_admitted(self._envelope(request, "running", accepted.model_dump()))
        if manifest.effect == "controlled_write":
            try:
                await self._ensure_write_preparation(
                    owner,
                    request.task_id,
                    run_id,
                    manifest,
                    arguments,
                )
                current = await self._store.get(owner, request.task_id, run_id)
                accepted = BrowserAcceptedView(
                    task_id=current.task_id,
                    run_id=current.run_id,
                    state_revision=current.state_revision,
                )
            except Exception:
                # The Run has committed and remains queued. A canonical retry
                # can recover the same preview; never claim dispatch readiness.
                raise BrowserChatError("browser_write_preparation_unavailable") from None
        diagnostic = None
        try:
            if request.trace_id is None:
                raise BrowserChatError("browser_task_created_trace_unavailable")
            await self._trace.record_step(
                request.trace_id,
                request.task_id,
                owner.session_id,
                tenant_id=owner.tenant_id,
                ai_user_id=owner.user_id,
                event_type="task_created",
                status="ok",
                capability_id=capability.capability_id,
                attributes={
                    "browser_caller": "authenticated_session_v1",
                    "browser_entrypoint": entrypoint,
                    "browser_representation": "self",
                    "browser_claimed_channel": channel,
                },
            )
        except Exception:
            diagnostic = "browser_task_created_trace_unavailable"
        return self._envelope(request, "running", accepted.model_dump(), diagnostic=diagnostic)

    @staticmethod
    def _write_confirmation_view(preparation: BrowserWritePreparation) -> BrowserWritePreview:
        return BrowserWritePreview(
            request_id=preparation.request_id,
            action_digest=preparation.action_digest,
            request_digest=preparation.request_digest,
            skill_id=preparation.skill_id,
            skill_version=preparation.skill_version,
            publication_digest=preparation.publication_digest,
            site_id=preparation.site_id,
            object_type=preparation.object_type,
            business_key_digest=preparation.business_key_digest,
            parameter_set_digest=preparation.parameter_set_digest,
            binding_revision=preparation.binding_revision,
            write_steps_digest=preparation.write_steps_digest,
            expires_at=preparation.expires_at,
        )

    async def _ensure_write_preparation(
        self,
        owner: BrowserOwner,
        task_id: str,
        run_id: str,
        manifest: BrowserPublicationManifest,
        arguments: dict[str, Any],
    ) -> BrowserWritePreparation:
        gate, key_digest = self._write_gate, self._confirmed_key_digest
        if gate is None or key_digest is None:
            raise BrowserChatError("browser_write_preparation_unavailable")
        run = await self._store.get(owner, task_id, run_id)
        if run.admission.publication_digest.hex() != manifest.digest:
            raise BrowserChatError("browser_publication_unavailable")
        if not self._awaiting_write_preparation(run):
            raise BrowserRunStoreError("browser_write_preparation_stale")
        key_name = manifest.site.read_rule.key_ref.name
        # The form identity is server-bound, never a fourth model/user parameter.
        # The preparation also binds the exact input, owner, binding and Run.
        value = (
            "bound_form"
            if manifest.execution_mode == "d_form_fill_stop_v1"
            else arguments.get(key_name)
        )
        if type(value) is not str or not 1 <= len(value) <= 256 or len(value.encode("utf-8")) > 512:
            raise BrowserChatError("browser_request_input_invalid", http_status=422)
        confirmed = ConfirmedBusinessKey(
            confirmation_ref=run_id,
            object_type=manifest.site.read_rule.object_type,
            key_digest=key_digest(run.admission.input_digest, value),
            value_ref=manifest.site.read_rule.key_ref,
        )
        expected_binding = VersionBinding(
            resource_type="skill",
            resource_id=manifest.skill.skill_id,
            version=manifest.skill.version,
            digest=manifest.skill.digest,
        )
        binding = await gate.get_task_binding(task_id)
        if binding is None:
            try:
                binding = await gate.bind_task(
                    build_task_version_binding_manifest(
                        task_id=task_id,
                        bindings=(expected_binding,),
                        locked_at=datetime.now(UTC),
                    )
                )
            except HumanGateConflictError:
                binding = await gate.get_task_binding(task_id)
                if binding is None:
                    raise
        if binding.bindings != (expected_binding,):
            raise BrowserChatError("browser_write_task_binding_invalid")
        existing = await self._store.load_write_preparation(owner, task_id, run_id)
        preparation = prepare_browser_write(
            run,
            manifest,
            confirmed,
            binding,
            now=existing.requested_at if existing is not None else datetime.now(UTC),
        )
        if existing is not None and existing != preparation:
            raise BrowserChatError("browser_write_preparation_mismatch")
        gate_request = await gate.get_request(preparation.request_id)
        if gate_request is not None and existing is None:
            # The gate may have committed before the preparation event failed.
            # Preserve its original timestamp/TTL and require every bound field.
            preparation = prepare_browser_write(
                run,
                manifest,
                confirmed,
                binding,
                now=gate_request.requested_at,
            )
        if gate_request is not None and gate_request != preparation.gate_request(owner):
            raise BrowserChatError("browser_write_preparation_mismatch")
        if gate_request is None:
            try:
                await gate.create_request(preparation.gate_request(owner))
            except HumanGateConflictError:
                gate_request = await gate.get_request(preparation.request_id)
                if gate_request is None:
                    raise
                if existing is None:
                    preparation = prepare_browser_write(
                        run,
                        manifest,
                        confirmed,
                        binding,
                        now=gate_request.requested_at,
                    )
                if gate_request != preparation.gate_request(owner):
                    raise BrowserChatError("browser_write_preparation_mismatch") from None
        if existing is None:
            try:
                await self._store.record_write_preparation(run, preparation)
            except BrowserRunStoreError as error:
                if error.code != "browser_run_stale":
                    raise
                recorded = await self._store.load_write_preparation(owner, task_id, run_id)
                if recorded != preparation:
                    raise
        return preparation

    @staticmethod
    def _awaiting_write_preparation(run: RunSnapshot) -> bool:
        return (
            run.status == "running"
            and run.phase == "queued"
            and run.worker_id is None
            and run.lease_epoch is None
            and not run.cancel_requested
            and run.effect == "not_sent"
            and run.verification is None
        )

    async def _replay_canonical(
        self,
        invocation: _Invocation,
        request: CanonicalRequest,
        semantic: bytes,
        *,
        structured: bool,
        ordinary: bool = False,
    ) -> ResponseEnvelope:
        """Read the same Run; recover only its still-unconsumed write preparation."""
        self._recheck(invocation)
        owner = invocation.owner
        if request.owner != owner:
            raise BrowserChatError("browser_run_projection_invalid")
        if request.run is None:
            return self._duplicate(request)
        try:
            run = await self._store.get(owner, request.task_id, request.run.run_id)
            if (structured or ordinary) and self._awaiting_write_preparation(run):
                # Ordinary semantic identity contains the original message,
                # not parsed parameters. Reuse the admitted protected input.
                payload = (
                    self._cipher.decrypt_input(run.admission) if ordinary else json.loads(semantic)
                )
                publication = await self._publications.get_frozen(
                    owner,
                    run.admission.publication_digest.hex(),
                )
                plan = None
                if (
                    publication is None
                    and run.admission.publication_digest.hex() == self._teaching_candidate_digest
                ):
                    plan = await self._store.load_teaching_plan(owner, run.task_id, run.run_id)
                    if (
                        plan is None
                        or plan.task_id != run.task_id
                        or plan.run_id != run.run_id
                        or plan.publication_digest != self._teaching_candidate_digest
                    ):
                        raise BrowserChatError("browser_teaching_candidate_unavailable")
                    publication = await self._prepared_teaching_candidate(
                        owner,
                        plan.publication_digest,
                    )
                if (
                    publication is None
                    or publication.manifest.digest != run.admission.publication_digest.hex()
                    or (
                        ordinary
                        and (
                            publication.state != "active"
                            or publication.manifest.execution_mode != "d_form_fill_stop_v1"
                            or payload.get("schema_version") != "browser.request.input.v2"
                            or payload.get("capability_id")
                            != publication.manifest.capability.capability_id
                        )
                    )
                    or (
                        not ordinary
                        and (
                            payload.get("skill_id") != publication.manifest.skill.skill_id
                            or payload.get("skill_version") != publication.manifest.skill.version
                        )
                    )
                ):
                    raise BrowserChatError("browser_publication_unavailable")
                manifest = publication.manifest
                if manifest.effect == "controlled_write" and publication.state in {
                    "active",
                    "prepared",
                }:
                    if publication.state == "prepared" and (
                        plan is None
                        or plan.purpose != "initial_validation"
                        or self._teaching_base_digest is not None
                    ):
                        raise BrowserChatError("browser_write_preparation_unavailable")
                    if publication.state == "active":
                        await self._publications.assert_current(owner, manifest)
                    arguments = payload.get("arguments")
                    if type(arguments) is not dict:
                        raise BrowserChatError("browser_request_input_invalid", http_status=422)
                    _validate_schema(arguments, manifest.capability.input_schema)
                    self._recheck(invocation)
                    try:
                        await self._ensure_write_preparation(
                            owner,
                            run.task_id,
                            run.run_id,
                            manifest,
                            arguments,
                        )
                    except BrowserRunStoreError as error:
                        if error.code != "browser_write_preparation_stale":
                            raise
                        fresh = await self._store.get(owner, run.task_id, run.run_id)
                        if self._awaiting_write_preparation(fresh):
                            raise
                    run = await self._store.get(owner, run.task_id, run.run_id)
            self._recheck(invocation)
            return self._duplicate(request, run=run)
        except BrowserRunStoreError as error:
            raise self._store_error(error) from None
        except BrowserAuthorizationError as error:
            raise BrowserChatError(error.code, http_status=403) from None
        except SchemaValidationError:
            raise BrowserChatError("browser_request_input_invalid", http_status=422) from None
        except BrowserChatError:
            raise
        except Exception:
            # A committed Run must not be projected as ready when recovery of
            # its preparation failed. Cancellation still propagates unchanged.
            raise BrowserChatError("browser_write_preparation_unavailable") from None

    def _duplicate(
        self,
        request: CanonicalRequest,
        *,
        run: RunSnapshot | None = None,
    ) -> ResponseEnvelope:
        if request.run is not None:
            run = run or request.run
            if run.owner != request.owner or run.task_id != request.task_id:
                raise BrowserChatError("browser_run_projection_invalid")
            data = BrowserAcceptedView(
                task_id=run.task_id,
                run_id=run.run_id,
                state_revision=run.state_revision,
            ).model_dump()
            return self._envelope(request, "running", data)
        if request.task_status == "failed":
            return self._envelope(
                request,
                "failed",
                {
                    "kind": "failed",
                    "error_code": request.error_code or "browser_request_abandoned",
                },
            )
        return self._envelope(request, "running", {"kind": "pending", "task_id": request.task_id})

    @staticmethod
    def _envelope(
        request: CanonicalRequest,
        status: Literal["running", "failed"],
        data: dict[str, Any],
        *,
        diagnostic: str | None = None,
    ) -> ResponseEnvelope:
        text = (
            "Browser request is being prepared."
            if data.get("kind") == "pending"
            else "Browser request accepted for processing."
            if status == "running"
            else "Browser request could not be accepted."
        )
        return ResponseEnvelope(
            response_id=uuid4().hex,
            task_id=request.task_id,
            session_id=request.owner.session_id,
            status=status,
            message=text,
            fallback_text=text,
            ui=UIComponent(component_type="none", action="none"),
            data=data,
            trace_id=request.trace_id or "",
            trace_summary=diagnostic,
        )

    async def get(
        self,
        principal: Principal,
        bound_session: str,
        task_id: str,
        run_id: str,
    ) -> BrowserRunResponse:
        invocation = self._invocation(principal, bound_session)
        owner = invocation.owner
        try:
            run = await self._store.get(owner, task_id, run_id)
            value = None
            if run.status == "completed" and run.verification == "verified":
                publication = await self._publications.get_frozen(
                    owner,
                    run.admission.publication_digest.hex(),
                )
                if (
                    publication is None
                    and run.admission.publication_digest.hex() == self._teaching_candidate_digest
                ):
                    plan = await self._store.load_teaching_plan(owner, task_id, run_id)
                    if (
                        plan is None
                        or plan.task_id != task_id
                        or plan.run_id != run_id
                        or plan.publication_digest != self._teaching_candidate_digest
                    ):
                        raise BrowserChatError("browser_teaching_candidate_unavailable")
                    publication = await self._prepared_teaching_candidate(
                        owner,
                        plan.publication_digest,
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
                    fresh.admission != run.admission
                    or fresh.status != "completed"
                    or fresh.verification != "verified"
                    or fresh.result_digest != run.result_digest
                    or fresh.protected_result != run.protected_result
                ):
                    raise BrowserChatError("browser_target_stale", http_status=409)
                run = fresh
            artifacts: tuple[BrowserArtifactView, ...] = ()
            if (
                run.status == "completed"
                and run.verification == "verified"
                and run.verification_evidence_digest is not None
                and self._list_artifacts is not None
            ):
                artifacts = await self._list_artifacts(run)
                fresh = await self._store.get(owner, task_id, run_id)
                if (
                    fresh.admission != run.admission
                    or fresh.status != run.status
                    or fresh.verification != run.verification
                    or fresh.result_digest != run.result_digest
                ):
                    raise BrowserChatError("browser_target_stale", http_status=409)
                run = fresh
            preview = await self._current_write_preview(run)
            if preview is not None:
                fresh = await self._store.get(owner, task_id, run_id)
                if fresh != run:
                    raise BrowserChatError("browser_target_stale", http_status=409)
                run = fresh
            fill_stop = (
                await self._store.load_fill_stop(owner, task_id, run_id)
                if self._fill_stop_reports
                else None
            )
            handoff = await self._handoff_view(owner, run) if self._fill_stop_reports else None
            self._recheck(invocation)
            return BrowserRunResponse(
                run=self._view(run, artifacts=artifacts),
                value=value,
                write_preview=preview,
                fill_stop=fill_stop,
                handoff=handoff,
            )
        except BrowserRunStoreError as error:
            raise self._store_error(error) from None
        except BrowserChatError:
            raise
        except Exception as error:
            try:
                exception_type = type(error).__name__
                logging.getLogger(__name__).warning(
                    "browser_fallback_diagnostic stage=run_projection exception_type=%s",
                    exception_type
                    if exception_type.isascii()
                    and exception_type.isidentifier()
                    and len(exception_type) <= 64
                    else "UnknownException",
                )
            except Exception:
                pass  # Diagnostic failure must preserve the original failure path.
            raise BrowserChatError("browser_run_projection_invalid") from None

    async def _handoff_view(
        self,
        owner: BrowserOwner,
        run: RunSnapshot,
    ) -> BrowserHandoffView | None:
        """Sealed D handoff for its owner; values decrypt only after store.get authority."""
        record = await self._store.load_handoff(owner, run.task_id, run.run_id)
        if record is None:
            return None
        publication = await self._publications.get_frozen(
            owner,
            run.admission.publication_digest.hex(),
        )
        if publication is None:
            raise BrowserChatError("browser_publication_unavailable")
        arguments = self._cipher.decrypt_input(run.admission).get("arguments")
        if not isinstance(arguments, dict):
            raise BrowserChatError("browser_run_projection_invalid")
        _validate_schema(arguments, publication.manifest.capability.input_schema)
        return BrowserHandoffView(
            sealed_at=record.sealed_at,
            deadline=record.deadline,
            fields=BrowserHandoffFields(
                category=arguments["category"],
                position=arguments["position"],
                description=arguments["description"],
            ),
        )

    async def find_active_handoffs(
        self,
        *,
        principal: Principal,
        bound_session: str,
    ) -> tuple[tuple[str, str], ...]:
        invocation = self._invocation(principal, bound_session)
        if not self._fill_stop_reports:
            return ()
        try:
            found = await self._store.find_active_handoffs(invocation.owner)
        except BrowserRunStoreError as error:
            if error.code == "browser_run_store_unavailable":
                # Known DB availability failure: callers fail closed, never "none".
                raise BrowserChatError("browser_chat_unavailable") from None
            raise self._store_error(error) from None
        except BrowserAuthorizationError as error:
            raise BrowserChatError(error.code, http_status=403) from None
        self._recheck(invocation)
        return found

    async def read_artifact(
        self,
        principal: Principal,
        bound_session: str,
        task_id: str,
        run_id: str,
        reference: str,
    ) -> tuple[str, bytes]:
        """Internal authenticated retrieval; transport is installed separately."""
        invocation = self._invocation(principal, bound_session)
        if self._read_artifact is None:
            raise BrowserChatError("browser_file_unavailable")
        owner = invocation.owner
        try:
            run = await self._store.get(owner, task_id, run_id)
            if (
                run.status != "completed"
                or run.verification != "verified"
                or run.verification_evidence_digest is None
            ):
                raise BrowserChatError("browser_file_unavailable", http_status=404)
            media_type, payload = await self._read_artifact(run, reference)
            fresh = await self._store.get(owner, task_id, run_id)
            if (
                fresh.admission != run.admission
                or fresh.status != run.status
                or fresh.verification != run.verification
                or fresh.result_digest != run.result_digest
            ):
                raise BrowserChatError("browser_target_stale", http_status=409)
            self._recheck(invocation)
            return media_type, payload
        except BrowserChatError:
            raise
        except BrowserRunStoreError as error:
            raise self._store_error(error) from None
        except Exception:
            raise BrowserChatError("browser_file_unavailable") from None

    async def _prepared_teaching_candidate(
        self,
        owner: BrowserOwner,
        digest: str,
    ) -> BrowserPublicationRecord:
        resolve = self._teaching_publication
        version = self._teaching_candidate_version
        if resolve is None or version is None or digest != self._teaching_candidate_digest:
            raise BrowserChatError("browser_teaching_candidate_unavailable")
        record = await resolve(owner, self._skill_id, version)
        if record is None or record.state != "prepared" or record.manifest.digest != digest:
            raise BrowserChatError("browser_teaching_candidate_unavailable")
        return record

    async def _read_teaching_source(
        self,
        owner: BrowserOwner,
        task_id: str,
        run_id: str,
    ) -> RunSnapshot:
        select = self._teaching_capture_stores
        store = (await select(owner, task_id, run_id))[0] if select is not None else self._store
        return await store.get(owner, task_id, run_id)

    async def _capture_teaching_context(
        self,
        principal: Principal,
        bound_session: str,
        task_id: str,
        run_id: str,
    ) -> tuple[
        RunTeachingSource, RunTeachingAuthority, ScopeBinding, RunSnapshot, TeachingPlan | None
    ]:
        """Read a verified prepared or active exploration Run into a draft source."""
        invocation = self._invocation(principal, bound_session)
        owner = invocation.owner
        grant = self._teaching_capture_grant
        if grant is None:
            raise BrowserChatError("browser_teaching_unavailable")
        try:
            select_stores = self._teaching_capture_stores
            source_store, source_publications = (
                await select_stores(owner, task_id, run_id)
                if select_stores is not None
                else (self._store, self._publications)
            )
            run = await source_store.get(owner, task_id, run_id)
            plan = await source_store.load_teaching_plan(owner, task_id, run_id)
            candidate_digest = (
                plan.publication_digest if plan is not None else self._teaching_candidate_digest
            )
            source_digest = (
                plan.source_publication_digest
                if plan is not None
                else run.admission.publication_digest.hex()
            )
            if (
                candidate_digest is None
                or run.lease_epoch is None
                or (
                    plan is not None
                    and (
                        plan.task_id != task_id
                        or plan.run_id != run_id
                        or plan.publication_digest != run.admission.publication_digest.hex()
                    )
                )
                or (plan is None and source_digest != self._teaching_base_digest)
                or await grant(owner, candidate_digest) is not True
            ):
                raise BrowserChatError("browser_teaching_capture_denied", http_status=403)
            steps = await source_store.read_teaching_steps(owner, task_id, run_id)
            terminal_after = await source_store.load_traversal_terminal(
                owner,
                task_id,
                run_id,
            )
            candidate = await self._prepared_teaching_candidate(
                owner,
                candidate_digest,
            )
            base = await source_publications.get_frozen(owner, source_digest)
            if (
                candidate is None
                or base is None
                or candidate.manifest.digest != candidate_digest
                or base.manifest.digest != source_digest
                or (
                    plan is None
                    and (
                        base.state != "active"
                        or base.manifest.site.exploration is None
                        or base.manifest.source_kind != "registered_site"
                        or base.manifest.effect != "read_only"
                    )
                )
            ):
                raise BrowserChatError("browser_teaching_capture_denied", http_status=403)
            binding = ScopeBinding(
                owner=owner,
                binding_id=run.admission.binding_id,
                binding_revision=run.admission.binding_revision,
                authorization_run_id=run_id,
                evidence_version=run.admission.auth_evidence_version,
                lease_epoch=run.lease_epoch,
            )
            frozen = {
                candidate.manifest.digest: candidate.manifest,
                base.manifest.digest: base.manifest,
            }
            rules = {item.step.step_id: item for item in base.manifest.site.primitive_rules}
            authority = RunTeachingAuthority(
                current_binding=lambda: binding,
                read_run=lambda requested, scope: (
                    run
                    if (requested == run_id and scope == binding)
                    else (_raise_teaching_source_unavailable())
                ),
                read_steps=lambda requested, scope: (
                    steps
                    if (requested == run_id and scope == binding)
                    else (_raise_teaching_source_unavailable())
                ),
                read_parameter_set_ref=lambda requested, scope: (
                    (
                        plan.parameter_set_ref
                        if plan is not None
                        else run.admission.input_digest.hex()
                    )
                    if (requested == run_id and scope == binding)
                    else (_raise_teaching_source_unavailable())
                ),
                frozen_publication=lambda digest, scope: (
                    frozen[digest]
                    if scope == binding and digest in frozen
                    else (_raise_teaching_source_unavailable())
                ),
                primitive_rule=lambda primitive_id, publication: (
                    rules.get(primitive_id) if publication == base.manifest else None
                ),
                authorize_capture=lambda requested, scope: requested == run_id and scope == binding,
                read_plan=lambda requested, scope: (
                    plan if requested == run_id and scope == binding else None
                ),
                read_traversal_terminal=lambda requested, scope: (
                    terminal_after
                    if requested == run_id and scope == binding
                    else (_raise_teaching_source_unavailable())
                ),
            )
            source = capture_authorized_run(run_id, binding=binding, authority=authority)
            fresh = await source_store.get(owner, task_id, run_id)
            if fresh != run or await grant(owner, candidate_digest) is not True:
                raise BrowserChatError("browser_target_stale", http_status=409)
            self._recheck(invocation)
            return source, authority, binding, run, plan
        except BrowserChatError:
            raise
        except Exception:
            raise BrowserChatError("browser_teaching_capture_unavailable") from None

    async def capture_teaching(
        self,
        principal: Principal,
        bound_session: str,
        task_id: str,
        run_id: str,
    ) -> RunTeachingSource:
        return (
            await self._capture_teaching_context(
                principal,
                bound_session,
                task_id,
                run_id,
            )
        )[0]

    async def _compile_teaching_context(
        self,
        principal: Principal,
        bound_session: str,
        task_id: str,
        run_id: str,
        parameter_whitelist: tuple[ParameterApproval, ...],
    ) -> tuple[DurableTeachingDraft, DurableTeachingCompilerAuthority, RunSnapshot]:
        """Compile a verified Run only into the installed prepared candidate."""
        routes_for = self._teaching_published_routes
        resolve = self._teaching_publication
        grant = self._teaching_capture_grant
        if routes_for is None or resolve is None or grant is None:
            raise BrowserChatError("browser_teaching_unavailable")
        source, run_authority, binding, run, plan = await self._capture_teaching_context(
            principal,
            bound_session,
            task_id,
            run_id,
        )
        try:
            owner = binding.owner
            candidate_digest = (
                plan.publication_digest if plan is not None else self._teaching_candidate_digest
            )
            if candidate_digest is None:
                raise BrowserChatError("browser_teaching_candidate_unavailable")
            candidate = await self._prepared_teaching_candidate(owner, candidate_digest)
            current = await resolve(
                owner,
                candidate.manifest.skill.skill_id,
                candidate.manifest.skill.version,
            )
            if current != candidate:
                raise BrowserChatError("browser_teaching_candidate_unavailable")
            base = run_authority.frozen_publication(
                source.manifest.source_publication_digest,
                binding,
            )
            published = await routes_for(owner, candidate.manifest.skill.skill_id)
            compiler = DurableTeachingCompilerAuthority(
                run=run_authority,
                authorize_target=lambda scope, source_base, target: (
                    scope == binding
                    and source_base == base
                    and target == candidate.manifest.skill.skill_id
                ),
                unpublished_combination=lambda scope, target, primitives: (
                    scope == binding
                    and target == candidate.manifest.skill.skill_id
                    and primitives not in published
                ),
            )
            draft = compile_authorized_run(
                source,
                binding=binding,
                target_skill_id=candidate.manifest.skill.skill_id,
                version=candidate.manifest.skill.version,
                site_version=candidate.manifest.site.version,
                parameter_whitelist=parameter_whitelist,
                authority=compiler,
            )
            if draft.publication != candidate.manifest:
                raise BrowserChatError("browser_teaching_candidate_mismatch")
            if (
                await self._read_teaching_source(owner, task_id, run_id) != run
                or await routes_for(owner, candidate.manifest.skill.skill_id) != published
                or await grant(owner, candidate.manifest.digest) is not True
            ):
                raise BrowserChatError("browser_target_stale", http_status=409)
            self._recheck(self._invocation(principal, bound_session))
            return draft, compiler, run
        except BrowserChatError:
            raise
        except Exception:
            raise BrowserChatError("browser_teaching_compile_unavailable") from None

    async def compile_teaching(
        self,
        principal: Principal,
        bound_session: str,
        task_id: str,
        run_id: str,
        parameter_whitelist: tuple[ParameterApproval, ...],
    ) -> DurableTeachingDraft:
        return (
            await self._compile_teaching_context(
                principal,
                bound_session,
                task_id,
                run_id,
                parameter_whitelist,
            )
        )[0]

    async def request_teaching_review(
        self,
        principal: Principal,
        bound_session: str,
        *,
        draft_digest: str,
        replay_digest: str,
        request_id: str,
    ) -> HumanGateRequest:
        """Create a value-free HumanGate request for this installed candidate."""
        invocation = self._invocation(principal, bound_session)
        callback, digest = self._teaching_review_request, self._teaching_candidate_digest
        if callback is None or digest is None:
            raise BrowserChatError("browser_teaching_review_unavailable")
        try:
            publication = await self._prepared_teaching_candidate(invocation.owner, digest)
            self._recheck(invocation)
            request = await callback(
                invocation.owner,
                publication.manifest,
                draft_digest,
                replay_digest,
                request_id,
            )
            self._recheck(invocation)
            return request
        except BrowserChatError:
            raise
        except Exception:
            raise BrowserChatError("browser_teaching_review_unavailable") from None

    async def decide_teaching_review(
        self,
        principal: Principal,
        bound_session: str,
        *,
        draft_digest: str,
        replay_digest: str,
        request_id: str,
        decision: Literal["confirmed", "rejected"],
    ) -> HumanGateDecisionRecord:
        """Record the reviewer subject's exact decision through existing HumanGate."""
        invocation = self._invocation(principal, bound_session)
        callback, digest = self._teaching_review_decide, self._teaching_candidate_digest
        if callback is None or digest is None or decision not in {"confirmed", "rejected"}:
            raise BrowserChatError("browser_teaching_review_unavailable")
        try:
            publication = await self._prepared_teaching_candidate(invocation.owner, digest)
            self._recheck(invocation)
            record = await callback(
                invocation.owner,
                publication.manifest,
                draft_digest,
                replay_digest,
                request_id,
                decision,
            )
            self._recheck(invocation)
            return record
        except BrowserChatError:
            raise
        except Exception:
            raise BrowserChatError("browser_teaching_review_unavailable") from None

    async def request_initial_review(
        self,
        principal: Principal,
        bound_session: str,
        *,
        task_id: str,
        run_id: str,
        request_id: str,
    ) -> HumanGateRequest:
        invocation = self._invocation(principal, bound_session)
        callback = self._initial_review_request
        digest = self._teaching_candidate_digest
        if callback is None or digest is None or self._teaching_base_digest is not None:
            raise BrowserChatError("browser_initial_review_unavailable")
        try:
            publication = await self._prepared_teaching_candidate(invocation.owner, digest)
            self._recheck(invocation)
            request = await callback(
                invocation.owner,
                publication.manifest,
                task_id,
                run_id,
                request_id,
            )
            self._recheck(invocation)
            return request
        except BrowserChatError:
            raise
        except Exception:
            raise BrowserChatError("browser_initial_review_unavailable") from None

    async def decide_initial_review(
        self,
        principal: Principal,
        bound_session: str,
        *,
        task_id: str,
        run_id: str,
        request_id: str,
        decision: Literal["confirmed", "rejected"],
    ) -> HumanGateDecisionRecord:
        invocation = self._invocation(principal, bound_session)
        callback = self._initial_review_decide
        digest = self._teaching_candidate_digest
        if (
            callback is None
            or digest is None
            or self._teaching_base_digest is not None
            or decision not in {"confirmed", "rejected"}
        ):
            raise BrowserChatError("browser_initial_review_unavailable")
        try:
            publication = await self._prepared_teaching_candidate(invocation.owner, digest)
            self._recheck(invocation)
            record = await callback(
                invocation.owner,
                publication.manifest,
                task_id,
                run_id,
                request_id,
                decision,
            )
            self._recheck(invocation)
            return record
        except BrowserChatError:
            raise
        except Exception:
            raise BrowserChatError("browser_initial_review_unavailable") from None

    async def decide_write(
        self,
        principal: Principal,
        bound_session: str,
        task_id: str,
        run_id: str,
        decision: Literal["confirmed", "rejected"],
    ) -> BrowserRunResponse:
        """Internal same-subject HumanGate decision for an exact queued Run."""
        invocation = self._invocation(principal, bound_session)
        gate = self._write_gate
        if gate is None or decision not in {"confirmed", "rejected"}:
            raise BrowserChatError("browser_write_confirmation_unavailable")
        owner = invocation.owner
        try:
            run = await self._store.get(owner, task_id, run_id)
            preparation = await self._store.load_write_preparation(owner, task_id, run_id)
            if (
                preparation is None
                or preparation.run_id != run_id
                or preparation.task_id != task_id
                or preparation.publication_digest != run.admission.publication_digest.hex()
            ):
                raise BrowserChatError("browser_write_confirmation_unavailable", http_status=409)
            request = await gate.get_request(preparation.request_id)
            if request != preparation.gate_request(owner):
                raise BrowserChatError("browser_write_confirmation_unavailable", http_status=409)
            candidate = HumanGateDecisionRecord(
                request_id=request.request_id,
                task_id=task_id,
                decided_by_ai_user_id=owner.user_id,
                decided_session_id=owner.session_id,
                decided_tenant_id=owner.tenant_id,
                decision=decision,
                request_digest=request.request_digest,
                binding_manifest_digest=request.binding_manifest_digest,
                decided_at=datetime.now(UTC),
            )
            self._recheck(invocation)
            existing = await gate.get_decision(request.request_id)
            if existing is None:
                if (
                    run.phase != "queued"
                    or run.cancel_requested
                    or request.expires_at <= datetime.now(UTC)
                ):
                    raise BrowserChatError(
                        "browser_write_confirmation_unavailable",
                        http_status=409,
                    )
                try:
                    existing = await gate.record_decision(candidate)
                except HumanGateConflictError:
                    existing = await gate.get_decision(request.request_id)
            if (
                existing is None
                or not request.requested_at <= existing.decided_at < request.expires_at
                or existing.model_dump(exclude={"decided_at"})
                != candidate.model_dump(exclude={"decided_at"})
            ):
                raise BrowserChatError("browser_write_confirmation_conflict", http_status=409)
            if self._fill_stop_reports and decision == "rejected":
                await self._store.request_cancel(owner, task_id, run_id)
            fresh = await self._store.get(owner, task_id, run_id)
            if fresh.admission != run.admission:
                raise BrowserChatError("browser_target_stale", http_status=409)
            self._recheck(invocation)
            return BrowserRunResponse(run=self._view(fresh), value=None)
        except BrowserChatError:
            raise
        except BrowserRunStoreError as error:
            raise self._store_error(error) from None
        except Exception:
            raise BrowserChatError("browser_write_confirmation_unavailable") from None

    async def _current_write_preview(self, run: RunSnapshot) -> BrowserWritePreview | None:
        gate = self._write_gate
        if run.phase != "queued" or run.cancel_requested or gate is None:
            return None
        preparation = await self._store.load_write_preparation(
            run.owner,
            run.task_id,
            run.run_id,
        )
        if preparation is None:
            return None
        if (
            preparation.task_id != run.task_id
            or preparation.run_id != run.run_id
            or preparation.publication_digest != run.admission.publication_digest.hex()
        ):
            raise BrowserChatError("browser_write_confirmation_unavailable", http_status=409)
        request = await gate.get_request(preparation.request_id)
        if (
            preparation.expires_at <= datetime.now(UTC)
            or request != preparation.gate_request(run.owner)
            or request.expires_at <= datetime.now(UTC)
        ):
            return None
        return self._write_confirmation_view(preparation)

    async def write_preview(
        self,
        principal: Principal,
        bound_session: str,
        task_id: str,
        run_id: str,
    ) -> BrowserWritePreview:
        """Internal value-free preview for the same authenticated Run owner."""
        invocation = self._invocation(principal, bound_session)
        try:
            run = await self._store.get(invocation.owner, task_id, run_id)
            preview = await self._current_write_preview(run)
            if preview is None:
                raise BrowserChatError("browser_write_confirmation_unavailable", http_status=409)
            if await self._store.get(invocation.owner, task_id, run_id) != run:
                raise BrowserChatError("browser_target_stale", http_status=409)
            self._recheck(invocation)
            return preview
        except BrowserChatError:
            raise
        except BrowserRunStoreError as error:
            raise self._store_error(error) from None
        except Exception:
            raise BrowserChatError("browser_write_confirmation_unavailable") from None

    async def cancel(
        self,
        principal: Principal,
        bound_session: str,
        task_id: str,
        run_id: str,
    ) -> BrowserRunResponse:
        invocation = self._invocation(principal, bound_session)
        owner = invocation.owner
        try:
            run = await self._store.request_cancel(owner, task_id, run_id)
            self._recheck(invocation)
            # Requesting cancellation is not proof that execution stopped.
            return BrowserRunResponse(run=self._view(run), value=None)
        except BrowserRunStoreError as error:
            raise self._store_error(error) from None
        except BrowserChatError:
            raise
        except Exception:
            raise BrowserChatError("browser_run_projection_invalid") from None

    async def check_handoff(
        self,
        principal: Principal,
        bound_session: str,
        task_id: str,
        run_id: str,
    ) -> BrowserRunResponse:
        invocation = self._invocation(principal, bound_session)
        try:
            run = await self._store.request_handoff_check(invocation.owner, task_id, run_id)
            self._recheck(invocation)
            # A requested check is not a result; the worker settles it.
            return BrowserRunResponse(run=self._view(run), value=None)
        except BrowserRunStoreError as error:
            raise self._store_error(error) from None
        except BrowserChatError:
            raise
        except Exception:
            raise BrowserChatError("browser_run_projection_invalid") from None

    @staticmethod
    def _view(
        run: RunSnapshot,
        *,
        artifacts: tuple[BrowserArtifactView, ...] = (),
    ) -> BrowserRunView:
        terminal = run.status in {"completed", "failed", "cancelled"}
        result = None
        progress = None
        if terminal:
            # Model validation checks completed implies verified, fixed error codes,
            # effect uncertainty, terminal revision and cancellation consistency.
            result = BrowserResultView.model_validate(
                {
                    "business": run.status,
                    "effect": run.effect,
                    "verification": run.verification,
                    "cleanup": run.cleanup,
                    "error_code": run.error_code,
                    "dispatch_failure_code": run.dispatch_failure_code,
                    "terminal_revision": run.terminal_revision,
                }
            )
        else:
            progress = BrowserProgressView.model_validate({"phase": run.phase})
        return BrowserRunView(
            task_id=run.task_id,
            run_id=run.run_id,
            state_revision=run.state_revision,
            status=run.status,
            progress=progress,
            result=result,
            cancel=BrowserCancelView(
                requested=run.cancel_requested,
                acknowledged=run.cancel_acknowledged,
            ),
            artifacts=artifacts,
        )

    @staticmethod
    def _store_error(error: BrowserRunStoreError) -> BrowserChatError:
        status = (
            409
            if error.code
            in {"request_key_conflict", "browser_handoff_not_waiting", "browser_handoff_expired"}
            else (
                404 if error.code in {"browser_run_not_found", "browser_request_not_found"} else 503
            )
        )
        return BrowserChatError(error.code, http_status=status)
