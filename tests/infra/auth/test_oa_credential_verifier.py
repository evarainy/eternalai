from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from http.cookiejar import Cookie, CookieJar
from typing import Any, cast
from urllib.parse import parse_qs, urlparse
from urllib.request import OpenerDirector, Request

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.infra.auth.oa import OACredentialVerifier, make_urllib_session_factory
from app.ports.auth import (
    AuthenticationError,
    LoginCredential,
    OASessionCredential,
)


class RecordingCredentialStore:
    def __init__(self) -> None:
        self.records: list[tuple[str, OASessionCredential]] = []

    async def store(
        self,
        ai_user_id: str,
        credential: OASessionCredential,
    ) -> None:
        self.records.append((ai_user_id, credential))

    async def load(self, ai_user_id: str) -> OASessionCredential | None:
        del ai_user_id
        raise AssertionError("authentication write path must not load credentials")


class StaticRoleReader:
    async def list_roles(self, ai_user_id: str) -> tuple[str, ...]:
        assert ai_user_id.startswith("usr_v1_")
        return ("viewer", "admin", "admin")


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def getcode(self) -> int:
        return 200

    def read(self, limit: int) -> bytes:
        return self._body[:limit]

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_: Any) -> None:
        return None


class HARFixtureOpener:
    def __init__(
        self,
        *,
        cookie_jar: CookieJar,
        private_key: rsa.RSAPrivateKey,
        rsa_code: str,
        expected_login_digest: bytes,
        expected_password_digest: bytes,
        oa_user_id: str,
        cookie_values: dict[str, str],
        login_succeeds: bool,
        rsa_flag: str | None,
    ) -> None:
        self._cookie_jar = cookie_jar
        self._private_key = private_key
        self._rsa_code = rsa_code
        self._expected_login_digest = expected_login_digest
        self._expected_password_digest = expected_password_digest
        self._oa_user_id = oa_user_id
        self._cookie_values = cookie_values
        self._login_succeeds = login_succeeds
        self._rsa_flag = rsa_flag
        self.check_login_calls = 0
        public_der = private_key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        self._rsa_pub = base64.b64encode(public_der).decode("ascii")

    def open(self, request: Request, timeout: float) -> FakeResponse:
        assert timeout > 0
        path = urlparse(request.full_url).path
        if path == "/rsa/weaver.rsa.GetRsaInfo":
            payload = {
                "rsa_pub": self._rsa_pub,
                "rsa_code": self._rsa_code,
            }
            if self._rsa_flag is not None:
                payload["rsa_flag"] = self._rsa_flag
            return FakeResponse(payload)
        if path == "/api/hrm/login/checkLogin":
            self.check_login_calls += 1
            assert request.data is not None
            fields = parse_qs(request.data.decode("utf-8"), strict_parsing=True)
            login_plaintext = self._decrypt(fields["loginid"][0])
            password_plaintext = self._decrypt(fields["userpassword"][0])
            assert hmac.compare_digest(
                hashlib.sha256(login_plaintext).digest(),
                self._expected_login_digest,
            )
            assert hmac.compare_digest(
                hashlib.sha256(password_plaintext).digest(),
                self._expected_password_digest,
            )
            if not self._login_succeeds:
                return FakeResponse({"msgcode": "16", "loginstatus": "false"})
            for name, value in self._cookie_values.items():
                self._cookie_jar.set_cookie(_cookie(name, value))
            return FakeResponse(
                {
                    "msgcode": "0",
                    "loginstatus": "true",
                    "userid": self._oa_user_id,
                }
            )
        if path == "/api/hrm/usericon/getUserIcon":
            return FakeResponse({"lastname": "Synthetic User"})
        raise AssertionError(f"Unexpected OA fixture path: {path}")

    def _decrypt(self, encrypted_value: str) -> bytes:
        assert encrypted_value.endswith("RSA")
        return self._private_key.decrypt(
            base64.b64decode(encrypted_value[:-3], validate=True),
            padding.PKCS1v15(),
        )


def _cookie(name: str, value: str) -> Cookie:
    return Cookie(
        version=0,
        name=name,
        value=value,
        port=None,
        port_specified=False,
        domain="oa.invalid",
        domain_specified=True,
        domain_initial_dot=False,
        path="/",
        path_specified=True,
        secure=True,
        expires=None,
        discard=True,
        comment=None,
        comment_url=None,
        rest={},
        rfc2109=False,
    )


