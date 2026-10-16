"""Actual task factory and management adapter. No environment IO at import."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.browser_skill.models import BrowserOwner
from app.infra.auth.crypto import HMACSessionToken, PrincipalSessionBinder
from app.infra.browser.d_form_authority import (
    DFormActor,
    DFormPublicationGrants,
    DFormRequestActor,
    FixedDFormCleanupGrant,
    FixedDFormPublicationGrants,
    OwnedDFormTargetAuthority,
    current_actor,
)
from app.infra.browser.d_form_configuration import (
    ActorReference,
    DFormConfigurationError,
    LoadedDFormConfiguration,
    decode_key,
    decode_ring,
    missing_conditions,
)
from app.infra.browser.d_form_execution import DFormOptions, DFormRuntime
from app.infra.browser.d_form_installation import (
    DFormInstallationDependencies,
    DFormProductionInputs,
    build_d_form_production_installation,
)
from app.infra.browser.d_form_manifest import build_fill_stop_manifest, source_digest
from app.infra.browser.d_form_owned_browser import (
    OwnedOALoginContract,
    _D_FORM_LOG_CODES,
    log_d_form_stage,
    owned_provider_digest,
    validate_owned_paths,
)
from app.infra.browser.local_installation import PostgreSQLLocalCleanupGrant
from app.infra.browser.teaching_installation import (
    InstalledReplayProofLookup,
    PostgreSQLTeachingPublicationReview,
)
from app.infra.persistence.browser.authorization import PostgreSQLBrowserBindingReader
from app.infra.persistence.browser.crypto import BrowserClaimProofContext
from app.infra.persistence.browser.leases import BrowserProviderPool, BrowserProviderPoolRegistry
from app.ports.auth import Principal, SessionTokenPort, VerifiedSessionToken, authenticated_session
from app.ports.browser_fill_stop import (
    DFormHandoffView,
    DFormManagementCommand,
    DFormManagementError,
    DFormManagementOperation,
    DFormManagementState,
    DFormReadinessCode,
)
from app.ports.browser_publication_store import BrowserPublicationError, PublicationOperation
from app.ports.browser_store import BrowserBindingKey, BrowserLeaseError
from app.ports.credential_vault import BrowserAuthorizationError, BrowserBindingFact

if TYPE_CHECKING:
    from app.composition import ProductionComponents
    from app.infra.browser.composition import BrowserVerticalComponents

_configuration: ContextVar[LoadedDFormConfiguration | None] = ContextVar(
    "d_form_task_configuration",
    default=None,
)


def _management_error_code(error: Exception) -> object:
    # Publication errors carry a fixed code in args, not a .code attribute.
    # The existing logger still filters every result through its closed set.
    if isinstance(error, BrowserPublicationError):
        return error.args[0] if len(error.args) == 1 and type(error.args[0]) is str else "error"
    if isinstance(error, (DFormManagementError, BrowserLeaseError, BrowserAuthorizationError)):
        return error.code
    if isinstance(error, SQLAlchemyError):
        return "store_error"
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, (TypeError, ValueError)):
        return "value_error"
    if isinstance(error, OSError):
        return "os_error"
    return "error"


def _log_cleanup_failure(step: str, error: Exception, began: float) -> None:
    """Keep the failed close step without writing exception or business values."""
    try:
        code = _management_error_code(error)
        safe_code = code if type(code) is str and code in _D_FORM_LOG_CODES else "error"
        name = type(error).__name__
        safe_name = name if name in {
            "DFormManagementError", "BrowserLeaseError", "BrowserAuthorizationError",
            "BrowserRunStoreError", "BrowserReadExecutionError", "ProgrammingError",
            "DBAPIError", "OperationalError", "IntegrityError", "SQLAlchemyError",
            "TimeoutError", "ValueError", "TypeError", "OSError",
        } else "UnknownException"
        sqlstate = "none"
        if isinstance(error, SQLAlchemyError):
            value = getattr(getattr(error, "orig", None), "sqlstate", None)
            if (type(value) is str and len(value) == 5
                    and all(char in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ" for char in value)):
                sqlstate = value
        logging.getLogger("uvicorn.error.d_form").error(
            "d_form_cleanup_failure step=%s exception_type=%s error_code=%s sqlstate=%s elapsed_ms=%d",
            step, safe_name, safe_code, sqlstate,
            max(0, int((time.monotonic() - began) * 1000)),
        )
    except Exception:
        pass  # A diagnostic failure must not replace the cleanup exception.


def pool_manifest_digest(origin: str, chromium_sha256: str) -> bytes:
    manifest = build_fill_stop_manifest(origin=origin)
    return owned_provider_digest(manifest.digest, source_digest(), chromium_sha256)


def check_configuration(loaded: LoadedDFormConfiguration) -> tuple[DFormReadinessCode, ...]:
    """Local references only: neither PG nor browser nor a role/grant proof."""
    config = loaded.config
    if config.authorization_mode == "fixed_owner":
        from app.infra.browser.d_form_fixed_approval import APPROVED_FIXED_SOURCE_DIGEST

        if (loaded.settings.source_tenant_id != "default"
                or source_digest() != APPROVED_FIXED_SOURCE_DIGEST):
            raise DFormConfigurationError("d_form_authorization_missing")
    try:
        build_fill_stop_manifest(origin=config.origin)
    except Exception:
        raise DFormConfigurationError("d_form_config_invalid") from None
    if (
        config.pool is not None
        and config.chromium_sha256 is not None
        and config.pool.manifest_digest != pool_manifest_digest(
            config.origin, config.chromium_sha256,
        ).hex()
    ):
        raise DFormConfigurationError("d_form_pool_mismatch")
    if config.pool is not None:
        try:
            BrowserProviderPoolRegistry(
                (
                    BrowserProviderPool(
                        config.pool.provider_key,
                        config.pool.aliases,
                        bytes.fromhex(config.pool.manifest_digest),
                    ),
                )
            )
        except Exception:
            raise DFormConfigurationError("d_form_pool_mismatch") from None
    if (config.chromium is not None and config.chromium_sha256 is not None
            and config.owned_runs_root is not None):
        try:
            validate_owned_paths(config.chromium, config.owned_runs_root,
                                 config.chromium_sha256, config.receipts_root)
        except Exception:
            raise DFormConfigurationError("d_form_config_invalid") from None
    if config.login_controls is not None:
        _login_contract(loaded)
    settings = loaded.settings
    tokens = HMACSessionToken(
        signing_key=settings.session_signing_key,
        ttl_seconds=settings.session_cookie_ttl_seconds,
        tenant_id=settings.source_tenant_id,
    )
    binder = PrincipalSessionBinder(binding_key=settings.session_binding_key)
    if config.authorization_mode == "actors":
        actors = []
        for reference in (
            config.business_actor,
            config.publication_actor,
            config.cleanup_actor,
        ):
            actors.append(
                _inspect_actor(tokens, binder, loaded, reference) if reference is not None else None
            )
        business, publisher, cleanup = actors
        if business is not None and (
            config.binding is None
            or business.owner.user_id != config.binding.user_id
            or business.verified.principal.org_ctx.directory_user_id is None
            or any(
                actor is not None and actor.owner.user_id == business.owner.user_id
                for actor in (publisher, cleanup)
            )
        ):
            raise DFormConfigurationError("d_form_identity_invalid")
    return cast(tuple[DFormReadinessCode, ...], missing_conditions(config))


def _login_contract(loaded: LoadedDFormConfiguration) -> OwnedOALoginContract | None:
    controls = loaded.config.login_controls
    if controls is None:
        return None
    try:
        return OwnedOALoginContract(
            login_path=controls.login_path,
            login_id=controls.login_id, password=controls.password, submit=controls.submit,
            stops={name: value for name in ("captcha", "mfa", "sso", "warning")
                   if (value := getattr(controls, name)) is not None},
        )
    except Exception:
        raise DFormConfigurationError("d_form_login_contract_missing") from None


def _inspect_actor(
    tokens: SessionTokenPort,
    binder: PrincipalSessionBinder,
    loaded: LoadedDFormConfiguration,
    reference: ActorReference | None,
) -> DFormActor:
    if reference is None:
        raise DFormConfigurationError("d_form_identity_missing")
    try:
        value = loaded.environment.get(reference.token_ref)
        if value is None:
            raise ValueError
        verified = tokens.inspect(value)
        if (
            type(verified) is not VerifiedSessionToken
            or verified.principal.org_ctx.tenant_id != loaded.settings.source_tenant_id
            or reference.required_role not in verified.principal.roles
            or not reference.bound_session.startswith("sid_v1.")
            or binder.bind(verified.principal, reference.bound_session) != reference.bound_session
        ):
            raise ValueError
        return DFormActor(verified, reference.bound_session, reference.required_role)
    except Exception:
        raise DFormConfigurationError("d_form_identity_invalid") from None


def _actor(
    components: ProductionComponents,
    loaded: LoadedDFormConfiguration,
    reference: ActorReference | None,
) -> DFormActor:
    return _inspect_actor(components.session_tokens, components.session_binder, loaded, reference)


class DFormManagement:
    def __init__(
        self,
        components: ProductionComponents,
        loaded: LoadedDFormConfiguration,
        gaps: tuple[DFormReadinessCode, ...],
    ) -> None:
        self.components, self.loaded, self.gaps = components, loaded, gaps
        self.business: DFormActor | None = None
        self.cleanup_actor: DFormActor | None = None
        self.fixed_binding: BrowserBindingFact | None = None
        self.grants: DFormPublicationGrants | FixedDFormPublicationGrants | None = None
        self.cleanup: PostgreSQLLocalCleanupGrant | FixedDFormCleanupGrant | None = None
        self.targets: OwnedDFormTargetAuthority | None = None
        self.vertical: BrowserVerticalComponents | None = None
        self._run: tuple[str, str] | None = None
        self._request: str | None = None

    def install_vertical(self, vertical: BrowserVerticalComponents) -> None:
        if (
            self.vertical is not None
            or vertical._sessions is not self.components.browser_session_factory
        ):
            raise ValueError("d_form_identity_invalid")
        self.vertical = vertical

    @staticmethod
    def _matches_caller(principal: Principal, actor: DFormActor | None) -> bool:
        captured = authenticated_session.get()
        return (
            actor is not None
            and captured is not None
            and captured.principal == principal == actor.verified.principal
            and captured.fingerprint == actor.verified.fingerprint
            and captured.expires_at == actor.verified.expires_at
        )

    async def _caller(
        self, principal: Principal, *, handoff: bool = False,
        session_id: str | None = None,
    ) -> DFormActor | DFormRequestActor:
        began = time.monotonic()
        log_d_form_stage("management_caller", "started", "OK", began)
        if self.fixed_binding is not None:
            binding = self.fixed_binding
            captured = authenticated_session.get()
            if (
                captured is None or captured.principal != principal
                or principal.org_ctx.tenant_id != binding.tenant_id
                or principal.ai_user_id != binding.ai_user_id
                or principal.org_ctx.directory_user_id is None
                or type(captured.fingerprint) is not bytes
                or len(captured.fingerprint) != 32
            ):
                log_d_form_stage("management_caller", "failed", "d_form_authorization_denied", began)
                raise DFormManagementError("d_form_authorization_denied")
            log_d_form_stage("management_caller", "success", "OK", began)
            log_d_form_stage("management_binding", "started", "OK", began)
            try:
                bound = self.components.session_binder.bind(
                    principal, session_id or f"admin-v1:{principal.ai_user_id}",
                )
            except Exception as error:
                log_d_form_stage("management_binding", "failed", _management_error_code(error), began)
                raise DFormManagementError("d_form_authorization_denied") from None
            log_d_form_stage("management_binding", "success", "OK", began)
            log_d_form_stage("management_grant", "started", "OK", began)
            try:
                if self.grants is None or not await self.grants.check(
                    BrowserOwner(
                        tenant_id=binding.tenant_id,
                        user_id=binding.ai_user_id,
                        session_id=bound,
                    ),
                    self.grants.skill_id, "read", self.grants.digest,
                ):
                    raise DFormManagementError("d_form_authorization_denied")
            except Exception as error:
                log_d_form_stage("management_grant", "failed", _management_error_code(error), began)
                raise
            log_d_form_stage("management_grant", "success", "OK", began)
            return DFormRequestActor(
                principal, captured.fingerprint, captured.expires_at, bound,
            )
        actor = self.cleanup_actor if handoff else self.business
        if not self._matches_caller(principal, actor):
            log_d_form_stage("management_caller", "failed", "d_form_authorization_denied", began)
            raise DFormManagementError("d_form_authorization_denied")
        assert actor is not None
        try:
            async with self.components.browser_session_factory() as session:
                await current_actor(session, self.components.session_binder, actor)
        except Exception as error:
            log_d_form_stage("management_caller", "failed", _management_error_code(error), began)
            raise
        log_d_form_stage("management_caller", "success", "OK", began)
        return actor

    async def _available_caller(self, principal: Principal, *, handoff: bool) -> bool:
        try:
            await self._caller(principal, handoff=handoff)
        except DFormManagementError as error:
            if error.code != "d_form_authorization_denied":
                raise
            return False
        except BrowserLeaseError as error:
            if error.code != "d_form_authorization_denied":
                raise
            return False
        return True

    async def check_fixed_access(self, principal: Principal) -> bool:
        """D-only API admission from the verified request and installed binding."""
        if self.fixed_binding is None or self.grants is None:
            return False
        try:
            await self._caller(principal)
        except (DFormManagementError, BrowserLeaseError, BrowserAuthorizationError):
            return False
        return True

    async def state(self, principal: Principal) -> DFormManagementState:
        began = time.monotonic()
        if principal.org_ctx.tenant_id != self.loaded.settings.source_tenant_id:
            raise DFormManagementError("d_form_authorization_denied")
        if self.vertical is None or self.grants is None or self.targets is None:
            return DFormManagementState(
                structure_valid=True, gaps=self.gaps, publication="unavailable",
                fixed_activation=self.loaded.config.authorization_mode == "fixed_owner",
            )
        # Read metadata only, through existing authenticated management policy.
        is_business = await self._available_caller(principal, handoff=False)
        is_handoff = await self._available_caller(principal, handoff=True)
        if not is_business and not is_handoff:
            raise DFormManagementError("d_form_authorization_denied")
        vertical, grants = self.vertical, self.grants
        gaps: list[DFormReadinessCode] = []
        choices: list[Literal["installed_binding", "recorded_resource"]] = ["installed_binding"]
        operations: list[DFormManagementOperation] = []
        handoff_preview: DFormHandoffView | None = None
        if self.loaded.config.login_controls is None:
            gaps.append("d_form_login_contract_missing")
        if is_handoff:
            operations.append("close_owned_browser")
        publication: Literal["unavailable", "missing", "prepared", "active", "inactive"] = (
            "unavailable"
        )
        if not is_business:
            return DFormManagementState(
                structure_valid=True, gaps=tuple(dict.fromkeys(gaps)),
                choices=tuple(choices), available_operations=tuple(operations),
                publication=publication, handoff_preview=handoff_preview,
                fixed_activation=self.fixed_binding is not None,
            )
        actor = await self._caller(principal)
        stage = "management_grant"
        try:
            log_d_form_stage(stage, "started", "OK", began)
            if not await grants.check(
                actor.owner, grants.skill_id, "read", grants.digest
            ):
                raise DFormManagementError("d_form_authorization_denied")
            log_d_form_stage(stage, "success", "OK", began)
            stage = "management_read"
            log_d_form_stage(stage, "started", "OK", began)
            async with self.components.browser_session_factory() as session:
                if self.cleanup is None:
                    raise DFormManagementError("d_form_authorization_missing")
                if isinstance(self.cleanup, PostgreSQLLocalCleanupGrant):
                    await self.cleanup._current(session)
                found = (
                    await session.execute(
                        text(
                            "SELECT state FROM browser_publications WHERE tenant_id=:tenant"
                            " AND skill_id=:skill AND publication_digest=:digest"
                        ),
                        {
                            "tenant": grants.binding.tenant_id,
                            "skill": grants.skill_id,
                            "digest": bytes.fromhex(grants.digest),
                        },
                    )
                ).scalar_one_or_none()
            log_d_form_stage(stage, "success", "OK", began)
            publication = cast(
                Literal["missing", "prepared", "active", "inactive"],
                found if found in {"prepared", "active", "inactive"} else "missing",
            )
            if publication != "active":
                gaps.append("d_form_publication_pending")
            else:
                stage = "publication_read"
                log_d_form_stage(stage, "started", "OK", began)
                await vertical.publications.assert_current(actor.owner, vertical._seed)
                log_d_form_stage(stage, "success", "OK", began)
            if is_business:
                stage = "management_grant"
                log_d_form_stage(stage, "started", "OK", began)
                operations_to_check = (
                    (("prepare", "prepare"), ("activate", "activate"))
                    if self.fixed_binding is not None else
                    (("prepare", "prepare"), ("validate", "execute"),
                     ("request_review", "activate"),
                     ("confirm_review", "activate"), ("activate", "activate"))
                )
                for operation, grant in operations_to_check:
                    if await grants.check(
                        actor.owner,
                        grants.skill_id,
                        cast(PublicationOperation, grant),
                        grants.digest,
                    ):
                        if self.fixed_binding is not None and operation == "activate" and not callable(
                            getattr(vertical.publications, "activate_fixed_initial", None)
                        ):
                            gaps.append("d_form_authorization_missing")
                        elif operation != "validate" or self.loaded.config.login_controls is not None:
                            operations.append(cast(DFormManagementOperation, operation))
                log_d_form_stage(stage, "success", "OK", began)
        except Exception as error:
            log_d_form_stage(stage, "failed", _management_error_code(error), began)
            gaps.append("d_form_review_pending")
        return DFormManagementState(
            structure_valid=True,
            fixed_activation=self.fixed_binding is not None,
            gaps=tuple(dict.fromkeys(gaps)),
            choices=tuple(choices),
            available_operations=tuple(operations),
            publication=publication,
            task_id=self._run[0] if self._run else None,
            run_id=self._run[1] if self._run else None,
            request_id=self._request,
            handoff_preview=handoff_preview,
        )

    async def perform(
        self, principal: Principal, command: DFormManagementCommand
    ) -> DFormManagementState:
        began = time.monotonic()
        stage = {"prepare": "management_prepare", "activate": "management_activate"}.get(
            command.operation, "management_operation",
        )
        log_d_form_stage(stage, "started", "OK", began)
        try:
            result = await self._perform(principal, command)
            log_d_form_stage(stage, "success", "OK", began)
            return result
        except DFormManagementError as error:
            log_d_form_stage(stage, "failed", _management_error_code(error), began)
            raise
        except BrowserLeaseError as error:
            log_d_form_stage(stage, "failed", _management_error_code(error), began)
            if error.code in {
                "d_form_resource_scope_denied",
                "d_form_subject_proof_unavailable",
                "d_form_subject_mismatch",
                "d_form_authorization_denied",
            }:
                raise DFormManagementError(cast(DFormReadinessCode, error.code)) from None
            raise DFormManagementError("d_form_authorization_denied") from None
        except Exception as error:
            log_d_form_stage(stage, "failed", _management_error_code(error), began)
            # Upstream exceptions may contain native identifiers or business values.
            raise DFormManagementError("d_form_authorization_denied") from None

    async def _perform(
        self, principal: Principal, command: DFormManagementCommand
    ) -> DFormManagementState:
        began = time.monotonic()
        if self.vertical is None or self.grants is None or self.targets is None:
            raise DFormManagementError("d_form_authorization_missing")
        if self.fixed_binding is not None and command.operation in {
            "validate", "request_review", "confirm_review", "confirm_handoff",
        }:
            raise DFormManagementError("d_form_authorization_denied")
        vertical, grants = self.vertical, self.grants
        if command.operation == "close_owned_browser":
            actor = await self._caller(
                principal, handoff=True, session_id=command.session_id,
            )
            if (command.selection != "installed_binding" or command.parameters is not None
                    or command.handoff_confirmation is not None or command.request_id is not None
                    or command.task_id is None or command.run_id is None
                    or command.session_id is None
                    or self.components.session_binder.bind(principal, command.session_id)
                    != actor.bound_session or (
                        self.business is None and self.fixed_binding is None
                    )
                    or self.cleanup is None or type(vertical.execution) is not DFormRuntime):
                raise DFormManagementError("d_form_authorization_denied")
            cleanup_step = "get_for_cleanup"
            try:
                run = await vertical.runs.get_for_cleanup(
                    actor.owner, command.task_id, command.run_id,
                )
                cleanup_step = "authorize_run"
                async with self.components.browser_session_factory() as session:
                    await self.cleanup.authorize_run(session, run)
                cleanup_step = "close_owned_browser"
                cleanup = await vertical.execution.close_owned_browser(run)
                if cleanup != "released":
                    raise DFormManagementError("d_form_resource_scope_denied")
                cleanup_step = "record_cleanup"
                await vertical.runs.record_cleanup(run, cleanup=cleanup)
            except Exception as error:
                _log_cleanup_failure(cleanup_step, error, began)
                raise
            return await self.state(principal)
        if command.operation == "confirm_handoff":
            raise DFormManagementError("d_form_resource_scope_denied")
        actor = await self._caller(principal, session_id=command.session_id)
        if (
            command.selection != "installed_binding"
            or command.handoff_confirmation is not None
            or self.fixed_binding is None and command.session_id is None
            or command.session_id is not None and (
                self.components.session_binder.bind(principal, command.session_id)
                != actor.bound_session
            )
        ):
            raise DFormManagementError("d_form_authorization_denied")
        grant: PublicationOperation = (
            "prepare"
            if command.operation == "prepare"
            else "execute"
            if command.operation == "validate"
            else "activate"
        )
        log_d_form_stage("management_grant", "started", "OK", began)
        try:
            if not await grants.check(actor.owner, grants.skill_id, grant, grants.digest):
                raise DFormManagementError("d_form_authorization_denied")
        except Exception as error:
            log_d_form_stage("management_grant", "failed", _management_error_code(error), began)
            raise
        log_d_form_stage("management_grant", "success", "OK", began)
        if command.operation == "prepare":
            if (
                command.parameters is not None
                or self.fixed_binding is not None and any(
                    item is not None for item in (
                        command.task_id, command.run_id, command.request_id,
                    )
                )
            ):
                raise DFormManagementError("d_form_authorization_denied")
            log_d_form_stage("publication_prepare", "started", "OK", began)
            try:
                await vertical.prepare_seed(actor.owner)
            except Exception as error:
                log_d_form_stage("publication_prepare", "failed", _management_error_code(error), began)
                raise
            log_d_form_stage("publication_prepare", "success", "OK", began)
        elif self.fixed_binding is not None and command.operation == "activate":
            if any(item is not None for item in (
                command.parameters, command.task_id, command.run_id, command.request_id,
            )):
                raise DFormManagementError("d_form_authorization_denied")
            activate = getattr(vertical.publications, "activate_fixed_initial", None)
            if not callable(activate):
                raise DFormManagementError("d_form_authorization_missing")
            log_d_form_stage("publication_activate", "started", "OK", began)
            try:
                await activate(actor.owner, vertical._seed)
            except Exception as error:
                log_d_form_stage("publication_activate", "failed", _management_error_code(error), began)
                raise
            log_d_form_stage("publication_activate", "success", "OK", began)
        elif command.operation == "validate":
            if command.parameters is None or command.request_id is None:
                raise DFormManagementError("d_form_review_pending")
            # Explicit prepared-version admission. It still requires the existing
            # human write decision and exact owned-resource proof before the worker acts.
            envelope = await vertical.submission.submit_initial_validation(
                principal=principal,
                bound_session=actor.bound_session,
                skill_id=vertical._seed.skill.skill_id,
                skill_version=vertical._seed.skill.version,
                arguments=command.parameters.model_dump(),
                client_request_id=command.request_id,
            )
            data = envelope.data
            if not isinstance(data, dict) or not all(
                type(data.get(k)) is str for k in ("task_id", "run_id")
            ):
                raise DFormManagementError("d_form_review_pending")
            self._run = (data["task_id"], data["run_id"])
        else:
            if command.parameters is not None or not all(
                v is not None for v in (command.task_id, command.run_id, command.request_id)
            ):
                raise DFormManagementError("d_form_review_pending")
            task_id, run_id, request_id = command.task_id, command.run_id, command.request_id
            assert task_id is not None and run_id is not None and request_id is not None
            if command.operation == "request_review":
                await vertical.submission.request_initial_review(
                    principal,
                    actor.bound_session,
                    task_id=task_id,
                    run_id=run_id,
                    request_id=request_id,
                )
                self._request = request_id
            elif command.operation == "confirm_review":
                await vertical.submission.decide_initial_review(
                    principal,
                    actor.bound_session,
                    task_id=task_id,
                    run_id=run_id,
                    request_id=request_id,
                    decision="confirmed",
                )
            elif command.operation == "activate":
                log_d_form_stage("publication_activate", "started", "OK", began)
                try:
                    await vertical.activate_initial(
                        actor.owner, task_id=task_id, run_id=run_id, approval_ref=request_id
                    )
                except Exception as error:
                    log_d_form_stage("publication_activate", "failed", _management_error_code(error), began)
                    raise
                log_d_form_stage("publication_activate", "success", "OK", began)
            else:
                raise DFormManagementError("d_form_authorization_denied")
        return await self.state(principal)


@dataclass(frozen=True, slots=True, repr=False)
class DFormPreparedInstallation:
    installation: DFormInstallationDependencies | None
    management: DFormManagement


@asynccontextmanager
async def bind_task_configuration(loaded: LoadedDFormConfiguration) -> AsyncIterator[None]:
    token = _configuration.set(loaded)
    try:
        yield
    finally:
        _configuration.reset(token)


@asynccontextmanager
async def open_installation(
    components: ProductionComponents,
) -> AsyncIterator[DFormPreparedInstallation]:
    began = time.monotonic()
    log_d_form_stage("configuration", "started", "OK", began)
    loaded = _configuration.get()
    if loaded is None:
        log_d_form_stage("configuration", "failed", "d_form_config_missing", began)
        raise DFormConfigurationError("d_form_config_missing")
    gaps = check_configuration(loaded)
    management = DFormManagement(components, loaded, gaps)
    config, environment = loaded.config, loaded.environment
    if any(gap != "d_form_login_contract_missing" for gap in gaps):
        log_d_form_stage("configuration", "pending", "CONFIG_FAILED", began)
        yield DFormPreparedInstallation(None, management)
        return
    fixed_owner = config.authorization_mode == "fixed_owner"
    business = None if fixed_owner else _actor(components, loaded, config.business_actor)
    publisher = None if fixed_owner else _actor(components, loaded, config.publication_actor)
    cleanup_actor = None if fixed_owner else _actor(components, loaded, config.cleanup_actor)
    selector, keys, pool = config.binding, config.keys, config.pool
    assert selector is not None and keys is not None and pool is not None
    if not fixed_owner and (
        business is None or publisher is None or cleanup_actor is None
        or config.publication_actor is None or config.cleanup_actor is None
        or business.owner.user_id != selector.user_id
        or cleanup_actor.owner.user_id == business.owner.user_id
    ):
        raise DFormConfigurationError("d_form_identity_invalid")
    binding: BrowserBindingFact | None = None
    try:
        async with components.browser_session_factory() as session:
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT binding_revision,binding_state,binding_subject_digest,"
                            "binding_subject_verified_at,revoked_at FROM oa_session_credentials"
                            " WHERE tenant_id=:tenant AND ai_user_id=:user"
                            " AND target_system='oa' AND binding_id=:binding"
                        ),
                        {
                            "tenant": loaded.settings.source_tenant_id,
                            "user": selector.user_id,
                            "binding": selector.binding_id,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
            if (
                row is None or row["binding_subject_digest"] is None
                or row["binding_subject_verified_at"] is None
                or len(row["binding_subject_digest"]) != 32
            ):
                management.gaps = ("d_form_binding_unverified",)
            else:
                binding = BrowserBindingFact(
                    loaded.settings.source_tenant_id,
                    selector.user_id,
                    "oa",
                    selector.binding_id,
                    selector.revision,
                    bytes(row["binding_subject_digest"]),
                )
                if row["binding_state"] != "active":
                    log_d_form_stage("binding", "failed", "browser_binding_inactive", began)
                elif row["binding_revision"] != binding.binding_revision:
                    log_d_form_stage("binding", "failed",
                                     "browser_binding_revision_mismatch", began)
                PostgreSQLBrowserBindingReader.check_row(row, binding)
                if not fixed_owner:
                    assert business is not None and publisher is not None
                    assert cleanup_actor is not None
                    for actor in (business, publisher, cleanup_actor):
                        await current_actor(session, components.session_binder, actor)
    except Exception as error:
        log_d_form_stage("binding", "failed", getattr(error, "code", "error"), began)
        management.gaps = ("d_form_binding_unavailable",)
        binding = None
    if binding is None:
        log_d_form_stage("binding", "unavailable", "d_form_binding_unavailable", began)
        yield DFormPreparedInstallation(None, management)
        return
    manifest = build_fill_stop_manifest(origin=config.origin)
    if fixed_owner:
        grants: DFormPublicationGrants | FixedDFormPublicationGrants = FixedDFormPublicationGrants(
            components.browser_session_factory, components.session_binder,
            binding, manifest.skill.skill_id, manifest.digest,
        )
    else:
        assert business is not None and publisher is not None
        assert config.publication_actor is not None
        grants = DFormPublicationGrants(
            components.browser_session_factory, components.session_binder,
            business, publisher, binding, manifest.skill.skill_id,
            manifest.digest, frozenset(config.publication_actor.operations),
        )
    targets = OwnedDFormTargetAuthority(components.browser_session_factory, binding)
    if fixed_owner:
        cleanup: PostgreSQLLocalCleanupGrant | FixedDFormCleanupGrant = FixedDFormCleanupGrant(
            components.browser_session_factory, binding, manifest.digest,
        )
    else:
        assert cleanup_actor is not None and config.cleanup_actor is not None
        cleanup = PostgreSQLLocalCleanupGrant(
            session_factory=components.browser_session_factory,
            session_binder=components.session_binder,
            verified_actor=cleanup_actor.verified,
            bound_session=cleanup_actor.bound_session,
            binding=BrowserBindingKey(
                binding.tenant_id, binding.ai_user_id, "oa", binding.binding_id,
            ),
            publication_digest=manifest.digest,
            required_role=cleanup_actor.required_role,
            allowed_operations=frozenset(config.cleanup_actor.operations),
        )
    lookup = InstalledReplayProofLookup(components.browser_session_factory)
    review = PostgreSQLTeachingPublicationReview(components.browser_session_factory, lookup)
    installation = build_d_form_production_installation(
        components,
        DFormProductionInputs(
            binding=binding,
            origin=config.origin,
            options=DFormOptions(
                config.python,
                config.playwright_node,
                config.receipts_root,
                targets,
                BrowserClaimProofContext(decode_key(environment, keys.proof_context)),
                pool.provider_key,
                chromium=config.chromium, chromium_sha256=config.chromium_sha256,
                owned_runs_root=config.owned_runs_root, login_contract=_login_contract(loaded),
            ),
            provider_pools=BrowserProviderPoolRegistry(
                (
                    BrowserProviderPool(
                        pool.provider_key,
                        pool.aliases,
                        bytes.fromhex(pool.manifest_digest),
                    ),
                )
            ),
            publication_grants=grants,
            publication_review=review,
            cleanup_authority=cleanup,
            payload_keys=decode_ring(environment, keys.payload),
            active_payload_key_id=keys.payload.active_id,
            resource_keys=decode_ring(environment, keys.resource),
            active_resource_key_id=keys.resource.active_id,
            request_digest_keys=decode_ring(environment, keys.request_digest),
            active_request_digest_key_id=keys.request_digest.active_id,
            input_digest_key=decode_key(environment, keys.input_digest),
            result_digest_key=decode_key(environment, keys.result_digest),
            fixed_owner=fixed_owner,
        ),
    )
    management.business, management.cleanup_actor = business, cleanup_actor
    management.fixed_binding = binding if fixed_owner else None
    management.grants = grants
    management.targets, management.cleanup = targets, cleanup
    log_d_form_stage("installation", "ready", "OK", began)
    yield DFormPreparedInstallation(installation, management)
