"""Authenticated self-service password binding endpoints."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import NoReturn, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.api.v1.auth import PrincipalDependency
from app.ports.auth import LoginCredential, Principal, authenticated_session
from app.ports.credential_binding import (
    BrowserBindingActivationRequest,
    BrowserBindingActivationView,
    BrowserBindingStatusView,
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
_BROWSER_ACTIVATION_BODY = {
    "requestBody": {
        "required": True,
        "content": {"application/json": {
            "schema": BrowserBindingActivationRequest.model_json_schema(),
        }},
    }
}
BindingRequest = TypeVar("BindingRequest", bound=BaseModel)


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
                principal.ai_user_id, target_system, tenant_id=principal.org_ctx.tenant_id
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
            async with self._store.poll_lock(
                principal.ai_user_id, target_system, tenant_id=principal.org_ctx.tenant_id
            ) as acquired:
                if not acquired:
                    _raise_binding_failed()
                snapshot = await self._store.snapshot(
                    principal.ai_user_id, target_system, tenant_id=principal.org_ctx.tenant_id
                )
                stamp = await self._store.claim_write(snapshot)
                if target_system == "oa":
                    verification = await self._verifier.verify_for_binding(
                        LoginCredential(
                            loginid=credential.login_id,
                            userpassword=credential.password,
                        )
                    )
                    verified = verification.principal
                    if (verified.org_ctx.tenant_id, verified.ai_user_id) != (
                        principal.org_ctx.tenant_id,
                        principal.ai_user_id,
                    ):
                        _raise_binding_failed()
                return await self._store.bind_password(
                    principal.ai_user_id,
                    target_system,
                    credential,
                    tenant_id=principal.org_ctx.tenant_id,
                    expected_write=stamp,
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
                principal.ai_user_id, target_system, tenant_id=principal.org_ctx.tenant_id
            )
        except Exception:
            _raise_binding_unavailable()

    async def activate_browser_binding(
        self, principal: Principal, request: BrowserBindingActivationRequest,
    ) -> BrowserBindingActivationView:
        """First verification uses this explicit request, never a Vault/poll reader."""
        try:
            captured = authenticated_session.get()
            if (captured is None or captured.principal != principal
                    or captured.expires_at <= datetime.now(UTC)):
                _raise_binding_failed()
            async with self._store.poll_lock(
                principal.ai_user_id, "oa", tenant_id=principal.org_ctx.tenant_id,
            ) as acquired:
                if not acquired:
                    _raise_binding_failed()
                snapshot = await self._store.browser_activation_snapshot(
                    principal, request.binding_id, request.binding_revision,
                )
                if (snapshot.absent or snapshot.tenant_id != principal.org_ctx.tenant_id
                        or snapshot.ai_user_id != principal.ai_user_id
                        or snapshot.target_system != "oa"
                        or snapshot.binding_id != request.binding_id
                        or snapshot.binding_revision != request.binding_revision):
                    _raise_binding_failed()
                stamp = await self._store.claim_write(snapshot)
                verification = await self._verifier.verify_for_binding(LoginCredential(
                    loginid=request.login_id, userpassword=request.password,
                ))
                verified = verification.principal
                subject = verified.org_ctx.directory_user_id
                if ((verified.org_ctx.tenant_id, verified.ai_user_id) != (
                        principal.org_ctx.tenant_id, principal.ai_user_id)
                        or type(subject) is not str or not subject.strip()
                        or subject != subject.strip() or len(subject) > 256
                        or (principal.org_ctx.directory_user_id is not None
                            and subject != principal.org_ctx.directory_user_id)
                        or authenticated_session.get() != captured
                        or captured.expires_at <= datetime.now(UTC)):
                    _raise_binding_failed()
                return await self._store.activate_browser_binding(
                    PasswordBindingCredential(login_id=request.login_id, password=request.password),
                    verified=verified, session_credential=verification.session_credential,
                    captured_session=captured, expected_write=stamp,
                )
        except HTTPException:
            raise
        except Exception:
            _raise_binding_failed()

    async def browser_binding_status(self, principal: Principal) -> BrowserBindingStatusView:
        try:
            return await self._store.get_browser_binding_status(principal)
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

    @router.post(
        "/oa/browser-activation", response_model=BrowserBindingActivationView,
        openapi_extra=_BROWSER_ACTIVATION_BODY,
    )
    async def activate_browser_binding(
        request: Request, principal: Principal = Depends(require_principal),
    ) -> BrowserBindingActivationView:
        # The production host supplies the same authenticated, CSRF-protected
        # dependency used by ordinary credential-binding mutations.
        activation = await _parse_binding_request(request, BrowserBindingActivationRequest)
        if activation is None:
            _raise_binding_failed()
        return await configured().activate_browser_binding(principal, activation)

    @router.get("/oa/browser-activation", response_model=BrowserBindingStatusView)
    async def browser_binding_status(
        principal: Principal = Depends(require_principal),
    ) -> BrowserBindingStatusView:
        return await configured().browser_binding_status(principal)

    return router


async def _parse_binding_credential(
    request: Request,
) -> PasswordBindingCredential | None:
    return await _parse_binding_request(request, PasswordBindingCredential)


async def _parse_binding_request(
    request: Request, model: type[BindingRequest],
) -> BindingRequest | None:
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
        return model.model_validate(json.loads(raw_body))
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
