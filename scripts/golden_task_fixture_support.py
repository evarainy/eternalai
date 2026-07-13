"""Golden Task fixture loading and mock state injection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = REPO_ROOT / "tests" / "golden_tasks" / "fixtures"


def load_fixture(gt_id: str) -> dict[str, Any]:
    path = FIXTURES_DIR / f"{gt_id}.json"
    with path.open(encoding="utf-8") as fixture_file:
        return cast(dict[str, Any], json.load(fixture_file))


def apply_mock_state(adapter: Any, state: Any) -> None:
    """Inject state into adapter; leave sentinel state untouched."""
    if isinstance(state, dict):
        adapter.set_state(state)
    elif state != "should_not_be_called":
        raise ValueError(f"Unexpected mock state value: {state!r}")


__all__ = ("FIXTURES_DIR", "apply_mock_state", "load_fixture")
