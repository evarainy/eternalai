"""Published-text grants and the dedicated IM Inbox; no owner session delegation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.ports.auth import Principal
from app.ports.communication import DeliveryMessage, InboundEvent
from app.ports.im_deliveries import DeliveryBinding, DeliveryRecord, DeliveryState

REFUSAL_TEXT = "不具备有效授权或资料已失效，无法代答"


class PersonalAgentError(Exception):
    def __init__(self, code: str, status_code: int = 409) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class PublishMaterial(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1)
    body: str = Field(min_length=1, repr=False)
    expires_at: AwareDatetime


class GrantMaterial(BaseModel):
    model_config = ConfigDict(extra="forbid")
    material_version: int = Field(gt=0)
    grantee_ai_user_id: str = Field(min_length=1)
    expires_at: AwareDatetime


class AgentView(BaseModel):
    agent_id: UUID
    display_name: str
    bot_user_id: str
    status: Literal["active", "disabled"]


class MaterialView(BaseModel):
    version: int
    title: str
    body: str = Field(repr=False)
    content_sha256: str
    published_at: datetime
    expires_at: datetime
    withdrawn_at: datetime | None


class GranteeView(BaseModel):
    ai_user_id: str
    display_name: str


class GrantView(BaseModel):
    grant_id: UUID
    material_version: int
    grantee_ai_user_id: str
    grantee_display_name: str
    expires_at: datetime
    revoked_at: datetime | None


class ReplyView(BaseModel):
    delivery_id: UUID
    reply_slot: Literal["private", "group_status"]
    state: DeliveryState
    result_code: str | None
    remote_message_id: str | None


class DelegationView(BaseModel):
    target_agent_id: UUID
    target_agent_display_name: str


class RequestView(BaseModel):
    inbox_id: UUID
    agent_display_name: str
    state: Literal["queued", "running", "completed", "refused", "failed"]
    result_code: str | None
    received_at: datetime
    updated_at: datetime
    deliveries: list[ReplyView]
    delegation: DelegationView | None = None


@dataclass(frozen=True)
class PersonalAgent:
    agent_id: UUID
    tenant_id: str
    owner_binding_id: UUID
    owner_binding_version: bytes = field(repr=False)
    provider: str
    im_instance_id: str
    realm_id: str
    bot_user_id: str
    display_name: str
    status: str


@dataclass(frozen=True)
class DelegationContext:
    target_agent: PersonalAgent
    target_owner: DeliveryBinding


@dataclass(frozen=True)
class InboxContext:
    inbox_id: UUID
    agent: PersonalAgent
    actor: DeliveryBinding
    owner: DeliveryBinding
    actor_binding_version: bytes = field(repr=False)
    event: InboundEvent
    grant_id: UUID | None
    state: str
    delegation: DelegationContext | None = None

    @property
    def execution_agent(self) -> PersonalAgent:
        return self.agent if self.delegation is None else self.delegation.target_agent

    @property
    def execution_owner(self) -> DeliveryBinding:
        return self.owner if self.delegation is None else self.delegation.target_owner


@dataclass(frozen=True)
class AuthorizedMaterial:
    grant_id: UUID
    material: MaterialView = field(repr=False)


@dataclass(frozen=True)
class ReceiveResult:
    inbox_id: UUID | None
    result_code: str


class AgentReplyPort(Protocol):
    async def claim_reply(
        self, delivery_id: UUID,
    ) -> tuple[DeliveryRecord, DeliveryMessage] | None: ...


class PersonalAgentStorePort(Protocol):
    async def agents(self, principal: Principal) -> list[AgentView]: ...
    async def agent(self, agent_id: UUID) -> PersonalAgent: ...
    async def materials(self, principal: Principal, agent_id: UUID) -> list[MaterialView]: ...
    async def publish(
        self, principal: Principal, agent_id: UUID, request: PublishMaterial,
    ) -> MaterialView: ...
    async def grants(self, principal: Principal, agent_id: UUID) -> list[GrantView]: ...
    async def recipients(self, principal: Principal, agent_id: UUID) -> list[GranteeView]: ...
    async def grant(
        self, principal: Principal, agent_id: UUID, request: GrantMaterial,
    ) -> GrantView: ...
    async def revoke(
        self, principal: Principal, agent_id: UUID, grant_id: UUID,
    ) -> GrantView: ...
    async def withdraw(
        self, principal: Principal, agent_id: UUID, version: int,
    ) -> MaterialView: ...
    async def requests(self, principal: Principal) -> list[RequestView]: ...
    async def inbound_actor(
        self, agent: PersonalAgent, event: InboundEvent,
    ) -> DeliveryBinding | None: ...
    async def delegation_agents(
        self, actor: DeliveryBinding, bot_ids: frozenset[str],
    ) -> tuple[PersonalAgent, PersonalAgent] | None: ...
    async def accept(
        self, agent_id: UUID, event: InboundEvent, *, delegate_agent_id: UUID | None = None,
    ) -> ReceiveResult: ...
    async def claim_inbox(self, inbox_id: UUID) -> InboxContext | None: ...
    async def material(self, context: InboxContext) -> AuthorizedMaterial | None: ...
    async def enqueue_status(
        self, context: InboxContext, delivery_id: UUID, message: DeliveryMessage,
    ) -> None: ...
    async def complete(
        self, context: InboxContext, delivery_id: UUID, answer: DeliveryMessage | None,
        refusal: DeliveryMessage, result_code: str,
    ) -> None: ...
    async def fail(self, inbox_id: UUID, code: str) -> None: ...
    async def reply_context(self, delivery_id: UUID) -> InboxContext | None: ...
    async def claim_reply(
        self, context: InboxContext, delivery_id: UUID, *, source_valid: bool,
        refusal: DeliveryMessage,
    ) -> tuple[DeliveryRecord, DeliveryMessage] | None: ...
    async def maintain_inbox(self) -> None: ...
