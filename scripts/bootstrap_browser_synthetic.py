"""Private-console, explicit task bootstrap. Default is read-only dry run.

This CLI is code only until a human runs it in the controlled Linux volume.
It never accepts credential or passphrase values through argv or environment.
Publication requires separate explicit prepare and activate commands.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from typing import NoReturn

from app.browser_skill.models import ModelManifest
from app.infra.browser.synthetic_bootstrap import (
    initialize_synthetic_bootstrap,
    preflight_synthetic_bootstrap,
)
from app.infra.browser.synthetic_configuration import synthetic_jev_manifest


class _SilentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ValueError("browser_bootstrap_arguments_invalid")


def main(argv: list[str] | None = None) -> int:
    parser = _SilentParser(allow_abbrev=False, add_help=False)
    parser.add_argument("--enable", action="store_true")
    parser.add_argument("--init", action="store_true")
    parser.add_argument("--jev-request-model")
    parser.add_argument("--jev-deployment-model")
    parser.add_argument("--jev-manifest-digest")
    try:
        args = parser.parse_args(argv)
        fields = (args.jev_request_model, args.jev_deployment_model,
                  args.jev_manifest_digest)
        if args.init:
            if args.enable is not True or (any(value is None for value in fields)
                                           and any(value is not None for value in fields)):
                raise ValueError("browser_bootstrap_arguments_invalid")
            manifest = (synthetic_jev_manifest() if all(value is None for value in fields)
                        else ModelManifest(
                            request_model=args.jev_request_model,
                            deployment_model=args.jev_deployment_model,
                            manifest_digest=args.jev_manifest_digest,
                        ))
            if manifest != synthetic_jev_manifest():
                raise ValueError("browser_bootstrap_manifest_invalid")
            asyncio.run(initialize_synthetic_bootstrap(manifest, enabled=True))
            print("browser_bootstrap_initialized; publication_requires_explicit_prepare_activate")
        else:
            if args.enable or any(value is not None for value in fields):
                raise ValueError("browser_bootstrap_arguments_invalid")
            asyncio.run(preflight_synthetic_bootstrap())
            print("browser_bootstrap_preflight_ready")
        return 0
    except ValueError as error:
        code = str(error)
        print(code if re.fullmatch(r"browser_(?:bootstrap|vault)_[a-z_]{1,80}", code)
              else "browser_bootstrap_unavailable", file=sys.stderr)
        return 2
    except BaseException:
        print("browser_bootstrap_unavailable", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
