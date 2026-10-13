"""Submit only one server-installed synthetic readonly capability, through Gateway."""

from __future__ import annotations

import asyncio
from typing import Any

from app.ports.adapter import AdapterResult
from app.ports.browser_chat import BrowserChatError, BrowserOrdinarySubmissionPort
from app.ports.browser_gateway import (
    BrowserGatewayDiagnostic, BrowserGatewayInvocation,
    BrowserGatewayRegistration,
)
from app.ports.error_codes import ErrorCode
from app.ports.response_envelope import ResponseEnvelope


def browser_failure(code: str) -> tuple[ErrorCode, BrowserGatewayDiagnostic]:
    """Trusted code only; unknown strings never escape to Trace or an envelope."""
    mapping: dict[str, tuple[ErrorCode, BrowserGatewayDiagnostic]] = {
        "request_key_conflict": ("adapter_payload_invalid", BrowserGatewayDiagnostic(
            "admission", "request_key_conflict")),
        "browser_request_input_invalid": ("adapter_payload_invalid", BrowserGatewayDiagnostic(
            "input", "input_invalid")),
        "browser_request_id_required": ("adapter_missing_required_field", BrowserGatewayDiagnostic(
            "input", "input_missing")),
        "browser_capability_unavailable": ("capability_not_found", BrowserGatewayDiagnostic(
            "publication", "capability_unavailable")),
        "browser_publication_unavailable": ("capability_not_found", BrowserGatewayDiagnostic(
            "publication", "capability_unavailable")),
        "browser_binding_stale": ("identity_revoked", BrowserGatewayDiagnostic(
            "binding", "identity_revoked")),
        "browser_auth_expired": ("identity_expired", BrowserGatewayDiagnostic(
            "binding", "identity_expired")),
        "browser_policy_denied": ("policy_denied", BrowserGatewayDiagnostic(
            "binding", "policy_denied")),
        "browser_owner_or_binding_mismatch": ("policy_denied", BrowserGatewayDiagnostic(
            "binding", "context_mismatch")),
        "browser_session_authorization_invalid": ("identity_revoked", BrowserGatewayDiagnostic(
            "context", "identity_revoked")),
        "authentication_required": ("identity_expired", BrowserGatewayDiagnostic(
            "context", "identity_expired")),
        "session_not_found": ("policy_denied", BrowserGatewayDiagnostic(
            "context", "context_mismatch")),
        "browser_request_admission_unavailable": ("adapter_error", BrowserGatewayDiagnostic(
            "admission", "admission_uncertain")),
        "browser_request_rejection_unavailable": ("adapter_error", BrowserGatewayDiagnostic(
            "admission", "admission_uncertain")),
        "browser_session_unavailable": ("adapter_error", BrowserGatewayDiagnostic(
            "context", "provider_unavailable")),
        "browser_chat_unavailable": ("adapter_error", BrowserGatewayDiagnostic(
            "admission", "provider_unavailable")),
        "browser_request_admission_failed": ("adapter_error", BrowserGatewayDiagnostic(
            "admission", "provider_unavailable")),
    }
    return mapping.get(code, ("adapter_error", BrowserGatewayDiagnostic(
        "admission", "unknown_exception")))


class BrowserCapabilityAdapter:
    def __init__(
        self, registration: BrowserGatewayRegistration, submission: BrowserOrdinarySubmissionPort,
    ) -> None:
        self.registration, self._submission = registration, submission

    async def execute(
        self, capability_id: str, arguments: dict[str, Any], execution_context: dict[str, Any],
    ) -> AdapterResult:
        invocation = execution_context.get("browser_invocation")
        registered = self.registration
        if (type(invocation) is not BrowserGatewayInvocation
                or invocation.capability_id != capability_id
                or invocation.capability_version != registered.capability_version
                or capability_id != registered.capability_id
                or invocation.owner.tenant_id != registered.tenant_id
                or invocation.owner.user_id != registered.user_id
                or invocation.scope.attempt.message is None or invocation.scope.attempt.channel is None
                or not invocation.scope.matches(invocation.scope.verified.principal,
                                                invocation.owner.session_id)):
            return AdapterResult(status="permission_denied", error_code="policy_denied",
                                 browser_diagnostic=BrowserGatewayDiagnostic("context", "context_mismatch"))
        def received(response: ResponseEnvelope) -> None:
            invocation.scope.capture_admission(response)

        try:
            response = await self._submission.submit(
                principal=invocation.scope.verified.principal,
                bound_session=invocation.owner.session_id,
                skill_id=registered.skill_id, skill_version=registered.skill_version,
                arguments=arguments, client_request_id=invocation.scope.client_request_id,
                request_message=invocation.scope.attempt.message,
                request_channel=invocation.scope.attempt.channel,
                on_admitted=received,
            )
            if response.status == "failed":
                data = response.data
                code = (data.get("error_code") if type(data) is dict
                        and set(data) == {"kind", "error_code"} and data.get("kind") == "failed"
                        else None)
                error, diagnostic = browser_failure(code if type(code) is str else "")
                return AdapterResult(status="error", error_code=error, browser_diagnostic=diagnostic)
            admission = invocation.scope.capture_admission(response)
            return AdapterResult(status="accepted", browser_admission=admission)
        except BrowserChatError as error:
            code, diagnostic = browser_failure(error.code)
            return AdapterResult(status="error", error_code=code, browser_diagnostic=diagnostic)
        except TimeoutError:
            return AdapterResult(status="timeout", error_code="adapter_timeout",
                                 browser_diagnostic=BrowserGatewayDiagnostic("admission", "admission_timeout"))
        except asyncio.CancelledError:
            raise
        except Exception:
            # An accept commit may have succeeded. Never reject, cancel, or issue a new key.
            return AdapterResult(status="error", error_code="adapter_error",
                                 browser_diagnostic=BrowserGatewayDiagnostic("admission", "admission_uncertain"))
