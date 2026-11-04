"""The only backend adapter that understands Zulip 12.3 APIs and identifiers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import ssl
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast
from urllib.parse import quote
from uuid import UUID

import httpx2
from pydantic import SecretStr

from app.config import ZulipSettings
from app.ports.communication import (
    DeliveryMessage,
    DeliveryResult,
    IMAccountBinding,
    IMLoginForm,
    IMMessage,
    IMReadContext,
    IMReadIdentity,
    IMSelection,
    InboundEvent,
    VerifiedInboundMessage,
)
from app.ports.im_context import IMContextError


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _source_conversation(raw: dict[str, Any]) -> tuple[str, Literal["private", "group"]]:
    """Use the same exact topic and full participant set for callback and rereads."""
    if raw.get("type") == "stream":
        if type(raw.get("stream_id")) is not int or not isinstance(raw.get("subject"), str):
            raise IMContextError("im_source_changed")
        return _canonical({
            "type": "stream", "stream_id": raw["stream_id"], "topic": raw["subject"],
        }), "group"
    recipients = raw.get("display_recipient")
    if (raw.get("type") != "private" or not isinstance(recipients, list)
            or not recipients or any(
                not isinstance(user, dict) or type(user.get("id")) is not int
                or user["id"] <= 0 for user in recipients
            )):
        raise IMContextError("im_source_changed")
    users = sorted({user["id"] for user in recipients})
    if raw.get("sender_id") not in users:
        raise IMContextError("im_source_changed")
    return _canonical({"type": "private", "user_ids": users}), (
        "private" if len(users) == 2 else "group"
    )


def _inbound_event(
    raw: object, *, instance: str, realm: str, target_bot_id: str,
    trigger: object,
) -> InboundEvent:
    if (not isinstance(raw, dict) or type(raw.get("id")) is not int or raw["id"] <= 0
            or type(raw.get("sender_id")) is not int or raw["sender_id"] <= 0
            or not isinstance(raw.get("content"), str)
            or raw.get("content_type") != "text/x-markdown"
            or trigger not in ("direct_message", "mention")):
        raise IMContextError("im_source_changed")
    if ((raw.get("type") == "stream" and trigger != "mention")
            or (raw.get("type") == "private" and trigger != "direct_message")):
        raise IMContextError("im_source_changed")
    conversation, kind = _source_conversation(raw)
    if raw["type"] == "private" and int(target_bot_id) not in json.loads(conversation)["user_ids"]:
        raise IMContextError("im_source_changed")
    content = raw["content"].replace("\r\n", "\n").replace("\r", "\n")
    fingerprint = hashlib.sha256(_canonical({
        "provider": "zulip", "instance": instance, "realm": realm,
        "target_bot": target_bot_id, "source_message_id": str(raw["id"]),
        "trigger": trigger, "sender": str(raw["sender_id"]),
        "conversation": conversation,
        "source_version": {name: raw.get(name) for name in (
            "timestamp", "last_edit_timestamp", "last_moved_timestamp", "edit_history",
        )},
        "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
    }).encode()).digest()
    return InboundEvent(
        provider="zulip", im_instance_id=instance, realm_id=realm,
        target_bot_id=target_bot_id, source_message_id=str(raw["id"]),
        trigger_kind=trigger,
        sender_id=str(raw["sender_id"]), conversation_ref=conversation,
        conversation_kind=kind, payload_fingerprint=fingerprint,
    )


@dataclass(slots=True)
class _Batch:
    client: httpx2.AsyncClient = field(repr=False)
    user_id: int
    human_active: bool


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
                _Batch(client, uid, me.get("is_bot") is False and me.get("is_active") is True),
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


class ZulipInboundAdapter:
    def __init__(
        self, reader: ZulipCommunicationAdapter, *,
        webhook_tokens: Mapping[str, SecretStr],
        bot_credentials: Mapping[str, tuple[str, SecretStr]],
    ) -> None:
        self._reader = reader
        self._tokens = webhook_tokens
        self._bots = bot_credentials

    async def check_identity(self, binding: IMAccountBinding) -> None:
        settings = self._reader._settings
        if (binding.status != "active" or (
            binding.provider, binding.im_instance_id, binding.realm_id,
        ) != ("zulip", settings.im_instance_id, settings.realm_id)):
            raise IMContextError("im_identity_changed")
        async with self._reader.current_identity(binding) as identity:
            if (identity.im_user_id != binding.im_user_id
                    or not cast(_Batch, identity.handle).human_active):
                raise IMContextError("im_identity_changed")

    def authenticate_callback(
        self, *, target_bot_id: str, payload: dict[str, object],
    ) -> InboundEvent:
        token = payload.get("token")
        expected = self._tokens.get(target_bot_id)
        if (expected is None or not isinstance(token, str)
                or not hmac.compare_digest(token.encode(), expected.get_secret_value().encode())):
            raise IMContextError("im_inbound_authentication_failed", 401)
        settings = self._reader._settings
        return _inbound_event(
            payload.get("message"), instance=settings.im_instance_id,
            realm=settings.realm_id, target_bot_id=target_bot_id, trigger=payload.get("trigger"),
        )

    async def reread_trigger(
        self, binding: IMAccountBinding, event: InboundEvent,
    ) -> VerifiedInboundMessage:
        settings = self._reader._settings
        if (binding.status != "active" or (
            binding.provider, binding.im_instance_id, binding.realm_id, binding.im_user_id,
        ) != (event.provider, event.im_instance_id, event.realm_id, event.sender_id)
                or (event.provider, event.im_instance_id, event.realm_id)
                != ("zulip", settings.im_instance_id, settings.realm_id)):
            raise IMContextError("im_identity_changed")
        credentials = self._bots.get(event.target_bot_id)
        if credentials is None:
            raise IMContextError("im_bot_identity_unavailable", 503)
        mid = self._reader._number(event.source_message_id)
        params = {"apply_markdown": "false", "allow_empty_topic_name": "true"}
        async with self._reader.current_identity(binding) as identity:
            if identity.im_user_id != binding.im_user_id:
                raise IMContextError("im_identity_changed")
            batch = cast(_Batch, identity.handle)
            if not batch.human_active:
                raise IMContextError("im_inbound_sender_invalid")
            result = await self._reader._request(
                batch.client, "GET", f"/api/v1/messages/{mid}", params=params,
            )
            raw = result.get("message")
            current = _inbound_event(
                raw, instance=settings.im_instance_id, realm=settings.realm_id,
                target_bot_id=event.target_bot_id, trigger=event.trigger_kind,
            )
            if current != event:
                raise IMContextError("im_source_changed")
            # The reader established visibility. The Bot read only verifies its own
            # real recipient/mention flags; it cannot substitute for that reader.
            email, api_key = credentials
            auth = httpx2.BasicAuth(email, api_key.get_secret_value())
            bot = await self._reader._request(
                batch.client, "GET", "/api/v1/users/me", auth=auth,
            )
            if (str(bot.get("user_id")) != event.target_bot_id
                    or bot.get("is_bot") is not True or bot.get("is_active") is not True):
                raise IMContextError("im_bot_identity_changed")
            bot_result = await self._reader._request(
                batch.client, "GET", f"/api/v1/messages/{mid}", auth=auth, params=params,
            )
            bot_raw = bot_result.get("message")
            target_event = _inbound_event(
                bot_raw, instance=settings.im_instance_id, realm=settings.realm_id,
                target_bot_id=event.target_bot_id, trigger=event.trigger_kind,
            )
            if target_event != event:
                raise IMContextError("im_source_changed")
            if event.conversation_kind == "group":
                flags = cast(dict[str, Any], bot_raw).get("flags")
                if not isinstance(flags, list) or "mentioned" not in flags:
                    raise IMContextError("im_inbound_target_invalid")
            return VerifiedInboundMessage(
                event,
                cast(dict[str, Any], raw)["content"].replace("\r\n", "\n").replace("\r", "\n"),
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
        bots: Mapping[str, tuple[str, SecretStr]] | None = None,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._provider = provider
        self._instance = im_instance_id
        self._realm = realm_id
        self._bot_id = sender_bot_id
        self._bots = dict(bots or {})
        self._bots[sender_bot_id] = (bot_email, bot_api_key)
        self._host_origin = host_origin.rstrip("/")

    @staticmethod
    def _number(value: str) -> int:
        if not value.isascii() or not value.isdecimal() or int(value) <= 0:
            raise ValueError("im_delivery_identity_invalid")
        return int(value)

    def _conversation(self, recipient: str, sender_bot_id: str) -> str:
        return json.dumps(
            {"type": "private", "user_ids": sorted(
                {self._number(sender_bot_id), self._number(recipient)}
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
    ) -> DeliveryMessage:
        target = self._conversation(recipient_im_user_id, self._bot_id)
        content = (
            f"EternalAI 助手，受{initiator_label}委托\n\n{note}\n\n"
            f"投递编号：{delivery_id}\n"
            f"[回到 EternalAI 核实文字]({self._host_origin}/messages?delivery={delivery_id})"
        )
        return DeliveryMessage(
            provider=self._provider,
            im_instance_id=self._instance,
            realm_id=self._realm,
            sender_bot_id=self._bot_id,
            target_conversation_ref=target,
            card_content=content,
        )

    def prepare_agent_reply(
        self, *, delivery_id: UUID, sender_bot_id: str, recipient_im_user_id: str, text: str,
    ) -> DeliveryMessage:
        return DeliveryMessage(
            provider=self._provider, im_instance_id=self._instance, realm_id=self._realm,
            sender_bot_id=sender_bot_id,
            target_conversation_ref=self._conversation(recipient_im_user_id, sender_bot_id),
            card_content=f"{text}\n\n投递编号：{delivery_id}",
        )

    def prepare_group_status(
        self, *, delivery_id: UUID, sender_bot_id: str, conversation_ref: str,
    ) -> DeliveryMessage:
        return DeliveryMessage(
            provider=self._provider, im_instance_id=self._instance, realm_id=self._realm,
            sender_bot_id=sender_bot_id, target_conversation_ref=conversation_ref,
            card_content=f"请求已接收，请查看与助手的私聊。\n投递编号：{delivery_id}",
            kind="group_status",
        )

    def _target(
        self, message: DeliveryMessage,
    ) -> tuple[httpx2.BasicAuth, dict[str, str], list[dict[str, object]]]:
        if ((message.provider, message.im_instance_id, message.realm_id)
                != (self._provider, self._instance, self._realm)
                or message.sender_bot_id not in self._bots):
            raise ValueError("im_delivery_target_changed")
        email, api_key = self._bots[message.sender_bot_id]
        auth = httpx2.BasicAuth(email, api_key.get_secret_value())
        conversation = json.loads(message.target_conversation_ref)
        if conversation["type"] == "private":
            users = conversation["user_ids"]
            bot_id = self._number(message.sender_bot_id)
            if (not isinstance(users, list) or any(type(user) is not int for user in users)
                    or bot_id not in users or len(set(users)) != len(users)
                    or len(users) < 2 or (message.kind == "private" and len(users) != 2)):
                raise ValueError("im_delivery_target_changed")
            recipients = [user for user in users if user != bot_id]
            data = {"type": "direct", "to": json.dumps(recipients)}
            narrow: list[dict[str, object]] = [{"operator": "dm", "operand": recipients}]
        elif conversation["type"] == "stream" and message.kind == "group_status":
            if (type(conversation.get("stream_id")) is not int
                    or not isinstance(conversation.get("topic"), str)):
                raise ValueError("im_delivery_target_changed")
            data = {
                "type": "stream", "to": str(conversation["stream_id"]),
                "topic": conversation["topic"],
            }
            narrow = [
                {"operator": "channel", "operand": conversation["stream_id"]},
                {"operator": "topic", "operand": conversation["topic"]},
            ]
        else:
            raise ValueError("im_delivery_target_changed")
        return auth, data, narrow

    async def _bot_identity(self, bot_id: str, auth: httpx2.BasicAuth) -> bool:
        response = await self._client.get(
            self._base_url + "/api/v1/users/me", auth=auth, follow_redirects=False
        )
        data = response.json()
        return (
            response.status_code == 200
            and isinstance(data, dict)
            and data.get("result") == "success"
            and type(data.get("user_id")) is int
            and str(data["user_id"]) == bot_id
            and data.get("is_bot") is True
            and data.get("is_active") is True
        )

    async def send_message_once(self, message: DeliveryMessage) -> DeliveryResult:
        try:
            auth, target, _ = self._target(message)
        except (ValueError, KeyError, TypeError):
            return DeliveryResult("known_rejected", None, "im_delivery_target_changed")
        try:
            if not await self._bot_identity(message.sender_bot_id, auth):
                return DeliveryResult("known_rejected", None, "im_bot_identity_changed")
        except (httpx2.RequestError, ValueError):
            # No send request was made, so this preflight failure is unambiguous.
            return DeliveryResult("known_rejected", None, "im_bot_identity_unavailable")
        try:
            response = await self._client.post(
                self._base_url + "/api/v1/messages",
                auth=auth,
                follow_redirects=False,
                data={**target, "content": message.card_content},
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

    async def reconcile_message(
        self, message: DeliveryMessage, delivery_id: UUID
    ) -> DeliveryResult:
        unknown = DeliveryResult("unknown", None, "im_delivery_unconfirmed")
        try:
            auth, _, narrow = self._target(message)
            if not await self._bot_identity(message.sender_bot_id, auth):
                return unknown
            response = await self._client.get(
                self._base_url + "/api/v1/messages",
                auth=auth,
                follow_redirects=False,
                params={
                    "anchor": "newest",
                    "num_before": 100,
                    "num_after": 0,
                    "apply_markdown": "false",
                    "allow_empty_topic_name": "true",
                    "narrow": json.dumps(narrow + [
                        {"operator": "search", "operand": str(delivery_id)},
                    ]),
                },
            )
            data = response.json()
        except (httpx2.RequestError, ValueError, KeyError, TypeError):
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
        remote = cast(dict[str, Any], raw)
        if (type(remote.get("sender_id")) is not int
                or str(remote["sender_id"]) != message.sender_bot_id):
            return unknown
        try:
            conversation, _ = _source_conversation(remote)
        except IMContextError:
            return unknown
        if conversation != message.target_conversation_ref:
            return unknown
        content = remote.get("content")
        if (remote.get("content_type") != "text/x-markdown"
                or not isinstance(content, str)
                or remote.get("last_edit_timestamp") is not None
                or remote.get("edit_history")
                or content != message.card_content
                or type(remote.get("id")) is not int or remote["id"] <= 0):
            return unknown
        return DeliveryResult("accepted", str(remote["id"]), "im_delivery_reconciled")
