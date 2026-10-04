"""Opt-in, fixed single-Run synthetic browser HTTP client."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict
from typing import NoReturn

from app.infra.browser.synthetic_trial_client import TrialClientError, run_trial
from app.infra.browser.synthetic_vault import DIAGNOSTIC_TRIAL, ORIGINAL_TRIAL


class _SilentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise TrialClientError("browser_trial_arguments_invalid")


def main(argv: list[str] | None = None) -> int:
    parser = _SilentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--operation", choices=("submit", "inspect", "cancel"), required=True)
    parser.add_argument("--enable", action="store_true")
    parser.add_argument("--private-stdin", action="store_true")
    parser.add_argument(
        "--trial-id", choices=(ORIGINAL_TRIAL, DIAGNOSTIC_TRIAL), default=ORIGINAL_TRIAL
    )
    try:
        args = parser.parse_args(argv)
        outcome = asyncio.run(run_trial(args.operation, enabled=args.enable,
                                        private_stdin=args.private_stdin, trial_id=args.trial_id))
    except TrialClientError as error:
        print(json.dumps({"code": error.code}, sort_keys=True))
        return 2
    except Exception:
        print(json.dumps({"code": "browser_trial_unavailable"}, sort_keys=True))
        return 2
    print(json.dumps({key: value for key, value in asdict(outcome).items()
                      if key != "exit_code"}, sort_keys=True))
    return outcome.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
