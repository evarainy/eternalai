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
    U8LoginPort,
)

_log = logging.getLogger(__name__)


class SystemEntryService:
    def __init__(
        self, *, reader: EntryPasswordReaderPort, oa_verifier: CredentialBindingVerifierPort,
        oa_tickets: OAEntryTicketPort | None, oa_bridge_base: str | None,
        u8_login: U8LoginPort | None, u8_base: str,
    ) -> None:
        self._reader = reader
        self._oa_verifier = oa_verifier
        self._tickets = oa_tickets
        self._oa_bridge_base = oa_bridge_base
        self._u8_login = u8_login
        self._u8_base = u8_base

    def availability(self) -> dict[str, bool]:
        return {"oa": self._tickets is not None, "u8": self._u8_login is not None}

    async def enter(self, principal: Principal, target: SystemEntryTarget) -> SystemEntryView:
        if not self.availability()[target]:
            raise SystemEntryError("entry_not_configured")
        credential = await self._reader.load_password_for_entry(
            principal.ai_user_id, target, tenant_id=principal.org_ctx.tenant_id,
        )
        if target == "u8":
            assert self._u8_login is not None
            token = await self._u8_login.login(credential)
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
