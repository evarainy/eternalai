"""Export browser and affected API contracts without configuration or connections."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from fastapi import FastAPI

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.api.v1.auth import make_require_principal  # noqa: E402
from app.api.v1.browser_runs import make_router as browser_router  # noqa: E402
from app.api.v1.me import make_router as me_router  # noqa: E402
from app.api.v1.runtime import make_router as runtime_router  # noqa: E402


def documents() -> dict[str, dict[str, object]]:
    principal = make_require_principal(None)
    routes = {
        "browser": (browser_router(None, principal, None), "/api/v1"),
        "runtime": (runtime_router(None, principal, None), "/api/v1/runtime"),
        "me": (me_router(None, principal), "/api/v1/me"),
    }
    result: dict[str, dict[str, object]] = {}
    for name, (router, prefix) in routes.items():
        application = FastAPI(title="EternalAI", version="0.1.0")
        application.include_router(router, prefix=prefix)
        result[name] = application.openapi()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("web/openapi"))
    output = parser.parse_args().output_dir
    output.mkdir(parents=True, exist_ok=True)
    for name, document in documents().items():
        (output / f"{name}.openapi.json").write_text(
            json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8", newline="\n",
        )
