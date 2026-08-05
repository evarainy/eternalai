"""Real OA read adapter with a fixed capability and credential boundary."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

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
    OALiveTimeout,
    OAReadProvider,
)
from app.ports.adapter import AdapterFailureStage, AdapterResult, AdapterTraceMetadata
from app.ports.auth import OASessionCredential
from app.ports.capability_gateway import ErrorCode
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
    ) -> None:
        self._provider = provider
        self._secret_provider = secret_provider
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
            return _adapter_error(argument_keys, "unknown")
        try:
            binding.arguments_model.model_validate(arguments, strict=True)
        except ValidationError:
            return _adapter_error(argument_keys, "argument_validation")
        return await binding.handler(execution_context, argument_keys)

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
        credential: OASessionCredential | None = None
        try:
            if self._provider.requires_credential:
                credential_ref = execution_context.get("credential_ref")
                if not isinstance(credential_ref, str) or not credential_ref.strip():
                    return _classified_error(
                        "identity_unbound", argument_keys, "credential_read"
                    )
                if self._secret_provider is None:
                    return _adapter_error(argument_keys, "credential_read")
                credential = await self._secret_provider.resolve_oa_session(
                    credential_ref
                )
            collection = await provider_call(credential)
            return AdapterResult(
                status="success",
                data=collection.model_dump(mode="json"),
                error_code=None,
                trace_metadata=_trace_metadata(argument_keys),
            )
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
        finally:
            credential = None


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
