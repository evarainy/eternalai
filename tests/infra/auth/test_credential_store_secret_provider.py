"""Focused tests for late, typed OA Session resolution."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

import pytest
from pydantic import SecretStr

from app.infra.auth.secret_provider import CredentialStoreSecretProvider
from app.ports.auth import OASessionCredential
from app.ports.secret_provider import (
    CredentialExpiredError,
    CredentialNotFoundError,
    CredentialStorageError,
    InvalidCredentialReferenceError,
)

AI_USER_ID = "usr_v1_" + "A" * 43
CREDENTIAL_REF = f"oa-session-v1:{AI_USER_ID}"
NOW = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)


class RecordingCredentialStore:
    def __init__(self, credential: OASessionCredential | None) -> None:
        self.credential = credential
        self.loaded_ai_user_ids: list[str] = []

    async def store(
        self,
        ai_user_id: str,
        credential: OASessionCredential,
    ) -> None:
        raise AssertionError("read provider must not write credentials")

    async def load(self, ai_user_id: str) -> OASessionCredential | None:
        self.loaded_ai_user_ids.append(ai_user_id)
        return self.credential


class ExplodingCredentialStore:
    def __init__(self, sensitive_marker: str) -> None:
        self._sensitive_marker = sensitive_marker

    def __repr__(self) -> str:
        return f"ExplodingCredentialStore({self._sensitive_marker})"

    async def store(
        self,
        ai_user_id: str,
        credential: OASessionCredential,
    ) -> None:
        raise AssertionError

    async def load(self, ai_user_id: str) -> OASessionCredential | None:
        raise RuntimeError(self._sensitive_marker)


def _credential(*, expires_at: datetime) -> OASessionCredential:
    return OASessionCredential(
        oa_user_id=SecretStr("synthetic-" + uuid4().hex),
        cookies={"synthetic_name": SecretStr("synthetic-" + uuid4().hex)},
        expires_at=expires_at,
    )


@pytest.mark.anyio
async def test_resolve_oa_session_loads_exact_namespaced_surrogate() -> None:
    credential = _credential(expires_at=NOW + timedelta(minutes=5))
    store = RecordingCredentialStore(credential)
    provider = CredentialStoreSecretProvider(
        credential_store=store,
        now=lambda: NOW,
    )

    resolved = await provider.resolve_oa_session(CREDENTIAL_REF)

    assert resolved is credential
    assert store.loaded_ai_user_ids == [AI_USER_ID]


@pytest.mark.anyio
async def test_resolve_oa_session_does_not_cache_plaintext_credential() -> None:
    store = RecordingCredentialStore(
        _credential(expires_at=NOW + timedelta(minutes=5))
    )
    provider = CredentialStoreSecretProvider(
        credential_store=store,
        now=lambda: NOW,
    )

    await provider.resolve_oa_session(CREDENTIAL_REF)
    await provider.resolve_oa_session(CREDENTIAL_REF)

    assert store.loaded_ai_user_ids == [AI_USER_ID, AI_USER_ID]
    assert set(vars(provider)) == {"_credential_store", "_now"}


@pytest.mark.anyio
@pytest.mark.parametrize(
    "credential_ref",
    (
        "",
        "oa-session-v2:usr_v1_" + "A" * 43,
        "oa-session-v1:raw-user-id",
        "oa-session-v1:usr_v1_" + "A" * 42,
        "oa-session-v1:usr_v1_" + "A" * 44,
        "oa-session-v1:usr_v1_" + "A" * 42 + ":",
    ),
)
async def test_invalid_reference_is_rejected_before_storage_lookup(
    credential_ref: str,
) -> None:
    store = RecordingCredentialStore(
        _credential(expires_at=NOW + timedelta(minutes=5))
    )
    provider = CredentialStoreSecretProvider(
        credential_store=store,
        now=lambda: NOW,
    )

    with pytest.raises(InvalidCredentialReferenceError) as exc_info:
        await provider.resolve_oa_session(credential_ref)

    assert store.loaded_ai_user_ids == []
    if credential_ref:
        assert credential_ref not in str(exc_info.value)
    assert exc_info.value.__context__ is None


@pytest.mark.anyio
async def test_non_string_reference_is_typed_fail_closed() -> None:
    store = RecordingCredentialStore(None)
    provider = CredentialStoreSecretProvider(
        credential_store=store,
        now=lambda: NOW,
    )

    with pytest.raises(InvalidCredentialReferenceError):
        await provider.resolve_oa_session(cast(Any, None))

    assert store.loaded_ai_user_ids == []


@pytest.mark.anyio
async def test_missing_credential_is_typed_unavailable() -> None:
    store = RecordingCredentialStore(None)
    provider = CredentialStoreSecretProvider(
        credential_store=store,
        now=lambda: NOW,
    )

    with pytest.raises(CredentialNotFoundError) as exc_info:
        await provider.resolve_oa_session(CREDENTIAL_REF)

    assert store.loaded_ai_user_ids == [AI_USER_ID]
    assert AI_USER_ID not in str(exc_info.value)
    assert exc_info.value.__context__ is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "expires_at",
    (
        NOW,
        NOW - timedelta(microseconds=1),
    ),
)
async def test_expired_credential_is_rechecked_after_storage_load(
    expires_at: datetime,
) -> None:
    store = RecordingCredentialStore(_credential(expires_at=expires_at))
    provider = CredentialStoreSecretProvider(
        credential_store=store,
        now=lambda: NOW,
    )

    with pytest.raises(CredentialExpiredError) as exc_info:
        await provider.resolve_oa_session(CREDENTIAL_REF)

    assert store.loaded_ai_user_ids == [AI_USER_ID]
    assert exc_info.value.__context__ is None
    assert exc_info.value.__cause__ is None
    traceback = exc_info.value.__traceback__
    provider_frame_seen = False
    while traceback is not None:
        frame = traceback.tb_frame
        if frame.f_globals.get("__name__") == "app.infra.auth.secret_provider":
            provider_frame_seen = True
            assert frame.f_locals.get("credential") is None
        traceback = traceback.tb_next
    assert provider_frame_seen


@pytest.mark.anyio
async def test_storage_exception_repr_and_logs_do_not_retain_sensitive_context(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sensitive_marker = "synthetic-" + uuid4().hex
    store = ExplodingCredentialStore(sensitive_marker)
    provider = CredentialStoreSecretProvider(
        credential_store=store,
        now=lambda: NOW,
    )
    caplog.set_level(logging.DEBUG)

    with pytest.raises(CredentialStorageError) as exc_info:
        await provider.resolve_oa_session(CREDENTIAL_REF)

    rendered = "\n".join(
        (
            repr(provider),
            repr(exc_info.value),
            str(exc_info.value),
            caplog.text,
        )
    )
    assert sensitive_marker not in rendered
    assert exc_info.value.__context__ is None
    assert exc_info.value.__cause__ is None


@pytest.mark.anyio
async def test_invalid_ttl_is_typed_storage_failure_without_context() -> None:
    naive_expiry = datetime(2026, 7, 30, 13, 0)
    store = RecordingCredentialStore(_credential(expires_at=naive_expiry))
    provider = CredentialStoreSecretProvider(
        credential_store=store,
        now=lambda: NOW,
    )

    with pytest.raises(CredentialStorageError) as exc_info:
        await provider.resolve_oa_session(CREDENTIAL_REF)

    assert exc_info.value.__context__ is None


@pytest.mark.anyio
async def test_legacy_methods_remain_redacted_and_ignore_execution_context() -> None:
    provider = CredentialStoreSecretProvider(
        credential_store=RecordingCredentialStore(None),
        now=lambda: NOW,
    )
    sensitive_marker = "synthetic-" + uuid4().hex

    resolved = await provider.resolve_secret_ref(CREDENTIAL_REF, "task", "capability")
    injected = await provider.inject_execution_secret(
        {"internal_token": sensitive_marker},
        CREDENTIAL_REF,
    )

    assert resolved == {
        "credential_ref": CREDENTIAL_REF,
        "redacted_placeholder": "<redacted>",
    }
    assert injected == {
        "credential_ref": CREDENTIAL_REF,
        "mock_secret_injected": True,
    }
    assert sensitive_marker not in repr(injected)
