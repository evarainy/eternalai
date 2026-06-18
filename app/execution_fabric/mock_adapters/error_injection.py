"""Error injection registry for Phase 0 mock adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.ports.adapter import (
    MOCK_ERROR_MODE_TO_ERROR_CODE,
    AdapterPort,
    AdapterResult,
    MockErrorMode,
)

MockInjectionDuration = Literal["next_1_call", "next_3_calls", "permanent"]


@dataclass(frozen=True)
class MockErrorInjection:
    capability_id: str
    error_mode: MockErrorMode
    duration: MockInjectionDuration
    remaining_calls: int | None
    error_detail: str | None = None


_INJECTIONS: dict[str, MockErrorInjection] = {}


def set_injection(
    capability_id: str,
    error_mode: MockErrorMode,
    duration: MockInjectionDuration,
    error_detail: str | None = None,
) -> MockErrorInjection:
    remaining = {"next_1_call": 1, "next_3_calls": 3, "permanent": None}.get(duration)
    injection = MockErrorInjection(
        capability_id=capability_id,
        error_mode=error_mode,
        duration=duration,
        remaining_calls=remaining,
        error_detail=error_detail,
    )
    _INJECTIONS[capability_id] = injection
    return injection


def get_injection(capability_id: str) -> MockErrorInjection | None:
    return _INJECTIONS.get(capability_id)


def consume_injection(capability_id: str) -> MockErrorInjection | None:
    injection = _INJECTIONS.get(capability_id)
    if injection is None:
        return None
    if injection.remaining_calls is None:
        return injection
    if injection.remaining_calls > 1:
        _INJECTIONS[capability_id] = MockErrorInjection(
            capability_id=injection.capability_id,
            error_mode=injection.error_mode,
            duration=injection.duration,
            remaining_calls=injection.remaining_calls - 1,
            error_detail=injection.error_detail,
        )
    else:
        del _INJECTIONS[capability_id]
    return injection


def clear_injection(capability_id: str | None = None) -> None:
    if capability_id is None:
        _INJECTIONS.clear()
    else:
        _INJECTIONS.pop(capability_id, None)


class InjectionAwareAdapter:
    """Wrap a mock adapter and check the injection registry before delegating."""

    def __init__(self, inner: AdapterPort) -> None:
        self._inner = inner

    async def execute(
        self,
        capability_id: str,
        arguments: dict[str, Any],
        execution_context: dict[str, Any],
    ) -> AdapterResult:
        injection = consume_injection(capability_id)
        if injection is not None:
            return AdapterResult(
                status="error",
                error_code=MOCK_ERROR_MODE_TO_ERROR_CODE[injection.error_mode],
            )
        return await self._inner.execute(capability_id, arguments, execution_context)
