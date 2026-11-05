"""Export only the IM delivery HTTP contract, without composition or database access."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.api.v1.auth import make_require_principal  # noqa: E402
from app.api.v1.im_deliveries import make_router  # noqa: E402


def document() -> dict[str, Any]:
    application = FastAPI(title="EternalAI", version="0.1.0")
    principal = make_require_principal(None)
    application.include_router(make_router(None, principal), prefix="/api/v1/im-deliveries")
    return application.openapi()


if __name__ == "__main__":
    target = Path(__file__).resolve().parents[1] / "web/openapi/im-deliveries.openapi.json"
    target.write_text(
        json.dumps(document(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
