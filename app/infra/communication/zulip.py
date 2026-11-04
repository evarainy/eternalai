"""Zulip 12.3 private-card sending and read-only reconciliation.

The caller persists its one send attempt before calling ``send_private_card``.
No method retries a send or uses local-echo IDs as a delivery guarantee.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, cast
from uuid import UUID

import httpx2
from pydantic import SecretStr

from app.ports.communication import DeliveryResult, PrivateCard


class ZulipDeliveryAdapter:
    def __init__(
        self,
        *,
        client: httpx2.AsyncClient,
        base_url: str,
        provider: str,
        im_instance_id: str,
        realm_id: str,
        sender_bot_id: str,
        bot_email: str,
        bot_api_key: SecretStr,
        host_origin: str,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._provider = provider
        self._instance = im_instance_id
        self._realm = realm_id
        self._bot_id = sender_bot_id
        self._auth = httpx2.BasicAuth(bot_email, bot_api_key.get_secret_value())
        self._host_origin = host_origin.rstrip("/")

    @staticmethod
    def _number(value: str) -> int:
        if not value.isascii() or not value.isdecimal() or int(value) <= 0:
            raise ValueError("im_delivery_identity_invalid")
        return int(value)

    def _conversation(self, recipient: str) -> str:
        return json.dumps(
            {"type": "private", "user_ids": sorted(
                {self._number(self._bot_id), self._number(recipient)}
            )},
            sort_keys=True,
            separators=(",", ":"),
        )

    def prepare_private_card(
        self,
        *,
        delivery_id: UUID,
        initiator_label: str,
        recipient_im_user_id: str,
        note: str,
    ) -> PrivateCard:
        target = self._conversation(recipient_im_user_id)
        content = (
            f"EternalAI 助手，受{initiator_label}委托\n\n{note}\n\n"
            f"投递编号：{delivery_id}\n"
            f"[回到 EternalAI 核实文字]({self._host_origin}/messages?delivery={delivery_id})"
        )
        return PrivateCard(
            provider=self._provider,
            im_instance_id=self._instance,
            realm_id=self._realm,
            sender_bot_id=self._bot_id,
            recipient_im_user_id=recipient_im_user_id,
            target_conversation_ref=target,
            card_content=content,
        )

    def _same_target(self, card: PrivateCard) -> bool:
        return (
            (card.provider, card.im_instance_id, card.realm_id, card.sender_bot_id)
            == (self._provider, self._instance, self._realm, self._bot_id)
            and card.target_conversation_ref == self._conversation(card.recipient_im_user_id)
        )

    async def _bot_identity(self) -> bool:
        response = await self._client.get(
            self._base_url + "/api/v1/users/me", auth=self._auth, follow_redirects=False
        )
        data = response.json()
        return (
            response.status_code == 200
            and isinstance(data, dict)
            and data.get("result") == "success"
            and type(data.get("user_id")) is int
            and str(data["user_id"]) == self._bot_id
            and data.get("is_bot") is True
            and data.get("is_active") is True
        )

    async def send_private_card(self, card: PrivateCard) -> DeliveryResult:
        if not self._same_target(card):
            return DeliveryResult("known_rejected", None, "im_delivery_target_changed")
        try:
            if not await self._bot_identity():
                return DeliveryResult("known_rejected", None, "im_bot_identity_changed")
        except (httpx2.RequestError, ValueError):
            # No send request was made, so this preflight failure is unambiguous.
            return DeliveryResult("known_rejected", None, "im_bot_identity_unavailable")
        try:
            response = await self._client.post(
                self._base_url + "/api/v1/messages",
                auth=self._auth,
                follow_redirects=False,
                data={
                    "type": "direct",
                    "to": json.dumps([self._number(card.recipient_im_user_id)]),
                    "content": card.card_content,
                },
            )
            data = response.json()
        except (httpx2.RequestError, ValueError):
            return DeliveryResult("unknown", None, "im_delivery_response_unknown")
        if isinstance(data, dict):
            if (response.status_code == 200 and data.get("result") == "success"
                    and type(data.get("id")) is int and data["id"] > 0):
                return DeliveryResult("accepted", str(data["id"]), "im_delivery_accepted")
            if (response.status_code in (400, 401, 403, 404, 429)
                    and data.get("result") == "error"):
                return DeliveryResult("known_rejected", None, "im_delivery_rejected")
        return DeliveryResult("unknown", None, "im_delivery_response_unknown")

    async def reconcile_private_card(
        self, card: PrivateCard, delivery_id: UUID
    ) -> DeliveryResult:
        unknown = DeliveryResult("unknown", None, "im_delivery_unconfirmed")
        if not self._same_target(card):
            return unknown
        try:
            if not await self._bot_identity():
                return unknown
            response = await self._client.get(
                self._base_url + "/api/v1/messages",
                auth=self._auth,
                follow_redirects=False,
                params={
                    "anchor": "newest",
                    "num_before": 100,
                    "num_after": 0,
                    "apply_markdown": "false",
                    "narrow": json.dumps([
                        {"operator": "dm", "operand": self._number(card.recipient_im_user_id)},
                        {"operator": "search", "operand": str(delivery_id)},
                    ]),
                },
            )
            data = response.json()
        except (httpx2.RequestError, ValueError):
            return unknown
        if (response.status_code != 200 or not isinstance(data, dict)
                or data.get("result") != "success"
                or data.get("found_oldest") is not True
                or data.get("found_newest") is not True
                or data.get("history_limited") is not False):
            return unknown
        messages = data.get("messages")
        if not isinstance(messages, list) or len(messages) != 1:
            return unknown
        raw = messages[0]
        if not isinstance(raw, dict):
            return unknown
        message = cast(dict[str, Any], raw)
        recipients = message.get("display_recipient")
        if (message.get("type") != "private"
                or type(message.get("sender_id")) is not int
                or str(message["sender_id"]) != card.sender_bot_id
                or not isinstance(recipients, list)
                or any(not isinstance(user, dict) or type(user.get("id")) is not int
                       for user in recipients)):
            return unknown
        expected = {self._number(card.sender_bot_id), self._number(card.recipient_im_user_id)}
        if (len(recipients) != len(expected) or {user["id"] for user in recipients} != expected):
            return unknown
        content = message.get("content")
        if (message.get("content_type") != "text/x-markdown"
                or not isinstance(content, str) or str(delivery_id) not in content
                or message.get("last_edit_timestamp") is not None
                or message.get("edit_history")
                or hashlib.sha256(content.encode()).digest()
                != hashlib.sha256(card.card_content.encode()).digest()
                or type(message.get("id")) is not int or message["id"] <= 0):
            return unknown
        return DeliveryResult("accepted", str(message["id"]), "im_delivery_reconciled")
