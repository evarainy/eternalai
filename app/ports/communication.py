"""Reader-scoped communication boundary; external identifiers are opaque strings."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, AsyncContextManager, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


@dataclass(frozen=True, slots=True)
class IMAccountBinding:
    tenant_id: str
    ai_user_id: str
    directory_user_id: str = field(repr=False)
    provider: str
    im_instance_id: str
    realm_id: str
    im_user_id: str
    login_subject: str = field(repr=False)
    status: str


class IMSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(min_length=1)
    im_instance_id: str = Field(min_length=1)
    realm_id: str = Field(min_length=1)
    im_user_id: str = Field(min_length=1)
    message_ids: tuple[
        Annotated[str, Field(strict=True, min_length=1, max_length=20, pattern="^[0-9]+$")], ...
    ] = Field(
        min_length=1,
        max_length=50,
        json_schema_extra={"uniqueItems": True},
        description=(
            "Positive ASCII decimal message IDs. Leading zeros are normalized; "
            "numeric aliases count as duplicates and are rejected. Returned in numeric order."
        ),
    )
    conversation_ref: str = Field(min_length=1, repr=False)

    @field_validator("message_ids")
    @classmethod
    def normalize_message_ids(cls, message_ids: tuple[str, ...]) -> tuple[str, ...]:
        ids = sorted(int(message_id) for message_id in message_ids)
        if ids[0] == 0:
            raise ValueError("message IDs must be positive")
        if len(set(ids)) != len(ids):
            raise ValueError("message IDs must be unique after normalization")
        return tuple(str(message_id) for message_id in ids)


@dataclass(frozen=True, slots=True)
class IMReadIdentity:
    provider: str
    im_instance_id: str
    realm_id: str
    im_user_id: str
    handle: object = field(repr=False)


@dataclass(frozen=True, slots=True)
class IMMessage:
    message_id: str
    conversation_ref: str = field(repr=False)
    text: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class IMReadContext:
    messages: tuple[IMMessage, ...] = field(repr=False)
    found_anchor: bool
    deep_link: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class IMLoginForm:
    action_url: str
    fields: dict[str, str] = field(repr=False)
    expires_at: datetime


class CommunicationReaderPort(Protocol):
    def current_identity(
        self, binding: IMAccountBinding
    ) -> AsyncContextManager[IMReadIdentity]: ...

    async def read_anchor(self, identity: IMReadIdentity, selection: IMSelection) -> IMMessage: ...

    async def read_context(
        self,
        identity: IMReadIdentity,
        selection: IMSelection,
    ) -> IMReadContext: ...


class CommunicationLoginPort(Protocol):
    def build_login_form(self, binding: IMAccountBinding) -> IMLoginForm: ...


@dataclass(frozen=True, slots=True)
class DeliveryMessage:
    provider: str
    im_instance_id: str
    realm_id: str
    sender_bot_id: str
    target_conversation_ref: str = field(repr=False)
    card_content: str = field(repr=False)
    kind: Literal["private", "group_status"] = "private"


@dataclass(frozen=True, slots=True)
class InboundEvent:
    """Authenticated callback metadata, never a user principal or stored question."""

    provider: str
    im_instance_id: str
    realm_id: str
    target_bot_id: str
    source_message_id: str
    trigger_kind: Literal["direct_message", "mention"]
    sender_id: str
    conversation_ref: str = field(repr=False)
    conversation_kind: Literal["private", "group"]
    payload_fingerprint: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class AuthenticatedInboundMessage:
    event: InboundEvent
    text: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class VerifiedInboundMessage:
    event: InboundEvent
    question: str = field(repr=False)


class CommunicationInboundPort(Protocol):
    async def check_identity(self, binding: IMAccountBinding) -> None: ...

    def authenticate_callback(
        self, *, target_bot_id: str, payload: dict[str, object],
    ) -> AuthenticatedInboundMessage: ...

    async def delegation_mentions(
        self, binding: IMAccountBinding, event: InboundEvent,
    ) -> frozenset[str] | None: ...

    async def reread_trigger(
        self, binding: IMAccountBinding, event: InboundEvent,
        *, delegate_bot_id: str | None = None,
    ) -> VerifiedInboundMessage: ...


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    state: Literal["accepted", "known_rejected", "unknown"]
    remote_message_id: str | None
    result_code: str


class CommunicationDeliveryPort(Protocol):
    def prepare_private_card(
        self,
        *,
        delivery_id: UUID,
        initiator_label: str,
        recipient_im_user_id: str,
        note: str,
    ) -> DeliveryMessage: ...

    def prepare_agent_reply(
        self, *, delivery_id: UUID, sender_bot_id: str, recipient_im_user_id: str, text: str,
    ) -> DeliveryMessage: ...

    def prepare_group_status(
        self, *, delivery_id: UUID, sender_bot_id: str, conversation_ref: str,
        delegate_display_name: str | None = None,
    ) -> DeliveryMessage: ...

    async def send_message_once(self, message: DeliveryMessage) -> DeliveryResult: ...

    async def reconcile_message(
        self, message: DeliveryMessage, delivery_id: UUID
    ) -> DeliveryResult: ...
