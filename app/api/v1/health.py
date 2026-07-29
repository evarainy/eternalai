from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, ConfigDict

HealthCheck = Callable[[], Awaitable[bool]]
_DEFAULT_HEALTH_TIMEOUT_SECONDS = 5.0


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "unhealthy"]
    checks: dict[str, Literal["ok", "failed"]]


def make_router(
    checks: Mapping[str, HealthCheck] | None = None,
    *,
    timeout_seconds: float = _DEFAULT_HEALTH_TIMEOUT_SECONDS,
) -> APIRouter:
    if timeout_seconds <= 0:
        raise ValueError("Health timeout must be positive")
    router = APIRouter()
    configured_checks = dict(checks or {})

    @router.get(
        "/health",
        response_model=HealthResponse,
        responses={
            status.HTTP_503_SERVICE_UNAVAILABLE: {"model": HealthResponse},
        },
    )
    async def health(response: Response) -> HealthResponse:
        if not configured_checks:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return HealthResponse(status="unhealthy", checks={})
        results = await asyncio.gather(
            *(
                _check(check, timeout_seconds=timeout_seconds)
                for check in configured_checks.values()
            )
        )
        check_statuses: dict[str, Literal["ok", "failed"]] = {
            name: "ok" if healthy else "failed"
            for name, healthy in zip(configured_checks, results, strict=True)
        }
        healthy = all(results)
        if not healthy:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(
            status="ok" if healthy else "unhealthy",
            checks=check_statuses,
        )

    return router


async def _check(check: HealthCheck, *, timeout_seconds: float) -> bool:
    try:
        async with asyncio.timeout(timeout_seconds):
            return bool(await check())
    except Exception:
        return False


__all__ = ("HealthCheck", "HealthResponse", "make_router")
