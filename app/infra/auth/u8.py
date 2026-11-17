"""U8's two-request RSA/AES login, shared by binding and browser entry."""

from __future__ import annotations

import base64
import json
import logging
from datetime import date
from pathlib import Path
from typing import Any, NoReturn

import httpx2
from cryptography.exceptions import InvalidSignature
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
                _reject(result)
            data = result["data"]
            header, encoded = data["BAS64"].split(",", 1)
            if header.replace(" ", "") != "data:image/png;base64":
                raise ValueError("U8 captcha must be a PNG image")
            base64.b64decode(encoded, validate=True)
            return U8CaptchaView(guid=data["GUID"], image="data:image/png;base64," + encoded)
        except Exception as exc:
            failure = _failure(exc)
            _log.warning("system_entry target=u8 stage=captcha result=failed code=%s status=%s",
                         failure.code, failure.upstream_status)
            raise failure from None

    async def login(
        self, credential: PasswordBindingCredential, *, captcha: U8CaptchaAnswer | None = None,
    ) -> SecretStr:
        if captcha is None:
            raise SystemEntryError("entry_captcha_required")
        stage = "key"
        try:
            try:
                public_key = serialization.load_pem_public_key(self._key_path.read_bytes())
                if not isinstance(public_key, rsa.RSAPublicKey):
                    raise ValueError("U8 key must be RSA")
            except (OSError, ValueError, TypeError):
                raise SystemEntryError("entry_configuration_error") from None
            async with httpx2.AsyncClient(
                timeout=self._timeout, trust_env=False, follow_redirects=False,
            ) as client:
                response = await client.post(self._base + "/pub/common/getEncrySecrtKey", json={})
                response.raise_for_status()
                key_result = response.json()
                if key_result["status"] != 200:
                    _reject(key_result)
                # U8 signs the session-key block; the browser recovers it with its public key.
                key = public_key.recover_data_from_signature(
                    base64.b64decode(key_result["data"]), rsa_padding.PKCS1v15(), None,
                ).decode().split("_", 1)[0].encode()
                body = json.dumps({
                    "user": credential.login_id.get_secret_value().strip(),
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
                _reject(result)
            token = result["data"]["token"]
            if not isinstance(token, str) or not token:
                raise ValueError("U8 token missing")
            _log.info("system_entry target=u8 stage=login result=accepted")
            return SecretStr(token)
        except Exception as exc:
            failure = _failure(exc)
            _log.warning("system_entry target=u8 stage=%s result=failed code=%s status=%s",
                         stage, failure.code, failure.upstream_status)
            raise failure from None


def _reject(result: dict[str, Any]) -> NoReturn:
    code = result["status"]
    if not isinstance(code, int):
        raise ValueError("U8 business status must be numeric")
    message = " ".join(value for name in ("msg", "errorMsg", "msgDetails")
                       if isinstance(value := result.get(name), str)).casefold()
    if any(text in message for text in (
        "验证码已过期", "验证码过期", "验证码已失效", "验证码失效", "verification code expired",
        "verification code has expired", "captcha expired",
    )):
        raise SystemEntryError("entry_captcha_expired", upstream_status=code)
    if any(text in message for text in (
        "验证码错误", "验证码不正确", "验证码校验失败",
        "verification code error", "invalid captcha",
    )):
        raise SystemEntryError("entry_captcha_invalid", upstream_status=code)
    if any(text in message for text in (
        "username or password error", "用户名或密码错误", "用户或密码错误", "账号或密码错误",
    )):
        raise SystemEntryError("entry_credentials_rejected", upstream_status=code)
    raise SystemEntryError("entry_upstream_rejected", upstream_status=code)


def _failure(exc: Exception) -> SystemEntryError:
    if isinstance(exc, SystemEntryError):
        return exc
    if isinstance(exc, httpx2.TimeoutException):
        return SystemEntryError("entry_timeout")
    if isinstance(exc, httpx2.HTTPStatusError):
        return SystemEntryError(
            "entry_upstream_http_error", upstream_status=exc.response.status_code,
        )
    if isinstance(exc, (httpx2.DecodingError, httpx2.RemoteProtocolError,
                        ValueError, KeyError, TypeError, InvalidSignature)):
        return SystemEntryError("entry_invalid_response")
    if isinstance(exc, httpx2.RequestError):
        return SystemEntryError("entry_connection_failed")
    return SystemEntryError("entry_unavailable")


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
