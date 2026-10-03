"""Explicit existing publication operations; never bootstrap identities or grants."""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path
from typing import NoReturn

from app.infra.browser.openrouter_jev import prompt_openrouter_key
from app.infra.browser.synthetic_operator import (
    deactivate_synthetic_publication,
    open_synthetic_operator,
    prompt_deactivation_bundle,
    prompt_operator_bundle,
)
from app.ports.browser_publication_store import BrowserPublicationError


class _SilentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ValueError("browser_operator_arguments_invalid")


async def _operate(
    operation: str, *, input_mode: str = "chat", operator_vault: Path | None = None,
    deactivation_vault: Path | None = None,
) -> None:
    if operation == "deactivate":
        if operator_vault is not None:
            raise ValueError("browser_operator_arguments_invalid")
        await deactivate_synthetic_publication(
            prompt_deactivation_bundle(deactivation_vault), enabled=True
        )
        return
    if deactivation_vault is not None:
        raise ValueError("browser_operator_arguments_invalid")
    bundle = prompt_operator_bundle(operator_vault)
    key = prompt_openrouter_key()
    async with open_synthetic_operator(
        bundle, jev_key=key, enabled=True, require_active_publication=False,
        input_mode=input_mode,
    ) as components:
        vertical, owner = components.vertical, components.publication_owner
        if operation == "prepare":
            await vertical.prepare_seed(owner)
        elif operation == "activate":
            await vertical.activate_seed(owner)
        else:
            raise ValueError("browser_operator_arguments_invalid")


def main(argv: list[str] | None = None) -> int:
    parser = _SilentParser(allow_abbrev=False, add_help=False)
    parser.add_argument("--enable", action="store_true")
    parser.add_argument("--operation", choices=("prepare", "activate", "deactivate"))
    parser.add_argument("--input-mode", choices=("chat", "structured"), default="chat")
    parser.add_argument("--operator-vault", type=Path)
    parser.add_argument("--deactivation-vault", type=Path)
    try:
        args = parser.parse_args(argv)
        if not args.enable or args.operation is None:
            raise ValueError("browser_operator_disabled")
        asyncio.run(_operate(
            args.operation, input_mode=args.input_mode,
            operator_vault=args.operator_vault, deactivation_vault=args.deactivation_vault,
        ))
    except BrowserPublicationError as error:
        code = str(error)
        print(code if re.fullmatch(r"browser_publication_[a-z_]{1,80}", code)
              else "browser_synthetic_publication_unavailable", file=sys.stderr)
        return 2
    except BaseException:
        print("browser_synthetic_publication_unavailable", file=sys.stderr)
        return 2
    print("browser_synthetic_publication_updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
