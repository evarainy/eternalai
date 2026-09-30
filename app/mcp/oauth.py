"""Authorization-code-only orchestration with durable single-use transactions."""

from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from app.mcp.models import McpFailure, ServiceConfig
from app.ports.auth import AuthenticatedSessionContext, SessionRevocationStorePort
from app.ports.mcp import McpOAuthHttpPort
from app.ports.mcp_store import IdentityAssertionPort, McpStorePort


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
    ) -> None:
        self.configs, self.store, self.http = configs, store, http
        self.revocations, self.identity_policy = revocations, identity_policy

    def service(self, service_id: str, session: AuthenticatedSessionContext) -> ServiceConfig:
        profile = self.configs.get(service_id)
        if (
            profile is None
            or not profile.enabled
            or profile.tenant_id != session.principal.org_ctx.tenant_id
        ):
            raise McpFailure("mcp_service_unavailable")
        return profile

    async def authorize(self, service_id: str, session: AuthenticatedSessionContext) -> str:
        profile = self.service(service_id, session)
        if session.expires_at <= datetime.now(UTC) or await self.revocations.is_revoked(
            session.fingerprint
        ):
            raise McpFailure("mcp_authorization_invalid")
        registration_id, client_id, create = await self.store.begin_registration(profile)
        if create:
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
            except Exception:
                # Unknown registration outcome must never trigger a second registration.
                await self.store.complete_registration(registration_id, "")
                raise McpFailure("mcp_registration_requires_reconciliation") from None
        if client_id is None:
            raise McpFailure("mcp_registration_requires_reconciliation")
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        await self.store.begin_authorization(
            profile,
            user_id=session.principal.ai_user_id,
            fingerprint=session.fingerprint.hex(),
            registration_id=registration_id,
            client_id=client_id,
            state_digest=hashlib.sha256(state.encode()).hexdigest(),
            verifier=verifier,
            expires_at=min(session.expires_at, datetime.now(UTC) + timedelta(seconds=600)),
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
        code: str,
        issuer: str,
        callback_uri: str,
    ) -> None:
        if not state or not code or len(state) > 512 or len(code) > 2048:
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
        if profile.issuer != issuer or profile.callback_uri != callback_uri:
            raise McpFailure("mcp_authorization_invalid")
        transaction = await self.store.claim_authorization(
            state_digest,
            tenant_id=session.principal.org_ctx.tenant_id,
            user_id=session.principal.ai_user_id,
            fingerprint=session.fingerprint.hex(),
            issuer=issuer,
            resource=profile.resource,
            callback_uri=callback_uri,
            now=datetime.now(UTC),
        )
        profile = self.service(transaction.owner.service_config_id, session)
        if profile.service_config_version != transaction.service_config_version:
            raise McpFailure("mcp_authorization_stale")
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
        evidence = None
        if self.identity_policy.approved:
            # Only explicitly accepted token metadata; never send the bearer to identity hooks.
            evidence = await self.identity_policy.verify(
                tenant_id=transaction.owner.tenant_id,
                user_id=transaction.owner.user_id,
                service_config_id=transaction.owner.service_config_id,
                token_metadata={
                    "scope": "mcp:business",
                    "issuer": issuer,
                    "registration_id": transaction.registration_id,
                },
            )
        if await self.revocations.is_revoked(session.fingerprint):
            raise McpFailure("mcp_authorization_invalid")
        await self.store.store_grant(
            transaction,
            token=token,
            expires_at=min(session.expires_at, datetime.now(UTC) + timedelta(seconds=expires)),
            identity_policy_version=self.identity_policy.version if evidence else None,
            identity_evidence=evidence,
        )
