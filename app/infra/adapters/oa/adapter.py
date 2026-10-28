"""Real OA read adapter with a fixed capability and credential boundary."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from app.infra.adapters.oa.provider import (
    OAContractPackError,
    OAContractPackPayloadInvalid,
    OALiveHTTPServerError,
    OALiveIdentityExpired,
    OALiveIdentityUnbound,
    OALivePayloadInvalid,
    OALivePermissionDenied,
    OALiveProviderError,
    OALiveRequestError,
    OALiveSessionLocallyExpired,
    OALiveSessionSuspected,
    OALiveTimeout,
    OAReadProvider,
    _oa_stage_scope,
    _record_chat_stage,
)
from app.ports.adapter import AdapterFailureStage, AdapterResult, AdapterTraceMetadata
from app.ports.auth import OASessionCredential, authenticated_session
from app.ports.capability_gateway import ErrorCode
from app.ports.credential_binding import (
    CredentialRecoveryState,
    CredentialRecoveryTrigger,
    OASessionRenewalPort,
)
from app.ports.secret_provider import (
    CredentialExpiredError,
    CredentialNotFoundError,
    CredentialStorageError,
    InvalidCredentialReferenceError,
    SecretProviderError,
    SecretProviderPort,
)

OA_LIST_PENDING_WORKFLOWS = "oa.list_pending_workflows"
OA_LIST_SYSTEM_MESSAGES = "oa.list_system_messages"
_OA_SESSION_REF_PREFIX = "oa-session-v1:"
_SESSION_LOGGER = logging.getLogger("uvicorn.error.oa_session")


class _ReadFailed(Exception):
    """Carry one read failure and the Session it used to the renewal decision."""

    def __init__(self, cause: Exception, credential: OASessionCredential | None) -> None:
        super().__init__("OA read failed")
        self.cause = cause
        self.credential = credential


class ListPendingWorkflowsArguments(BaseModel):
    """Frozen GT-001 zero-argument capability input."""

    model_config = ConfigDict(extra="forbid", strict=True)


class ListSystemMessagesArguments(BaseModel):
    """Natural-language system-message read with provider-internal pagination."""

    model_config = ConfigDict(extra="forbid", strict=True)


@dataclass(frozen=True)
class _CapabilityBinding:
    arguments_model: type[BaseModel]
    handler: Callable[[dict[str, Any], tuple[str, ...]], Awaitable[AdapterResult]]


class OAReadAdapter:
    """Dispatch only explicitly registered OA read capabilities."""

    def __init__(
        self,
        provider: OAReadProvider,
        secret_provider: SecretProviderPort | None = None,
        session_renewal: OASessionRenewalPort | None = None,
    ) -> None:
        self._provider = provider
        self._secret_provider = secret_provider
        self._session_renewal = session_renewal
        self._bindings: Mapping[str, _CapabilityBinding] = MappingProxyType(
            {
                OA_LIST_PENDING_WORKFLOWS: _CapabilityBinding(
                    arguments_model=ListPendingWorkflowsArguments,
                    handler=self._list_pending_workflows,
                ),
                OA_LIST_SYSTEM_MESSAGES: _CapabilityBinding(
                    arguments_model=ListSystemMessagesArguments,
                    handler=self._list_system_messages,
                ),
            }
        )

    async def execute(
        self,
        capability_id: str,
        arguments: dict[str, Any],
        execution_context: dict[str, Any],
    ) -> AdapterResult:
        argument_keys = tuple(sorted(arguments))
        binding = self._bindings.get(capability_id)
        if binding is None:
            _record_chat_stage(execution_context.get("trace_id"), "oa_result", "blocked",
                               reason="capability_not_found", capability_id=capability_id)
            return _adapter_error(argument_keys, "unknown")
        try:
            binding.arguments_model.model_validate(arguments, strict=True)
        except ValidationError:
            _record_chat_stage(execution_context.get("trace_id"), "oa_result", "blocked",
                               reason="argument_validation", capability_id=capability_id)
            return _adapter_error(argument_keys, "argument_validation")
        trace_id = execution_context.get("trace_id")
        token = _oa_stage_scope.set((trace_id, capability_id))
        try:
            result = await binding.handler(execution_context, argument_keys)
            stage = result.trace_metadata.failure_stage if result.trace_metadata else None
            _record_chat_stage(trace_id,
                               stage if stage in {"credential_read", "provider_transport",
                                                  "normalization"} else "oa_result",
                               "ok" if result.status == "success" else "failed",
                               reason=result.error_code or "provider_returned",
                               capability_id=capability_id)
            return result
        finally:
            _oa_stage_scope.reset(token)

    async def _list_pending_workflows(
        self,
        execution_context: dict[str, Any],
        argument_keys: tuple[str, ...],
    ) -> AdapterResult:
        return await self._execute_read(
            OA_LIST_PENDING_WORKFLOWS,
            execution_context,
            argument_keys,
            self._provider.list_pending_workflows,
        )

    async def _list_system_messages(
        self,
        execution_context: dict[str, Any],
        argument_keys: tuple[str, ...],
    ) -> AdapterResult:
        return await self._execute_read(
            OA_LIST_SYSTEM_MESSAGES,
            execution_context,
            argument_keys,
            self._provider.list_system_messages,
        )

    async def _execute_read(
        self,
        capability_id: str,
        execution_context: dict[str, Any],
        argument_keys: tuple[str, ...],
        provider_call: Callable[[OASessionCredential | None], Awaitable[BaseModel]],
    ) -> AdapterResult:
        trace_id = execution_context.get("trace_id")
        _record_chat_stage(trace_id, "credential_read", "start", capability_id=capability_id)
        credential_ref: str | None = None
        if self._provider.requires_credential:
            raw_ref = execution_context.get("credential_ref")
            if not isinstance(raw_ref, str) or not raw_ref.strip():
                return _classified_error(
                    "identity_unbound", argument_keys, "credential_read"
                )
            if self._secret_provider is None:
                return _adapter_error(argument_keys, "credential_read")
            credential_ref = raw_ref
        owner = self._renewal_owner(credential_ref)
        observed = await self._observe(owner)
        try:
            return await self._read(
                trace_id, capability_id, credential_ref, argument_keys, provider_call
            )
        except _ReadFailed as failed:
            first = failed
        decision: AdapterResult | Literal["retry"] | None = None
        if owner is not None and observed is not None:
            decision = await self._renew_after_failure(
                owner, observed, first, argument_keys
            )
        first.credential = None
        if decision == "retry":
            try:
                return await self._read(
                    trace_id, capability_id, credential_ref, argument_keys, provider_call
                )
            except _ReadFailed as second:
                second.credential = None
                if isinstance(second.cause, OALiveSessionSuspected):
                    _SESSION_LOGGER.warning(
                        "oa_002_persisted_after_relogin step=%s", second.cause.step
                    )
                    return _adapter_error(argument_keys, "provider_transport")
                return _result_for_failure(second.cause, argument_keys, capability_id)
        if isinstance(decision, AdapterResult):
            return decision
        return _result_for_failure(first.cause, argument_keys, capability_id)

    async def _read(
        self,
        trace_id: object,
        capability_id: str,
        credential_ref: str | None,
        argument_keys: tuple[str, ...],
        provider_call: Callable[[OASessionCredential | None], Awaitable[BaseModel]],
    ) -> AdapterResult:
        credential: OASessionCredential | None = None
        try:
            if credential_ref is not None and self._secret_provider is not None:
                credential = await self._secret_provider.resolve_oa_session(
                    credential_ref
                )
            _record_chat_stage(trace_id, "credential_read",
                               "ok" if self._provider.requires_credential else "skipped",
                               reason="credential_resolved" if self._provider.requires_credential
                               else "credential_not_required", capability_id=capability_id)
            collection = await provider_call(credential)
            _record_chat_stage(trace_id, "normalization", "ok", reason="provider_returned",
                               capability_id=capability_id)
            return AdapterResult(
                status="success",
                data=collection.model_dump(mode="json"),
                error_code=None,
                trace_metadata=_trace_metadata(argument_keys),
            )
        except Exception as error:
            raise _ReadFailed(error, credential) from None
        finally:
            credential = None

    def _renewal_owner(self, credential_ref: str | None) -> tuple[str, str] | None:
        """Recover only the signed-in user's own Session, never another reference."""

        if self._session_renewal is None or credential_ref is None:
            return None
        session = authenticated_session.get()
        if (
            session is None
            or session.expires_at <= datetime.now(UTC)
            or credential_ref != f"{_OA_SESSION_REF_PREFIX}{session.principal.ai_user_id}"
        ):
            return None
        return session.principal.org_ctx.tenant_id, session.principal.ai_user_id

    async def _observe(
        self, owner: tuple[str, str] | None
    ) -> CredentialRecoveryState | None:
        if owner is None or self._session_renewal is None:
            return None
        try:
            return await self._session_renewal.observe(
                tenant_id=owner[0], ai_user_id=owner[1]
            )
        except Exception as error:
            _SESSION_LOGGER.info(
                "oa_session_observe_failed exception_type=%s", type(error).__name__
            )
            return None

    async def _renew_after_failure(
        self,
        owner: tuple[str, str],
        observed: CredentialRecoveryState,
        failed: _ReadFailed,
        argument_keys: tuple[str, ...],
    ) -> AdapterResult | Literal["retry"] | None:
        """Probe first; relogin only when OA verifiably rejected the Session."""

        renewal = self._session_renewal
        if renewal is None:
            return None
        tenant_id, ai_user_id = owner
        cause = failed.cause
        trigger: CredentialRecoveryTrigger
        try:
            if isinstance(cause, OALiveSessionSuspected):
                probe = await renewal.probe(
                    tenant_id=tenant_id, ai_user_id=ai_user_id,
                    observed=observed, credential=failed.credential,
                )
                if probe == "alive":
                    # A live Session keeps the business failure; no relogin loop.
                    _SESSION_LOGGER.info("oa_002_heartbeat_alive step=%s", cause.step)
                    return None
                if probe == "unavailable":
                    return None
                trigger = "oa_002"
            elif isinstance(cause, (CredentialExpiredError, OALiveSessionLocallyExpired)):
                probe = await renewal.probe(
                    tenant_id=tenant_id, ai_user_id=ai_user_id,
                    observed=observed, credential=failed.credential,
                )
                if probe == "alive":
                    return "retry"
                if probe == "unavailable":
                    # Unreachable OA is not proof of expiry; no relogin, no reread.
                    return _adapter_error(argument_keys, "provider_transport")
                trigger = "local_expiry"
            elif isinstance(cause, OALiveIdentityExpired):
                trigger = "read_identity_expired"
            else:
                return None
            outcome = await renewal.recover(
                tenant_id=tenant_id, ai_user_id=ai_user_id,
                observed=observed, trigger=trigger,
            )
        except Exception as error:
            _SESSION_LOGGER.warning(
                "oa_session_recovery_failed exception_type=%s", type(error).__name__
            )
            return None
        if outcome in {"recovered", "refreshed_by_other"}:
            return "retry"
        if outcome in {"unavailable", "backoff"}:
            return _adapter_error(argument_keys, "provider_transport")
        return _classified_error("identity_expired", argument_keys, "provider_transport")


