"""Authenticated self-service password binding endpoints."""

from __future__ import annotations

import json
from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.v1.auth import PrincipalDependency
from app.ports.auth import LoginCredential, Principal
from app.ports.credential_binding import (
    CredentialBindingStorePort,
    CredentialBindingVerifierPort,
    CredentialBindingView,
    CredentialTargetSystem,
    PasswordBindingCredential,
)

_MAX_BINDING_BODY_BYTES = 16_384
_BINDING_REQUEST_BODY = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": PasswordBindingCredential.model_json_schema(),
            }
        },
    }
}


class CredentialBindingService:
    def __init__(
        self,
        *,
        store: CredentialBindingStorePort,
        verifier: CredentialBindingVerifierPort,
    ) -> None:
        self._store = store
        self._verifier = verifier

    async def get(
        self,
        principal: Principal,
        target_system: CredentialTargetSystem,
    ) -> CredentialBindingView:
        try:
            return await self._store.get_password_binding(
                principal.ai_user_id,
                target_system,
            )
        except Exception:
            _raise_binding_unavailable()

    async def bind(
        self,
        principal: Principal,
        target_system: CredentialTargetSystem,
        credential: PasswordBindingCredential,
    ) -> CredentialBindingView:
        try:
            if target_system == "oa":
                verified = await self._verifier.verify_for_binding(
                    LoginCredential(
                        loginid=credential.login_id,
                        userpassword=credential.password,
                    )
                )
                if verified.ai_user_id != principal.ai_user_id:
                    _raise_binding_failed()
            return await self._store.bind_password(
                principal.ai_user_id,
                target_system,
                credential,
            )
        except HTTPException:
            raise
        except Exception:
            _raise_binding_failed()

    async def unbind(
        self,
        principal: Principal,
        target_system: CredentialTargetSystem,
    ) -> CredentialBindingView:
        try:
            return await self._store.unbind_password(
                principal.ai_user_id,
                target_system,
            )
        except Exception:
            _raise_binding_unavailable()


def make_router(
    service: CredentialBindingService | None,
    require_principal: PrincipalDependency,
) -> APIRouter:
    router = APIRouter()

    def configured() -> CredentialBindingService:
        if service is None:
            _raise_binding_unavailable()
        return service

    @router.get("/{target_system}", response_model=CredentialBindingView)
    async def get_binding(
        target_system: CredentialTargetSystem,
        principal: Principal = Depends(require_principal),
    ) -> CredentialBindingView:
        return await configured().get(principal, target_system)

    @router.put(
        "/{target_system}",
        response_model=CredentialBindingView,
        openapi_extra=_BINDING_REQUEST_BODY,
    )
    async def bind_password(
        target_system: CredentialTargetSystem,
        request: Request,
        principal: Principal = Depends(require_principal),
    ) -> CredentialBindingView:
        credential = await _parse_binding_credential(request)
        if credential is None:
            _raise_binding_failed()
        return await configured().bind(principal, target_system, credential)

    @router.delete("/{target_system}", response_model=CredentialBindingView)
    async def unbind_password(
        target_system: CredentialTargetSystem,
        principal: Principal = Depends(require_principal),
    ) -> CredentialBindingView:
        return await configured().unbind(principal, target_system)

    return router


async def _parse_binding_credential(
    request: Request,
) -> PasswordBindingCredential | None:
    declared_length = request.headers.get("content-length")
    if declared_length is not None:
        if (
            not declared_length.isascii()
            or not declared_length.isdigit()
            or int(declared_length) > _MAX_BINDING_BODY_BYTES
        ):
            return None
    try:
        raw_body = await request.body()
        if len(raw_body) > _MAX_BINDING_BODY_BYTES:
            return None
        return PasswordBindingCredential.model_validate(json.loads(raw_body))
    except Exception:
        return None


def _raise_binding_failed() -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={
            "code": "credential_binding_failed",
            "message": "Credential binding failed.",
        },
    )


def _raise_binding_unavailable() -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": "credential_binding_unavailable",
            "message": "Credential binding is unavailable.",
        },
    )


__all__ = ("CredentialBindingService", "make_router")
