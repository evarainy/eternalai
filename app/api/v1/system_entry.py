"""Authenticated, CSRF-protected browser entry; no credential-bearing cache."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Response

from app.api.v1.auth import PrincipalDependency
from app.ports.auth import Principal
from app.ports.system_entry import SystemEntryError, SystemEntryTarget, SystemEntryView
from app.system_entry.service import SystemEntryService

_log = logging.getLogger(__name__)
_MESSAGES = {
    "entry_not_configured": "免登入口未配置。",
    "entry_not_bound": "请先在账号设置中绑定账号和密码。",
    "entry_credentials_rejected": "登录未通过，请核对所绑定的账号和密码。",
    "entry_unavailable": "系统暂时无法连接，请稍后再试。",
}
_STATUS = {
    "entry_not_configured": 503, "entry_not_bound": 409,
    "entry_credentials_rejected": 400, "entry_unavailable": 502,
}
_NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


def make_router(
    service: SystemEntryService | None, require_principal: PrincipalDependency,
) -> APIRouter:
    router = APIRouter()

    @router.get("")
    async def availability(
        response: Response, principal: Principal = Depends(require_principal),
    ) -> dict[str, bool]:
        response.headers.update(_NO_STORE)
        return service.availability() if service is not None else {"oa": False, "u8": False}

    @router.post("/{target}", response_model=SystemEntryView)
    async def enter(
        target: SystemEntryTarget, response: Response,
        principal: Principal = Depends(require_principal),
    ) -> SystemEntryView:
        response.headers.update(_NO_STORE)
        try:
            if service is None:
                raise SystemEntryError("entry_not_configured")
            return await service.enter(principal, target)
        except SystemEntryError as exc:
            raise HTTPException(
                _STATUS[exc.code],
                detail={"code": exc.code, "message": _MESSAGES[exc.code]},
                headers=_NO_STORE,
            ) from None
        except Exception:
            # External login/storage boundary: never serialize upstream URLs or payloads.
            _log.warning("system_entry target=%s stage=entry result=unavailable", target)
            raise HTTPException(
                502,
                detail={"code": "entry_unavailable", "message": _MESSAGES["entry_unavailable"]},
                headers=_NO_STORE,
            ) from None

    return router
