"""U8's two-request RSA/AES login, shared by binding and browser entry."""

from __future__ import annotations

import base64
import json
import logging
from datetime import date
from pathlib import Path

import httpx2
from cryptography.hazmat.primitives import padding, serialization
from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from pydantic import SecretStr

from app.ports.credential_binding import PasswordBindingCredential
from app.ports.system_entry import SystemEntryError, U8CaptchaAnswer, U8CaptchaView

_log = logging.getLogger(__name__)


class U8Login:
    def __init__(self, *, base_url: str, public_key_path: Path, timeout: float) -> None:
        self._base = base_url
        self._key_path = public_key_path
        self._timeout = timeout

    async def get_captcha(self) -> U8CaptchaView:
        try:
            async with httpx2.AsyncClient(timeout=self._timeout, trust_env=False) as client:
                response = await client.get(self._base + "/pub/user/getGifCode")
                response.raise_for_status()
                result = response.json()
            if result["status"] != 200:
                raise ValueError("U8 captcha unavailable")
            data = result["data"]
            header, encoded = data["BAS64"].split(",", 1)
            if header.replace(" ", "") != "data:image/png;base64":
                raise ValueError("U8 captcha must be a PNG image")
            base64.b64decode(encoded, validate=True)
            return U8CaptchaView(guid=data["GUID"], image="data:image/png;base64," + encoded)
        except Exception:
            _log.warning("system_entry target=u8 stage=captcha result=unavailable")
            raise SystemEntryError("entry_unavailable") from None

    async def login(
        self, credential: PasswordBindingCredential, *, captcha: U8CaptchaAnswer | None = None,
    ) -> SecretStr:
        if captcha is None:
            raise SystemEntryError("entry_captcha_required")
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
                    "GUID": captcha.guid,
                    "AUTHCODE": captcha.code.get_secret_value(),
                    "ywrq": date.today().strftime("%Y%m%d"),
                    "loginType": self._base + "/login",
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
            # The native client accepts 300 as a warning with a completed login.
            if result["status"] not in (200, 300):
                message = (result.get("msg") or "") + (result.get("errorMsg") or "")
                if "验证码" in message:
                    raise SystemEntryError("entry_captcha_invalid")
                # No distinct bad-password business code is documented for this deployment.
                # License expiry and server failure must not be called a password error.
                raise SystemEntryError("entry_unavailable")
            token = result["data"]["token"]
            if not isinstance(token, str) or not token:
                raise ValueError("U8 token missing")
            _log.info("system_entry target=u8 stage=login result=accepted")
            return SecretStr(token)
        except SystemEntryError as exc:
            _log.info("system_entry target=u8 stage=login result=rejected code=%s", exc.code)
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
