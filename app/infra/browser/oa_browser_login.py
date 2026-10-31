"""Run-bound OA login authority. No resource creation or secret access at import.

The dedicated resource/normal-login controls still need a deployment contract.
This private seam is not an HTTP credential API or an attached-D fallback.
"""

from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from app.browser_skill.models import ScopeBinding
from app.browser_skill.site_rules import canonical_origin
from app.infra.auth.oa import parse_oa_login_subject
from app.infra.auth.postgresql import PostgreSQLCredentialStore
from app.infra.browser.local_resource_lifecycle import registered_subject_digest
from app.infra.persistence.browser.authorization import (
    PostgreSQLBrowserBindingReader,
    PostgreSQLBrowserCurrentAuth,
)
from app.infra.persistence.browser.credential_vault import BrowserCredentialVault
from app.ports.auth import OASessionCredential
from app.ports.browser_run_store import BrowserRunStorePort, RunSnapshot
from app.ports.credential_binding import PasswordBindingCredential
from app.ports.credential_vault import (
    BrowserAuthFact,
    BrowserAuthorizationError,
    BrowserBindingFact,
)

if TYPE_CHECKING:
    from playwright.async_api import Response


class RunBoundOACredentialVault:
    """One exact worker Run and one single-use private login consumer.

    Reads canonical durable facts before authorize, consume and release of
    plaintext. Client references and configuration cannot manufacture a subject.
    """

    def __init__(
        self, *, run: RunSnapshot, binding: BrowserBindingFact, origin: str,
        runs: BrowserRunStorePort, current_auth: PostgreSQLBrowserCurrentAuth,
        binding_reader: PostgreSQLBrowserBindingReader,
        credential_store: PostgreSQLCredentialStore,
        check_lease: Callable[[], Awaitable[None]],
    ) -> None:
        if (binding.target_system != "oa" or binding.tenant_id != run.owner.tenant_id
                or binding.ai_user_id != run.owner.user_id
                or binding.binding_id != run.admission.binding_id
                or binding.binding_revision != run.admission.binding_revision
                or run.admission.target_system != "oa"):
            raise BrowserAuthorizationError("browser_owner_or_binding_mismatch")
        self._run, self._binding, self._runs = run, binding, runs
        self._auth, self._reader, self._store = current_auth, binding_reader, credential_store
        self._origin = urlsplit(canonical_origin(origin))
        self._check_lease = check_lease
        self._used = False

    async def _current(self) -> RunSnapshot:
        run = await self._runs.get(self._run.owner, self._run.task_id, self._run.run_id)
        if (run.admission != self._run.admission or run.worker_epoch != self._run.worker_epoch
                or run.worker_id is None or run.worker_id != self._run.worker_id
                or run.lease_epoch is None or run.lease_epoch != self._run.lease_epoch
                or run.status != "running" or run.phase not in {"acquiring", "running"}
                or run.worker_deadline is None or run.worker_deadline <= datetime.now(UTC)
                or run.cancel_requested or run.cancel_acknowledged or run.cleanup != "pending"
                or run.effect != "not_sent" or run.verification is not None):
            raise BrowserAuthorizationError("browser_login_run_stale")
        # The Run lease epoch is only a reference; validate the exact live DB claim.
        await self._check_lease()
        return run

    @staticmethod
    def _fact(run: RunSnapshot) -> BrowserAuthFact:
        return BrowserAuthFact(
            run.owner, None, run.admission.auth_fingerprint, run.admission.auth_expires_at,
            run.run_id, "verified-session-v1",
        )

    async def check_current(self, fact: BrowserAuthFact) -> None:
        await self._auth.check_current(fact)

    async def _facts(self, scope: ScopeBinding) -> tuple[BrowserAuthFact, BrowserBindingFact]:
        return self._fact(self._run), self._binding

    @staticmethod
    def _scope(run: RunSnapshot) -> ScopeBinding:
        if run.lease_epoch is None:
            raise BrowserAuthorizationError("browser_login_run_stale")
        return ScopeBinding(
            owner=run.owner, binding_id=run.admission.binding_id,
            binding_revision=run.admission.binding_revision, lease_epoch=run.lease_epoch,
            authorization_run_id=run.run_id, evidence_version="verified-session-v1",
        )

    async def consume_login(
        self, consumer: Callable[[PasswordBindingCredential], Awaitable[None]],
    ) -> None:
        """Never retries or exposes a plaintext return value or restore purpose."""
        if self._used:
            raise BrowserAuthorizationError("browser_credential_grant_invalid")
        self._used = True

        async def private_consumer(
            credential: PasswordBindingCredential | OASessionCredential,
        ) -> None:
            if type(credential) is not PasswordBindingCredential:
                raise BrowserAuthorizationError("browser_credential_purpose_invalid")
            await consumer(credential)

        run = await self._current()
        vault = BrowserCredentialVault(
            binding_reader=self._reader, current_auth=self, fact_reader=self._facts,
            credential_store=self._store, consumer=private_consumer,
        )
        grant = await vault.authorize(self._scope(run), "browser_login")
        await vault.consume(grant)

    async def verify_login_response(self, *, response: Response) -> None:
        """Only a successful checkLogin response on the configured OA origin.

        The trusted resource owner matches the response to its own single login
        request. Neither the response nor its identity text is recorded/returned.
        """
        expected = self._origin
        try:
            actual = urlsplit(response.url)
            valid = (
                response.status == 200
                and actual.username is None and actual.password is None
                and (actual.scheme, actual.hostname, actual.port or
                     (443 if actual.scheme == "https" else 80)) == (
                         expected.scheme, expected.hostname, expected.port or
                         (443 if expected.scheme == "https" else 80))
                and actual.path == "/api/hrm/login/checkLogin"
            )
        except Exception:
            raise BrowserAuthorizationError("browser_login_response_invalid") from None
        if not valid:
            raise BrowserAuthorizationError("browser_login_response_invalid")
        try:
            payload: Any = await response.json()
        except Exception:
            raise BrowserAuthorizationError("browser_login_response_invalid") from None
        if type(payload) is not dict:
            raise BrowserAuthorizationError("browser_login_response_invalid")
        try:
            subject = parse_oa_login_subject(payload)
        except Exception:
            raise BrowserAuthorizationError("browser_login_response_invalid") from None
        del payload
        expected_subject = self._auth.input(self._run.admission).principal.org_ctx.directory_user_id
        if (type(expected_subject) is not str or not expected_subject
                or not hmac.compare_digest(subject.encode(), expected_subject.encode())
                or registered_subject_digest("oa", subject) != self._binding.subject_digest):
            raise BrowserAuthorizationError("browser_login_subject_mismatch")
        del subject
