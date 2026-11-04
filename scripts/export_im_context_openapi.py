"""Export IM and its changed Runtime contract without settings, lifespan or a database."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.api.v1.auth import make_require_principal  # noqa: E402
from app.api.v1.im_context import make_router  # noqa: E402
from app.api.v1.runtime import make_router as runtime_router  # noqa: E402


def document(*, runtime: bool = False) -> dict[str, Any]:
    application = FastAPI(title="EternalAI", version="0.1.0")
    principal = make_require_principal(None)
    if runtime:
        application.include_router(runtime_router(None, principal, None), prefix="/api/v1/runtime")
    else:
        application.include_router(make_router(None, principal, None), prefix="/api/v1/im-context")
    return application.openapi()


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1] / "web/openapi"
    for filename, runtime in (("im-context", False), ("runtime", True)):
        (root / f"{filename}.openapi.json").write_text(
            json.dumps(document(runtime=runtime), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8", newline="\n",
        )
