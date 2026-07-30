from __future__ import annotations

import inspect
import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import get_type_hints
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError

from app.ports.auth import (
    CredentialStoreError,
    CredentialStorePort,
    LoginCredential,
    OASessionCredential,
    Principal,
    PrincipalOrgContext,
)


def test_login_credential_repr_and_serialization_mask_secrets() -> None:
    loginid = "synthetic-" + "loginid"
    password = "synthetic-" + "password"
    credential = LoginCredential(loginid=loginid, userpassword=password)

    assert loginid not in repr(credential)
    assert password not in repr(credential)
    assert loginid not in credential.model_dump_json()
    assert password not in credential.model_dump_json()


def test_principal_contract_forbids_raw_extra_identity_fields() -> None:
    with pytest.raises(ValidationError):
        Principal.model_validate(
            {
                "ai_user_id": "usr_v1_synthetic",
                "display_name": "Synthetic User",
                "roles": [],
                "org_ctx": PrincipalOrgContext().model_dump(),
                "loginid": "forbidden",
            }
        )


def test_credential_store_load_contract_returns_typed_optional_credential() -> None:
    signature = inspect.signature(CredentialStorePort.load)
    hints = get_type_hints(CredentialStorePort.load)

    assert list(signature.parameters) == ["self", "ai_user_id"]
    assert hints["ai_user_id"] is str
    assert hints["return"] == OASessionCredential | None
    assert inspect.iscoroutinefunction(CredentialStorePort.load)


def test_oa_session_credential_masks_repr_dump_json_and_asdict_paths() -> None:
    oa_user_id = "synthetic-" + uuid4().hex
    cookie_value = "synthetic-" + uuid4().hex
    credential = OASessionCredential(
        oa_user_id=SecretStr(oa_user_id),
        cookies={"synthetic_name": SecretStr(cookie_value)},
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )

    rendered = "\n".join(
        (
            repr(credential),
            credential.model_dump_json(),
            json.dumps(credential.model_dump(), default=str),
        )
    )
    assert oa_user_id not in rendered
    assert cookie_value not in rendered

    with pytest.raises(TypeError) as exc_info:
        asdict(credential)  # type: ignore[arg-type]
    assert oa_user_id not in str(exc_info.value)
    assert cookie_value not in str(exc_info.value)


def test_credential_store_error_has_no_sensitive_context_by_default() -> None:
    error = CredentialStoreError("OA session credential cannot be loaded")

    assert error.__context__ is None
    assert error.__cause__ is None
