"""Authenticated browser Chat admission/read boundaries, independent of infrastructure."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import Field

from app.browser_skill.models import BrowserOwner, Contract
from app.browser_skill.run_contracts import BrowserRunView
from app.ports.auth import Principal
from app.ports.browser_run_store import ProtectedRunEnvelope, RunSnapshot
from app.ports.capability_registry import CapabilitySpec
from app.ports.credential_vault import BrowserBindingFact
from app.ports.response_envelope import ResponseEnvelope


class BrowserChatError(RuntimeError):
    def __init__(self, code: str, *, http_status: int = 503) -> None:
        if re.fullmatch(r"[a-z][a-z0-9_]{0,95}", code) is None:
            code = "browser_chat_unavailable"
        super().__init__("browser chat operation refused")
        self.code = code
        self.http_status = http_status if http_status in {401, 403, 404, 409, 422, 503} else 503


@dataclass(frozen=True, slots=True, repr=False)
class BrowserChatInputIdentity:
    owner: BrowserOwner
    task_id: str
    run_id: str
    target_system: str
    binding_id: str
    binding_revision: int
    auth_fingerprint: bytes = field(repr=False)
    auth_expires_at: datetime
    publication_digest: bytes
    input_revision: int
    input_digest: bytes = field(repr=False)
    auth_evidence_version: Literal["verified-session-v1"] = "verified-session-v1"


class BrowserChatCipherPort(Protocol):
    def encrypt_input(
        self, identity: BrowserChatInputIdentity, value: Mapping[str, object],
    ) -> ProtectedRunEnvelope: ...

    def decrypt_result(self, run: RunSnapshot) -> dict[str, Any]: ...


class BrowserChatParserPort(Protocol):
    async def parse(
        self, principal: Principal, message: str, capability: CapabilitySpec,
    ) -> dict[str, Any]:
        """Trusted parser for this frozen READ_ONLY capability; no browser dispatch."""
        ...


class BrowserChatBindingResolverPort(Protocol):
    async def resolve(
        self, principal: Principal, bound_session: str, capability: CapabilitySpec,
    ) -> BrowserBindingFact:
        """Read the current exact binding; ambiguity must fail closed, never choose a default."""
        ...


class BrowserRunResponse(Contract):
    run: BrowserRunView
    value: dict[str, Any] | None = Field(default=None, repr=False)


class BrowserChatPort(Protocol):
    @property
    def skill_id(self) -> str: ...

    def owner_cache_scope(self, principal: Principal) -> tuple[str, str]:
        """Stable opaque cache aliases, never internal IDs or authorization grants."""
        ...

    async def start(
        self, *, channel: Literal["web", "cli", "api", "mock"], principal: Principal,
        bound_session: str, message: str, client_capabilities: dict[str, Any],
        client_request_id: str, skill_id: str,
    ) -> ResponseEnvelope: ...

    async def get(
        self, principal: Principal, bound_session: str, task_id: str, run_id: str,
    ) -> BrowserRunResponse: ...

    async def cancel(
        self, principal: Principal, bound_session: str, task_id: str, run_id: str,
    ) -> BrowserRunResponse: ...
