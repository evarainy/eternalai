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
    OALiveTimeout,
    OAReadProvider,
)
from app.ports.adapter import AdapterResult
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


class ListPendingWorkflowsArguments(BaseModel):
    """Frozen GT-001 zero-argument capability input."""

    model_config = ConfigDict(extra="forbid", strict=True)


@dataclass(frozen=True)
class _CapabilityBinding:
    arguments_model: type[BaseModel]
    handler: Callable[[dict[str, Any]], Awaitable[AdapterResult]]


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
                )
            }
        )

    async def execute(
        self,
        capability_id: str,
        arguments: dict[str, Any],
        execution_context: dict[str, Any],
    ) -> AdapterResult:
        binding = self._bindings.get(capability_id)
        if binding is None:
            return _adapter_error()
        try:
            binding.arguments_model.model_validate(arguments, strict=True)
        except ValidationError:
            return _adapter_error()
        return await binding.handler(execution_context)

    async def _list_pending_workflows(
        self,
        execution_context: dict[str, Any],
    ) -> AdapterResult:
        credential: OASessionCredential | None = None
        try:
            if self._provider.requires_credential:
                credential_ref = execution_context.get("credential_ref")
                if not isinstance(credential_ref, str) or not credential_ref.strip():
                    return _classified_error("identity_unbound")
                if self._secret_provider is None:
                    return _adapter_error()
                credential = await self._secret_provider.resolve_oa_session(
                    credential_ref
                )
            collection = await self._provider.list_pending_workflows(credential)
            return AdapterResult(
                status="success",
                data=collection.model_dump(mode="json"),
                error_code=None,
            )
        except CredentialNotFoundError:
            return _classified_error("identity_unbound")
        except CredentialExpiredError:
            return _classified_error("identity_expired")
        except (
            InvalidCredentialReferenceError,
            CredentialStorageError,
            SecretProviderError,
        ):
            return _adapter_error()
        except OAContractPackPayloadInvalid:
            return _classified_error("adapter_payload_invalid")
        except OAContractPackError:
            return _adapter_error()
        except OALiveIdentityUnbound:
            return _classified_error("identity_unbound")
        except OALiveIdentityExpired:
            return _classified_error("identity_expired")
        except OALivePermissionDenied:
            return AdapterResult(
                status="permission_denied",
                data=None,
                error_code="upstream_permission_denied",
            )
        except OALiveTimeout:
            return AdapterResult(
                status="timeout",
                data=None,
                error_code="adapter_timeout",
            )
        except OALiveHTTPServerError:
            return _classified_error("adapter_http_500")
        except OALivePayloadInvalid:
            return _classified_error("adapter_payload_invalid")
        except OALiveProviderError:
            return _adapter_error()
        except Exception:
            logging.getLogger(__name__).error(
                "oa_read_adapter_failure capability_id=%s classification=%s",
                OA_LIST_PENDING_WORKFLOWS,
                "adapter_error",
            )
            return _adapter_error()
        finally:
            credential = None


def _classified_error(error_code: ErrorCode) -> AdapterResult:
    return AdapterResult(status="error", data=None, error_code=error_code)


def _adapter_error() -> AdapterResult:
    return AdapterResult(status="error", data=None, error_code="adapter_error")


__all__ = (
    "ListPendingWorkflowsArguments",
    "OA_LIST_PENDING_WORKFLOWS",
    "OAReadAdapter",
)
