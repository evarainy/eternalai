from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.ports.auth import (
    LoginCredential,
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
