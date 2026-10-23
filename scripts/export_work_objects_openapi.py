"""Export Work Object route schemas without production composition or lifecycle.

Only HTTP factories/DTOs are imported. No service, engine, settings or database is
constructed; no request or lifespan is executed. The sole output is the checked-in
Work Object OpenAPI input for its existing Orval project.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.api.v1.auth import make_require_principal  # noqa: E402
from app.api.v1.work_object_series import make_router as make_series_router  # noqa: E402
from app.api.v1.work_objects import make_router  # noqa: E402


def document() -> dict[str, Any]:
    application = FastAPI(title="EternalAI", version="0.1.0")
    principal = make_require_principal(None)
    application.include_router(make_router(None, principal), prefix="/api/v1/work-objects")
    application.include_router(
        make_series_router(None, principal), prefix="/api/v1/work-object-series"
    )
    return application.openapi()


if __name__ == "__main__":
    target = Path(__file__).resolve().parents[1] / "web/openapi/work-objects.openapi.json"
    target.write_text(
        json.dumps(document(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
