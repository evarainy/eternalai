"""OA credential verification over an isolated standard-library HTTP session."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from http.cookiejar import CookieJar
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypeAlias
from urllib.parse import urlencode
from urllib.request import (
    HTTPCookieProcessor,
    OpenerDirector,
    Request,
    build_opener,
)

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from pydantic import SecretStr

from app.infra.auth.crypto import ensure_json_object, identity_surrogate, require_hmac_key
from app.ports.auth import (
    AuthenticationError,
    AuthenticationPort,
    CredentialStorePort,
    LoginCredential,
    OASessionCredential,
    Principal,
    PrincipalOrgContext,
)

_REQUIRED_OA_COOKIES = frozenset(
    {
        "ecology_JSessionid",
        "loginidweaver",
        "loginuuids",
    }
)
_MAX_RESPONSE_BYTES = 1_048_576
_AUTH_FAILURE_LOG_PREFIX = "oa_authentication_failure_stage="

OAAuthenticationFailureStage: TypeAlias = Literal[
    "oa_session_setup_failed",
    "oa_rsa_request_failed",
    "oa_rsa_response_invalid",
    "oa_credential_encryption_failed",
    "oa_login_request_failed",
    "oa_credentials_rejected",
    "oa_identity_response_invalid",
    "oa_required_cookies_missing",
    "oa_user_info_request_failed",
    "oa_user_info_response_invalid",
    "local_identity_derivation_failed",
    "local_role_lookup_failed",
    "local_credential_store_failed",
    "local_principal_build_failed",
]


class OAAuthenticationError(AuthenticationError):
    """Generic authentication failure carrying only a fixed, value-free stage."""

    def __init__(self, stage: OAAuthenticationFailureStage) -> None:
        super().__init__("authentication failed")
        self.stage = stage


class OAHttpSession(Protocol):
    async def get_json(
        self,
        path: str,
        parameters: dict[str, str],
    ) -> dict[str, Any]: ...

    async def post_form(
        self,
        path: str,
        fields: dict[str, str],
    ) -> dict[str, Any]: ...

    def cookies(self) -> dict[str, str]: ...


class PrincipalRoleReader(Protocol):
    async def list_roles(self, ai_user_id: str) -> tuple[str, ...]: ...


class UrllibOASession:
    """Per-login urllib session with an isolated CookieJar."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        opener_factory: Callable[[CookieJar], OpenerDirector] | None = None,
    ) -> None:
        normalized_base_url = base_url.rstrip("/")
        if not normalized_base_url.startswith(("http://", "https://")):
            raise ValueError("OA base URL must use HTTP or HTTPS")
        if timeout_seconds <= 0:
            raise ValueError("OA HTTP timeout must be positive")
        self._base_url = normalized_base_url
        self._timeout_seconds = timeout_seconds
        self._cookie_jar = CookieJar()
        self._common_headers = {
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Origin": normalized_base_url,
            "Referer": f"{normalized_base_url}/wui/index.html",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 6.1; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/86.0.4240.111 Safari/537.36"
            ),
            "X-Requested-With": "XMLHttpRequest",
        }
        factory = opener_factory or _default_opener
        self._opener = factory(self._cookie_jar)

    async def get_json(
        self,
        path: str,
        parameters: dict[str, str],
    ) -> dict[str, Any]:
        url = f"{self._base_url}{path}?{urlencode(parameters)}"
        request = Request(
            url,
            headers=self._common_headers,
            method="GET",
        )
        return await asyncio.to_thread(self._open_json, request)

    async def post_form(
        self,
        path: str,
        fields: dict[str, str],
    ) -> dict[str, Any]:
        request = Request(
            f"{self._base_url}{path}",
            data=urlencode(fields).encode("utf-8"),
            headers={
                **self._common_headers,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        return await asyncio.to_thread(self._open_json, request)

    def cookies(self) -> dict[str, str]:
        return {
            cookie.name: cookie.value
            for cookie in self._cookie_jar
            if cookie.value is not None
        }

    def _open_json(self, request: Request) -> dict[str, Any]:
        with self._opener.open(request, timeout=self._timeout_seconds) as response:
            status_code = int(response.getcode())
            if status_code < 200 or status_code >= 300:
                raise ValueError("OA response status is not successful")
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise ValueError("OA response exceeds the size limit")
        return ensure_json_object(json.loads(raw.decode("utf-8")))


class OACredentialVerifier:
    """Authenticate once against OA, then return only server-controlled identity."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], OAHttpSession],
        credential_store: CredentialStorePort,
        role_reader: PrincipalRoleReader,
        identity_hmac_key: bytes,
        credential_ttl_seconds: int,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._session_factory = session_factory
        self._credential_store = credential_store
        self._role_reader = role_reader
        self._identity_hmac_key = require_hmac_key(
            identity_hmac_key,
            purpose="identity surrogate",
        )
        if credential_ttl_seconds <= 0:
            raise ValueError("OA credential TTL must be positive")
        self._credential_ttl_seconds = credential_ttl_seconds
        self._clock = clock

    async def authenticate(self, credential: LoginCredential) -> Principal:
        return await self._authenticate_once(credential)

    async def _authenticate_once(
        self,
        credential: LoginCredential,
    ) -> Principal:
        failure_stage: OAAuthenticationFailureStage = "oa_session_setup_failed"
        try:
            session = self._session_factory()
            now = self._clock()
            failure_stage = "oa_rsa_request_failed"
            rsa_info = await session.get_json(
                "/rsa/weaver.rsa.GetRsaInfo",
                {"ts": _timestamp_millis(now)},
            )
            failure_stage = "oa_rsa_response_invalid"
            rsa_pub = _required_string(rsa_info, "rsa_pub")
            rsa_code = _required_string(rsa_info, "rsa_code")
            rsa_flag = rsa_info.get("rsa_flag", "RSA")
            if rsa_flag != "RSA":
                raise ValueError("OA RSA flag is invalid")

            loginid = credential.loginid.get_secret_value()
            password = credential.userpassword.get_secret_value()
            failure_stage = "oa_credential_encryption_failed"
            encrypted_loginid = _encrypt_oa_value(
                loginid,
                rsa_pub,
                rsa_code,
                rsa_flag,
            )
            encrypted_password = _encrypt_oa_value(
                password,
                rsa_pub,
                rsa_code,
                rsa_flag,
            )
            failure_stage = "oa_login_request_failed"
            login_result = await session.post_form(
                "/api/hrm/login/checkLogin",
                {
                    "islanguid": "7",
                    "loginid": encrypted_loginid,
                    "userpassword": encrypted_password,
                    "dynamicPassword": "",
                    "tokenAuthKey": "",
                    "validatecode": "",
                    "validateCodeKey": "",
                    "logintype": "1",
                    "messages": "",
                    "isie": "false",
                    "appid": "",
                    "service": "",
                    "isRememberPassword": "false",
                    "": "",
                },
            )
            if str(login_result.get("msgcode", "")) != "0" or not _is_true(
                login_result.get("loginstatus")
            ):
                failure_stage = "oa_credentials_rejected"
                raise ValueError("OA rejected the credential")
            failure_stage = "oa_identity_response_invalid"
            oa_user_id = _required_oa_user_id(login_result)
            failure_stage = "oa_required_cookies_missing"
            cookies = session.cookies()
            if not _REQUIRED_OA_COOKIES.issubset(cookies):
                raise ValueError("OA response is missing required cookies")

            failure_stage = "oa_user_info_request_failed"
            user_info = await session.get_json(
                "/api/hrm/usericon/getUserIcon",
                {
                    "userId": oa_user_id,
                    "__random__": _timestamp_millis(now),
                },
            )
            failure_stage = "oa_user_info_response_invalid"
            display_name = _optional_string(user_info, "lastname") or _optional_string(
                user_info,
                "shortname",
            )
            if display_name is None:
                raise ValueError("OA response is missing the display name")

            failure_stage = "local_identity_derivation_failed"
            ai_user_id = identity_surrogate(loginid, key=self._identity_hmac_key)
            failure_stage = "local_role_lookup_failed"
            roles = tuple(sorted(set(await self._role_reader.list_roles(ai_user_id))))
            failure_stage = "local_credential_store_failed"
            await self._credential_store.store(
                ai_user_id,
                OASessionCredential(
                    oa_user_id=SecretStr(oa_user_id),
                    cookies={
                        name: SecretStr(value)
                        for name, value in sorted(cookies.items())
                    },
                    expires_at=now + timedelta(seconds=self._credential_ttl_seconds),
                ),
            )
            failure_stage = "local_principal_build_failed"
            return Principal(
                ai_user_id=ai_user_id,
                display_name=display_name,
                roles=roles,
                org_ctx=PrincipalOrgContext(),
            )
        except Exception:
            logging.getLogger(__name__).warning(
                "%s%s",
                _AUTH_FAILURE_LOG_PREFIX,
                failure_stage,
            )
        raise OAAuthenticationError(failure_stage)


def make_urllib_session_factory(
    *,
    base_url: str,
    timeout_seconds: float,
    opener_factory: Callable[[CookieJar], OpenerDirector] | None = None,
) -> Callable[[], OAHttpSession]:
    """Create isolated OA HTTP sessions without introducing an HTTP dependency."""

    def factory() -> OAHttpSession:
        return UrllibOASession(
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            opener_factory=opener_factory,
        )

    return factory


def _default_opener(cookie_jar: CookieJar) -> OpenerDirector:
    return build_opener(HTTPCookieProcessor(cookie_jar))


def _encrypt_oa_value(
    plaintext: str,
    rsa_pub: str,
    rsa_code: str,
    rsa_flag: str,
) -> str:
    public_key = serialization.load_der_public_key(
        base64.b64decode(rsa_pub, validate=True)
    )
    if not isinstance(public_key, rsa.RSAPublicKey):
        raise ValueError("OA public key is not RSA")
    encrypted = public_key.encrypt(
        (plaintext + rsa_code).encode("utf-8"),
        padding.PKCS1v15(),
    )
    return base64.b64encode(encrypted).decode("ascii") + rsa_flag


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = _optional_string(payload, key)
    if value is None:
        raise ValueError("OA response is missing a required field")
    return value


def _required_oa_user_id(payload: dict[str, Any]) -> str:
    value = payload.get("userid")
    if isinstance(value, bool):
        raise ValueError("OA response userid is invalid")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ValueError("OA response userid is invalid")


def _optional_string(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _is_true(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.lower() == "true")


def _timestamp_millis(value: datetime) -> str:
    return str(int(value.timestamp() * 1000))


if TYPE_CHECKING:

    def _authentication_protocol_check(
        verifier: OACredentialVerifier,
    ) -> AuthenticationPort:
        return verifier


__all__ = (
    "OAAuthenticationError",
    "OAAuthenticationFailureStage",
    "OAHttpSession",
    "OACredentialVerifier",
    "PrincipalRoleReader",
    "UrllibOASession",
    "make_urllib_session_factory",
)
