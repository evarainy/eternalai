"""Capability gateway interface contract."""

from __future__ import annotations

from typing import Any, Literal, Protocol, TypeAlias

from pydantic import BaseModel, Field

RequestChannel: TypeAlias = Literal["web", "cli", "api", "mock"]

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


class RequestOrgContext(BaseModel):
    request_id: str
    tenant_id: str = "default"
    org_id: str | None = None
    department_id: str | None = None
    roles: list[str] = Field(default_factory=list)
    channel: RequestChannel = "web"
    locale: str = "zh-CN"
    account_set_id: str | None = None
    device_domain_id: str | None = None
    resource_scope: str | None = None


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
