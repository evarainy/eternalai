"""The only backend adapter that understands Zulip 12.3 APIs and identifiers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import ssl
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from urllib.parse import quote
from uuid import UUID

import httpx2
from pydantic import SecretStr

from app.config import ZulipSettings
from app.ports.communication import (
    DeliveryResult,
    IMAccountBinding,
    IMLoginForm,
    IMMessage,
    IMReadContext,
    IMReadIdentity,
    IMSelection,
    PrivateCard,
)
from app.ports.im_context import IMContextError


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


@dataclass(slots=True)
class _Batch:
    client: httpx2.AsyncClient = field(repr=False)
    user_id: int


class ZulipCommunicationAdapter:
    def __init__(self, settings: ZulipSettings) -> None:
        self._settings = settings
        self._tls = ssl.create_default_context(cafile=settings.tls_ca_file)

    def _token(self, binding: IMAccountBinding) -> tuple[str, datetime]:
        expires = datetime.now(UTC) + timedelta(seconds=60)
        header = _b64(b'{"alg":"HS256","typ":"JWT"}')
        payload = _b64(
            json.dumps(
                {
                    "email": binding.login_subject,
                    "iat": int(datetime.now(UTC).timestamp()),
                    "exp": int(expires.timestamp()),
                },
                separators=(",", ":"),
            ).encode()
        )
        unsigned = header + "." + payload
        signature = hmac.new(self._settings.jwt_secret, unsigned.encode(), hashlib.sha256).digest()
        return unsigned + "." + _b64(signature), expires

    def build_login_form(self, binding: IMAccountBinding) -> IMLoginForm:
        token, expires = self._token(binding)
        return IMLoginForm(
            self._settings.origin + "/accounts/login/jwt/", {"token": token}, expires
        )

    @asynccontextmanager
    async def current_identity(self, binding: IMAccountBinding) -> AsyncIterator[IMReadIdentity]:
        async with httpx2.AsyncClient(
            base_url=self._settings.origin,
            verify=self._tls,
            follow_redirects=False,
            trust_env=False,
            timeout=15,
        ) as client:
            token, _ = self._token(binding)
            credentials = await self._request(
                client, "POST", "/api/v1/jwt/fetch_api_key", data={"token": token}
            )
            key = credentials.get("api_key")
            email = credentials.get("email")
            if not isinstance(key, str) or not key or not isinstance(email, str) or not email:
                raise IMContextError("im_identity_unavailable", 503)
            client.auth = httpx2.BasicAuth(email, key)
            me = await self._request(client, "GET", "/api/v1/users/me")
            uid = me.get("user_id")
            if type(uid) is not int or uid <= 0:
                raise IMContextError("im_identity_unavailable", 503)
            yield IMReadIdentity(
                "zulip",
                self._settings.im_instance_id,
                self._settings.realm_id,
                str(uid),
                _Batch(client, uid),
            )

    async def _request(
        self,
        client: httpx2.AsyncClient,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        try:
            response = await client.request(method, path, **kwargs)
            if response.status_code in (400, 401, 403, 404):
                raise IMContextError("im_source_not_found", 404)
            if response.status_code != 200:
                raise IMContextError("im_upstream_unavailable", 503)
            result = response.json()
        except (httpx2.RequestError, ValueError):
            raise IMContextError("im_upstream_unavailable", 503) from None
        if not isinstance(result, dict) or result.get("result") != "success":
            raise IMContextError("im_upstream_unavailable", 503)
        return cast(dict[str, Any], result)

    @staticmethod
    def _number(value: str) -> int:
        if not value.isascii() or not value.isdecimal() or len(value) > 20 or int(value) <= 0:
            raise IMContextError("im_source_not_found", 404)
        return int(value)

    def _conversation(self, raw: dict[str, Any], uid: int) -> str:
        if raw.get("type") == "stream":
            if type(raw.get("stream_id")) is not int or not isinstance(raw.get("subject"), str):
                raise IMContextError("im_source_changed")
            conversation = {
                "type": "stream",
                "stream_id": raw["stream_id"],
                "topic": raw["subject"],
            }
        elif raw.get("type") == "private":
            recipients = raw.get("display_recipient")
            if (
                not isinstance(recipients, list)
                or not recipients
                or any(not isinstance(r, dict) or type(r.get("id")) is not int for r in recipients)
            ):
                raise IMContextError("im_source_changed")
            ids = sorted(r["id"] for r in recipients if r["id"] != uid) or [uid]
            conversation = {"type": "private", "to_user_ids": ids}
        else:
            raise IMContextError("im_source_changed")
        # Canonical JSON is opaque to EternalAI; the IM-side v2 adapter uses the same encoding.
        return json.dumps(conversation, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    def _message(self, raw: Any, selection: IMSelection, uid: int) -> IMMessage:
        if (
            not isinstance(raw, dict)
            or type(raw.get("id")) is not int
            or not isinstance(raw.get("content"), str)
        ):
            raise IMContextError("im_source_changed")
        conversation = self._conversation(raw, uid)
        try:
            provided = json.dumps(json.loads(selection.conversation_ref), sort_keys=True,
                                  separators=(",", ":"), ensure_ascii=False)
        except ValueError:
            raise IMContextError("im_source_not_found", 404) from None
        if conversation != provided:
            raise IMContextError("im_source_not_found", 404)
        return IMMessage(
            str(raw["id"]),
            selection.conversation_ref,
            raw["content"].replace("\r\n", "\n").replace("\r", "\n"),
        )

    async def read_anchor(self, identity: IMReadIdentity, selection: IMSelection) -> IMMessage:
        batch = cast(_Batch, identity.handle)
        mid = self._number(selection.message_id)
        result = await self._request(
            batch.client, "GET", f"/api/v1/messages/{mid}", params={"apply_markdown": "false"}
        )
        return self._message(result.get("message"), selection, batch.user_id)

    async def read_context(self, identity: IMReadIdentity, selection: IMSelection) -> IMReadContext:
        batch = cast(_Batch, identity.handle)
        mid = self._number(selection.message_id)
        # The preceding anchor read established the canonical conversation; never accept raw narrow.
        conversation = json.loads(selection.conversation_ref)
        if conversation["type"] == "stream":
            narrow = [
                {"operator": "channel", "operand": conversation["stream_id"]},
                {"operator": "topic", "operand": conversation["topic"]},
            ]
            fragment = (
                "narrow/channel/"
                + str(conversation["stream_id"])
                + "/topic/"
                + "".join({"%": ".", ".": ".2E"}.get(c, c)
                          for c in quote(conversation["topic"], safe=""))
                + "/near/"
                + str(mid)
            )
        else:
            narrow = [{"operator": "dm", "operand": conversation["to_user_ids"]}]
            fragment = (
                "narrow/dm/" + ",".join(map(str, conversation["to_user_ids"])) + "/near/" + str(mid)
            )
        result = await self._request(
            batch.client,
            "GET",
            "/api/v1/messages",
            params={
                "anchor": str(mid),
                "num_before": "3",
                "num_after": "0",
                "include_anchor": "true",
                "narrow": json.dumps(narrow),
                "apply_markdown": "false",
            },
        )
        raw = result.get("messages")
        if not isinstance(raw, list) or any(
            not isinstance(m, dict) or type(m.get("id")) is not int or m["id"] > mid for m in raw
        ):
            raise IMContextError("im_source_changed")
        messages = tuple(self._message(m, selection, batch.user_id) for m in raw)
        return IMReadContext(
            messages, result.get("found_anchor") is True, self._settings.origin + "/#" + fragment
        )


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
            target_conversation_ref=target,
            card_content=content,
        )

    def _recipient(self, card: PrivateCard) -> int:
        if ((card.provider, card.im_instance_id, card.realm_id, card.sender_bot_id)
                != (self._provider, self._instance, self._realm, self._bot_id)):
            raise ValueError("im_delivery_target_changed")
        users = cast(list[int], json.loads(card.target_conversation_ref)["user_ids"])
        return next(user for user in users if str(user) != self._bot_id)

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
        try:
            recipient = self._recipient(card)
        except ValueError:
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
                    "to": json.dumps([recipient]),
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
        try:
            recipient = self._recipient(card)
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
                        {"operator": "dm", "operand": recipient},
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
        expected = {self._number(card.sender_bot_id), recipient}
        if (len(recipients) != len(expected) or {user["id"] for user in recipients} != expected):
            return unknown
        content = message.get("content")
        if (message.get("content_type") != "text/x-markdown"
                or not isinstance(content, str)
                or message.get("last_edit_timestamp") is not None
                or message.get("edit_history")
                or content != card.card_content
                or type(message.get("id")) is not int or message["id"] <= 0):
            return unknown
        return DeliveryResult("accepted", str(message["id"]), "im_delivery_reconciled")
