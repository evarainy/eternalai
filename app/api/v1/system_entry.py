"""Authenticated, CSRF-protected browser entry; no credential-bearing cache."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.api.v1.auth import PrincipalDependency
from app.ports.auth import Principal
from app.ports.system_entry import (
    SystemEntryError,
    SystemEntryTarget,
    SystemEntryView,
    U8CaptchaAnswer,
    U8CaptchaView,
)
from app.system_entry.service import SystemEntryService

_log = logging.getLogger(__name__)
_MESSAGES = {
    "entry_not_configured": "免登入口未配置。",
    "entry_not_bound": "请先在账号设置中绑定账号和密码。",
    "entry_credentials_rejected": "账号或密码未通过系统验证，请核对账号设置。",
    "entry_unavailable": "暂时无法完成登录，具体原因尚未确认。",
    "entry_captcha_required": "请输入财务验证码。",
    "entry_captcha_invalid": "验证码错误，请换一张后重新填写。",
    "entry_captcha_expired": "验证码已过期，请换一张后重新填写。",
    "entry_timeout": "连接财务系统超时，请稍后重试。",
    "entry_connection_failed": "与财务系统的连接无法建立或已中断，请检查网络后重试。",
    "entry_upstream_http_error": "财务系统返回了 HTTP 错误，请联系系统管理员。",
    "entry_upstream_rejected": "财务系统拒绝本次登录，具体原因尚未确认。",
    "entry_invalid_response": "财务系统响应无法按当前协议解析，暂不能完成登录。",
    "entry_configuration_error": "财务免登配置无法读取或格式不正确，请联系管理员。",
}
_STATUS = {
    "entry_not_configured": 503, "entry_not_bound": 409,
    "entry_credentials_rejected": 400, "entry_unavailable": 502,
    "entry_captcha_required": 400, "entry_captcha_invalid": 400, "entry_captcha_expired": 400,
    "entry_timeout": 504, "entry_connection_failed": 502, "entry_upstream_http_error": 502,
    "entry_upstream_rejected": 502, "entry_invalid_response": 502, "entry_configuration_error": 503,
}
_NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


def system_entry_failure(exc: SystemEntryError) -> HTTPException:
    """Share fixed, redacted U8 failures between binding and browser entry."""
    message = _MESSAGES[exc.code]
    if exc.upstream_status is not None:
        if exc.code == "entry_upstream_rejected":
            message = f"财务系统拒绝本次登录（业务码 {exc.upstream_status}），具体原因尚未确认。"
        elif exc.code == "entry_upstream_http_error":
            message = f"财务系统返回 HTTP {exc.upstream_status} 错误，请联系系统管理员。"
    return HTTPException(_STATUS[exc.code], detail={"code": exc.code, "message": message},
                         headers=_NO_STORE)


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

    @router.post("/u8/captcha", response_model=U8CaptchaView)
    async def u8_captcha(
        response: Response, principal: Principal = Depends(require_principal),
    ) -> U8CaptchaView:
        response.headers.update(_NO_STORE)
        try:
            if service is None:
                raise SystemEntryError("entry_not_configured")
            return await service.u8_captcha()
        except SystemEntryError as exc:
            raise system_entry_failure(exc) from None

    @router.post(
        "/{target}", response_model=SystemEntryView,
        openapi_extra={"requestBody": {"required": False, "content": {
            "application/json": {"schema": U8CaptchaAnswer.model_json_schema()},
        }}},
    )
    async def enter(
        target: SystemEntryTarget, request: Request, response: Response,
        principal: Principal = Depends(require_principal),
    ) -> SystemEntryView:
        response.headers.update(_NO_STORE)
        try:
            if service is None:
                raise SystemEntryError("entry_not_configured")
            captcha = None
            raw = await request.body() if target == "u8" else b""
            if raw:
                try:
                    captcha = U8CaptchaAnswer.model_validate(json.loads(raw))
                except ValueError:
                    raise SystemEntryError("entry_captcha_invalid") from None
            return await service.enter(principal, target, captcha=captcha)
        except SystemEntryError as exc:
            raise system_entry_failure(exc) from None
        except Exception:
            # External login/storage boundary: never serialize upstream URLs or payloads.
            _log.warning("system_entry target=%s stage=entry result=unavailable", target)
            raise HTTPException(
                502,
                detail={"code": "entry_unavailable", "message": _MESSAGES["entry_unavailable"]},
                headers=_NO_STORE,
            ) from None

    return router
