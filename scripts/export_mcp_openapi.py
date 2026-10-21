"""Export the local MCP API without environment configuration or connections."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from fastapi import FastAPI

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.api.v1.auth import make_require_principal  # noqa: E402
from app.api.v1.mcp import make_router  # noqa: E402


def document() -> dict:
    application = FastAPI(title="EternalAI", version="0.1.0")
    application.include_router(
        make_router(None, make_require_principal(None)), prefix="/api/v1/mcp"
    )
    return application.openapi()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("web/openapi/mcp.openapi.json"))
    target = parser.parse_args().output
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(document(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
