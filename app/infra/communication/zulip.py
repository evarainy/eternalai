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

import httpx2

from app.config import ZulipSettings
from app.ports.communication import (
    IMAccountBinding,
    IMLoginForm,
    IMMessage,
    IMReadContext,
    IMReadIdentity,
    IMSelection,
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
