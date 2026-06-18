"""Mock adapter error injection control endpoint for testing environments."""

from __future__ import annotations

import os

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from app.execution_fabric.mock_adapters.error_injection import (
    MockInjectionDuration,
    set_injection,
)
from app.ports.adapter import MockErrorMode

router = APIRouter()


class ErrorInjectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error_mode: MockErrorMode
    duration: MockInjectionDuration
    error_detail: str | None = None


def should_register() -> bool:
    return (
        os.environ.get("ENV", "").lower() == "testing"
        or os.environ.get("PHASE0_MOCK_MODE", "").lower() == "true"
    )


@router.post("/mock/{capability_id}/inject")
async def inject_error(
    capability_id: str,
    body: ErrorInjectionRequest,
) -> dict[str, str | int | None]:
    injection = set_injection(
        capability_id=capability_id,
        error_mode=body.error_mode,
        duration=body.duration,
        error_detail=body.error_detail,
    )
    return {
        "capability_id": capability_id,
        "error_mode": injection.error_mode,
        "duration": injection.duration,
        "remaining_calls": injection.remaining_calls,
        "error_detail": injection.error_detail,
    }
