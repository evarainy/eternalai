"""Minimal production process entrypoint for EternalAI."""

from __future__ import annotations

import os

import uvicorn

_DEFAULT_API_HOST = "0.0.0.0"
_DEFAULT_API_PORT = 8000


def main() -> None:
    """Start the production ASGI application with the supported loop factory."""

    uvicorn.run(
        "app.main:app",
        host=os.environ.get("API_HOST", _DEFAULT_API_HOST),
        port=_api_port(os.environ.get("API_PORT")),
        loop="app.event_loop:make_event_loop",
        access_log=False,
    )


def _api_port(raw: str | None) -> int:
    if raw is None:
        return _DEFAULT_API_PORT
    try:
        port = int(raw)
    except ValueError as exc:
        raise RuntimeError("API_PORT must be an integer") from exc
    if port < 1 or port > 65_535:
        raise RuntimeError("API_PORT must be between 1 and 65535")
    return port


if __name__ == "__main__":
    main()
