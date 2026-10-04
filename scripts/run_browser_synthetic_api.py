"""Explicit internal API host for the existing synthetic browser Runtime routes."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any, Literal, NoReturn

import uvicorn

from app.infra.browser.openrouter_jev import prompt_openrouter_key
from app.infra.browser.synthetic_api import create_synthetic_api
from app.infra.browser.synthetic_operator import open_synthetic_operator, prompt_operator_bundle
from app.infra.browser.synthetic_private_input import read_private_operator_input
from app.infra.browser.synthetic_vault import (
    DIAGNOSTIC_TRIAL,
    OBSERVE_TRIAL,
    ORIGINAL_TRIAL,
    VISIBLE_TRIAL,
    approved_trial_id,
)


class _SilentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ValueError("browser_operator_arguments_invalid")


async def _serve(
    *, input_mode: Literal["chat", "structured"], operator_vault: Path | None,
    private_stdin: bool = False,
    trial_id: str = ORIGINAL_TRIAL,
) -> None:
    approved_trial_id(trial_id)
    if trial_id != ORIGINAL_TRIAL and (not private_stdin or input_mode != "structured"):
        raise ValueError("browser_operator_arguments_invalid")
    if private_stdin:
        if operator_vault is not None:
            raise ValueError("browser_operator_arguments_invalid")
        bundle, key = (read_private_operator_input() if trial_id == ORIGINAL_TRIAL
                       else read_private_operator_input(trial_id=trial_id))
    else:
        bundle = prompt_operator_bundle(encrypted_path=operator_vault)
        key = prompt_openrouter_key()
    trial_options: dict[str, Any] = {"trial_id": trial_id} if trial_id != ORIGINAL_TRIAL else {}
    async with open_synthetic_operator(
        bundle, jev_key=key, enabled=True, input_mode=input_mode,
        **trial_options,
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
    parser.add_argument("--private-stdin", action="store_true")
    parser.add_argument(
        "--trial-id", choices=(ORIGINAL_TRIAL, DIAGNOSTIC_TRIAL, VISIBLE_TRIAL, OBSERVE_TRIAL),
        default=ORIGINAL_TRIAL,
    )
    try:
        args = parser.parse_args(argv)
        if not args.enable:
            raise ValueError("browser_operator_disabled")
        asyncio.run(_serve(input_mode=args.input_mode, operator_vault=args.operator_vault,
                           private_stdin=args.private_stdin, trial_id=args.trial_id))
    except BaseException:
        print("browser_synthetic_api_unavailable", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
