"""Bounded stage records through the application's existing logging handlers."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic
from typing import Literal, TypedDict

from app.mcp.models import ServiceConfig

# The production entrypoint already configures uvicorn.error at INFO. Using
# that handler hierarchy makes stage records visible without replacing the
# application's logging configuration or depending on the root WARNING level.
_LOGGER = logging.getLogger("uvicorn.error.mcp_stages")
_TRACE_ID: ContextVar[str] = ContextVar("mcp_trace_id", default="")


class McpLogRefs(TypedDict, total=False):
    task_id: str
    trace_id: str
    connection_id: str


@contextmanager
def mcp_trace_scope(trace_id: str) -> Iterator[None]:
    token = _TRACE_ID.set(trace_id)
    try:
        yield
    finally:
        _TRACE_ID.reset(token)


def record_mcp_event(
    stage: str,
    status: Literal["started", "success", "failed", "cancelled", "pending"],
    *,
    profile: ServiceConfig | None = None,
    tool: str = "",
    task_id: str = "",
    trace_id: str = "",
    connection_id: str = "",
    error_code: str = "",
    started_at: float | None = None,
    outbound_count: int = 0,
) -> None:
    # Callers supply only code-owned labels and internal references. Never pass
    # payloads, URLs, login fingerprints, OAuth parameters or exception text.
    _LOGGER.info(
        "mcp stage=%s status=%s service=%s version=%s tool=%s task=%s trace=%s "
        "connection=%s error=%s elapsed_ms=%s outbound_count=%s",
        stage,
        status,
        profile.service_config_id if profile else "",
        profile.service_config_version if profile else "",
        tool,
        task_id,
        trace_id or _TRACE_ID.get(),
        connection_id,
        error_code,
        round((monotonic() - started_at) * 1000) if started_at is not None else 0,
        outbound_count,
    )


CatalogDriftReason = Literal[
    "tool_missing",
    "tool_duplicate",
    "input_schema_mismatch",
    "raw_annotations_missing",
    "raw_annotations_mismatch",
    "sdk_annotations_missing",
    "sdk_annotations_mismatch",
    "unclassified",
]


def record_mcp_catalog_drift(
    reason: CatalogDriftReason,
    *,
    profile: ServiceConfig,
    tool: str,
    match_count: int,
    input_expected: str,
    input_actual: str,
    safety_expected: str,
    raw_actual: str | None,
    sdk_actual: str | None,
    sdk_shape: str = "",
    trace_id: str = "",
) -> None:
    # Temporary drift evidence: closed reason, counts, presence flags, SHA-256
    # digests and the fixed-position SDK annotation key-state code only. Never
    # pass schemas, annotations, titles or descriptions.
    _LOGGER.info(
        "mcp_catalog_drift_detail service=%s version=%s tool=%s reason=%s match_count=%s "
        "input_expected=%s input_actual=%s safety_expected=%s raw_present=%s raw_actual=%s "
        "sdk_present=%s sdk_actual=%s sdk_shape=%s trace=%s",
        profile.service_config_id,
        profile.service_config_version,
        tool,
        reason,
        match_count,
        input_expected,
        input_actual,
        safety_expected,
        raw_actual is not None,
        raw_actual or "",
        sdk_actual is not None,
        sdk_actual or "",
        sdk_shape,
        trace_id or _TRACE_ID.get(),
    )