def _fixture(
    *,
    login_succeeds: bool,
    rsa_flag: str | None = "RSA",
) -> tuple[
    OACredentialVerifier,
    RecordingCredentialStore,
    list[HARFixtureOpener],
    LoginCredential,
]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    rsa_code = "synthetic-rsa-code"
    loginid = "1" * 17 + "X"
    password = "synthetic-" + "password"
    oa_user_id = "synthetic-oa-user"
    cookie_values = {
        "ecology_JSessionid": "synthetic-cookie-a",
        "loginidweaver": "synthetic-cookie-b",
        "loginuuids": "synthetic-cookie-c",
    }
    openers: list[HARFixtureOpener] = []

    def opener_factory(cookie_jar: CookieJar) -> OpenerDirector:
        opener = HARFixtureOpener(
            cookie_jar=cookie_jar,
            private_key=private_key,
            rsa_code=rsa_code,
            expected_login_digest=hashlib.sha256(
                (loginid + rsa_code).encode("utf-8")
            ).digest(),
            expected_password_digest=hashlib.sha256(
                (password + rsa_code).encode("utf-8")
            ).digest(),
            oa_user_id=oa_user_id,
            cookie_values=cookie_values,
            login_succeeds=login_succeeds,
            rsa_flag=rsa_flag,
        )
        openers.append(opener)
        return cast(OpenerDirector, opener)

    store = RecordingCredentialStore()
    verifier = OACredentialVerifier(
        session_factory=make_urllib_session_factory(
            base_url="https://oa.invalid",
            timeout_seconds=3,
            opener_factory=opener_factory,
        ),
        credential_store=store,
        role_reader=StaticRoleReader(),
        identity_hmac_key=bytes(range(32)),
        credential_ttl_seconds=7200,
        clock=lambda: datetime(2026, 7, 24, tzinfo=UTC),
    )
    credential = LoginCredential(
        loginid=loginid,
        userpassword=password,
    )
    return verifier, store, openers, credential


def test_oa_fixture_proves_rsa_login_principal_and_encrypted_store_handoff() -> None:
    verifier, store, openers, credential = _fixture(login_succeeds=True)

    principal = asyncio.run(verifier.authenticate(credential))

    assert principal.ai_user_id.startswith("usr_v1_")
    assert principal.roles == ("admin", "viewer")
    assert principal.display_name == "Synthetic User"
    assert credential.loginid.get_secret_value() not in repr(principal)
    assert len(store.records) == 1
    stored_user_id, stored_credential = store.records[0]
    assert stored_user_id == principal.ai_user_id
    assert set(stored_credential.cookies) == {
        "ecology_JSessionid",
        "loginidweaver",
        "loginuuids",
    }
    assert stored_credential.expires_at == datetime(
        2026, 7, 24, tzinfo=UTC
    ) + timedelta(hours=2)
    assert openers[0].check_login_calls == 1


def test_oa_rejection_is_generic_fail_closed_and_never_retries() -> None:
    verifier, store, openers, credential = _fixture(login_succeeds=False)

    with pytest.raises(AuthenticationError, match="authentication failed") as exc_info:
        asyncio.run(verifier.authenticate(credential))

    assert exc_info.value.__context__ is None
    assert credential.loginid.get_secret_value() not in str(exc_info.value)
    assert credential.userpassword.get_secret_value() not in str(exc_info.value)
    assert store.records == []
    assert openers[0].check_login_calls == 1


def test_oa_rsa_flag_defaults_to_rsa_when_fixture_omits_it() -> None:
    verifier, store, openers, credential = _fixture(
        login_succeeds=True,
        rsa_flag=None,
    )

    principal = asyncio.run(verifier.authenticate(credential))

    assert store.records[0][0] == principal.ai_user_id
    assert openers[0].check_login_calls == 1


def test_oa_non_rsa_flag_is_generic_fail_closed() -> None:
    verifier, store, openers, credential = _fixture(
        login_succeeds=True,
        rsa_flag="INVALID",
    )

    with pytest.raises(AuthenticationError, match="authentication failed"):
        asyncio.run(verifier.authenticate(credential))

    assert store.records == []
    assert openers[0].check_login_calls == 0
