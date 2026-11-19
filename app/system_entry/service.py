"""User-initiated browser entry using the caller's own saved binding."""

from __future__ import annotations

import logging
from urllib.parse import urlencode

from app.ports.auth import LoginCredential, Principal
from app.ports.credential_binding import CredentialBindingVerifierPort
from app.ports.system_entry import (
    EntryPasswordReaderPort,
    OAEntryTicketPort,
    SystemEntryError,
    SystemEntryTarget,
    SystemEntryView,
    U8CaptchaAnswer,
    U8CaptchaView,
    U8LoginPort,
    ZhywBindingView,
    ZhywEntryPort,
)

_log = logging.getLogger(__name__)


class SystemEntryService:
    def __init__(
        self, *, reader: EntryPasswordReaderPort, oa_verifier: CredentialBindingVerifierPort,
        oa_tickets: OAEntryTicketPort | None, oa_bridge_base: str | None,
        u8_login: U8LoginPort | None, u8_base: str, zhyw: ZhywEntryPort | None = None,
    ) -> None:
        self._reader = reader
        self._oa_verifier = oa_verifier
        self._tickets = oa_tickets
        self._oa_bridge_base = oa_bridge_base
        self._u8_login = u8_login
        self._u8_base = u8_base
        self._zhyw = zhyw

    def availability(self) -> dict[str, bool]:
        return {"oa": self._tickets is not None, "u8": self._u8_login is not None}

    async def zhyw_binding(self, principal: Principal) -> ZhywBindingView:
        if self._zhyw is None:
            return ZhywBindingView(configured=False, bound=False)
        return ZhywBindingView(configured=True, bound=await self._zhyw.is_bound(principal))

    async def bind_zhyw(self, principal: Principal, login_id: str) -> ZhywBindingView:
        if self._zhyw is None:
            raise SystemEntryError("entry_not_configured")
        await self._zhyw.bind(principal, login_id)
        _log.info("system_entry target=zhyw stage=binding result=saved")
        return ZhywBindingView(configured=True, bound=True)

    async def u8_captcha(self) -> U8CaptchaView:
        if self._u8_login is None:
            raise SystemEntryError("entry_not_configured")
        return await self._u8_login.get_captcha()

    async def enter(
        self, principal: Principal, target: SystemEntryTarget, *,
        captcha: U8CaptchaAnswer | None = None,
    ) -> SystemEntryView:
        if target == "zhyw":
            if self._zhyw is None:
                raise SystemEntryError("entry_not_configured")
            entry = await self._zhyw.enter(principal)
            _log.info("system_entry target=%s stage=handoff result=ready", target)
            return SystemEntryView(url=entry)
        if not self.availability()[target]:
            raise SystemEntryError("entry_not_configured")
        credential = await self._reader.load_password_for_entry(
            principal.ai_user_id, target, tenant_id=principal.org_ctx.tenant_id,
        )
        if target == "u8":
            assert self._u8_login is not None
            token = await self._u8_login.login(credential, captcha=captcha)
            url = self._u8_base + "/?" + urlencode({"Authorization": token.get_secret_value()})
        else:
            assert self._tickets is not None and self._oa_bridge_base is not None
            verified = await self._oa_verifier.verify_for_binding(LoginCredential(
                loginid=credential.login_id, userpassword=credential.password,
            ))
            owner = verified.principal
            if (owner.org_ctx.tenant_id, owner.ai_user_id) != (
                principal.org_ctx.tenant_id, principal.ai_user_id,
            ) or (principal.org_ctx.directory_user_id is not None
                  and owner.org_ctx.directory_user_id != principal.org_ctx.directory_user_id):
                raise SystemEntryError("entry_credentials_rejected")
            ticket = await self._tickets.issue(verified.session_credential.cookies)
            url = self._oa_bridge_base + "/oa-enter/" + ticket
        _log.info("system_entry target=%s stage=handoff result=ready", target)
        return SystemEntryView(url=url)
