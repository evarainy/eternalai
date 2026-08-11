"""Canonical trace evidence required from one completed Runtime request."""

from __future__ import annotations

REQUIRED_TRACE_EVENTS = frozenset(
    {
        "task_created",
        "intent_parsed",
        "capability_selected",
        "identity_check",
        "policy_checked",
        "gateway_pre_recorded",
        "adapter_called",
        "gateway_post_recorded",
        "evaluation_recorded",
        "response_envelope_created",
        "task_completed",
    }
)

__all__ = ("REQUIRED_TRACE_EVENTS",)
