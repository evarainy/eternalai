"""Local MCP configuration and immutable ownership values, never bearer secrets."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

ProtocolVersion = Literal["2025-11-25", "2026-07-28"]


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


class McpFailure(RuntimeError):
    """Closed local failure classification; no upstream exception or payload text."""

    def __init__(self, code: str, *, may_have_sent: bool = False) -> None:
        self.code = code
        self.may_have_sent = may_have_sent
        super().__init__(code)


class ServiceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    service_config_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    service_config_version: int = Field(ge=1)
    deployment_id: str = Field(min_length=1, max_length=100)
    tenant_id: str = Field(min_length=1, max_length=100)
    display_name: str = Field(min_length=1, max_length=100)
    endpoint: str
    issuer: str
    resource: str
    registration_endpoint: str
    authorization_endpoint: str
    token_endpoint: str
    callback_uri: str
    callback_config_version: int = Field(ge=1)
    protocol_version: ProtocolVersion = "2025-11-25"
    enabled: bool = False
    response_limit: int = Field(default=32 * 1024 * 1024, ge=1024, le=32 * 1024 * 1024)
    deadline_seconds: float = Field(default=90, gt=0, le=90)

    @model_validator(mode="after")
    def endpoints_are_fixed(self) -> ServiceConfig:
        for name in (
            "endpoint",
            "issuer",
            "resource",
            "registration_endpoint",
            "authorization_endpoint",
            "token_endpoint",
            "callback_uri",
        ):
            value = getattr(self, name)
            parsed = urlsplit(value)
            if (
                any(ord(c) < 33 for c in value)
                or parsed.username
                or parsed.password
                or parsed.fragment
                or parsed.query
                or not parsed.hostname
                or not (
                    parsed.scheme == "https"
                    or (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "::1"})
                )
            ):
                raise ValueError("mcp_configuration_invalid")
        issuer = urlsplit(self.issuer)
        for name in ("registration_endpoint", "authorization_endpoint", "token_endpoint"):
            endpoint = urlsplit(getattr(self, name))
            if (endpoint.scheme, endpoint.netloc) != (issuer.scheme, issuer.netloc):
                raise ValueError("mcp_configuration_invalid")
        if urlsplit(self.callback_uri).hostname == issuer.hostname:
            raise ValueError("mcp_callback_host_invalid")
        return self


class ToolBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    capability_id: str
    capability_version: str
    service_config_id: str
    remote_tool: str
    input_digest: str
    safety_digest: str
    output_contract_version: str
    policy_version: str
    internal_only: bool = False


class Ownership(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str
    user_id: str
    service_config_id: str
    connection_id: str


OperationState = Literal[
    "READY",
    "WAITING_LOCAL_CONFIRM",
    "WAITING_EXTERNAL_CONFIRM",
    "SENDING",
    "UNKNOWN",
    "VERIFIED_SUCCESS",
    "FAILED",
    "CANCELLED",
    "EXPIRED",
]
