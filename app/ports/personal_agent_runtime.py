"""One authorized material snapshot, analyzed as the verified IM requester."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from app.ports.auth import Principal
from app.ports.request_context import RequestOrgContext


@dataclass(frozen=True, slots=True)
class PublishedMaterialRequest:
    inbox_id: UUID
    principal: Principal
    target_agent_id: UUID
    grant_id: UUID
    material_version: int
    content_sha256: bytes
    question: str = field(repr=False)
    material: str = field(repr=False)
    org_context: RequestOrgContext


@dataclass(frozen=True, slots=True)
class PublishedMaterialAnswer:
    text: str = field(repr=False)
    result_code: str
    material_version: int
    content_sha256: bytes


class PersonalAgentRuntimeError(RuntimeError):
    """A symbolic failure, with no provider response or material text."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class PersonalAgentRuntimePort(Protocol):
    async def answer_published_material(
        self, request: PublishedMaterialRequest,
    ) -> PublishedMaterialAnswer: ...
