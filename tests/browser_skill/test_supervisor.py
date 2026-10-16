"""Business pass wakeup keeps the durable scan moving without polling delay."""

import asyncio

import pytest

from app.browser_skill.supervisor import BrowserWorkerSupervisor


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_completed_business_pass_scans_again_without_fixed_delay() -> None:
    scanned_twice = asyncio.Event()
    calls = 0

    async def run_ready() -> tuple[object, ...]:
        nonlocal calls
        calls += 1
        if calls == 2:
            scanned_twice.set()
        return (object(),) if calls == 1 else ()

    supervisor = BrowserWorkerSupervisor(run_ready)
    await supervisor.start()
    try:
        await asyncio.wait_for(scanned_twice.wait(), timeout=0.5)
    finally:
        await supervisor.stop()
    assert calls == 2
