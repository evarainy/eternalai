"""Shared closed error-code vocabulary without gateway/workflow import cycles."""

from typing import Literal, TypeAlias

ErrorCode: TypeAlias = Literal[
    "identity_unbound",
    "identity_expired",
    "identity_revoked",
    "needs_binding_scope",
    "policy_denied",
    "confirm_required",
    "adapter_timeout",
    "capability_not_found",
    "adapter_error",
    "adapter_payload_invalid",
    "adapter_missing_required_field",
    "adapter_empty_response",
    "adapter_http_500",
    "upstream_permission_denied",
    "internal_error",
    "capability_candidates_low_confidence",
    "capability_candidates_ambiguous",
    "capability_candidates_over_budget",
    "capability_catalog_invalid",
    "capability_candidate_out_of_scope",
    "capability_candidate_stale",
    "mcp_contract_unconfirmed",
    "mcp_outcome_unknown",
]
