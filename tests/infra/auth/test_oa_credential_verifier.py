from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
from datetime import UTC, datetime, timedelta
from http.cookiejar import Cookie, CookieJar
from typing import Any, cast
from urllib.parse import parse_qs, urlparse
from urllib.request import OpenerDirector, Request
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.infra.auth.crypto import identity_surrogate
from app.infra.auth.oa import (
    OAAuthenticationError,
    OACredentialVerifier,
    make_urllib_session_factory,
)
from app.infra.auth.postgresql import PostgreSQLCredentialStore
from app.ports.auth import (
    AuthenticationError,
    CredentialStorePort,
    LoginCredential,
    OASessionCredential,
)

DATABASE_URL = os.environ.get("DATABASE_URL")

if hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # type: ignore[attr-defined]


def _require_test_db() -> str:
    if not DATABASE_URL:
        raise AssertionError("DATABASE_URL must be set by the test runner environment")
    url = make_url(DATABASE_URL)
    if (url.host, url.port, url.database) != (
        "127.0.0.1",
        15432,
        "eternalai_test",
    ):
        raise AssertionError("credential verifier DB test requires the fixed test database")
    return DATABASE_URL


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
        rsa_code: object,
        expected_login_digest: bytes,
        expected_password_digest: bytes,
        oa_user_id: object,
        cookie_values: dict[str, str],
        login_succeeds: bool,
        rsa_flag: object | None,
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
        self.requested_paths: list[str] = []
        self.requested_user_ids: list[str] = []
        public_der = private_key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        self._rsa_pub = base64.b64encode(public_der).decode("ascii")

    def open(self, request: Request, timeout: float) -> FakeResponse:
        assert timeout > 0
        path = urlparse(request.full_url).path
        self.requested_paths.append(path)
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
            fields = parse_qs(
                request.data.decode("utf-8"),
                keep_blank_values=True,
                strict_parsing=True,
            )
            assert set(fields) == {
                "",
                "appid",
                "dynamicPassword",
                "isie",
                "isRememberPassword",
                "islanguid",
                "loginid",
                "logintype",
                "messages",
                "service",
                "tokenAuthKey",
                "userpassword",
                "validateCodeKey",
                "validatecode",
            }
            assert {
                key: values
                for key, values in fields.items()
                if key not in {"loginid", "userpassword"}
            } == {
                "": [""],
                "appid": [""],
                "dynamicPassword": [""],
                "isie": ["false"],
                "isRememberPassword": ["false"],
                "islanguid": ["7"],
                "logintype": ["1"],
                "messages": [""],
                "service": [""],
                "tokenAuthKey": [""],
                "validateCodeKey": [""],
                "validatecode": [""],
            }
            headers = {name.lower(): value for name, value in request.header_items()}
            assert headers["accept"] == "*/*"
            assert headers["origin"] == "https://oa.invalid"
            assert headers["referer"] == "https://oa.invalid/wui/index.html"
            assert headers["x-requested-with"] == "XMLHttpRequest"
            assert headers["content-type"] == "application/x-www-form-urlencoded"
            assert headers["user-agent"].startswith("Mozilla/5.0")
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
            parameters = parse_qs(urlparse(request.full_url).query, strict_parsing=True)
            self.requested_user_ids.append(parameters["userId"][0])
            return FakeResponse({"lastname": "Synthetic User"})
        raise AssertionError(f"Unexpected OA fixture path: {path}")

    def _decrypt(self, encrypted_value: str) -> bytes:
        suffix = self._rsa_flag if isinstance(self._rsa_flag, str) else ""
        assert encrypted_value.endswith(suffix)
        encrypted_payload = (
            encrypted_value[: -len(suffix)] if suffix else encrypted_value
        )
        return self._private_key.decrypt(
            base64.b64decode(encrypted_payload, validate=True),
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
    rsa_code: object = "synthetic-rsa-code",
    rsa_flag: object | None = "FLAG-V1",
    credential_store: CredentialStorePort | None = None,
    loginid_override: str | None = None,
    oa_user_id: object = 123,
) -> tuple[
    OACredentialVerifier,
    RecordingCredentialStore,
    list[HARFixtureOpener],
    LoginCredential,
]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    rsa_code_suffix = rsa_code if isinstance(rsa_code, str) else ""
    loginid = loginid_override or "1" * 17 + "X"
    password = "synthetic-" + "password"
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
                (loginid + rsa_code_suffix).encode("utf-8")
            ).digest(),
            expected_password_digest=hashlib.sha256(
                (password + rsa_code_suffix).encode("utf-8")
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
        credential_store=(
            credential_store if credential_store is not None else store
        ),
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
    assert stored_credential.oa_user_id.get_secret_value() == "123"
    assert set(stored_credential.cookies) == {
        "ecology_JSessionid",
        "loginidweaver",
        "loginuuids",
    }
    assert stored_credential.expires_at == datetime(
        2026, 7, 24, tzinfo=UTC
    ) + timedelta(hours=2)
    assert openers[0].check_login_calls == 1
    assert openers[0].requested_user_ids == ["123"]


def test_oa_string_userid_shape_remains_compatible() -> None:
    verifier, store, openers, credential = _fixture(
        login_succeeds=True,
        oa_user_id="123",
    )

    principal = asyncio.run(verifier.authenticate(credential))

    assert store.records[0][0] == principal.ai_user_id
    assert store.records[0][1].oa_user_id.get_secret_value() == "123"
    assert openers[0].requested_user_ids == ["123"]


def test_oa_success_does_not_require_undocumented_session_initializer_calls() -> None:
    verifier, _, openers, credential = _fixture(login_succeeds=True)

    asyncio.run(verifier.authenticate(credential))

    assert openers[0].requested_paths == [
        "/rsa/weaver.rsa.GetRsaInfo",
        "/api/hrm/login/checkLogin",
        "/api/hrm/usericon/getUserIcon",
    ]


@pytest.mark.parametrize(
    "oa_user_id",
    [True, False, None, 123.0, "", "   ", [], {}],
)
def test_oa_invalid_userid_shapes_remain_fail_closed(oa_user_id: object) -> None:
    verifier, store, openers, credential = _fixture(
        login_succeeds=True,
        oa_user_id=oa_user_id,
    )

    with pytest.raises(OAAuthenticationError, match="authentication failed") as exc_info:
        asyncio.run(verifier.authenticate(credential))

    assert exc_info.value.stage == "oa_identity_response_invalid"
    assert store.records == []
    assert openers[0].requested_user_ids == []


def test_integer_and_string_userid_share_one_principal_credential_and_mapping() -> None:
    from app.db.session import make_async_engine, make_async_session_factory
    from app.infra.identity.postgresql import PostgreSQLOAIdentityMapping

    database_url = _require_test_db()
    loginid = f"{uuid4().hex[:17]}X"
    expected_ai_user_id = identity_surrogate(loginid, key=bytes(range(32)))

    async def exercise() -> None:
        engine = make_async_engine(database_url)
        factory = make_async_session_factory(engine)
        try:
            store = PostgreSQLCredentialStore(
                session_factory=factory,
                encryption_key=bytes(range(32)),
            )
            integer_verifier, _, integer_openers, credential = _fixture(
                login_succeeds=True,
                credential_store=store,
                loginid_override=loginid,
                oa_user_id=123,
            )
            string_verifier, _, string_openers, _ = _fixture(
                login_succeeds=True,
                credential_store=store,
                loginid_override=loginid,
                oa_user_id="123",
            )

            integer_principal = await integer_verifier.authenticate(credential)
            string_principal = await string_verifier.authenticate(credential)

            assert integer_principal.ai_user_id == expected_ai_user_id
            assert string_principal.ai_user_id == expected_ai_user_id
            assert integer_openers[0].requested_user_ids == ["123"]
            assert string_openers[0].requested_user_ids == ["123"]

            stored_credential = await store.load(expected_ai_user_id)
            assert stored_credential is not None
            assert stored_credential.oa_user_id.get_secret_value() == "123"

            async with factory() as session:
                credential_row_count = (
                    await session.execute(
                        text(
                            "SELECT COUNT(*) FROM oa_session_credentials"
                            " WHERE ai_user_id = :ai_user_id"
                        ),
                        {"ai_user_id": expected_ai_user_id},
                    )
                ).scalar_one()

            mapping = PostgreSQLOAIdentityMapping(
                session_factory=factory,
                now=lambda: datetime(2026, 7, 24, tzinfo=UTC),
            )
            identity_mappings = await mapping.list_mappings(
                expected_ai_user_id,
                "oa",
            )

            assert credential_row_count == 1
            assert len(identity_mappings) == 1
            assert identity_mappings[0].binding_id == (
                f"oa-session-v1:{expected_ai_user_id}"
            )
        finally:
            async with factory() as session:
                await session.execute(
                    text(
                        "DELETE FROM oa_session_credentials"
                        " WHERE ai_user_id = :ai_user_id"
                    ),
                    {"ai_user_id": expected_ai_user_id},
                )
                await session.commit()
            await engine.dispose()

    asyncio.run(exercise())


def test_oa_rejection_is_fixed_stage_fail_closed_and_never_retries(
    caplog: pytest.LogCaptureFixture,
) -> None:
    verifier, store, openers, credential = _fixture(login_succeeds=False)

    with caplog.at_level("WARNING", logger="app.infra.auth.oa"):
        with pytest.raises(
            OAAuthenticationError,
            match="authentication failed",
        ) as exc_info:
            asyncio.run(verifier.authenticate(credential))

    assert exc_info.value.stage == "oa_credentials_rejected"
    assert exc_info.value.__context__ is None
    assert credential.loginid.get_secret_value() not in str(exc_info.value)
    assert credential.userpassword.get_secret_value() not in str(exc_info.value)
    assert "oa_authentication_failure_stage=oa_credentials_rejected" in caplog.text
    assert credential.loginid.get_secret_value() not in caplog.text
    assert credential.userpassword.get_secret_value() not in caplog.text
    assert store.records == []
    assert openers[0].check_login_calls == 1


def test_oa_rsa_transport_failure_reports_only_fixed_stage(
    caplog: pytest.LogCaptureFixture,
) -> None:
    verifier, store, _, credential = _fixture(login_succeeds=True)
    sensitive_detail = "synthetic-sensitive-transport-detail"

    class _FailingSession:
        async def get_json(
            self,
            _path: str,
            _parameters: dict[str, str],
        ) -> dict[str, object]:
            raise OSError(sensitive_detail)

        async def post_form(
            self,
            _path: str,
            _fields: dict[str, str],
        ) -> dict[str, object]:
            raise AssertionError("RSA failure must stop before login")

        def cookies(self) -> dict[str, str]:
            raise AssertionError("RSA failure must stop before cookies")

    verifier._session_factory = _FailingSession  # type: ignore[assignment]

    with caplog.at_level("WARNING", logger="app.infra.auth.oa"):
        with pytest.raises(OAAuthenticationError) as exc_info:
            asyncio.run(verifier.authenticate(credential))

    assert exc_info.value.stage == "oa_rsa_request_failed"
    assert str(exc_info.value) == "authentication failed"
    assert "oa_authentication_failure_stage=oa_rsa_request_failed" in caplog.text
    assert sensitive_detail not in caplog.text
    assert store.records == []


def test_failed_oa_login_preserves_existing_revocation_timestamp() -> None:
    from app.db.session import make_async_engine, make_async_session_factory

    database_url = _require_test_db()
    identity_hmac_key = bytes(range(32))
    loginid = f"{uuid4().hex[:17]}X"
    ai_user_id = identity_surrogate(loginid, key=identity_hmac_key)
    revoked_at = datetime(2026, 7, 23, 12, 34, 56, 123456, tzinfo=UTC)

    async def exercise() -> None:
        engine = make_async_engine(database_url)
        factory = make_async_session_factory(engine)
        try:
            store = PostgreSQLCredentialStore(
                session_factory=factory,
                encryption_key=bytes(range(32)),
            )
            await store.store(
                ai_user_id,
                OASessionCredential(
                    oa_user_id=SecretStr(f"synthetic-{uuid4().hex}"),
                    cookies={
                        "loginuuids": SecretStr(f"synthetic-{uuid4().hex}")
                    },
                    expires_at=datetime(2099, 1, 1, tzinfo=UTC),
                ),
            )
            async with factory() as session:
                await session.execute(
                    text(
                        "UPDATE oa_session_credentials"
                        " SET revoked_at = :revoked_at"
                        " WHERE ai_user_id = :ai_user_id"
                    ),
                    {"ai_user_id": ai_user_id, "revoked_at": revoked_at},
                )
                await session.commit()
                before = (
                    await session.execute(
                        text(
                            "SELECT revoked_at FROM oa_session_credentials"
                            " WHERE ai_user_id = :ai_user_id"
                        ),
                        {"ai_user_id": ai_user_id},
                    )
                ).one()

            assert before.revoked_at == revoked_at
            verifier, _, openers, credential = _fixture(
                login_succeeds=False,
                credential_store=store,
                loginid_override=loginid,
            )

            with pytest.raises(AuthenticationError, match="authentication failed"):
                await verifier.authenticate(credential)

            async with factory() as session:
                after = (
                    await session.execute(
                        text(
                            "SELECT revoked_at FROM oa_session_credentials"
                            " WHERE ai_user_id = :ai_user_id"
                        ),
                        {"ai_user_id": ai_user_id},
                    )
                ).one()

            assert after.revoked_at == revoked_at
            assert openers[0].check_login_calls == 1
        finally:
            async with factory() as session:
                await session.execute(
                    text(
                        "DELETE FROM oa_session_credentials"
                        " WHERE ai_user_id = :ai_user_id"
                    ),
                    {"ai_user_id": ai_user_id},
                )
                await session.commit()
            await engine.dispose()

    asyncio.run(exercise())


def test_oa_missing_rsa_flag_uses_empty_suffix() -> None:
    verifier, store, openers, credential = _fixture(
        login_succeeds=True,
        rsa_flag=None,
    )

    principal = asyncio.run(verifier.authenticate(credential))

    assert store.records[0][0] == principal.ai_user_id
    assert openers[0].check_login_calls == 1


def test_oa_server_supplied_non_rsa_flag_is_preserved() -> None:
    verifier, store, openers, credential = _fixture(
        login_succeeds=True,
        rsa_flag="INVALID",
    )

    principal = asyncio.run(verifier.authenticate(credential))

    assert store.records[0][0] == principal.ai_user_id
    assert openers[0].check_login_calls == 1


def test_oa_empty_rsa_code_is_allowed() -> None:
    verifier, store, openers, credential = _fixture(
        login_succeeds=True,
        rsa_code="",
    )

    principal = asyncio.run(verifier.authenticate(credential))

    assert store.records[0][0] == principal.ai_user_id
    assert openers[0].check_login_calls == 1


@pytest.mark.parametrize(
    ("field_name", "field_value", "expected_stage"),
    [
        ("rsa_code", 7, "oa_rsa_code_type_invalid"),
        ("rsa_flag", 7, "oa_rsa_flag_type_invalid"),
    ],
)
def test_oa_rsa_optional_field_wrong_type_reports_only_safe_shape(
    field_name: str,
    field_value: object,
    expected_stage: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fixture_options: dict[str, object] = {field_name: field_value}
    verifier, store, openers, credential = _fixture(
        login_succeeds=True,
        **fixture_options,
    )

    with caplog.at_level("WARNING", logger="app.infra.auth.oa"):
        with pytest.raises(OAAuthenticationError) as exc_info:
            asyncio.run(verifier.authenticate(credential))

    diagnostics = exc_info.value.diagnostics
    assert exc_info.value.stage == expected_stage
    assert diagnostics["rsa_response_field_count"] == "3"
    assert diagnostics[f"{field_name}_present"] == "true"
    assert diagnostics[f"{field_name}_type"] == "integer"
    assert f"{field_name}_character_count" not in diagnostics
    assert credential.loginid.get_secret_value() not in caplog.text
    assert credential.userpassword.get_secret_value() not in caplog.text
    assert "synthetic-rsa-code" not in caplog.text
    assert "oa_authentication_failure_diagnostic_" in caplog.text
    assert store.records == []
    assert openers[0].check_login_calls == 0


def test_oa_missing_rsa_public_key_reports_only_safe_shape(
    caplog: pytest.LogCaptureFixture,
) -> None:
    verifier, store, _, credential = _fixture(login_succeeds=True)

    class _MissingPublicKeySession:
        async def get_json(
            self,
            _path: str,
            _parameters: dict[str, str],
        ) -> dict[str, object]:
            return {"rsa_code": "", "rsa_flag": "FLAG-V1"}

        async def post_form(
            self,
            _path: str,
            _fields: dict[str, str],
        ) -> dict[str, object]:
            raise AssertionError("invalid RSA response must stop before login")

        def cookies(self) -> dict[str, str]:
            raise AssertionError("invalid RSA response must stop before cookies")

    verifier._session_factory = _MissingPublicKeySession  # type: ignore[assignment]

    with caplog.at_level("WARNING", logger="app.infra.auth.oa"):
        with pytest.raises(OAAuthenticationError) as exc_info:
            asyncio.run(verifier.authenticate(credential))

    assert exc_info.value.stage == "oa_rsa_public_key_missing_or_invalid"
    assert exc_info.value.diagnostics == {
        "rsa_code_character_count": "0",
        "rsa_code_present": "true",
        "rsa_code_type": "string",
        "rsa_flag_character_count": "7",
        "rsa_flag_present": "true",
        "rsa_flag_type": "string",
        "rsa_pub_present": "false",
        "rsa_pub_type": "missing",
        "rsa_response_field_count": "2",
    }
    assert "FLAG-V1" not in caplog.text
    assert credential.loginid.get_secret_value() not in caplog.text
    assert credential.userpassword.get_secret_value() not in caplog.text
    assert store.records == []
