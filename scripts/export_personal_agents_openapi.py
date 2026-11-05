"""Export personal-agent contracts without composition, credentials or DB access."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.api.v1.auth import make_require_principal  # noqa: E402
from app.api.v1.im_inbound import make_router as inbound_router  # noqa: E402
from app.api.v1.personal_agents import make_router  # noqa: E402


def document() -> dict[str, Any]:
    application = FastAPI(title="EternalAI", version="0.1.0")
    application.include_router(make_router(None, make_require_principal(None)),
                               prefix="/api/v1/personal-agents")
    application.include_router(inbound_router(None), prefix="/api/v1/im-inbound")
    return application.openapi()


if __name__ == "__main__":
    target = Path(__file__).resolve().parents[1] / "web/openapi/personal-agents.openapi.json"
    target.write_text(json.dumps(document(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8", newline="\n")
