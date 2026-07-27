from __future__ import annotations

import asyncio
import sys
from typing import Any

import pytest

from app import server
from app.event_loop import make_event_loop


def test_server_entrypoint_uses_production_app_and_supported_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: dict[str, Any] = {}

    def fake_run(application: str, **kwargs: Any) -> None:
        recorded["application"] = application
        recorded.update(kwargs)

    monkeypatch.setenv("API_HOST", "127.0.0.1")
    monkeypatch.setenv("API_PORT", "8123")
    monkeypatch.setattr(server.uvicorn, "run", fake_run)

    server.main()

    assert recorded == {
        "application": "app.main:app",
        "host": "127.0.0.1",
        "port": 8123,
        "loop": "app.event_loop:make_event_loop",
        "access_log": False,
    }


@pytest.mark.parametrize("value", ["0", "65536", "invalid"])
def test_server_entrypoint_rejects_invalid_port(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("API_PORT", value)

    with pytest.raises(RuntimeError, match="API_PORT"):
        server.main()


def test_event_loop_factory_is_psycopg_compatible_on_windows() -> None:
    loop = make_event_loop()
    try:
        if sys.platform == "win32":
            assert isinstance(loop, asyncio.SelectorEventLoop)
        else:
            assert isinstance(loop, asyncio.AbstractEventLoop)
    finally:
        loop.close()
