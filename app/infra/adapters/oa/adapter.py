"""Real OA read adapter with a fixed capability allowlist."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from app.infra.adapters.oa.provider import (
    LiveOAReadProviderNotImplemented,
    OAContractPackError,
    OAContractPackPayloadInvalid,
    OAReadProvider,
)
from app.ports.adapter import AdapterResult

OA_LIST_PENDING_WORKFLOWS = "oa.list_pending_workflows"


class ListPendingWorkflowsArguments(BaseModel):
    """Frozen GT-001 zero-argument capability input."""

    model_config = ConfigDict(extra="forbid", strict=True)


@dataclass(frozen=True)
class _CapabilityBinding:
    arguments_model: type[BaseModel]
    handler: Callable[[], Awaitable[AdapterResult]]


class OAReadAdapter:
    """Dispatch only explicitly registered OA read capabilities."""

    def __init__(self, provider: OAReadProvider) -> None:
        self._provider = provider
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
        del execution_context
        binding = self._bindings.get(capability_id)
        if binding is None:
            return _adapter_error()
        try:
            binding.arguments_model.model_validate(arguments, strict=True)
        except ValidationError:
            return _adapter_error()
        return await binding.handler()

    async def _list_pending_workflows(self) -> AdapterResult:
        try:
            collection = await self._provider.list_pending_workflows()
        except OAContractPackPayloadInvalid:
            return AdapterResult(
                status="error",
                data=None,
                error_code="adapter_payload_invalid",
            )
        except (OAContractPackError, LiveOAReadProviderNotImplemented):
            return _adapter_error()
        except Exception:
            return _adapter_error()
        return AdapterResult(
            status="success",
            data=collection.model_dump(mode="json"),
            error_code=None,
        )


def _adapter_error() -> AdapterResult:
    return AdapterResult(status="error", data=None, error_code="adapter_error")


__all__ = (
    "ListPendingWorkflowsArguments",
    "OA_LIST_PENDING_WORKFLOWS",
    "OAReadAdapter",
)
