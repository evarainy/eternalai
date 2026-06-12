"""Run Golden Task judgments and emit a machine-readable JSON summary."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--summary",
        action="store_true",
        help="emit the Golden Task summary JSON",
    )
    parser.parse_args(argv)

    try:
        evaluate_all_golden_tasks, build_summary = _load_runner()
        summary = build_summary(evaluate_all_golden_tasks())
    except Exception as exc:
        print(f"Golden Task runner infrastructure failure: {exc}", file=sys.stderr)
        return 2

    json.dump(summary, sys.stdout, ensure_ascii=False, sort_keys=True)
    sys.stdout.write("\n")
    return 0


def _load_runner() -> tuple[
    Callable[[], list[Any]],
    Callable[[Sequence[Any]], dict[str, Any]],
]:
    module = importlib.import_module("tests.golden_tasks.test_golden_tasks")
    return (
        cast(Callable[[], list[Any]], getattr(module, "evaluate_all_golden_tasks")),
        cast(Callable[[Sequence[Any]], dict[str, Any]], getattr(module, "build_summary")),
    )


if __name__ == "__main__":
    raise SystemExit(main())
