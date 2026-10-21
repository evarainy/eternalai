"""Closed original-operation recovery policies, independent of transport retries."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from app.mcp.contracts import NON_IDEMPOTENT_TOOLS, SUBMIT_TOOLS
from app.mcp.models import McpFailure
from app.ports.mcp import McpAuthorizationContext, McpTaskRuleEvidence


def validate_bound_task(
    context: McpAuthorizationContext,
    arguments: dict[str, Any],
    evidence: McpTaskRuleEvidence | None,
) -> None:
    if (
        evidence is None
        or not evidence.policy_version
        or (
            evidence.tenant_id,
            evidence.user_id,
            evidence.service_config_id,
            evidence.task_id,
            evidence.person_id,
        )
        != (
            context.tenant_id,
            context.user_id,
            context.service_config_id,
            arguments["taskId"],
            arguments["personId"],
        )
    ):
        raise McpFailure("mcp_task_rule_unconfirmed")
    try:
        start = date.fromisoformat(evidence.week_start)
        instant = datetime.fromisoformat(arguments["occurredAt"].replace("Z", "+00:00"))
        local = (
            instant.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
            if instant.tzinfo is None
            else instant.astimezone(ZoneInfo("Asia/Shanghai"))
        ).date()
    except ValueError:
        raise McpFailure("mcp_task_rule_unconfirmed") from None
    if start.weekday() != 0 or not start <= local <= start + timedelta(days=6):
        raise McpFailure("mcp_task_week_invalid")


def recovery_direction(
    tool: str,
) -> Literal["manual_reconcile", "read_original", "resume_original"]:
    if tool in NON_IDEMPOTENT_TOOLS:
        return "manual_reconcile"
    if tool in {"clothing_plan_submit", "talk_task_claim"}:
        return "read_original"
    if tool == "talk_record_submit":
        return "resume_original"
    raise McpFailure("mcp_recovery_denied")


def requires_external_confirmation(tool: str) -> bool:
    return tool in SUBMIT_TOOLS
