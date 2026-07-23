"""Compatibility export for the Trace contract sanitizer."""

from __future__ import annotations

from app.ports.trace import (
    REDACTED_TRACE_VALUE,
    redact_trace_attributes,
)

__all__ = ("REDACTED_TRACE_VALUE", "redact_trace_attributes")
