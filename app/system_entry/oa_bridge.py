"""Small independent OA bridge launcher; never imports app.main."""

from __future__ import annotations

import base64
import logging
import os
from urllib.parse import urlsplit

import uvicorn

from app.config import RedisConnectionURL
from app.infra.system_entry.oa_bridge import create_oa_bridge
from app.infra.system_entry.tickets import RedisOAEntryTickets


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    logging.getLogger("httpcore2").setLevel(logging.WARNING)
    public = os.environ["OA_BRIDGE_PUBLIC_BASE"].rstrip("/")
    if urlsplit(public).scheme not in {"http", "https"} or urlsplit(public).path:
        raise ValueError("oa_bridge_requires_origin")
    origins = os.environ["CSRF_ALLOWED_ORIGINS"].split(",")
    if any(urlsplit(public).hostname == urlsplit(origin.strip()).hostname for origin in origins):
        raise ValueError("oa_bridge_requires_separate_host")
    key = base64.b64decode(os.environ["ETERNALAI_CREDENTIAL_ENCRYPTION_KEY_B64"], validate=True)
    tickets = RedisOAEntryTickets(
        redis_url=RedisConnectionURL.parse(os.environ["REDIS_URL"]), encryption_key=key,
    )
    app = create_oa_bridge(
        tickets=tickets, upstream=os.environ["OA_BASE_URL"], public_base=public,
    )
    host, port = os.environ.get("OA_BRIDGE_LISTEN", "127.0.0.1:18083").rsplit(":", 1)
    # The redeem path carries a secret. No HTTP access log or traceback with a URL.
    uvicorn.run(app, host=host, port=int(port), access_log=False, log_level="critical")


if __name__ == "__main__":
    main()
