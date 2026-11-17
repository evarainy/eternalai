"""U8's two-request RSA/AES login, shared by binding and browser entry."""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

import httpx2
from cryptography.hazmat.primitives import padding, serialization
from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from pydantic import SecretStr

from app.ports.credential_binding import PasswordBindingCredential
from app.ports.system_entry import SystemEntryError

_log = logging.getLogger(__name__)


class U8Login:
    def __init__(self, *, base_url: str, public_key_path: Path, timeout: float) -> None:
        self._base = base_url
        self._key_path = public_key_path
        self._timeout = timeout

    async def login(self, credential: PasswordBindingCredential) -> SecretStr:
        stage = "key"
        try:
            public_key = serialization.load_pem_public_key(self._key_path.read_bytes())
            if not isinstance(public_key, rsa.RSAPublicKey):
                raise ValueError("U8 key must be RSA")
            async with httpx2.AsyncClient(
                timeout=self._timeout, trust_env=False, follow_redirects=False,
            ) as client:
                response = await client.post(self._base + "/pub/common/getEncrySecrtKey", json={})
                response.raise_for_status()
                # U8 signs the session-key block; the browser recovers it with its public key.
                key = public_key.recover_data_from_signature(
                    base64.b64decode(response.json()["data"]), rsa_padding.PKCS1v15(), None,
                ).decode().split("_", 1)[0].encode()
                body = json.dumps({
                    "user": credential.login_id.get_secret_value(),
                    "password": base64.b64encode(
                        credential.password.get_secret_value().encode(),
                    ).decode(),
                }, ensure_ascii=False).encode()
                stage = "login"
                response = await client.post(
                    self._base + "/pub/user/login", content=_encrypt(key, body),
                    headers={"Content-Type": "application/json;charset=UTF-8"},
                )
                response.raise_for_status()
                result = json.loads(_decrypt(key, response.json()))
            if result["status"] != 200:
                # No distinct bad-password business code is documented for this deployment.
                # License expiry and server failure must not be called a password error.
                raise SystemEntryError("entry_unavailable")
            token = result["data"]["token"]
            if not isinstance(token, str) or not token:
                raise ValueError("U8 token missing")
            _log.info("system_entry target=u8 stage=login result=accepted")
            return SecretStr(token)
        except SystemEntryError:
            _log.info("system_entry target=u8 stage=login result=rejected")
            raise
        except Exception:
            _log.warning("system_entry target=u8 stage=%s result=unavailable", stage)
            raise SystemEntryError("entry_unavailable") from None


def _encrypt(key: bytes, data: bytes) -> bytes:
    padder = padding.PKCS7(128).padder()
    padded = padder.update(data) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return base64.b64encode(cipher.update(padded) + cipher.finalize())


def _decrypt(key: bytes, data: str) -> bytes:
    cipher = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    padded = cipher.update(base64.b64decode(data)) + cipher.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return unpadder.update(padded) + unpadder.finalize()
