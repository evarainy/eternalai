"""Capability gateway interface contract."""

from __future__ import annotations

from typing import Any, Literal, Protocol, TypeAlias

from pydantic import BaseModel

from app.ports.request_context import RequestChannel as RequestChannel
from app.ports.request_context import RequestOrgContext as RequestOrgContext

ErrorCode: TypeAlias = Literal[
    "identity_unbound",
    "identity_expired",
    "identity_revoked",
    "needs_binding_scope",
    "policy_denied",
    "confirm_required",
    "adapter_timeout",
    "capability_not_found",
    "adapter_error",
    "adapter_payload_invalid",
    "adapter_missing_required_field",
    "adapter_empty_response",
    "adapter_http_500",
    "upstream_permission_denied",
    "internal_error",
]

ExecutionStatus: TypeAlias = Literal[
    "completed",
    "failed",
    "denied",
    "binding_required",
    "timeout",
    "no_capability_found",
    "waiting_user",
]


class ExecutionResult(BaseModel):
    status: ExecutionStatus
    data: dict[str, Any] | None = None
    error_code: ErrorCode | None = None
    trace_id: str


class CapabilityGatewayPort(Protocol):
    async def execute_capability(
        self,
        task_id: str,
        session_id: str,
        ai_user_id: str,
        capability_id: str,
        arguments: dict[str, Any],
        request_context: RequestOrgContext,
    ) -> ExecutionResult: ...
