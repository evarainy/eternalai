"""Request-scoped organization context shared by policy and execution ports."""

from __future__ import annotations

from typing import Literal, TypeAlias

from pydantic import BaseModel, Field

RequestChannel: TypeAlias = Literal["web", "cli", "api", "mock"]


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
