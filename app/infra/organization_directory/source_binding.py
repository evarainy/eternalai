"""Open a directory source using only the explicitly selected user's binding."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import AsyncContextManager

from app.ports.auth import CredentialStorePort, OASessionCredential
from app.ports.credential_binding import (
    BackgroundCredentialAcquirerPort,
    CredentialAcquisitionError,
    CredentialPollingStorePort,
)
from app.ports.organization_directory import OrganizationDirectorySourcePort
from app.ports.organization_directory_sync import DirectorySourceError

TransportFactory = Callable[
    [OASessionCredential], AsyncContextManager[OrganizationDirectorySourcePort]
]


class BoundOrganizationDirectorySourceFactory:
    def __init__(
        self,
        *,
        ai_user_id: str | None,
        tenant_id: str,
        polling_store: CredentialPollingStorePort,
        acquirer: BackgroundCredentialAcquirerPort,
        credential_store: CredentialStorePort,
        transport_factory: TransportFactory | None,
    ) -> None:
        if not tenant_id.strip():
            raise ValueError("source_profile_configuration_invalid")
        self._tenant_id = tenant_id
        self._ai_user_id = ai_user_id
        self._polling_store = polling_store
        self._acquirer = acquirer
        self._credential_store = credential_store
        self._transport_factory = transport_factory

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[OrganizationDirectorySourcePort]:
        user = self._ai_user_id
        factory = self._transport_factory
        if user is None or factory is None:
            raise DirectorySourceError("source_unconfigured")
        try:
            async with self._polling_store.poll_lock(
                user, "oa", tenant_id=self._tenant_id
            ) as locked:
                if not locked:
                    raise DirectorySourceError("source_binding_unavailable")
                candidate = await self._polling_store.refresh_poll_candidate(
                    user, "oa", tenant_id=self._tenant_id
                )
                if (
                    candidate is None
                    or candidate.tenant_id != self._tenant_id
                    or candidate.ai_user_id != user
                    or candidate.target_system != "oa"
                ):
                    raise DirectorySourceError("source_binding_unavailable")
                try:
                    principal = await self._acquirer.acquire(candidate)
                    if (
                        principal.ai_user_id != user
                        or principal.org_ctx.tenant_id != self._tenant_id
                    ):
                        raise CredentialAcquisitionError("identity_mismatch")
                except CredentialAcquisitionError as exc:
                    if exc.code in {
                        "credentials_rejected",
                        "identity_mismatch",
                        "captcha_required",
                    }:
                        await self._polling_store.mark_terminal_authentication_failure(
                            user,
                            "oa",
                            "captcha_required" if exc.code == "captcha_required" else "invalid",
                            tenant_id=self._tenant_id,
                        )
                        raise DirectorySourceError("source_authentication_failed") from None
                    if exc.code == "timeout":
                        raise DirectorySourceError("source_timeout") from None
                    if exc.code == "local_failure":
                        raise DirectorySourceError("storage_unavailable") from None
                    raise DirectorySourceError("source_unavailable") from None
                credential = await self._credential_store.load(
                    user, "oa", tenant_id=self._tenant_id
                )
                if credential is None:
                    raise DirectorySourceError("source_binding_unavailable")
                async with factory(credential) as source:
                    yield source
        except DirectorySourceError:
            raise
        except Exception:
            raise DirectorySourceError("storage_unavailable") from None
