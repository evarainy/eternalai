"""Value-free chat stage diagnostics shared by Runtime chat paths."""

from __future__ import annotations

import logging
from uuid import UUID

_CHAT_STAGE_LOGGER = logging.getLogger("uvicorn.error.chat_stages")
_CHAT_STAGE_NODES = frozenset(
    {
        "candidate",
        "catalog",
        "credential_read",
        "execution",
        "fixed_reply",
        "identity",
        "intent",
        "model",
        "normalization",
        "oa_result",
        "oa_route",
        "provider_transport",
        "d_form_collection",
        "d_form_category",
    }
)
_CHAT_STAGE_OUTCOMES = frozenset({"blocked", "failed", "ok", "skipped", "start", "unknown"})
_CHAT_STAGE_REASONS = frozenset(
    {
        "adapter_error",
        "adapter_http_500",
        "adapter_payload_invalid",
        "adapter_timeout",
        "ambiguous",
        "argument_validation",
        "binding_required",
        "blank_input",
        "capability_not_found",
        "catalog_invalid",
        "completed",
        "completion_returned",
        "confirm_required",
        "context_length_exceeded",
        "credential_not_required",
        "credential_resolved",
        "d_form_consumed",
        "denied",
        "empty",
        "empty_response",
        "failed",
        "http_server",
        "identity_expired",
        "identity_revoked",
        "identity_unbound",
        "internal_error",
        "invalid_request",
        "low_confidence",
        "match_capability",
        "match_none",
        "model_not_available",
        "no_active_capability_registered",
        "no_capability_found",
        "no_matching_capability",
        "no_unique_active_candidate",
        "no_visible_capability",
        "none",
        "not_configured",
        "over_budget",
        "payload_invalid",
        "payload_received",
        "permission_denied",
        "policy_denied",
        "provider_error",
        "provider_returned",
        "rate_limited",
        "ready",
        "request_error",
        "response_received",
        "running",
        "schema_invalid",
        "structured_output_error",
        "timeout",
        "unknown",
        "unparsed",
        "upstream_permission_denied",
        "visible_empty",
        "visible_present",
        "waiting_user",
        "initial_category_supplied",
        "initial_category_missing",
        "continuation_category_supplied",
        "continuation_category_missing",
        "category_options_available",
        "category_options_unavailable",
    }
)
_CHAT_STAGE_CAPABILITIES = frozenset(
    {"oa.list_pending_workflows", "oa.list_system_messages", "browser.form.fill_stop"}
)


def _record_chat_stage(
    trace_id: object,
    node: str,
    outcome: str,
    *,
    reason: object = None,
    capability_id: object = None,
    elapsed_ms: int | None = None,
    http_status: object = None,
) -> None:
    # Project only code-owned enums and Runtime's existing server UUID trace.
    try:
        if type(trace_id) is not str or node not in _CHAT_STAGE_NODES:
            return
        trace = UUID(trace_id)
        if trace.version != 4 or str(trace) != trace_id:
            return
        _CHAT_STAGE_LOGGER.info(
            "chat_stage node=%s outcome=%s capability=%s reason=%s "
            "http_status=%s elapsed_ms=%s trace=%s",
            node,
            outcome if outcome in _CHAT_STAGE_OUTCOMES else "unknown",
            capability_id
            if type(capability_id) is str and capability_id in _CHAT_STAGE_CAPABILITIES
            else "none"
            if capability_id is None
            else "other",
            reason
            if type(reason) is str and reason in _CHAT_STAGE_REASONS
            else "none"
            if reason is None
            else "unknown",
            http_status if type(http_status) is int and 100 <= http_status <= 599 else "null",
            elapsed_ms if type(elapsed_ms) is int and 0 <= elapsed_ms <= 86_400_000 else "null",
            trace.hex,
        )
    except Exception:
        # Diagnostics must not replace the existing business result.
        return