def _result_for_failure(
    error: Exception,
    argument_keys: tuple[str, ...],
    capability_id: str,
) -> AdapterResult:
    try:
        raise error
    except CredentialNotFoundError:
        return _classified_error(
            "identity_unbound", argument_keys, "credential_read"
        )
    except CredentialExpiredError:
        return _classified_error(
            "identity_expired", argument_keys, "credential_read"
        )
    except (
        InvalidCredentialReferenceError,
        CredentialStorageError,
        SecretProviderError,
    ):
        return _adapter_error(argument_keys, "credential_read")
    except OAContractPackPayloadInvalid:
        return _classified_error(
            "adapter_payload_invalid", argument_keys, "normalization"
        )
    except OAContractPackError:
        return _adapter_error(argument_keys, "unknown")
    except OALiveIdentityUnbound:
        return _classified_error(
            "identity_unbound", argument_keys, "provider_transport"
        )
    except OALiveSessionSuspected:
        # Unconfirmed 002 stays the pre-existing business failure classification.
        return _adapter_error(argument_keys, "provider_transport")
    except OALiveIdentityExpired:
        return _classified_error(
            "identity_expired", argument_keys, "provider_transport"
        )
    except OALivePermissionDenied:
        return AdapterResult(
            status="permission_denied",
            data=None,
            error_code="upstream_permission_denied",
            trace_metadata=_trace_metadata(
                argument_keys, "provider_transport"
            ),
        )
    except OALiveTimeout:
        return AdapterResult(
            status="timeout",
            data=None,
            error_code="adapter_timeout",
            trace_metadata=_trace_metadata(
                argument_keys, "provider_transport"
            ),
        )
    except OALiveHTTPServerError:
        return _classified_error(
            "adapter_http_500", argument_keys, "provider_transport"
        )
    except OALiveRequestError:
        return _adapter_error(argument_keys, "provider_transport")
    except OALivePayloadInvalid:
        return _classified_error(
            "adapter_payload_invalid", argument_keys, "normalization"
        )
    except OALiveProviderError:
        return _adapter_error(argument_keys, "unknown")
    except Exception as exc:
        logging.getLogger(__name__).error(
            "oa_read_adapter_failure capability_id=%s stage=%s "
            "exception_type=%s classification=%s",
            capability_id,
            "unknown",
            type(exc).__name__,
            "adapter_error",
        )
        return _adapter_error(
            argument_keys,
            "unknown",
        )


def _trace_metadata(
    argument_keys: tuple[str, ...],
    failure_stage: AdapterFailureStage | None = None,
) -> AdapterTraceMetadata:
    return AdapterTraceMetadata(
        argument_keys=argument_keys,
        failure_stage=failure_stage,
    )


def _classified_error(
    error_code: ErrorCode,
    argument_keys: tuple[str, ...],
    failure_stage: AdapterFailureStage,
) -> AdapterResult:
    return AdapterResult(
        status="error",
        data=None,
        error_code=error_code,
        trace_metadata=_trace_metadata(argument_keys, failure_stage),
    )


def _adapter_error(
    argument_keys: tuple[str, ...],
    failure_stage: AdapterFailureStage,
) -> AdapterResult:
    return AdapterResult(
        status="error",
        data=None,
        error_code="adapter_error",
        trace_metadata=_trace_metadata(
            argument_keys,
            failure_stage,
        ),
    )


__all__ = (
    "ListPendingWorkflowsArguments",
    "ListSystemMessagesArguments",
    "OA_LIST_PENDING_WORKFLOWS",
    "OA_LIST_SYSTEM_MESSAGES",
    "OAReadAdapter",
)
