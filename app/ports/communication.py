"""Provider-independent boundary for a confirmed, single private-card delivery."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class PrivateCard:
    provider: str
    im_instance_id: str
    realm_id: str
    sender_bot_id: str
    recipient_im_user_id: str
    target_conversation_ref: str = field(repr=False)
    card_content: str = field(repr=False)


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
    ) -> PrivateCard: ...

    async def send_private_card(self, card: PrivateCard) -> DeliveryResult: ...

    async def reconcile_private_card(
        self, card: PrivateCard, delivery_id: UUID
    ) -> DeliveryResult: ...
