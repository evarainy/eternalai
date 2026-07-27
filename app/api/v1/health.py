from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping

from fastapi import APIRouter, Response, status

HealthCheck = Callable[[], Awaitable[bool]]
_DEFAULT_HEALTH_TIMEOUT_SECONDS = 5.0


def make_router(
    checks: Mapping[str, HealthCheck] | None = None,
    *,
    timeout_seconds: float = _DEFAULT_HEALTH_TIMEOUT_SECONDS,
) -> APIRouter:
    if timeout_seconds <= 0:
        raise ValueError("Health timeout must be positive")
    router = APIRouter()
    configured_checks = dict(checks or {})

    @router.get("/health")
    async def health(response: Response) -> dict[str, str | dict[str, str]]:
        if not configured_checks:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {"status": "unhealthy", "checks": {}}
        results = await asyncio.gather(
            *(
                _check(check, timeout_seconds=timeout_seconds)
                for check in configured_checks.values()
            )
        )
        check_statuses = {
            name: "ok" if healthy else "failed"
            for name, healthy in zip(configured_checks, results, strict=True)
        }
        healthy = all(results)
        if not healthy:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "ok" if healthy else "unhealthy",
            "checks": check_statuses,
        }

    return router


async def _check(check: HealthCheck, *, timeout_seconds: float) -> bool:
    try:
        async with asyncio.timeout(timeout_seconds):
            return bool(await check())
    except Exception:
        return False


__all__ = ("HealthCheck", "make_router")
