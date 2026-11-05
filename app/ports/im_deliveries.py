"""Text delivery snapshots, receipts and the transactional Outbox boundary."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol, cast
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.ports.auth import Principal
from app.ports.communication import DeliveryMessage, DeliveryResult

DeliveryState = Literal[
    "prepared", "ready", "sending", "accepted", "known_rejected", "unknown",
    "cancelled", "expired",
]
VerificationResult = Literal["verified", "needs_changes"]


class IMDeliveryError(Exception):
    def __init__(self, code: str, status_code: int = 409) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class DeliveryPrepare(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    recipient_ai_user_id: str = Field(min_length=1)
    note: str = Field(min_length=1)
    send_expires_at: AwareDatetime
    idempotency_key: UUID


class DeliveryVerification(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    result: VerificationResult
    comment: str
    idempotency_key: UUID


class VerificationView(BaseModel):
    verification_id: UUID
    result: VerificationResult
    comment: str
    created_at: datetime


class DeliveryRecipient(BaseModel):
    ai_user_id: str
    display_name: str
    im_user_id: str


class DeliveryView(BaseModel):
    delivery_id: UUID
    initiator_ai_user_id: str
    recipient_ai_user_id: str
    viewer_role: Literal["initiator", "recipient"]
    provider: str
    im_instance_id: str
    realm_id: str
    sender_bot_id: str
    target_conversation_ref: str
    card_content: str
    note: str
    confirmation_sha256: str
    state: DeliveryState
    created_at: datetime
    confirmed_at: datetime | None
    send_expires_at: datetime
    remote_message_id: str | None
    accepted_at: datetime | None
    result_code: str | None
    updated_at: datetime
    verifications: list[VerificationView]


@dataclass(frozen=True)
class DeliveryBinding:
    binding_id: UUID
    tenant_id: str
    ai_user_id: str
    directory_user_id: str = field(repr=False)
    provider: str
    im_instance_id: str
    realm_id: str
    im_user_id: str
    login_subject: str = field(repr=False)
    status: str

    @property
    def version(self) -> bytes:
        return _digest({name: str(value) for name, value in vars(self).items()})


@dataclass(frozen=True)
class DeliveryRecord:
    delivery_id: UUID
    tenant_id: str
    initiator_ai_user_id: str
    recipient_ai_user_id: str | None
    recipient_binding_id: UUID | None
    recipient_mapping_version: bytes | None = field(repr=False)
    provider: str
    im_instance_id: str
    realm_id: str
    sender_bot_id: str
    target_conversation_ref: str
    card_content: str = field(repr=False)
    note: str = field(repr=False)
    confirmation_sha256: bytes | None = field(repr=False)
    idempotency_key: UUID
    state: DeliveryState
    created_at: datetime
    confirmed_at: datetime | None
    send_expires_at: datetime | None
    queue_job_id: int | None
    send_attempt_id: UUID | None
    attempt_started_at: datetime | None
    remote_message_id: str | None
    accepted_at: datetime | None
    result_code: str | None
    updated_at: datetime
    origin_inbox_id: UUID | None = None
    reply_slot: Literal["private", "group_status"] | None = None


def _digest(value: dict[str, str]) -> bytes:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode()).digest()


def confirmation_digest(
    record: DeliveryRecord, initiator: DeliveryBinding, recipient: DeliveryBinding,
) -> bytes:
    """Bind the actual persisted preview to both verified identity mappings."""
    return _digest({
        "delivery_id": str(record.delivery_id), "tenant_id": record.tenant_id,
        "initiator": record.initiator_ai_user_id,
        "initiator_binding": str(initiator.binding_id),
        "initiator_mapping": initiator.version.hex(),
        "recipient": cast(str, record.recipient_ai_user_id),
        "recipient_binding": str(recipient.binding_id),
        "recipient_mapping": recipient.version.hex(),
        "provider": record.provider, "instance": record.im_instance_id,
        "realm": record.realm_id, "bot": record.sender_bot_id,
        "conversation": record.target_conversation_ref,
        "content": record.card_content, "note": record.note,
        "send_expires_at": cast(datetime, record.send_expires_at).isoformat(),
    })


class IMDeliveryStorePort(Protocol):
    async def recipients(self, principal: Principal) -> list[DeliveryRecipient]: ...

    async def bindings(
        self, principal: Principal, recipient_ai_user_id: str,
    ) -> tuple[DeliveryBinding, DeliveryBinding]: ...

    async def find_request(
        self, principal: Principal, key: UUID,
    ) -> DeliveryRecord | None: ...

    async def prepare(self, record: DeliveryRecord) -> DeliveryRecord: ...

    async def confirm(
        self, principal: Principal, delivery_id: UUID, confirmation_sha256: bytes,
    ) -> DeliveryRecord: ...

    async def get(self, principal: Principal, delivery_id: UUID) -> DeliveryView: ...

    async def pending_reconciliation(self) -> list[UUID]: ...

    async def list(self, principal: Principal) -> list[DeliveryView]: ...

    async def cancel(self, principal: Principal, delivery_id: UUID) -> None: ...

    async def verify(
        self, principal: Principal, delivery_id: UUID, request: DeliveryVerification,
    ) -> None: ...

    async def origin_inbox(self, delivery_id: UUID) -> UUID | None: ...

    async def claim(self, delivery_id: UUID) -> tuple[DeliveryRecord, DeliveryMessage] | None: ...

    async def finish(
        self, delivery_id: UUID, attempt_id: UUID, result: DeliveryResult,
    ) -> None: ...

    async def uncertain(
        self, delivery_id: UUID,
    ) -> tuple[DeliveryRecord, DeliveryMessage] | None: ...

    async def recover_ready(self) -> None: ...
