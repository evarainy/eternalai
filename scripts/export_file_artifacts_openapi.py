"""Export the file artifact OpenAPI document (download + chat upload) without composition.

Only HTTP factories/DTOs are imported. No service, engine, settings or database is
constructed; no request or lifespan is executed. The output is a checked-in specification
only: the frontend downloads through plain links and uploads with a hand-written raw-body
request, so no generated client consumes it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.api.v1.auth import make_require_principal  # noqa: E402
from app.api.v1.file_artifacts import make_router  # noqa: E402


def document() -> dict[str, Any]:
    application = FastAPI(title="EternalAI", version="0.1.0")
    principal = make_require_principal(None)
    application.include_router(make_router(None, principal), prefix="/api/v1/file-artifacts")
    return application.openapi()


if __name__ == "__main__":
    target = Path(__file__).resolve().parents[1] / "web/openapi/file-artifacts.openapi.json"
    target.write_text(
        json.dumps(document(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
