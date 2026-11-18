"""Account-only browser SSO for the authenticated user's corresponding OA account."""

from __future__ import annotations

import base64
import hmac
import json
import logging
import secrets
import time
from hashlib import sha256
from urllib.parse import urlencode

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.infra.auth.crypto import exact_oa_login_proof
from app.ports.auth import Principal
from app.ports.system_entry import SystemEntryError

_JWT_TTL_SECONDS = 60
_log = logging.getLogger(__name__)


def _b64url(value: bytes) -> bytes:
    return base64.urlsafe_b64encode(value).rstrip(b"=")


class ZhywEntry:
    def __init__(
        self, *, redis: Redis, base_url: str, signing_key: bytes,
        identity_key: bytes, encryption_key: bytes,
    ) -> None:
        self._redis = redis
        self._base_url = base_url
        self._signing_key = signing_key
        self._identity_key = identity_key
        key = hmac.digest(encryption_key, b"eternalai-zhyw-binding-v1", "sha256")
        self._cipher = AESGCM(key)

    @staticmethod
    def _binding_key(principal: Principal) -> bytes:
        owner = json.dumps([
            principal.org_ctx.tenant_id, principal.ai_user_id, principal.org_ctx.directory_user_id,
        ]).encode()
        return b"eternalai:system-entry:zhyw:v1:" + sha256(owner).hexdigest().encode()

    def _require_owner(self, principal: Principal, account: str) -> None:
        proof = principal.org_ctx.oa_login_id_proof
        if proof is None:
            raise SystemEntryError("entry_oa_login_required")
        if not hmac.compare_digest(proof, exact_oa_login_proof(account, key=self._identity_key)):
            raise SystemEntryError("entry_account_mismatch")

    async def is_bound(self, principal: Principal) -> bool:
        try:
            return bool(await self._redis.exists(self._binding_key(principal)))
        except RedisError:
            _log.warning("system_entry target=zhyw stage=binding_read result=unavailable")
            raise SystemEntryError("entry_unavailable") from None

    async def bind(self, principal: Principal, login_id: str) -> None:
        account = login_id.strip()
        if not account:
            raise SystemEntryError("entry_account_required")
        self._require_owner(principal, account)
        key = self._binding_key(principal)
        nonce = secrets.token_bytes(12)
        try:
            await self._redis.set(key, nonce + self._cipher.encrypt(nonce, account.encode(), key))
        except RedisError:
            _log.warning("system_entry target=zhyw stage=binding_save result=unavailable")
            raise SystemEntryError("entry_unavailable") from None

    async def enter(self, principal: Principal) -> str:
        key = self._binding_key(principal)
        binding = await self._redis.get(key)
        if binding is None:
            raise SystemEntryError("entry_zhyw_not_bound")
        account = self._cipher.decrypt(binding[:12], binding[12:], key).decode()
        self._require_owner(principal, account)
        claims = json.dumps({
            "LoginId": account, "iss": "OA", "aud": "Zhyw",
            "exp": int(time.time()) + _JWT_TTL_SECONDS,
        }).encode()
        signed = _b64url(b'{"typ":"JWT","alg":"HS256"}') + b"." + _b64url(claims)
        signature = _b64url(hmac.digest(self._signing_key, signed, "sha256"))
        token = (signed + b"." + signature).decode("ascii")
        return self._base_url + "/login?" + urlencode({"arg0": token})
