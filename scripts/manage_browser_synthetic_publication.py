"""Explicit existing publication operations; never bootstrap identities or grants."""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
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


async def _operate(operation: str) -> None:
    if operation == "deactivate":
        await deactivate_synthetic_publication(prompt_deactivation_bundle(), enabled=True)
        return
    bundle = prompt_operator_bundle()
    key = prompt_openrouter_key()
    async with open_synthetic_operator(
        bundle, jev_key=key, enabled=True, require_active_publication=False,
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
    try:
        args = parser.parse_args(argv)
        if not args.enable or args.operation is None:
            raise ValueError("browser_operator_disabled")
        asyncio.run(_operate(args.operation))
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
