"""Classify the existing non-persisting OA verifier for browser entry."""

from __future__ import annotations

from app.infra.auth.oa import OAAuthenticationError
from app.ports.auth import LoginCredential
from app.ports.credential_binding import (
    CredentialBindingVerification,
    CredentialBindingVerifierPort,
)
from app.ports.system_entry import SystemEntryError


class OAEntryVerifier:
    def __init__(self, verifier: CredentialBindingVerifierPort) -> None:
        self._verifier = verifier

    async def verify_for_binding(
        self, credential: LoginCredential,
    ) -> CredentialBindingVerification:
        try:
            return await self._verifier.verify_for_binding(credential)
        except OAAuthenticationError as exc:
            if exc.failure_kind in {"credentials_rejected", "identity_mismatch"}:
                raise SystemEntryError("entry_credentials_rejected") from None
            raise SystemEntryError("entry_unavailable") from None
