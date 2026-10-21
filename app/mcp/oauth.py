"""Authorization-code-only orchestration with durable single-use transactions."""

from __future__ import annotations

import base64
import hashlib
import secrets
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Any
from urllib.parse import urlencode

from app.mcp.diagnostics import mcp_trace_scope, record_mcp_event
from app.mcp.models import McpFailure, ServiceConfig
from app.ports.auth import AuthenticatedSessionContext, SessionRevocationStorePort
from app.ports.mcp import McpOAuthHttpPort
from app.ports.mcp_store import AuthorizationTransaction, IdentityAssertionPort, McpStorePort

AuthorizationProbe = Callable[
    [ServiceConfig, AuthorizationTransaction, str, Callable[[], Awaitable[None]]],
    Awaitable[dict[str, Any]],
]


class PendingIdentityPolicy:
    version = "unconfirmed"
    approved = False

    async def verify(self, **kwargs: Any) -> None:
        return None


class OAuthConnections:
    def __init__(
        self,
        configs: dict[str, ServiceConfig],
        store: McpStorePort,
        http: McpOAuthHttpPort,
        revocations: SessionRevocationStorePort,
        identity_policy: IdentityAssertionPort,
        *,
        cancel_authorization: Callable[[AuthorizationTransaction], Awaitable[None]] | None = None,
        authorization_probe: AuthorizationProbe | None = None,
        issuer_parameter_supported: dict[str, bool] | None = None,
        authorization_allowed: Callable[[ServiceConfig, AuthenticatedSessionContext], bool]
        | None = None,
    ) -> None:
        self.configs, self.store, self.http = configs, store, http
        self.revocations, self.identity_policy = revocations, identity_policy
        self._cancel_authorization = cancel_authorization
        self._authorization_probe = authorization_probe
        # Missing metadata remains fail-closed. False must come from the fixed
        # service's confirmed authorization-server metadata.
        self._issuer_parameter_supported = dict(issuer_parameter_supported or {})
        self._authorization_allowed = authorization_allowed

    def service(self, service_id: str, session: AuthenticatedSessionContext) -> ServiceConfig:
        profile = self.configs.get(service_id)
        if (
            profile is None
            or not profile.enabled
            or profile.tenant_id != session.principal.org_ctx.tenant_id
            or (
                self._authorization_allowed is not None
                and not self._authorization_allowed(profile, session)
            )
        ):
            raise McpFailure("mcp_service_unavailable")
        return profile

    async def authorize(self, service_id: str, session: AuthenticatedSessionContext) -> str:
        started = monotonic()
        profile = self.service(service_id, session)
        record_mcp_event("authorization", "started", profile=profile)
        try:
            result = await self._authorize(service_id, session)
        except Exception as exc:
            record_mcp_event(
                "authorization",
                "failed",
                profile=profile,
                started_at=started,
                error_code=exc.code if isinstance(exc, McpFailure) else "mcp_authorization_failed",
            )
            raise
        record_mcp_event("authorization", "success", profile=profile, started_at=started)
        return result

    async def _authorize(self, service_id: str, session: AuthenticatedSessionContext) -> str:
        profile = self.service(service_id, session)
        if session.expires_at <= datetime.now(UTC) or await self.revocations.is_revoked(
            session.fingerprint
        ):
            raise McpFailure("mcp_authorization_invalid")
        registration_id, client_id, create = await self.store.begin_registration(profile)
        if create:
            record_mcp_event("registration_exchange", "started", profile=profile, outbound_count=1)
            try:
                registration = await self.http.post(
                    profile.registration_endpoint,
                    {
                        "client_name": "EternalAI",
                        "redirect_uris": [profile.callback_uri],
                        "token_endpoint_auth_method": "none",
                        "grant_types": ["authorization_code"],
                        "response_types": ["code"],
                        "scope": "mcp:business",
                    },
                    form=False,
                )
                received = registration.get("client_id")
                if (
                    not isinstance(received, str)
                    or not received
                    or len(received) > 256
                    or "client_secret" in registration
                    or registration.get("redirect_uris") != [profile.callback_uri]
                    or registration.get("token_endpoint_auth_method") != "none"
                    or registration.get("grant_types") != ["authorization_code"]
                ):
                    raise McpFailure("mcp_registration_invalid")
                await self.store.complete_registration(registration_id, received)
                client_id = received
                record_mcp_event(
                    "registration_exchange", "success", profile=profile, outbound_count=1
                )
            except Exception:
                record_mcp_event(
                    "registration_exchange",
                    "failed",
                    profile=profile,
                    error_code="mcp_registration_requires_reconciliation",
                    outbound_count=1,
                )
                # Unknown registration outcome must never trigger a second registration.
                await self.store.complete_registration(registration_id, "")
                raise McpFailure("mcp_registration_requires_reconciliation") from None
        if client_id is None:
            raise McpFailure("mcp_registration_requires_reconciliation")
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        transaction = await self.store.begin_authorization(
            profile,
            user_id=session.principal.ai_user_id,
            fingerprint=session.fingerprint.hex(),
            registration_id=registration_id,
            client_id=client_id,
            state_digest=hashlib.sha256(state.encode()).hexdigest(),
            verifier=verifier,
            expires_at=min(session.expires_at, datetime.now(UTC) + timedelta(seconds=600)),
        )
        record_mcp_event(
            "authorization_transaction",
            "success",
            profile=profile,
            connection_id=transaction.owner.connection_id,
        )
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )
        return (
            profile.authorization_endpoint
            + "?"
            + urlencode(
                {
                    "response_type": "code",
                    "client_id": client_id,
                    "redirect_uri": profile.callback_uri,
                    "scope": "mcp:business",
                    "resource": profile.resource,
                    "state": state,
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                }
            )
        )

    async def callback(
        self,
        session: AuthenticatedSessionContext,
        *,
        state: str,
        code: str | None = None,
        issuer: str | None = None,
        callback_uri: str,
        error: str | None = None,
    ) -> None:
        with mcp_trace_scope(secrets.token_hex(16)):
            started = monotonic()
            record_mcp_event("oauth_callback", "started")
            try:
                await self._callback(
                    session,
                    state=state,
                    code=code,
                    issuer=issuer,
                    callback_uri=callback_uri,
                    error=error,
                )
            except Exception as exc:
                record_mcp_event(
                    "oauth_callback",
                    "failed",
                    started_at=started,
                    error_code=(
                        exc.code if isinstance(exc, McpFailure)
                        else "mcp_authorization_failed"
                    ),
                )
                raise
            record_mcp_event(
                "oauth_callback", "cancelled" if error else "success", started_at=started
            )

    async def _callback(
        self,
        session: AuthenticatedSessionContext,
        *,
        state: str,
        code: str | None,
        issuer: str | None,
        callback_uri: str,
        error: str | None,
    ) -> None:
        if (
            not state
            or len(state) > 512
            or (code is None) == (error is None)
            or (code is not None and (not code or len(code) > 2048))
            or (error is not None and error != "access_denied")
        ):
            raise McpFailure("mcp_authorization_invalid")
        if session.expires_at <= datetime.now(UTC) or await self.revocations.is_revoked(
            session.fingerprint
        ):
            raise McpFailure("mcp_authorization_invalid")
        state_digest = hashlib.sha256(state.encode()).hexdigest()
        service_id = await self.store.authorization_service(
            state_digest,
            tenant_id=session.principal.org_ctx.tenant_id,
            user_id=session.principal.ai_user_id,
            fingerprint=session.fingerprint.hex(),
        )
        profile = self.service(service_id, session)
        if (
            profile.callback_uri != callback_uri
            or (issuer is not None and profile.issuer != issuer)
            or (issuer is None and self._issuer_parameter_supported.get(service_id, True))
        ):
            raise McpFailure("mcp_authorization_invalid")
        if error is not None and self._cancel_authorization is None:
            raise McpFailure("mcp_cancellation_unavailable")
        transaction = await self.store.claim_authorization(
            state_digest,
            tenant_id=session.principal.org_ctx.tenant_id,
            user_id=session.principal.ai_user_id,
            fingerprint=session.fingerprint.hex(),
            issuer=profile.issuer,
            resource=profile.resource,
            callback_uri=callback_uri,
            now=datetime.now(UTC),
        )
        profile = self.service(transaction.owner.service_config_id, session)
        if profile.service_config_version != transaction.service_config_version:
            raise McpFailure("mcp_authorization_stale")
        if error is not None:
            assert self._cancel_authorization is not None
            await self._cancel_authorization(transaction)
            record_mcp_event(
                "authorization_transaction",
                "cancelled",
                profile=profile,
                connection_id=transaction.owner.connection_id,
            )
            return

        token_expires_at: datetime | None = None

        async def revalidate() -> None:
            current = await self.store.connection(
                tenant_id=transaction.owner.tenant_id,
                user_id=transaction.owner.user_id,
                service_config_id=transaction.owner.service_config_id,
            )
            if (
                self.service(service_id, session) != profile
                or session.expires_at <= datetime.now(UTC)
                or transaction.expires_at <= datetime.now(UTC)
                or (token_expires_at is not None and token_expires_at <= datetime.now(UTC))
                or await self.revocations.is_revoked(session.fingerprint)
                or current is None
                or current.connection_id != transaction.owner.connection_id
                or current.binding_epoch != transaction.binding_epoch
                or current.registration_id != transaction.registration_id
                or current.service_config_version != transaction.service_config_version
                or current.login_session_fingerprint != transaction.login_session_fingerprint
                or current.state != "AUTHORIZING"
            ):
                raise McpFailure("mcp_authorization_stale")

        await revalidate()
        record_mcp_event(
            "token_exchange",
            "started",
            profile=profile,
            connection_id=transaction.owner.connection_id,
            outbound_count=1,
        )
        result = await self.http.post(
            profile.token_endpoint,
            {
                "grant_type": "authorization_code",
                "code": code,
                "client_id": transaction.client_id,
                "redirect_uri": transaction.callback_uri,
                "resource": transaction.resource,
                "code_verifier": transaction.verifier.get_secret_value(),
            },
            form=True,
        )
        token, expires = result.get("access_token"), result.get("expires_in")
        if (
            not isinstance(token, str)
            or not token
            or len(token) > 8192
            or result.get("token_type", "").lower() != "bearer"
            or type(expires) is not int
            or not 0 < expires <= 3600
            or "refresh_token" in result
            or "client_secret" in result
            or result.get("scope", "mcp:business") != "mcp:business"
        ):
            raise McpFailure("mcp_token_invalid")
        record_mcp_event(
            "token_exchange",
            "success",
            profile=profile,
            connection_id=transaction.owner.connection_id,
            outbound_count=1,
        )
        token_expires_at = min(session.expires_at, datetime.now(UTC) + timedelta(seconds=expires))
        evidence = None
        if self.identity_policy.approved:
            metadata: dict[str, Any] = {
                "scope": "mcp:business",
                "issuer": profile.issuer,
                "registration_id": transaction.registration_id,
            }
            if self._authorization_probe is not None:
                metadata.update(
                    await self._authorization_probe(profile, transaction, token, revalidate)
                )
            # Only explicitly accepted token metadata; never send the bearer to identity hooks.
            evidence = await self.identity_policy.verify(
                tenant_id=transaction.owner.tenant_id,
                user_id=transaction.owner.user_id,
                service_config_id=transaction.owner.service_config_id,
                token_metadata=metadata,
            )
        await revalidate()
        await self.store.store_grant(
            transaction,
            token=token,
            expires_at=token_expires_at,
            identity_policy_version=self.identity_policy.version if evidence else None,
            identity_evidence=evidence,
        )
        record_mcp_event(
            "identity_activation",
            "success" if evidence else "pending",
            profile=profile,
            connection_id=transaction.owner.connection_id,
        )
