"""Trace interface contract."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal, Protocol, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.ports.capability_gateway import ErrorCode

TraceEventType: TypeAlias = Literal[
    "task_created",
    "intent_parsed",
    "capability_selected",
    "no_capability_found",
    "identity_check",
    "blocked_by_identity",
    "policy_checked",
    "blocked_by_policy",
    "confirm_required",
    "gateway_pre_recorded",
    "adapter_called",
    "adapter_error",
    "adapter_error_mapped",
    "adapter_result_invalid",
    "gateway_post_recorded",
    "response_envelope_created",
    "task_completed",
    "task_failed",
    "evaluation_recorded",
    "admin_action",
    "user_action",
]

TraceEventStatus: TypeAlias = Literal["ok", "blocked", "failed", "skipped"]

SanitizerHookFn: TypeAlias = Callable[[dict[str, Any]], dict[str, Any]]
TRACE_QUERY_LIMIT = 100
REDACTED_TRACE_VALUE = "[REDACTED]"

_CREDENTIAL_KEYS = frozenset(
    {
        "authorization",
        "bearer",
        "cookie",
        "cookies",
        "set_cookie",
        "setcookie",
        "session",
        "session_id",
        "sessionid",
        "token",
        "access_token",
        "accesstoken",
        "refresh_token",
        "refreshtoken",
        "password",
        "passwd",
        "api_key",
        "apikey",
        "x_api_key",
        "x_auth_token",
        "x_access_token",
        "proxy_authorization",
        "secret",
        "client_secret",
        "clientsecret",
        "private_key",
        "privatekey",
        "loginid",
        "login_id",
        "userpassword",
        "user_password",
        "oa_password",
        "userid",
        "user_id",
        "oa_userid",
        "oa_user_id",
        "oa_cookies",
        "ecology_jsessionid",
        "loginidweaver",
        "loginuuids",
        "_clustersessioncookiename",
        "_clustersessionidcookiename",
        "cluster_session_cookie_name",
        "cluster_session_id_cookie_name",
        "credential",
        "credential_blob",
        "credential_ciphertext",
        "encrypted_loginid",
        "encrypted_userpassword",
        "rsa_code",
    }
)
_CREDENTIAL_VALUE_PATTERNS = (
    re.compile(r"(?<!\d)(?:\d{17}[\dXx]|\d{15})(?!\d)"),
    re.compile(r"bearer\s+\S+", re.IGNORECASE),
    re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/@]+@", re.IGNORECASE),
    re.compile(
        r"(?:authorization|session(?:[\s_-]?id)?|access[\s_-]?token|"
        r"refresh[\s_-]?token|set[\s_-]?cookie|cookie|password|passwd|"
        r"api[\s_-]?key|secret|client[\s_-]?secret|private[\s_-]?key|"
        r"loginid|userpassword|oa[\s_-]?userid|userid)"
        r"\s*[:=]\s*\S+",
        re.IGNORECASE,
    ),
)
_TOP_LEVEL_CREDENTIAL_VALUE_PATTERNS = _CREDENTIAL_VALUE_PATTERNS[1:]


def redact_trace_attributes(attributes: dict[str, Any]) -> dict[str, Any]:
    """Return a credential-redacted copy while preserving attribute keys."""

    return {
        key: REDACTED_TRACE_VALUE if _is_credential_key(key) else _redact_value(value)
        for key, value in attributes.items()
    }


def _redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return redact_trace_attributes(value)
    if isinstance(value, (list, tuple)):
        return [_redact_value(item) for item in value]
    if isinstance(value, str) and _is_credential_value(value):
        return REDACTED_TRACE_VALUE
    return value


def _is_credential_key(key: str) -> bool:
    normalized = re.sub(r"[\s_-]+", "_", key.strip().lower())
    return normalized in _CREDENTIAL_KEYS or normalized.endswith("_token")


def _is_credential_value(value: str) -> bool:
    return any(pattern.search(value) for pattern in _CREDENTIAL_VALUE_PATTERNS)


def _is_top_level_credential_value(value: str) -> bool:
    return any(pattern.search(value) for pattern in _TOP_LEVEL_CREDENTIAL_VALUE_PATTERNS)


class TraceEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: str
    task_id: str
    session_id: str
    tenant_id: str = Field(min_length=1)
    ai_user_id: str = Field(min_length=1)
    event_type: TraceEventType
    status: TraceEventStatus
    capability_id: str | None = None
    error_code: ErrorCode | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tenant_id", "ai_user_id")
    @classmethod
    def reject_blank_owner(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("trace owner must not be blank")
        return value

    @field_validator(
        "trace_id",
        "task_id",
        "session_id",
        "tenant_id",
        "ai_user_id",
        "capability_id",
    )
    @classmethod
    def reject_top_level_credential_shape(cls, value: str | None) -> str | None:
        if value is not None and _is_top_level_credential_value(value):
            raise ValueError("trace top-level identifier has credential shape")
        return value


class TracePersistedEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    trace_id: str
    task_id: str
    session_id: str
    tenant_id: str = Field(min_length=1)
    ai_user_id: str = Field(min_length=1)
    event_type: TraceEventType
    status: TraceEventStatus
    capability_id: str | None = None
    error_code: ErrorCode | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    @field_validator("tenant_id", "ai_user_id")
    @classmethod
    def reject_blank_owner(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("trace owner must not be blank")
        return value

    @field_validator(
        "event_id",
        "trace_id",
        "task_id",
        "session_id",
        "tenant_id",
        "ai_user_id",
        "capability_id",
    )
    @classmethod
    def reject_top_level_credential_shape(cls, value: str | None) -> str | None:
        if value is not None and _is_top_level_credential_value(value):
            raise ValueError("trace top-level identifier has credential shape")
        return value


class TracePort(Protocol):
    def set_sanitizer(self, hook: SanitizerHookFn) -> None: ...

    async def record_event(self, event: TraceEvent) -> None: ...

    async def start_task_trace(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
        *,
        tenant_id: str,
        ai_user_id: str,
    ) -> None: ...

    async def record_step(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
        *,
        tenant_id: str,
        ai_user_id: str,
        event_type: TraceEventType,
        status: TraceEventStatus,
        capability_id: str | None = None,
        error_code: ErrorCode | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None: ...

    async def record_policy_decision(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
        *,
        tenant_id: str,
        ai_user_id: str,
        status: TraceEventStatus,
        capability_id: str | None = None,
        error_code: ErrorCode | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None: ...

    async def record_gateway_call(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
        *,
        tenant_id: str,
        ai_user_id: str,
        status: TraceEventStatus,
        capability_id: str | None = None,
        error_code: ErrorCode | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None: ...

    async def finalize_task_trace(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
        *,
        tenant_id: str,
        ai_user_id: str,
        status: TraceEventStatus,
        capability_id: str | None = None,
        error_code: ErrorCode | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None: ...


class TraceQueryPort(Protocol):
    async def list_events_by_trace(
        self,
        trace_id: str,
        *,
        tenant_id: str,
        task_id: str | None = None,
        session_id: str | None = None,
        limit: int = TRACE_QUERY_LIMIT,
    ) -> list[TracePersistedEvent]: ...

    async def list_events_by_task(
        self,
        task_id: str,
        *,
        tenant_id: str,
        trace_id: str | None = None,
        session_id: str | None = None,
        limit: int = TRACE_QUERY_LIMIT,
    ) -> list[TracePersistedEvent]: ...

    async def list_events_by_session(
        self,
        session_id: str,
        *,
        tenant_id: str,
        trace_id: str | None = None,
        task_id: str | None = None,
        limit: int = TRACE_QUERY_LIMIT,
    ) -> list[TracePersistedEvent]: ...
