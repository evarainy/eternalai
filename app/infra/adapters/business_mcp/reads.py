"""Known read semantics; data absence never proves a business obligation complete."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

READ_TOOLS = frozenset(
    {
        "business_context_get",
        "person_find",
        "talk_context_get",
        "clothing_options_get",
        "clothing_result_get",
        "talk_record_get",
        "talk_tasks_list",
    }
)
SOURCE_STATES = frozenset({"FORBIDDEN", "EMPTY", "TRUNCATED", "UNAVAILABLE"})


def shanghai_week(instant: datetime) -> tuple[str, str]:
    local = instant.astimezone(ZoneInfo("Asia/Shanghai")).date()
    start = local - timedelta(days=local.weekday())
    return start.isoformat(), (start + timedelta(days=6)).isoformat()
