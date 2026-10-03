"""Explicit internal API host for the existing synthetic browser Runtime routes."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Literal, NoReturn

import uvicorn

from app.infra.browser.openrouter_jev import prompt_openrouter_key
from app.infra.browser.synthetic_api import create_synthetic_api
from app.infra.browser.synthetic_operator import open_synthetic_operator, prompt_operator_bundle


class _SilentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ValueError("browser_operator_arguments_invalid")


async def _serve(
    *, input_mode: Literal["chat", "structured"], operator_vault: Path | None,
) -> None:
    bundle = prompt_operator_bundle(encrypted_path=operator_vault)
    key = prompt_openrouter_key()
    async with open_synthetic_operator(
        bundle, jev_key=key, enabled=True, input_mode=input_mode,
    ) as components:
        application = create_synthetic_api(components)
        # API submission and the separate worker share the durable queue. This
        # process does not start a second worker or provide an authentication bypass.
        server = uvicorn.Server(uvicorn.Config(
            application, host="0.0.0.0", port=8000, access_log=False,
            log_level="critical", log_config=None,
        ))
        await server.serve()


def main(argv: list[str] | None = None) -> int:
    parser = _SilentParser(allow_abbrev=False, add_help=False)
    parser.add_argument("--enable", action="store_true")
    parser.add_argument("--input-mode", choices=("chat", "structured"), default="chat")
    parser.add_argument("--operator-vault", type=Path)
    try:
        args = parser.parse_args(argv)
        if not args.enable:
            raise ValueError("browser_operator_disabled")
        asyncio.run(_serve(input_mode=args.input_mode, operator_vault=args.operator_vault))
    except BaseException:
        print("browser_synthetic_api_unavailable", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
