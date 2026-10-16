"""Opt-in model-only smoke; missing operator registration/count evidence blocks calls."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import warnings
from collections.abc import Callable
from dataclasses import asdict

from pydantic import SecretStr

from app.infra.browser.synthetic_smoke import SyntheticSmoke


def _secret_input() -> SecretStr:
    # getpass must not fall back to echoing input on an unsupported terminal.
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        return SecretStr(getpass.getpass("TypeSafe API key: "))


def main(
    argv: list[str] | None = None,
    *,
    smoke: SyntheticSmoke | None = None,
    secret_supplier: Callable[[], SecretStr] = _secret_input,
) -> int:
    """Internal operator/test DI only; no new command-line configuration surface."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enable", action="store_true")
    args = parser.parse_args(argv)
    # No verified public registration, pinned deployment or independent count is
    # installed. Do not fabricate them or accept a CLI/environment substitute.
    runner = smoke if smoke is not None else SyntheticSmoke()
    outcome = asyncio.run(runner.run(enabled=args.enable, secret_input=secret_supplier))
    print(json.dumps(asdict(outcome), sort_keys=True))
    return 0 if outcome.status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
